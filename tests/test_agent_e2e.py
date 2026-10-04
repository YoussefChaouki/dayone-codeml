"""End-to-end conversation on the phone: offline capture -> AI -> review -> match -> sync."""

from __future__ import annotations

import pytest
from conftest import FakeExtractor, jpeg_bytes, page_png, wait_processed

from dayone.device.agent import Agent, field_crop
from dayone.device.lifecycle import RecordState
from dayone.device.store import DeviceStore
from dayone.device.sync import Network, ServerClient, SyncEngine
from dayone.evaluation.degrade import degrade

S = RecordState


class Phone:
    def __init__(self, path, server, registrar):
        self.path, self.server, self.registrar = path, server, registrar
        self.net = Network(online=False)
        self.boot()

    def boot(self):
        self.store = DeviceStore(self.path, "2468")
        self.agent = Agent(self.store, self.registrar, "sf-amina", is_online=lambda: self.net.online)
        self.engine = SyncEngine(self.store, ServerClient(self.server, "token-sf-amina", self.net),
                                 notify=self.agent.notify)

    def say(self, text):
        return self.agent.handle({"type": "text", "text": text})[1:]

    def tap(self, bid):
        return self.agent.handle({"type": "button", "id": bid})[1:]

    def photo(self, page_number, level="mild"):
        img, _ = degrade(page_png(page_number), level, seed=page_number)
        return self.agent.handle({"type": "image", "data": jpeg_bytes(img)})[1:]

    def sync(self):
        for _ in range(20):
            self.engine.run_once()
            self.store.db.execute("UPDATE outbox SET next_attempt_at=0")  # fast-forward retry delays
            pages = [p.id for r in self.store.list_records() for p in self.store.list_pages(r.id)]
            if pages:
                try:
                    wait_processed(self.server, pages, timeout=1.0)
                except TimeoutError:
                    pass
            if not self.store.pending_jobs():
                return

    def buttons(self, msgs):
        return [b["id"].partition("#")[0] for m in msgs for b in m["buttons"]]

    def raw_buttons(self, msgs):
        return [b["id"] for m in msgs for b in m["buttons"]]

    def text(self, msgs):
        return "\n".join(m["text"] for m in msgs)


@pytest.fixture
def phone(tmp_path, server, registrar):
    return Phone(tmp_path / "phone.db", server, registrar)


def capture_offline(phone):
    assert "menu:new" in phone.buttons(phone.say("menu"))
    phone.tap("menu:new")
    msgs = phone.photo(2)  # identification page: has personal data to mask
    assert "Identification" in phone.text(msgs) and "masqués" in phone.text(msgs)
    msgs = phone.photo(3)
    assert "Grossesse actuelle" in phone.text(msgs)
    msgs = phone.tap("cap:done")
    assert "Pas de réseau" in phone.text(msgs)
    rid = phone.store.list_records()[-1].id
    assert phone.store.get_record(rid).state == S.PENDING_AI
    pages = phone.store.list_pages(rid)
    assert {p.page_type for p in pages} == {"history", "pregnancy"}
    hist = next(p for p in pages if p.page_type == "history")
    assert set(hist.registration["pii_masked"]) == {"national_id", "address", "phone", "husband_name"}
    return rid


def test_full_flow_offline_capture_review_match_sync(phone):
    rid = capture_offline(phone)
    # connectivity comes back: pages are uploaded, read, results come back
    phone.net.online = True
    phone.sync()
    assert phone.store.get_record(rid).state == S.NEEDS_REVIEW
    chat = [m for _, m in phone.store.chat_log()]
    assert any(f"rev:start:{rid}" in [b["id"] for b in m["buttons"]] for m in chat)

    # review: blood pressure read 109/74 with doubt, the midwife picks the alternative 104/74
    msgs = phone.tap(f"rev:start:{rid}")
    assert "109/74" in phone.text(msgs) and "pas sûre" in phone.text(msgs)
    assert "deux lectures différentes" in phone.text(msgs)
    assert "f:alt:0" in phone.buttons(msgs) and "f:retake" in phone.buttons(msgs)
    crop = field_crop(phone.store, phone.store.get_record(rid).payload["fields"]["pregnancy.visit.t1v2.bp"]["page_id"],
                      "pregnancy.visit.t1v2.bp")
    assert crop[:2] == b"\xff\xd8"  # the midwife can see the exact area that was read
    msgs = phone.tap("f:alt:0")
    # next: an illegible haemoglobin; a wrong entry is refused, a valid one accepted
    assert "n'arrive pas à le lire" in phone.text(msgs)
    phone.tap("f:edit")
    assert "pas compris" in phone.text(phone.say("beaucoup"))
    msgs = phone.say("11,8")
    assert "11.8" in phone.text(msgs) and "rest:confirm" in phone.buttons(msgs)
    msgs = phone.tap("rest:confirm")
    assert phone.store.get_record(rid).state == S.VALIDATED
    assert "code patiente" in phone.text(msgs)
    msgs = phone.say("2026-823-001")
    assert set(phone.buttons(msgs)) >= {"m:new", "m:unsure"}
    phone.tap("m:new")
    rec = phone.store.get_record(rid)
    assert rec.state == S.REGISTERED and rec.patient_id
    assert rec.payload["fields"]["pregnancy.visit.t1v2.bp"]["value"] == "104/74"
    phone.sync()
    assert phone.store.get_record(rid).state == S.SYNCED
    assert phone.server.app.state.registry.execute("SELECT COUNT(*) FROM records")[0][0] == 1


def test_redigitisation_proposes_match_and_lets_midwife_choose(phone):
    test_full_flow_offline_capture_review_match_sync(phone)
    patient_id = phone.store.list_records()[-1].patient_id
    # the same registry is photographed again later
    phone.tap("menu:new")
    phone.photo(3, "medium")
    phone.tap("cap:done")
    phone.sync()
    rid2 = phone.store.list_records()[-1].id
    phone.tap(f"rev:start:{rid2}")
    phone.tap("f:confirm")  # keeps 109/74 this time: differs from the registered 104/74
    phone.tap("f:blank")
    phone.tap("rest:confirm")
    msgs = phone.say("2O26-823-OO1")  # code misread with letters O: still found
    assert {"m:pick:1", "m:new", "m:unsure"} <= set(phone.buttons(msgs))
    assert phone.store.get_record(rid2).state == S.VALIDATED  # nothing created behind the midwife's back
    assert len(phone.store.list_patients()) == 1
    msgs = phone.tap("m:pick:1")
    assert phone.store.get_record(rid2).state == S.DUPLICATE_SUSPECTED
    assert "104/74" in phone.text(msgs) and "109/74" in phone.text(msgs)
    phone.tap("d:old")
    rec2 = phone.store.get_record(rid2)
    assert rec2.state == S.REGISTERED and rec2.patient_id == patient_id
    assert rec2.payload["fields"]["pregnancy.visit.t1v2.bp"]["value"] == "104/74"


def test_unsure_match_parks_the_record(phone):
    rid = capture_offline(phone)
    phone.net.online = True
    phone.sync()
    phone.tap(f"rev:start:{rid}")
    phone.tap("f:confirm")
    phone.tap("f:illegible")
    phone.tap("rest:confirm")
    phone.say("2026-999-123")
    phone.tap("m:unsure")
    assert phone.store.get_record(rid).state == S.MANUAL_REVIEW_REQUIRED
    assert len(phone.store.list_patients()) == 0


def test_conversation_survives_app_crash(phone):
    rid = capture_offline(phone)
    phone.net.online = True
    phone.sync()
    phone.tap(f"rev:start:{rid}")
    phone.store.close()  # app killed in the middle of the review
    phone.boot()
    msgs = phone.tap("f:confirm")  # the agent remembers which field was on screen
    assert "n'arrive pas à le lire" in phone.text(msgs)


def test_manual_entry_without_ai(phone):
    phone.tap("menu:manual")
    msgs = phone.tap("manual:page:cover")
    assert "Registry code" not in phone.text(msgs) and "N° de la fiche" in phone.text(msgs)
    phone.say("2026-823-099")
    phone.say("Fès-Meknès")
    phone.say("passer")
    phone.say("CSC Hay Salam")
    phone.tap("manual:yes")
    msgs = phone.say("fin")
    assert "manual:finish" in phone.buttons(msgs)
    msgs = phone.tap("manual:finish")
    rid = phone.store.list_records()[-1].id
    rec = phone.store.get_record(rid)
    assert rec.payload["fields"]["cover.registry_code"]["value"] == "2026-823-099"
    assert rec.payload["fields"]["cover.province"]["status"] == "NOT_PROVIDED"
    assert {"m:new", "m:unsure"} <= set(phone.buttons(msgs))


def test_english_interface(phone):
    msgs = phone.say("language en")
    assert "Language: English" in phone.text(msgs)
    msgs = phone.tap("menu:new")
    assert "Photograph the registry pages" in phone.text(msgs)


def test_crash_during_registration_rolls_back_everything(phone, monkeypatch):
    rid = capture_offline(phone)
    phone.net.online = True
    phone.sync()
    phone.tap(f"rev:start:{rid}")
    phone.tap("f:confirm")
    phone.tap("f:illegible")
    phone.tap("rest:confirm")
    phone.say("2026-823-001")
    real = phone.store.transition

    def dying(rid_, target, *a, **kw):
        if target == S.REGISTERED:
            raise RuntimeError("battery died")
        return real(rid_, target, *a, **kw)

    monkeypatch.setattr(phone.store, "transition", dying)
    with pytest.raises(RuntimeError):
        phone.tap("m:new")
    monkeypatch.setattr(phone.store, "transition", real)
    rec = phone.store.get_record(rid)
    assert rec.state == S.VALIDATED and rec.patient_id is None  # nothing half-done
    assert phone.store.list_patients() == {} and not phone.store.pending_jobs()


def test_stale_buttons_are_refused(phone):
    rid = capture_offline(phone)
    msgs = phone.tap("m:new")  # a button from another step (e.g. still visible in WhatsApp)
    assert "plus valable" in phone.text(msgs)
    assert phone.store.get_record(rid).state == S.PENDING_AI and phone.store.list_patients() == {}
    phone.tap("menu:manual")
    phone.tap("rest:confirm")
    assert all(r.state != S.REGISTERED for r in phone.store.list_records())


def test_old_button_cannot_confirm_another_field(phone):
    rid = capture_offline(phone)
    phone.net.online = True
    phone.sync()
    msgs = phone.tap(f"rev:start:{rid}")
    confirm_bp = next(b for b in phone.raw_buttons(msgs) if b.startswith("f:confirm"))
    phone.tap(confirm_bp)  # first tap: confirms the blood pressure shown
    msgs = phone.tap(confirm_bp)  # second tap on the same old button: must not touch the next field
    assert "plus valable" in phone.text(msgs)
    rec = phone.store.get_record(rid)
    assert rec.payload["fields"]["pregnancy.visit.t1v2.hemoglobin"]["source"] == "ocr"


def test_old_review_button_on_registered_record_is_refused(phone):
    test_full_flow_offline_capture_review_match_sync(phone)
    rid = phone.store.list_records()[-1].id
    msgs = phone.tap(f"rev:start:{rid}")
    assert "plus valable" in phone.text(msgs)
    assert phone.store.get_record(rid).state == S.SYNCED
    msgs = phone.tap(f"manual:record:{rid}")
    assert "plus valable" in phone.text(msgs)


def test_code_read_from_the_cover_is_always_confirmed(phone, server):
    from dayone.schema import FieldResult, FieldStatus

    def with_code(image, expected=None, check_quality=True):
        result = FakeExtractor().extract(image, expected, check_quality)
        result.fields["cover.registry_code"] = FieldResult(field_id="cover.registry_code", status=FieldStatus.KNOWN,
                                                           value="2026-63-007", confidence=0.99, source="ocr")
        return result

    server.extractor.extract = with_code
    rid = capture_offline(phone)
    phone.net.online = True
    phone.sync()
    phone.tap(f"rev:start:{rid}")
    phone.tap("f:confirm")
    phone.tap("f:illegible")
    msgs = phone.tap("rest:confirm")
    assert "2026-63-007" in phone.text(msgs) and "m:code_edit" in phone.buttons(msgs)
    phone.tap("m:code_edit")
    msgs = phone.say("2026-163-007")
    assert "2026-163-007" in phone.text(msgs) and "m:new" in phone.buttons(msgs)


def test_longitudinal_record_can_be_consulted(phone):
    test_full_flow_offline_capture_review_match_sync(phone)
    rec = phone.store.list_records()[-1]
    msgs = phone.tap("menu:records")
    assert any(b.startswith("p:show:") for b in phone.buttons(msgs))
    msgs = phone.say("dossier 2026-823-OO1")  # code typed with letters O: still found
    text = phone.text(msgs)
    assert "2026-823-001" in text and "26/04/2025" in text  # code, LMP
    assert "104/74" in text and "58.8 kg" in text  # the validated visit values, not the misread 109/74
    msgs = phone.tap(f"p:show:{rec.patient_id}")
    assert "Dossier patiente" in phone.text(msgs) and "?" not in phone.text(msgs).split("\n")[1]
    assert "Aucun dossier ne correspond" in phone.text(phone.say("dossier 1999-000-000"))
