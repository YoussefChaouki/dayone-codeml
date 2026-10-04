"""The structured record: page extractions merged into one document, reviewed by the midwife.

A record (one registry photographed in one session) holds, per field: status, value,
confidence, source and provenance (page id). Sources tell who decided:
``ocr | checkbox | ink | rule`` (machine) and ``confirmed | corrected | manual`` (midwife).
Machine values are never "validated" until the midwife confirmed them, individually for
doubtful fields, in bulk for confident ones.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from dayone.extraction.normalize import VOCABULARIES
from dayone.forms.layout import ALL_FIELDS, PAGE_FIELDS, PAGE_ORDER
from dayone.linking import normalise_code
from dayone.schema import FieldKind, FieldStatus, PageType, ValueType

HUMAN_SOURCES = {"confirmed", "corrected", "manual"}
REVIEW_STATUSES = {FieldStatus.NEEDS_REVIEW.value, FieldStatus.ILLEGIBLE.value}
_ORDER = {fid: k for k, fid in enumerate(ALL_FIELDS)}

DISPLAY_FR = {
    "yes": "Oui", "no": "Non", "negative": "Négatif", "positive": "Positif", "not_done": "Non fait",
    "immune": "Immunisée", "non_immune": "Non immunisée", "normal": "Normal", "abnormal": "Anormal",
    "pale": "Pâles", "closed": "Fermé", "open": "Ouvert", "modified": "Modifié", "cephalic": "Céphalique",
    "breech": "Siège", "transverse": "Transverse", "vaginal": "Voie basse", "instrumental": "Instrumentale",
    "cesarean": "Césarienne", "none": "Rien à signaler", "primary": "Primaire", "secondary": "Secondaire",
    "higher": "Supérieur", "F": "Fille", "M": "Garçon",
}
DISPLAY_EN = {
    "yes": "Yes", "no": "No", "negative": "Negative", "positive": "Positive", "not_done": "Not done",
    "immune": "Immune", "non_immune": "Not immune", "normal": "Normal", "abnormal": "Abnormal", "pale": "Pale",
    "closed": "Closed", "open": "Open", "modified": "Changed", "cephalic": "Cephalic", "breech": "Breech",
    "transverse": "Transverse", "vaginal": "Vaginal", "instrumental": "Instrumental", "cesarean": "Cesarean",
    "none": "Nothing to report", "primary": "Primary", "secondary": "Secondary", "higher": "Higher education",
    "F": "Girl", "M": "Boy",
}
STATUS_DISPLAY = {
    "fr": {"NOT_PROVIDED": "(vide)", "NOT_APPLICABLE": "(non applicable)", "UNKNOWN": "(inconnu)",
           "ILLEGIBLE": "(illisible)"},
    "en": {"NOT_PROVIDED": "(blank)", "NOT_APPLICABLE": "(not applicable)", "UNKNOWN": "(unknown)",
           "ILLEGIBLE": "(illegible)"},
}


def label(fid: str, lang: str = "fr") -> str:
    spec = ALL_FIELDS[fid]
    return spec.label_fr if lang == "fr" else spec.label_en


def display(fid: str, entry: dict, lang: str = "fr") -> str:
    status = entry.get("status")
    if status not in (FieldStatus.KNOWN.value, FieldStatus.NEEDS_REVIEW.value):
        return STATUS_DISPLAY[lang].get(status, "?")
    return display_value(fid, entry.get("value"), lang)


def display_value(fid: str, value: Any, lang: str = "fr") -> str:
    spec = ALL_FIELDS[fid]
    table = DISPLAY_FR if lang == "fr" else DISPLAY_EN
    if spec.kind == FieldKind.CHECKBOX or isinstance(value, bool):
        return table["yes"] if value else table["no"]
    if spec.value_type == ValueType.DATE and isinstance(value, str):
        try:
            return date.fromisoformat(value).strftime("%d/%m/%Y")
        except ValueError:
            return value
    if isinstance(value, str) and value in table and (spec.vocabulary or spec.value_type == ValueType.ENUM):
        return table[value]
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if spec.value_type == ValueType.GEST_AGE and value is not None:
        return f"{value} SA" if lang == "fr" else f"{value} weeks"
    if spec.unit and value is not None and not isinstance(value, str):
        return f"{value} {spec.unit}"
    return "" if value is None else str(value)


def merge_extraction(payload: dict, page_id: str, extraction: dict) -> None:
    """Merge a page extraction into the record. Decisions already taken by the midwife are kept."""
    fields = payload.setdefault("fields", {})
    for fid, f in extraction.get("fields", {}).items():
        prev = fields.get(fid)
        if prev and prev.get("source") in HUMAN_SOURCES:
            continue
        fields[fid] = {"status": f["status"], "value": f.get("value"), "confidence": f.get("confidence", 0.0),
                       "source": f.get("source", "ocr"), "page_id": page_id, "raw": f.get("raw_text"),
                       "alternatives": f.get("alternatives", []), "flags": f.get("flags", []),
                       # the signals the agent uses to explain its doubt
                       "features": {k: v for k, v in (f.get("features") or {}).items()
                                    if k in ("second_agree", "second_missing", "lp_min", "fill")}}
    pt = extraction.get("page_type")
    if pt:
        payload.setdefault("page_types", {})[pt] = page_id


def needs_review(e: dict) -> bool:
    return e.get("source") not in HUMAN_SOURCES and (
        e.get("status") in REVIEW_STATUSES or "confirm_special" in e.get("flags", []))


def review_queue(payload: dict) -> list[str]:
    """Doubtful machine fields, in booklet order."""
    out = [fid for fid, e in payload.get("fields", {}).items() if needs_review(e)]
    return sorted(out, key=lambda f: _ORDER.get(f, 1 << 30))


def confident_fields(payload: dict, page_type: str | None = None) -> list[str]:
    out = [fid for fid, e in payload.get("fields", {}).items()
           if e.get("source") not in HUMAN_SOURCES and not needs_review(e)
           and (page_type is None or fid.startswith(page_type + "."))]
    return sorted(out, key=lambda f: _ORDER.get(f, 1 << 30))


def decide(payload: dict, fid: str, status: str, value: Any, source: str, actor: str, at: float) -> None:
    e = payload.setdefault("fields", {}).setdefault(fid, {})
    previous = {k: e.get(k) for k in ("status", "value", "source")}
    e.update({"status": status, "value": value, "source": source, "confidence": 1.0, "decided_by": actor,
              "decided_at": at})
    e.setdefault("audit", []).append({"at": at, "by": actor, "from": previous, "to": {"status": status, "value": value}})


def confirm_all(payload: dict, actor: str, at: float, page_type: str | None = None) -> int:
    n = 0
    for fid in confident_fields(payload, page_type):
        e = payload["fields"][fid]
        decide(payload, fid, e["status"], e.get("value"), "confirmed", actor, at)
        n += 1
    return n


def is_validated(payload: dict) -> bool:
    fields = payload.get("fields", {})
    return bool(fields) and all(e.get("source") in HUMAN_SOURCES for e in fields.values())


def quasi_identifiers(payload: dict) -> dict:
    """Non-identifying attributes used for patient matching (no name, no ID number, no phone)."""
    f = payload.get("fields", {})

    def v(fid):
        e = f.get(fid)
        return e.get("value") if e and e.get("status") in (FieldStatus.KNOWN.value, FieldStatus.NEEDS_REVIEW.value) \
            else None

    return {"code": payload.get("code") or v("cover.registry_code"), "age": v("history.age"),
            "lmp": v("pregnancy.lmp_date"), "edd": v("pregnancy.edd"), "gravidity": v("history.gravidity"),
            "parity": v("history.parity"), "province": v("cover.province"), "region": v("cover.region")}


def update_profile(profile: dict | None, record_id: str, payload: dict, at: float) -> dict:
    """Fold a validated record into the patient's longitudinal profile."""
    profile = profile or {"codes": [], "records": [], "fields": {}, "quasi": {}, "created_at": at}
    q = quasi_identifiers(payload)
    if q.get("code"):  # oldest first, most recently confirmed last (shown to the midwife)
        # an OCR variant of a known code ("2O26" for "2026") keeps the spelling already on file
        same = [c for c in profile["codes"] if normalise_code(c) == normalise_code(q["code"])]
        for c in same:
            profile["codes"].remove(c)
        profile["codes"].append(same[0] if same else q["code"])
    for k, v in q.items():
        if v is not None and k != "code":
            profile["quasi"][k] = v
    if record_id not in profile["records"]:
        profile["records"].append(record_id)
    for fid, e in payload.get("fields", {}).items():
        previous = profile["fields"].get(fid)
        if e.get("status") == FieldStatus.NOT_PROVIDED.value and previous:
            continue  # a blank on a re-photographed page never erases what is already known
        if e.get("status") in (FieldStatus.KNOWN.value, FieldStatus.UNKNOWN.value, FieldStatus.NOT_APPLICABLE.value):
            profile["fields"][fid] = {"status": e["status"], "value": e.get("value"), "record_id": record_id,
                                      "updated_at": at}
    profile["updated_at"] = at
    return profile


def diff_with_profile(profile: dict, payload: dict) -> list[dict]:
    """Fields of the new record that already have a different value in the profile (re-digitisation)."""
    out = []
    for fid, e in payload.get("fields", {}).items():
        old = profile.get("fields", {}).get(fid)
        if old is None:
            continue
        # a new value, or a new "unknown / not applicable" replacing a known value, is shown to the midwife
        replaces_known = old.get("status") == FieldStatus.KNOWN.value and e.get("status") != FieldStatus.KNOWN.value
        if e.get("status") != FieldStatus.KNOWN.value and not replaces_known:
            continue
        if old.get("value") != e.get("value") or old.get("status") != e.get("status"):
            out.append({"field_id": fid, "old": old, "new": {"status": e["status"], "value": e.get("value")}})
    return sorted(out, key=lambda d: _ORDER.get(d["field_id"], 1 << 30))


def pages_already_in_profile(profile: dict, payload: dict) -> list[str]:
    known_pages = {fid.split(".")[0] for fid in profile.get("fields", {})}
    return [pt for pt in payload.get("page_types", {}) if pt in known_pages]


def manual_fields(page_type: PageType, essential_only: bool = False) -> list[str]:
    """Fields asked during manual entry, in booklet order."""
    specs = PAGE_FIELDS[page_type]
    if not essential_only:
        return [s.id for s in specs]
    return [s.id for s in specs if s.kind.value == "text" and (s.col is None or s.col in ("prev1",))]


def vocabulary_examples(fid: str, lang: str = "fr") -> str:
    spec = ALL_FIELDS[fid]
    if spec.value_type == ValueType.DATE:
        return "12/03/2025"
    if spec.value_type == ValueType.BP:
        return "120/80"
    if spec.value_type == ValueType.GEST_AGE:
        return "24 SA"
    if spec.kind == FieldKind.CHECKBOX or spec.value_type == ValueType.BOOL:
        return "oui / non" if lang == "fr" else "yes / no"
    if spec.vocabulary in VOCABULARIES:
        forms = [forms[0] for forms in VOCABULARIES[spec.vocabulary].values()][:3]
        return " / ".join(forms)
    if spec.unit:
        return f"nombre ({spec.unit})" if lang == "fr" else f"number ({spec.unit})"
    return "texte libre" if lang == "fr" else "free text"


PAGE_INDEX = {pt.value: k for k, pt in enumerate(PAGE_ORDER)}


# ---------------------------------------------------------------------------
# Longitudinal record of a patient, as shown to the midwife ("dossier")
# ---------------------------------------------------------------------------
_SUMMARY = {
    "fr": {"title": "📁 *Dossier patiente* — code {code}", "age": "{v} ans", "records": "{n} fiche(s) : {dates}",
           "preg": "*Grossesse* : DDR {lmp} · DPA {edd}", "visits": "*Visites prénatales* (venue · AG · poids · TA) :",
           "tests": "*Tests* : {items}", "delivery": "*Accouchement* : {items}", "pp": "*Post-partum {when}* : {items}",
           "nb": "*Nouveau-né {when}* : {items}", "early": "précoce", "late": "tardif", "none": "—",
           "hint": "Données recopiées du registre et validées par la sage-femme. Pas d'interprétation clinique."},
    "en": {"title": "📁 *Patient record* — code {code}", "age": "{v} years", "records": "{n} record(s): {dates}",
           "preg": "*Pregnancy*: LMP {lmp} · EDD {edd}", "visits": "*Antenatal visits* (date · GA · weight · BP):",
           "tests": "*Tests*: {items}", "delivery": "*Delivery*: {items}", "pp": "*Postpartum {when}*: {items}",
           "nb": "*Newborn {when}*: {items}", "early": "early", "late": "late", "none": "—",
           "hint": "Copied from the registry and validated by the midwife. No clinical interpretation."},
}


def _known(profile: dict, fid: str):
    e = profile.get("fields", {}).get(fid)
    return e.get("value") if e and e.get("status") == FieldStatus.KNOWN.value else None


def _items(profile: dict, fids: list[str], lang: str, with_label: bool = True) -> str:
    parts = []
    for fid in fids:
        v = _known(profile, fid)
        if v is None:
            continue
        val = display_value(fid, v, lang)
        name = re.sub(r"\s*\(.*\)", "", label(fid, lang))  # the unit is already in the value
        parts.append(f"{name} {val}" if with_label else val)
    return " · ".join(parts)


def profile_summary(profile: dict, record_dates: list[str], lang: str = "fr") -> str:
    """Readable longitudinal summary of a patient profile (no name, no identifier)."""
    from dayone.forms.layout import VISIT_COLS

    t = _SUMMARY[lang]
    q = profile.get("quasi", {})
    dash = t["none"]
    ident = []
    if q.get("age") is not None:
        ident.append(t["age"].format(v=q["age"]))
    if q.get("gravidity") is not None or q.get("parity") is not None:
        ident.append(f"G{q.get('gravidity', '?')} P{q.get('parity', '?')}")
    ident.append(t["records"].format(n=len(record_dates), dates=", ".join(record_dates) or dash))
    lines = [t["title"].format(code=", ".join(profile.get("codes", [])) or "?"), " · ".join(ident)]
    lmp, edd = _known(profile, "pregnancy.lmp_date"), _known(profile, "pregnancy.edd")
    if lmp or edd:
        lines.append(t["preg"].format(lmp=display_value("pregnancy.lmp_date", lmp, lang) if lmp else dash,
                                      edd=display_value("pregnancy.edd", edd, lang) if edd else dash))
    visits = []
    for col, fr, en in VISIT_COLS:
        base = f"pregnancy.visit.{col}."
        cells = [_known(profile, base + r) for r in ("visit_date", "gest_age", "weight", "bp")]
        if not any(c is not None for c in cells):
            continue
        shown = [display_value(base + r, c, lang) if c is not None else dash
                 for r, c in zip(("visit_date", "gest_age", "weight", "bp"), cells, strict=True)]
        visits.append(f"• {fr if lang == 'fr' else en} — " + " · ".join(shown))
    if visits:
        lines += [t["visits"], *visits]
    tests = []
    for row in ("hiv", "syphilis", "hbsag", "hemoglobin"):
        values = [(col, _known(profile, f"pregnancy.visit.{col}.{row}")) for col, _, _ in VISIT_COLS]
        values = [(c, v) for c, v in values if v is not None]
        if values:
            fid = f"pregnancy.visit.{values[-1][0]}.{row}"
            name = re.sub(r"\s*\(.*\)", "", label(fid, lang).split(" — ")[0])  # "Hémoglobine (g/dL)" -> "Hémoglobine"
            tests.append(f"{name} {display_value(fid, values[-1][1], lang)}")
    if tests:
        lines.append(t["tests"].format(items=" · ".join(tests)))
    mode = next((label(f, lang) for f in ("delivery.mode.vaginal", "delivery.mode.instrumental",
                                           "delivery.mode.cesarean_planned", "delivery.mode.cesarean_emergency")
                 if _known(profile, f) is True), None)
    delivery = _items(profile, ["delivery.date"], lang, with_label=False)
    extra = _items(profile, ["delivery.newborn.sex", "delivery.newborn.weight", "delivery.newborn.gest_age"], lang)
    if delivery or mode or extra:
        lines.append(t["delivery"].format(items=" · ".join(x for x in (delivery, mode, extra) if x)))
    for when, key in (("early", "pp_early"), ("late", "pp_late")):
        mother = _items(profile, [f"{key}_mother.consultation_date", f"{key}_mother.bp", f"{key}_mother.temperature",
                                  f"{key}_mother.weight"], lang)
        if mother:
            lines.append(t["pp"].format(when=t[when], items=mother))
        baby = _items(profile, [f"{key}_newborn.consultation_date", f"{key}_newborn.weight",
                                f"{key}_newborn.temperature"], lang)
        if baby:
            lines.append(t["nb"].format(when=t[when], items=baby))
    lines.append(f"_{t['hint']}_")
    return "\n".join(lines)
