"""The conversational agent (WhatsApp-style), running on the phone — no network, no LLM needed.

Channel-agnostic: it consumes events ``{"type": "text"|"button"|"image", ...}`` and returns
messages ``{"text", "buttons": [{"id", "title"}], "image"}``. The web simulator and the
WhatsApp Cloud API adapter both drive the same engine.

Flows: capture (multi-page, quality check, on-device page recognition and PII masking)
-> queue for AI -> review of doubtful fields (confirm / alternative / edit / retake /
show the image) -> bulk confirmation of confident fields -> patient matching (never
auto-create) -> re-digitisation diff -> registration -> sync. Manual entry covers every
field when AI is unavailable.

The conversation state is persisted (encrypted) after every step: killing the app
resumes exactly where the midwife was.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from dayone.device.i18n import QUALITY, REASONS, t
from dayone.device.lifecycle import STATE_LABEL_FR, RecordState
from dayone.device.store import DeviceStore, new_id
from dayone.extraction.normalize import fold, parse_value
from dayone.extraction.quality import assess_capture
from dayone.extraction.register import Registrar
from dayone.forms.layout import ALL_FIELDS, PAGE_FIELDS, PAGE_ORDER, VISIT_COLS
from dayone.forms.templates import SCALE
from dayone.linking import find_candidates, plausible
from dayone.pii import redact_capture, scrub_text
from dayone.records import (
    confident_fields,
    confirm_all,
    decide,
    diff_with_profile,
    display,
    display_value,
    is_validated,
    label,
    pages_already_in_profile,
    profile_summary,
    quasi_identifiers,
    review_queue,
    update_profile,
    vocabulary_examples,
)
from dayone.schema import PAGE_TITLES_EN, PAGE_TITLES_FR, FieldKind, FieldStatus, PageType, ValueType

log = logging.getLogger(__name__)

REVIEWABLE = {RecordState.AI_PROCESSED, RecordState.NEEDS_REVIEW, RecordState.VALIDATED,
              RecordState.DUPLICATE_SUSPECTED, RecordState.MANUAL_REVIEW_REQUIRED}
MAX_SIDE = 2000  # stored photos are downscaled to this long side (enough for OCR, saves space)
SPECIAL_WORDS = {
    "vide": FieldStatus.NOT_PROVIDED, "blank": FieldStatus.NOT_PROVIDED, "passer": FieldStatus.NOT_PROVIDED,
    "skip": FieldStatus.NOT_PROVIDED, "illisible": FieldStatus.ILLEGIBLE, "illegible": FieldStatus.ILLEGIBLE,
    "inconnu": FieldStatus.UNKNOWN, "unknown": FieldStatus.UNKNOWN, "?": FieldStatus.UNKNOWN,
    "-": FieldStatus.NOT_APPLICABLE, "na": FieldStatus.NOT_APPLICABLE, "n/a": FieldStatus.NOT_APPLICABLE,
}


def _msg(text: str, buttons: list[tuple[str, str]] | None = None, image: str | None = None) -> dict:
    return {"role": "agent", "text": text, "buttons": [{"id": i, "title": s} for i, s in (buttons or [])],
            "image": image}


def page_title(page_type: str | None, lang: str) -> str:
    if page_type is None:
        return "?"
    return (PAGE_TITLES_FR if lang == "fr" else PAGE_TITLES_EN)[PageType(page_type)]


class Agent:
    def __init__(self, store: DeviceStore, registrar: Registrar, midwife_id: str,
                 is_online=lambda: False, kick_sync=lambda: None, retry_ai=None) -> None:
        self.store = store
        self.registrar = registrar
        self.midwife = midwife_id
        self.is_online = is_online
        self.kick_sync = kick_sync
        self.retry_ai = retry_ai  # SyncEngine.retry_ai, wired by the app
        self.lock = threading.RLock()
        self.state = store.get("agent", {"lang": "fr", "mode": "idle"})
        self._pending_image: bytes | None = None  # unmasked photo awaiting "keep anyway": memory only

    # ------------------------------------------------------------------ plumbing
    @property
    def lang(self) -> str:
        return self.state.get("lang", "fr")

    def tr(self, key: str, **kw) -> str:
        return t(self.lang, key, **kw)

    def _save(self) -> None:
        self.store.put("agent", self.state)

    def _set_mode(self, mode: str, **kw) -> None:
        self.state = {"lang": self.lang, "mode": mode, **kw}
        self._save()

    def _log(self, messages: list[dict]) -> list[dict]:
        for m in messages:
            m["id"] = self.store.log_chat(m)
        return messages

    # Buttons bound to the record (and field) on screen when they were offered: an old button
    # (WhatsApp keeps them all tappable) can never act on another record or another field.
    TAGGED = ("f:", "rest:", "m:pick:", "m:new", "m:unsure", "m:code_", "d:", "cap:", "q:")

    def _tag(self, messages: list[dict]) -> list[dict]:
        rid, fid = self.state.get("record_id"), self.state.get("current")
        for m in messages:
            for b in m.get("buttons", []):
                if rid and b["id"].startswith(self.TAGGED) and "#" not in b["id"]:
                    b["id"] += f"#{rid}" + (f"|{fid}" if b["id"].startswith("f:") and fid else "")
        return messages

    def handle(self, event: dict) -> list[dict]:
        with self.lock:
            user_msg = {"role": "user", "text": event.get("text") or event.get("title") or "",
                        "image": event.get("image_ref"), "buttons": []}
            self._log([user_msg])
            try:
                out = self._tag(self._dispatch(event))
            finally:
                self._save()
            return [user_msg] + self._log(out)

    def notify(self, kind: str, data: dict) -> list[dict]:
        """Called by the sync engine (another thread) when background work completes."""
        with self.lock:
            out: list[dict] = []
            rid = data.get("record_id")
            if kind == "processed":
                out = self._on_processed(rid)
            elif kind == "processing_failed":
                rec = self.store.get_record(rid)
                out = [_msg(self.tr("processing_failed", date=self._date(rec.created_at), reason=data.get("reason", "")),
                            [(f"manual:record:{rid}", self.tr("btn_manual_short")),
                             (f"ai:retry:{rid}", self.tr("btn_retry_ai"))])]
            elif kind == "synced":
                out = [_msg(self.tr("synced"))]
                if data.get("possible_duplicates"):
                    out.append(_msg(self.tr("sync_dup")))
            elif kind == "sync_failed":
                out = [_msg(self.tr("sync_failed", error=data.get("error", "")))]
            self._save()
            return self._log(out)

    def _date(self, ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")

    # ------------------------------------------------------------------ dispatch
    def _dispatch(self, ev: dict) -> list[dict]:
        typ = ev.get("type")
        mode = self.state.get("mode", "idle")
        if typ == "image":
            if mode in ("capture", "quality_pending"):
                return self._on_image(ev["data"])
            rec = self._start_capture()
            return [_msg(self.tr("capture_start"))] + self._on_image(ev["data"], record_id=rec)
        if typ == "button":
            return self._on_button(ev["id"])
        text = (ev.get("text") or "").strip()
        low = text.lower()
        # global commands
        if low in ("menu", "bonjour", "salut", "hello", "hi", "start"):
            return self._menu()
        if low in ("aide", "help", "?") and mode == "idle":
            return [_msg(self.tr("help"))] + self._menu(greet=False)
        if low.startswith(("langue", "language", "lang")):
            self.state["lang"] = "en" if "en" in low.split()[-1] else "fr"
            return [_msg(self.tr("lang_set"))] + self._menu(greet=False)
        if low in ("annuler", "cancel"):
            return self._cancel()
        if low in ("file", "queue", "état", "etat", "status"):
            return self._queue()
        if mode == "idle" and low.split(" ")[0] in ("dossier", "dossiers", "patiente", "patientes", "record",
                                                     "records", "patient"):
            return self._dossier(text.split(" ", 1)[1].strip() if " " in text.strip() else "")
        if mode == "correct":
            return self._on_correction(text)
        if mode == "manual":
            return self._on_manual_text(text)
        if mode == "ask_code":
            return self._on_code(text)
        if mode == "pick_field":
            return self._on_pick_field(text)
        if mode == "rest" and low in ("détail", "detail"):
            return self._rest_detail()
        if low in ("nouvelle", "new", "photo"):
            self._start_capture()
            return [_msg(self.tr("capture_start"), [("cap:done", self.tr("btn_done")), ("cap:cancel", self.tr("btn_cancel"))])]
        if low in ("saisie", "manual"):
            return self._manual_start(None)
        if low in ("vérifier", "verifier", "review"):
            return self._review_next_record()
        if low in ("fin", "terminer", "done", "end") and mode == "capture":
            return self._finish_capture()
        if mode == "capture":
            return [_msg(self.tr("busy_capture"), [("cap:done", self.tr("btn_done")), ("cap:cancel", self.tr("btn_cancel"))])]
        return [_msg(self.tr("unknown"))] + self._menu(greet=False)

    # A button is only valid in the step that offered it: in WhatsApp old buttons stay tappable.
    BUTTON_MODES = {"cap": {"capture", "quality_pending"}, "q": {"quality_pending", "capture"}, "f": {"review"},
                    "rest": {"rest", "pick_field"}, "m": {"match", "confirm_code"}, "d": {"redigit"}}
    MANUAL_ANSWERS = {"yes", "no", "skip", "end"}

    def _stale(self, bid: str) -> bool:
        bid, _, tag = bid.partition("#")
        if tag:
            rid, _, fid = tag.partition("|")
            if rid != self.state.get("record_id") or (fid and fid != self.state.get("current")):
                return True
        head, _, arg = bid.partition(":")
        mode = self.state.get("mode", "idle")
        if head in self.BUTTON_MODES:
            return mode not in self.BUTTON_MODES[head] and not bid.startswith("m:decide:")
        if head == "manual":
            if arg in self.MANUAL_ANSWERS:
                return mode != "manual"
            if arg.startswith(("page:", "visit:")) or arg in ("finish", "other"):
                return mode != "manual_pick"
        return False

    def _on_button(self, bid: str) -> list[dict]:
        if self._stale(bid):
            return [_msg(self.tr("stale_button"))] + self._menu(greet=False)
        bid = bid.partition("#")[0]
        head, _, arg = bid.partition(":")
        if bid == "menu:new":
            self._start_capture()
            return [_msg(self.tr("capture_start"), [("cap:done", self.tr("btn_done")), ("cap:cancel", self.tr("btn_cancel"))])]
        if bid == "menu:queue":
            return self._queue()
        if bid == "menu:manual":
            return self._manual_start(None)
        if bid == "menu:review":
            return self._review_next_record()
        if bid == "menu:records":
            return self._dossier("")
        if head == "p" and arg.startswith("show:"):
            return self._show_patient(arg.split(":", 1)[1])
        if bid == "cap:done":
            return self._finish_capture()
        if bid == "cap:cancel":
            return self._cancel()
        if bid == "q:retake":
            self.state["mode"] = "capture"
            self._pending_image = None
            return [_msg(self.tr("capture_start"))]
        if bid == "q:keep":
            data, self._pending_image = self._pending_image, None
            self.state["mode"] = "capture"
            if data is None:  # the app restarted meanwhile: the unmasked photo was never kept
                return [_msg(self.tr("photo_lost"))]
            return self._on_image(data, force=True)
        if head == "rev":
            if arg.startswith("start"):
                rid = arg.split(":", 1)[1] if ":" in arg else None
                return self._review_start(rid)
            return self._menu(greet=False)
        if head == "f":
            return self._on_field_button(arg)
        if head == "rest":
            return self._on_rest_button(arg)
        if head == "m":
            return self._on_match_button(arg)
        if head == "d":
            return self._on_redigit_button(arg)
        if head == "manual":
            return self._on_manual_button(arg)
        if head == "ai" and arg.startswith("retry:"):
            rid = arg.split(":", 1)[1]
            if self.store.get_record(rid).state != RecordState.MANUAL_REVIEW_REQUIRED or self.retry_ai is None:
                return [_msg(self.tr("stale_button"))] + self._menu(greet=False)
            n = self.retry_ai(rid, self.midwife)
            key = "queued_online" if self.is_online() else "queued_offline"
            return [_msg(self.tr(key, n=n))] + self._menu(greet=False)
        return [_msg(self.tr("unknown"))]

    # ------------------------------------------------------------------ menu / queue
    def _menu(self, greet: bool = True) -> list[dict]:
        self.state["mode"] = "idle"
        self._pending_image = None
        buttons = [("menu:new", self.tr("btn_new")), ("menu:records", self.tr("btn_records")),
                   ("menu:queue", self.tr("btn_queue")), ("menu:manual", self.tr("btn_manual"))]
        n = len(self._records_to_review())
        if n:
            buttons.insert(0, ("menu:review", self.tr("btn_review_pending", n=n)))
        return [_msg(self.tr("welcome") if greet else "👇", buttons)]

    def _records_to_review(self) -> list:
        recs = self.store.list_records({RecordState.AI_PROCESSED, RecordState.NEEDS_REVIEW,
                                        RecordState.MANUAL_REVIEW_REQUIRED, RecordState.VALIDATED,
                                        RecordState.DUPLICATE_SUSPECTED})
        return [r for r in recs if not r.payload.get("cancelled")]

    def _queue(self) -> list[dict]:
        recs = self.store.list_records()
        lines = [self.tr("queue_title", net=self.tr("net_on") if self.is_online() else self.tr("net_off"))]
        if not recs:
            lines.append(self.tr("queue_empty"))
        for r in recs[-12:]:
            pages = self.store.list_pages(r.id)
            label_state = STATE_LABEL_FR[r.state] if self.lang == "fr" else r.state.value
            extra = ""
            if r.state in (RecordState.NEEDS_REVIEW, RecordState.AI_PROCESSED):
                extra = f" · {len(review_queue(r.payload))} ❓"
            lines.append(self.tr("queue_line", date=self._date(r.created_at), pages=len(pages), state=label_state,
                                 extra=extra))
        return [_msg("\n".join(lines))] + self._menu(greet=False)

    def _cancel(self) -> list[dict]:
        rid = self.state.get("record_id")
        mode = self.state.get("mode")
        if mode in ("capture", "quality_pending") and rid:
            rec = self.store.get_record(rid)
            if rec.state == RecordState.CAPTURED:
                payload = rec.payload
                payload["cancelled"] = True
                with self.store.atomic():
                    self.store.update_record(rid, payload=payload)
                    self.store.transition(rid, RecordState.MANUAL_REVIEW_REQUIRED, self.midwife, "capture cancelled")
            self._set_mode("idle")
            return [_msg(self.tr("cancelled"))] + self._menu(greet=False)
        self._set_mode("idle")
        return self._menu(greet=False)

    # ------------------------------------------------------------------ capture
    def _start_capture(self, record_id: str | None = None, retake: str | None = None) -> str:
        rid = record_id or self.store.create_record(self.midwife, {"lang": self.lang})
        self._set_mode("capture", record_id=rid, retake=retake)
        return rid

    def _on_image(self, data: bytes, record_id: str | None = None, force: bool = False) -> list[dict]:
        rid = record_id or self.state.get("record_id")
        if rid is None:
            rid = self._start_capture()
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return [_msg(self.tr("unknown"))]
        quality = assess_capture(img)
        if not quality.ok and not force:
            # The photo is not masked yet: it is kept in memory only, never written to disk.
            self._pending_image = data
            self.state.update(mode="quality_pending")
            issues = "\n".join(f"• {QUALITY[self.lang][i]}" for i in quality.issues)
            return [_msg(self.tr("quality_bad", issues=issues),
                         [("q:retake", self.tr("btn_retake")), ("q:keep", self.tr("btn_keep"))])]
        retake = self.state.get("retake")
        reg = self.registrar.register(img, expected=PageType(retake) if retake else None)
        if not reg.ok:
            self.state["mode"] = "capture"
            if reg.reason.startswith("unexpected_page") and retake:
                return [_msg(self.tr("wrong_page", found=page_title(reg.page_type.value, self.lang),
                                     expected=page_title(retake, self.lang)))]
            return [_msg(self.tr("page_unknown"), [("q:retake", self.tr("btn_retake")),
                                                  ("manual:start", self.tr("btn_manual_short"))])]
        redacted, zones = redact_capture(img, reg.homography, reg.page_type)
        h, w = redacted.shape[:2]
        s = min(1.0, MAX_SIDE / max(h, w))
        if s < 1.0:
            redacted = cv2.resize(redacted, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        homography = reg.homography @ np.diag([1 / s, 1 / s, 1.0])  # stored image px -> template px
        ok, buf = cv2.imencode(".jpg", redacted, [cv2.IMWRITE_JPEG_QUALITY, 88])
        assert ok
        replaced = False
        for p in self.store.list_pages(rid):
            if p.page_type == reg.page_type.value:
                self.store.set_page_status(p.id, "replaced")
                replaced = True
        pid = self.store.add_page(rid, buf.tobytes(), reg.page_type.value,
                                  {"ok": quality.ok, "forced": force, "issues": quality.issues, **quality.metrics},
                                  {**reg.summary(), "homography": homography.tolist(), "pii_masked": zones})
        n = len(self.store.list_pages(rid))
        self.state.update(mode="capture", record_id=rid)
        text = self.tr("page_ok", page=page_title(reg.page_type.value, self.lang), n=n)
        if replaced:
            text += "\n" + self.tr("page_replaced")
        image = f"/api/pages/{pid}/image"
        if retake:
            return [_msg(text, image=image)] + self._finish_capture()
        return [_msg(text, [("cap:done", self.tr("btn_done")), ("cap:cancel", self.tr("btn_cancel"))], image=image)]

    def _finish_capture(self) -> list[dict]:
        rid = self.state.get("record_id")
        if not rid:
            return self._menu(greet=False)
        pages = [p for p in self.store.list_pages(rid) if p.status == "stored"]
        if not pages:
            return [_msg(self.tr("no_pages"), [("cap:cancel", self.tr("btn_cancel"))])]
        # From CAPTURED, or from NEEDS_REVIEW when a page was retaken (decisions already taken are kept).
        self.store.transition(rid, RecordState.PENDING_AI, self.midwife, f"{len(pages)} page(s) to read")
        for p in pages:
            self.store.enqueue("process_page", p.id, f"process:{p.id}")
        n = len(self.store.list_pages(rid))
        self._set_mode("idle")
        self.kick_sync()
        key = "queued_online" if self.is_online() else "queued_offline"
        return [_msg(self.tr(key, n=n))] + self._menu(greet=False)

    # ------------------------------------------------------------------ processing results
    def _on_processed(self, rid: str) -> list[dict]:
        rec = self.store.get_record(rid)
        if rec.state == RecordState.AI_PROCESSED:
            self.store.transition(rid, RecordState.NEEDS_REVIEW, "agent", "presented to the midwife")
        fields = rec.payload.get("fields", {})
        doubt = len(review_queue(rec.payload))
        text = self.tr("processed", date=self._date(rec.created_at), total=len(fields), sure=len(fields) - doubt,
                       doubt=doubt)
        return [_msg(text, [(f"rev:start:{rid}", self.tr("btn_review_now")), ("rev:later", self.tr("btn_later"))])]

    # ------------------------------------------------------------------ review
    def _review_next_record(self) -> list[dict]:
        recs = self._records_to_review()
        if not recs:
            return [_msg(self.tr("nothing_to_review"))] + self._menu(greet=False)
        return self._review_start(recs[0].id)

    def _review_start(self, rid: str | None) -> list[dict]:
        if rid is None:
            return self._review_next_record()
        rec = self.store.get_record(rid)
        if rec.state not in REVIEWABLE or rec.payload.get("cancelled"):
            return [_msg(self.tr("stale_button"))] + self._menu(greet=False)
        if rec.state == RecordState.AI_PROCESSED:
            self.store.transition(rid, RecordState.NEEDS_REVIEW, "agent", "presented to the midwife")
            rec = self.store.get_record(rid)
        if rec.state == RecordState.MANUAL_REVIEW_REQUIRED:
            if rec.payload.get("match_undecided") and is_validated(rec.payload):
                return [_msg(self.tr("match_unsure"), [(f"m:decide:{rid}", self.tr("btn_decide_now"))])]
            return self._manual_start(rid)
        if rec.state in (RecordState.VALIDATED, RecordState.DUPLICATE_SUSPECTED):
            return self._match_start(rid)
        queue = review_queue(rec.payload)
        self._set_mode("review", record_id=rid, total=len(queue))
        return self._review_next()

    def _review_next(self) -> list[dict]:
        rid = self.state["record_id"]
        rec = self.store.get_record(rid)
        queue = review_queue(rec.payload)
        if not queue:
            return self._rest_summary()
        fid = queue[0]
        total = max(self.state.get("total", len(queue)), len(queue))
        k = total - len(queue) + 1
        self.state.update(mode="review", current=fid, total=total)
        return [self._field_question(rec.payload, fid, k, total)]

    def _field_question(self, payload: dict, fid: str, k: int, n: int) -> dict:
        e = payload["fields"][fid]
        spec = ALL_FIELDS[fid]
        page = page_title(fid.split(".")[0], self.lang)
        lab = label(fid, self.lang)
        show = [("f:show", self.tr("btn_show")), ("f:retake", self.tr("btn_retake_page"))]
        if spec.kind == FieldKind.CHECKBOX:
            return _msg(self.tr("review_box", k=k, n=n, label=lab, page=page),
                        [("f:tick", self.tr("btn_ticked")), ("f:untick", self.tr("btn_unticked"))] + show)
        if e.get("status") in (FieldStatus.NOT_APPLICABLE.value, FieldStatus.UNKNOWN.value):
            shown = f"« {e.get('raw') or ''} » → {display(fid, e, self.lang)}"
            return _msg(self.tr("review_field", k=k, n=n, label=lab, page=page, value=shown,
                                conf=int(round(100 * float(e.get("confidence", 0)))), alts="", why=""),
                        [("f:confirm", self.tr("btn_confirm_value", value=display(fid, e, self.lang))),
                         ("f:edit", self.tr("btn_edit")), ("f:blank", self.tr("btn_blank"))] + show)
        if e.get("status") == FieldStatus.ILLEGIBLE.value or e.get("value") is None:
            return _msg(self.tr("review_illegible", k=k, n=n, label=lab, page=page),
                        [("f:edit", self.tr("btn_type_value")), ("f:blank", self.tr("btn_blank")),
                         ("f:illegible", self.tr("btn_illegible"))] + show)
        value = display_value(fid, e.get("value"), self.lang)
        raw = (e.get("raw") or "").strip()
        shown = value
        if raw and spec.vocabulary and fold(raw) != fold(value):
            shown = f"« {raw} » → {value}"  # what is written, and how it was understood
        alts = self._alternatives(fid, e)
        alt_text = self.tr("review_alts", alts=" · ".join(display_value(fid, a, self.lang) for a in alts)) if alts else ""
        reasons = [REASONS[self.lang].get(f, f) for f in e.get("flags", []) if f in REASONS[self.lang]]
        feats = e.get("features", {})
        if feats.get("second_agree") == 0.0 and not feats.get("second_missing"):
            reasons.append(REASONS[self.lang]["two_readers_disagree"])
        why = self.tr("why", reasons=", ".join(reasons)) if reasons else ""
        buttons = [("f:confirm", self.tr("btn_confirm_value", value=value))]
        buttons += [(f"f:alt:{i}", f"{display_value(fid, a, self.lang)}") for i, a in enumerate(alts)]
        buttons += [("f:edit", self.tr("btn_edit"))] + show
        return _msg(self.tr("review_field", k=k, n=n, label=lab, page=page, value=shown,
                            conf=int(round(100 * float(e.get("confidence", 0)))), alts=alt_text, why=why), buttons)

    def _alternatives(self, fid: str, e: dict) -> list:
        spec = ALL_FIELDS[fid]
        out = []
        for raw in e.get("alternatives", []):
            p = parse_value(spec, raw)
            if p.status == FieldStatus.KNOWN and p.ok and p.value != e.get("value") and p.value not in out:
                out.append(p.value)
        return out[:2]

    def _on_field_button(self, arg: str) -> list[dict]:
        rid, fid = self.state.get("record_id"), self.state.get("current")
        if not rid or not fid:
            return self._menu(greet=False)
        rec = self.store.get_record(rid)
        payload = rec.payload
        e = payload["fields"][fid]
        now = time.time()
        if arg == "confirm":
            status = e.get("status")
            keep = status if status in (FieldStatus.NOT_APPLICABLE.value, FieldStatus.UNKNOWN.value) \
                else FieldStatus.KNOWN.value
            decide(payload, fid, keep, e.get("value"), "confirmed", self.midwife, now)
        elif arg.startswith("alt:"):
            alts = self._alternatives(fid, e)
            k = int(arg.split(":")[1])
            if k >= len(alts):
                return [self._field_question(payload, fid, 1, 1)]
            decide(payload, fid, FieldStatus.KNOWN.value, alts[k], "corrected", self.midwife, now)
        elif arg in ("tick", "untick"):
            decide(payload, fid, FieldStatus.KNOWN.value, arg == "tick", "corrected", self.midwife, now)
        elif arg == "blank":
            decide(payload, fid, FieldStatus.NOT_PROVIDED.value, None, "corrected", self.midwife, now)
        elif arg == "illegible":
            decide(payload, fid, FieldStatus.ILLEGIBLE.value, None, "confirmed", self.midwife, now)
        elif arg == "edit":
            self.state.update(mode="correct", back="review")
            return [_msg(self.tr("ask_value", label=label(fid, self.lang), example=vocabulary_examples(fid, self.lang)))]
        elif arg == "show":
            # the crop, then the same question again so the answer buttons stay at hand
            return [_msg(self.tr("crop_caption", label=label(fid, self.lang)),
                         image=f"/api/crops/{e.get('page_id')}/{fid}")] + self._review_next()
        elif arg == "retake":
            page_type = fid.split(".")[0]
            self._set_mode("capture", record_id=rid, retake=page_type)
            return [_msg(self.tr("retake_start", page=page_title(page_type, self.lang)))]
        self.store.update_record(rid, payload=payload)
        return self._review_next()

    def _on_correction(self, text: str) -> list[dict]:
        rid, fid = self.state["record_id"], self.state["current"]
        spec = ALL_FIELDS[fid]
        special = SPECIAL_WORDS.get(text.strip().lower())
        rec = self.store.get_record(rid)
        payload = rec.payload
        now = time.time()
        if special is not None:
            decide(payload, fid, special.value, None, "corrected", self.midwife, now)
            shown = display(fid, {"status": special.value}, self.lang)
        else:
            if spec.kind == FieldKind.CHECKBOX:
                p = parse_value(spec.model_copy(update={"value_type": ValueType.BOOL}), text)
            else:
                p = parse_value(spec, text)
            if p.status != FieldStatus.KNOWN or not p.ok:
                return [_msg(self.tr("bad_value", text=text, label=label(fid, self.lang),
                                     example=vocabulary_examples(fid, self.lang)))]
            value = scrub_text(p.value) if spec.value_type == ValueType.TEXT else p.value  # never alter a code
            decide(payload, fid, FieldStatus.KNOWN.value, value, "corrected", self.midwife, now)
            shown = display_value(fid, value, self.lang)
        self.store.update_record(rid, payload=payload)
        out = [_msg(self.tr("saved", label=label(fid, self.lang), value=shown))]
        back = self.state.get("back", "review")
        if back == "rest":
            self.state["mode"] = "rest"
            return out + self._rest_summary()
        self.state["mode"] = "review"
        return out + self._review_next()

    # ------------------------------------------------------------------ confident fields
    def _rest_summary(self) -> list[dict]:
        rid = self.state["record_id"]
        rec = self.store.get_record(rid)
        rest = confident_fields(rec.payload)
        if not rest:
            return self._validate(rid)
        self.state.update(mode="rest", record_id=rid)
        interesting = [f for f in rest if rec.payload["fields"][f].get("status") == FieldStatus.KNOWN.value
                       and ALL_FIELDS[f].kind == FieldKind.TEXT]
        statuses = [rec.payload["fields"][f].get("status") for f in rest]
        lines = [self.tr("rest_intro", n=len(rest))]
        blanks, na = statuses.count(FieldStatus.NOT_PROVIDED.value), statuses.count(FieldStatus.NOT_APPLICABLE.value)
        if blanks or na:
            lines.append(self.tr("rest_empty", blank=blanks, na=na))
        for fid in interesting[:12]:
            lines.append(f"• {label(fid, self.lang)} : {display(fid, rec.payload['fields'][fid], self.lang)}")
        if len(rest) > 12:
            lines.append(self.tr("rest_more", n=len(rest) - min(12, len(interesting))))
        return [_msg("\n".join(lines), [("rest:confirm", self.tr("btn_confirm_all")),
                                        ("rest:fix", self.tr("btn_fix_one")), ("rest:detail", self.tr("btn_detail"))])]

    def _rest_detail(self) -> list[dict]:
        rid = self.state["record_id"]
        rec = self.store.get_record(rid)
        rest = confident_fields(rec.payload)
        self.state["numbered"] = rest
        lines, current_page = [], None
        for k, fid in enumerate(rest, 1):
            pt = fid.split(".")[0]
            if pt != current_page:
                lines.append(f"\n*{page_title(pt, self.lang)}*")
                current_page = pt
            lines.append(f"{k}. {label(fid, self.lang)} : {display(fid, rec.payload['fields'][fid], self.lang)}")
        return [_msg("\n".join(lines).strip(), [("rest:confirm", self.tr("btn_confirm_all")),
                                                ("rest:fix", self.tr("btn_fix_one"))])]

    def _on_rest_button(self, arg: str) -> list[dict]:
        rid = self.state.get("record_id")
        if arg == "confirm":
            rec = self.store.get_record(rid)
            payload = rec.payload
            confirm_all(payload, self.midwife, time.time())
            self.store.update_record(rid, payload=payload)
            return self._validate(rid)
        if arg == "detail":
            return self._rest_detail()
        if arg == "fix":
            rec = self.store.get_record(rid)
            rest = confident_fields(rec.payload)
            self.state.update(mode="pick_field", numbered=rest)
            lines = [f"{k}. {label(f, self.lang)} : {display(f, rec.payload['fields'][f], self.lang)}"
                     for k, f in enumerate(rest, 1)]
            return [_msg(self.tr("pick_field", list="\n".join(lines)))]
        return []

    def _on_pick_field(self, text: str) -> list[dict]:
        numbered = self.state.get("numbered", [])
        try:
            k = int(text.strip())
            fid = numbered[k - 1]
        except (ValueError, IndexError):
            return [_msg(self.tr("unknown"))]
        self.state.update(mode="correct", current=fid, back="rest")
        return [_msg(self.tr("ask_value", label=label(fid, self.lang), example=vocabulary_examples(fid, self.lang)))]

    def _validate(self, rid: str) -> list[dict]:
        rec = self.store.get_record(rid)
        if not is_validated(rec.payload):
            return self._review_next()
        self.store.transition(rid, RecordState.VALIDATED, self.midwife, "all fields validated by the midwife")
        out = [_msg(self.tr("validated", n=len(rec.payload.get("fields", {}))))]
        return out + self._match_start(rid)

    # ------------------------------------------------------------------ patient matching
    def _match_start(self, rid: str) -> list[dict]:
        rec = self.store.get_record(rid)
        if rec.state not in (RecordState.VALIDATED, RecordState.DUPLICATE_SUSPECTED) or not is_validated(rec.payload):
            return self._review_start(rid) if rec.state == RecordState.NEEDS_REVIEW else self._menu(greet=False)
        q = quasi_identifiers(rec.payload)
        if not q.get("code"):
            self._set_mode("ask_code", record_id=rid)
            return [_msg(self.tr("ask_code"))]
        code_entry = rec.payload.get("fields", {}).get("cover.registry_code", {})
        typed = code_entry.get("source") in ("manual", "corrected") or rec.payload.get("code")
        if not typed and not rec.payload.get("code_confirmed"):
            # The code links the visits: a misread digit would attach the visit to the wrong woman
            # or create a duplicate, so the read code is always shown and confirmed.
            self._set_mode("confirm_code", record_id=rid)
            return [_msg(self.tr("confirm_code", code=q["code"]),
                         [("m:code_ok", self.tr("btn_code_ok")), ("m:code_edit", self.tr("btn_code_edit"))])]
        cands = find_candidates(q, self.store.list_patients())
        self._set_mode("match", record_id=rid, candidates=[c.patient_id for c in cands])
        if plausible(cands):
            lines = [self.tr("match_line", k=k, code=c.summary.get("code"), age=c.summary.get("age") or "?",
                             edd=display_value("pregnancy.edd", c.summary.get("edd"), self.lang) if c.summary.get("edd") else "?",
                             visits=c.summary.get("visits"), reasons=", ".join(c.reasons + c.conflicts) or "—")
                     for k, c in enumerate(cands, 1)]
            buttons = [(f"m:pick:{k}", self.tr("btn_patient", k=k)) for k in range(1, len(cands) + 1)]
            buttons += [("m:new", self.tr("btn_none_create")), ("m:unsure", self.tr("btn_unsure"))]
            return [_msg(self.tr("match_question", lines="\n".join(lines)), buttons)]
        return [_msg(self.tr("match_none", code=q["code"]),
                     [("m:new", self.tr("btn_create")), ("m:unsure", self.tr("btn_unsure"))])]

    def _on_code(self, text: str) -> list[dict]:
        rid = self.state["record_id"]
        rec = self.store.get_record(rid)
        payload = rec.payload
        payload["code"] = text.strip().upper()
        self.store.update_record(rid, payload=payload)
        return self._match_start(rid)

    def _on_match_button(self, arg: str) -> list[dict]:
        if arg.startswith("decide:"):
            rid = arg.split(":", 1)[1]
            rec = self.store.get_record(rid)
            if rec.state != RecordState.MANUAL_REVIEW_REQUIRED or not rec.payload.get("match_undecided") \
                    or not is_validated(rec.payload):
                return [_msg(self.tr("stale_button"))] + self._menu(greet=False)
            payload = rec.payload
            payload.pop("match_undecided", None)
            with self.store.atomic():
                self.store.update_record(rid, payload=payload)
                self.store.transition(rid, RecordState.VALIDATED, self.midwife, "match decision resumed")
            return self._match_start(rid)
        rid = self.state.get("record_id")
        if rid is None:
            return self._menu(greet=False)
        rec = self.store.get_record(rid)
        if arg == "code_ok":
            payload = rec.payload
            payload["code_confirmed"] = True
            self.store.update_record(rid, payload=payload)
            return self._match_start(rid)
        if arg == "code_edit":
            self._set_mode("ask_code", record_id=rid)
            return [_msg(self.tr("ask_code"))]
        if arg == "unsure":
            payload = rec.payload
            payload["match_undecided"] = True
            with self.store.atomic():
                self.store.update_record(rid, payload=payload)
                self.store.transition(rid, RecordState.MANUAL_REVIEW_REQUIRED, self.midwife, "patient match undecided")
            self._set_mode("idle")
            return [_msg(self.tr("match_unsure"), [(f"m:decide:{rid}", self.tr("btn_decide_now"))])] + \
                self._menu(greet=False)
        if arg == "new":
            pid = new_id()
            return self._attach(rid, pid, None)
        if arg.startswith("pick:"):
            k = int(arg.split(":")[1])
            cands = self.state.get("candidates", [])
            if k - 1 >= len(cands):
                return self._match_start(rid)
            pid = cands[k - 1]
            profile = self.store.get_patient(pid)
            if profile and pages_already_in_profile(profile, rec.payload):
                diffs = diff_with_profile(profile, rec.payload)
                if diffs:
                    self.store.transition(rid, RecordState.DUPLICATE_SUSPECTED, self.midwife,
                                          f"{len(diffs)} field(s) differ from the registered record")
                    self._set_mode("redigit", record_id=rid, patient_id=pid, diffs=diffs, k=0)
                    lines = [self.tr("redigit_line", k=i, label=label(d["field_id"], self.lang),
                                     old=display(d["field_id"], d["old"], self.lang),
                                     new=display(d["field_id"], d["new"], self.lang)) for i, d in enumerate(diffs[:15], 1)]
                    return [_msg(self.tr("redigit_intro", n=len(diffs), lines="\n".join(lines)),
                                 [("d:new", self.tr("btn_update_all")), ("d:old", self.tr("btn_keep_old")),
                                  ("d:one", self.tr("btn_one_by_one"))])]
            return self._attach(rid, pid, profile)
        return []

    def _on_redigit_button(self, arg: str) -> list[dict]:
        rid, pid = self.state["record_id"], self.state["patient_id"]
        diffs = self.state.get("diffs", [])
        rec = self.store.get_record(rid)
        payload = rec.payload
        now = time.time()

        def keep_old(d: dict) -> None:
            decide(payload, d["field_id"], d["old"]["status"], d["old"].get("value"), "confirmed", self.midwife, now)

        if arg == "old":
            for d in diffs:
                keep_old(d)
        elif arg == "one" or arg.startswith(("one_old", "one_new")):
            k = self.state.get("k", 0)
            if arg.startswith("one_old"):
                keep_old(diffs[k])
                k += 1
            elif arg.startswith("one_new"):
                k += 1
            self.state["k"] = k
            self.store.update_record(rid, payload=payload)
            if k < len(diffs):
                d = diffs[k]
                return [_msg(self.tr("redigit_one", label=label(d["field_id"], self.lang),
                                     old=display(d["field_id"], d["old"], self.lang),
                                     new=display(d["field_id"], d["new"], self.lang)),
                             [("d:one_old", self.tr("btn_keep_old")), ("d:one_new", self.tr("btn_take_new"))])]
        self.store.update_record(rid, payload=payload)
        return self._attach(rid, pid, self.store.get_patient(pid))

    def _attach(self, rid: str, pid: str, profile: dict | None) -> list[dict]:
        now = time.time()
        # One transaction: profile update, link, both transitions and the sync job — a crash in the
        # middle can never leave a record matched but neither registered nor queued.
        with self.store.atomic():
            rec = self.store.get_record(rid)
            profile = update_profile(profile, rid, rec.payload, now)
            self.store.upsert_patient(pid, profile)
            self.store.update_record(rid, patient_id=pid, bump_version=True)
            self.store.transition(rid, RecordState.PATIENT_MATCHED, self.midwife, f"patient {pid[:8]}")
            rec = self.store.get_record(rid)
            self.store.transition(rid, RecordState.REGISTERED, "agent", "added to the longitudinal record",
                                  enqueue=("sync_record", rid, f"sync:{rid}:{rec.version}"))
        self._set_mode("idle")
        self.kick_sync()
        sync = self.tr("sync_now") if self.is_online() else self.tr("sync_pending")
        code = (profile.get("codes") or ["?"])[0]
        return [_msg(self.tr("registered", code=code, sync=sync), [(f"p:show:{pid}", self.tr("btn_show_record"))])] \
            + self._menu(greet=False)

    # ------------------------------------------------------------------ longitudinal record
    def _dossier(self, query: str) -> list[dict]:
        """List the midwife's patients, or find one by the code written on the registry."""
        patients = self.store.list_patients()
        if not patients:
            return [_msg(self.tr("no_patients"))] + self._menu(greet=False)
        if query:
            cands = [c for c in find_candidates({"code": query}, patients, limit=5) if c.score >= 0.25]
            if len(cands) == 1:
                return self._show_patient(cands[0].patient_id)
            if not cands:
                return [_msg(self.tr("patient_not_found", code=query))] + self._dossier("")
            ids = [c.patient_id for c in cands]
        else:
            ids = sorted(patients, key=lambda pid: -patients[pid].get("updated_at", 0))[:10]
        buttons = []
        for pid in ids:
            prof = patients[pid]
            age = prof.get("quasi", {}).get("age")
            buttons.append((f"p:show:{pid}", f"{(prof.get('codes') or ['?'])[0]}" + (f" · {age}" if age else "")))
        return [_msg(self.tr("pick_patient"), buttons)]

    def _show_patient(self, pid: str) -> list[dict]:
        profile = self.store.get_patient(pid)
        if profile is None:
            return [_msg(self.tr("stale_button"))] + self._menu(greet=False)
        dates = []
        for rid in profile.get("records", []):
            try:
                dates.append(self._date(self.store.get_record(rid).created_at).split(" ")[0])
            except KeyError:  # record synced from another phone: not on this device
                continue
        return [_msg(profile_summary(profile, dates, self.lang))] + self._menu(greet=False)

    # ------------------------------------------------------------------ manual entry
    def _manual_start(self, rid: str | None) -> list[dict]:
        if rid is not None and self.store.get_record(rid).state not in (RecordState.CAPTURED,
                                                                          RecordState.MANUAL_REVIEW_REQUIRED):
            return [_msg(self.tr("stale_button"))] + self._menu(greet=False)  # e.g. an old "type it in" button
        if rid is None:
            rid = self.store.create_record(self.midwife, {"lang": self.lang, "manual": True})
            self.store.transition(rid, RecordState.MANUAL_REVIEW_REQUIRED, self.midwife, "manual entry")
        rec = self.store.get_record(rid)
        if rec.state == RecordState.CAPTURED:
            self.store.transition(rid, RecordState.MANUAL_REVIEW_REQUIRED, self.midwife, "manual entry")
        self._set_mode("manual_pick", record_id=rid)
        captured = [p.page_type for p in self.store.list_pages(rid) if p.page_type]
        order = [pt for pt in PAGE_ORDER if pt.value in captured] or PAGE_ORDER
        buttons = [(f"manual:page:{pt.value}", page_title(pt.value, self.lang)) for pt in order]
        if rec.payload.get("fields"):
            buttons.append(("manual:finish", self.tr("btn_finish")))
        return [_msg(self.tr("manual_pick_page"), buttons)]

    def _on_manual_button(self, arg: str) -> list[dict]:
        if arg == "start":
            return self._manual_start(self.state.get("record_id"))
        if arg.startswith("record:"):
            return self._manual_start(arg.split(":", 1)[1])
        rid = self.state.get("record_id")
        if arg == "finish":
            rec = self.store.get_record(rid)
            if rec.state != RecordState.MANUAL_REVIEW_REQUIRED or not rec.payload.get("fields"):
                return self._manual_start(rid)
            if is_validated(rec.payload):
                self.store.transition(rid, RecordState.VALIDATED, self.midwife, "manual entry completed")
                return [_msg(self.tr("validated", n=len(rec.payload.get("fields", {}))))] + self._match_start(rid)
            # pages read by the AI before it failed: review those fields rather than retyping them
            self.store.transition(rid, RecordState.NEEDS_REVIEW, self.midwife, "review the fields read by the AI")
            return self._review_start(rid)
        if arg == "other":
            return self._manual_start(rid)
        if arg.startswith("page:"):
            pt = arg.split(":", 1)[1]
            if pt == PageType.PREGNANCY.value:
                self._set_mode("manual_pick", record_id=rid)
                buttons = [(f"manual:visit:{c}", cf if self.lang == "fr" else ce) for c, cf, ce in VISIT_COLS]
                return [_msg(self.tr("manual_pick_visit"), buttons)]
            fields = [s.id for s in PAGE_FIELDS[PageType(pt)]]
            return self._manual_begin(rid, pt, fields)
        if arg.startswith("visit:"):
            col = arg.split(":", 1)[1]
            fields = [s.id for s in PAGE_FIELDS[PageType.PREGNANCY] if s.col in (None, col)]
            return self._manual_begin(rid, PageType.PREGNANCY.value, fields)
        if arg in ("yes", "no", "skip", "end"):
            return self._manual_answer(arg)
        return []

    def _manual_begin(self, rid: str, page_type: str, fields: list[str]) -> list[dict]:
        self._set_mode("manual", record_id=rid, page_type=page_type, fields=fields, k=0)
        return [self._manual_question()]

    def _manual_question(self) -> dict:
        fields, k = self.state["fields"], self.state["k"]
        fid = fields[k]
        spec = ALL_FIELDS[fid]
        if spec.kind == FieldKind.CHECKBOX:
            return _msg(self.tr("manual_q_box", k=k + 1, n=len(fields), label=label(fid, self.lang)),
                        [("manual:yes", self.tr("btn_yes")), ("manual:no", self.tr("btn_no")),
                         ("manual:end", self.tr("btn_end_page"))])
        return _msg(self.tr("manual_q", k=k + 1, n=len(fields), label=label(fid, self.lang),
                            example=vocabulary_examples(fid, self.lang)),
                    [("manual:skip", self.tr("btn_skip")), ("manual:end", self.tr("btn_end_page"))])

    def _manual_store(self, fid: str, status: FieldStatus, value) -> None:
        rid = self.state["record_id"]
        rec = self.store.get_record(rid)
        payload = rec.payload
        decide(payload, fid, status.value, value, "manual", self.midwife, time.time())
        self.store.update_record(rid, payload=payload)

    def _manual_answer(self, arg: str) -> list[dict]:
        fields, k = self.state["fields"], self.state["k"]
        if arg == "end":
            for fid in fields[k:]:
                spec = ALL_FIELDS[fid]
                if spec.kind == FieldKind.CHECKBOX:
                    self._manual_store(fid, FieldStatus.KNOWN, False)
                else:
                    self._manual_store(fid, FieldStatus.NOT_PROVIDED, None)
            return self._manual_page_done()
        fid = fields[k]
        if arg in ("yes", "no"):
            self._manual_store(fid, FieldStatus.KNOWN, arg == "yes")
        elif arg == "skip":
            self._manual_store(fid, FieldStatus.NOT_PROVIDED, None)
        return self._manual_advance()

    def _manual_advance(self) -> list[dict]:
        self.state["k"] += 1
        if self.state["k"] >= len(self.state["fields"]):
            return self._manual_page_done()
        return [self._manual_question()]

    def _manual_page_done(self) -> list[dict]:
        pt, rid = self.state["page_type"], self.state["record_id"]
        rec = self.store.get_record(rid)
        payload = rec.payload
        payload.setdefault("page_types", {}).setdefault(pt, "manual")
        self.store.update_record(rid, payload=payload)
        n = len(self.state["fields"])
        self._set_mode("manual_pick", record_id=rid)
        return [_msg(self.tr("manual_page_done", page=page_title(pt, self.lang), n=n),
                     [("manual:other", self.tr("btn_other_page")), ("manual:finish", self.tr("btn_finish"))])]

    def _on_manual_text(self, text: str) -> list[dict]:
        low = text.strip().lower()
        if low in ("fin", "end"):
            return self._manual_answer("end")
        fid = self.state["fields"][self.state["k"]]
        spec = ALL_FIELDS[fid]
        if spec.kind == FieldKind.CHECKBOX:
            p = parse_value(spec.model_copy(update={"value_type": ValueType.BOOL}), text)
            if p.status != FieldStatus.KNOWN or not p.ok:
                return [self._manual_question()]
            self._manual_store(fid, FieldStatus.KNOWN, bool(p.value))
            return self._manual_advance()
        special = SPECIAL_WORDS.get(low)
        if special is not None:
            self._manual_store(fid, special, None)
            return self._manual_advance()
        p = parse_value(spec, text)
        if p.status != FieldStatus.KNOWN or not p.ok:
            return [_msg(self.tr("bad_value", text=text, label=label(fid, self.lang),
                                 example=vocabulary_examples(fid, self.lang)))]
        value = scrub_text(p.value) if spec.value_type == ValueType.TEXT else p.value  # never alter a code
        self._manual_store(fid, FieldStatus.KNOWN, value)
        return self._manual_advance()


def field_crop(store: DeviceStore, page_id: str, fid: str, pad_pt: float = 8.0) -> bytes:
    """JPEG of the area of the stored (redacted) photo where ``fid`` was read."""
    page = store.get_page(page_id)
    img = cv2.imdecode(np.frombuffer(store.page_image(page_id), np.uint8), cv2.IMREAD_COLOR)
    h = np.array(page.registration["homography"])
    x0, y0, x1, y1 = ALL_FIELDS[fid].region
    corners = np.float32([[x0 - pad_pt, y0 - pad_pt], [x1 + pad_pt, y0 - pad_pt], [x1 + pad_pt, y1 + pad_pt],
                          [x0 - pad_pt, y1 + pad_pt]]) * SCALE
    w_px, h_px = int((x1 - x0 + 2 * pad_pt) * SCALE), int((y1 - y0 + 2 * pad_pt) * SCALE)
    dst = np.float32([[0, 0], [w_px, 0], [w_px, h_px], [0, h_px]])
    # template region -> capture pixels -> crop rectified to the field box
    src = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), np.linalg.inv(h)).reshape(-1, 2)
    crop = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src.astype(np.float32), dst), (w_px, h_px))
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    return buf.tobytes()
