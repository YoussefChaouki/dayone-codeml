"""Cross-field consistency rules.

Rules never change a value: they attach flags to the fields involved, which lowers their
confidence and makes the agent ask the midwife. A second family of rules marks blank
fields that are *logically* not applicable (e.g. cesarean indication after a vaginal
birth) as NOT_APPLICABLE instead of NOT_PROVIDED.

Out of scope by design: no clinical interpretation (no risk scoring, no triage). Rules
only check internal consistency of what was written.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta

from dayone.forms.layout import VISIT_COLS
from dayone.schema import FieldResult, FieldStatus, PageType

Fields = dict[str, FieldResult]


def _val(fields: Fields, fid: str):
    f = fields.get(fid)
    if f is None or f.status not in (FieldStatus.KNOWN, FieldStatus.NEEDS_REVIEW):
        return None
    return f.value


def _date(fields: Fields, fid: str) -> date | None:
    v = _val(fields, fid)
    if isinstance(v, str):
        try:
            return date.fromisoformat(v)
        except ValueError:
            return None
    return None


def _num(fields: Fields, fid: str) -> float | None:
    v = _val(fields, fid)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _bp(fields: Fields, fid: str) -> tuple[int, int] | None:
    """A parsed blood pressure ("120/80"); anything else (an unparsed reading) is ignored."""
    v = _val(fields, fid)
    m = re.fullmatch(r"(\d{2,3})/(\d{2,3})", v) if isinstance(v, str) else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def _flag(fields: Fields, fids: list[str], flag: str) -> None:
    for fid in fids:
        if fid in fields and flag not in fields[fid].flags:
            fields[fid].flags.append(flag)


def _not_applicable(fields: Fields, fid: str, reason: str) -> None:
    f = fields.get(fid)
    if f is not None and f.status == FieldStatus.NOT_PROVIDED:
        f.status = FieldStatus.NOT_APPLICABLE
        f.source = "rule"
        f.flags.append(f"not_applicable:{reason}")


def _checked(fields: Fields, fid: str) -> bool | None:
    f = fields.get(fid)
    if f is None or f.status != FieldStatus.KNOWN:
        return None
    return bool(f.value)


# ---------------------------------------------------------------------------
def check_pregnancy(f: Fields) -> None:
    p = "pregnancy."
    lmp, edd, post = _date(f, p + "lmp_date"), _date(f, p + "edd"), _date(f, p + "term_exceeded_date")
    if lmp and edd and abs((edd - (lmp + timedelta(days=280))).days) > 4:
        _flag(f, [p + "lmp_date", p + "edd"], "edd_inconsistent_with_lmp")
    if edd and post and not (5 <= (post - edd).days <= 15):
        _flag(f, [p + "edd", p + "term_exceeded_date"], "post_term_inconsistent_with_edd")
    prev_date, prev_weight, prev_col = None, None, None
    for col, *_ in VISIT_COLS:
        v = f"{p}visit.{col}."
        vdate = _date(f, v + "visit_date")
        ga = _num(f, v + "gest_age")
        if lmp and vdate and ga is not None:
            expected = (vdate - lmp).days / 7
            if abs(expected - ga) > 2.5:
                _flag(f, [v + "gest_age", v + "visit_date"], "gest_age_inconsistent_with_dates")
        appt = _date(f, v + "appointment")
        if vdate and appt and abs((appt - vdate).days) > 120:
            _flag(f, [v + "appointment", v + "visit_date"], "appointment_far_from_visit")
        if vdate:
            if prev_date and vdate < prev_date:
                _flag(f, [v + "visit_date", f"{p}visit.{prev_col}.visit_date"], "visits_out_of_order")
            if lmp and not (0 <= (vdate - lmp).days <= 320):
                _flag(f, [v + "visit_date"], "visit_date_outside_pregnancy")
        weight = _num(f, v + "weight")
        if weight is not None:
            if prev_weight is not None and abs(weight - prev_weight) > 6:
                _flag(f, [v + "weight"], "weight_jump_between_visits")
            prev_weight = weight
        bp = _bp(f, v + "bp")
        if bp:
            s, d = bp
            if not (70 <= s <= 220 and 40 <= d <= 140 and s > d):
                _flag(f, [v + "bp"], "bp_implausible")
        if vdate:
            prev_date, prev_col = vdate, col


def check_history(f: Fields) -> None:
    p = "history."
    g, par, alive = _num(f, p + "gravidity"), _num(f, p + "parity"), _num(f, p + "living_children")
    if g is not None and par is not None and par > g:
        _flag(f, [p + "gravidity", p + "parity"], "parity_exceeds_gravidity")
    if par is not None and alive is not None and alive > par + 2:  # twins allow a small excess
        _flag(f, [p + "parity", p + "living_children"], "living_children_exceed_parity")
    ab = _num(f, p + "anomaly.abortion.count")
    if g is not None and par is not None and ab is not None and par + ab > g:
        _flag(f, [p + "gravidity", p + "parity", p + "anomaly.abortion.count"], "gravidity_lt_parity_plus_abortions")
    # Previous-delivery columns beyond the parity are logically not applicable.
    if par is not None:
        for k in range(int(par) + 1, 6):
            for row in ("date", "mode", "cs_indication", "complication", "weight", "nb_complication"):
                _not_applicable(f, f"{p}prev{k}.{row}", "beyond_parity")
    for k in range(1, 6):
        mode = _val(f, f"{p}prev{k}.mode")
        if mode in ("vaginal", "instrumental"):
            _not_applicable(f, f"{p}prev{k}.cs_indication", "not_a_cesarean")
    if _checked(f, p + "rubella_vaccinated") is False:
        _not_applicable(f, p + "rubella_date", "not_vaccinated")
    if _checked(f, p + "hepb_vaccinated") is False:
        _not_applicable(f, p + "hepb_date", "not_vaccinated")


def check_delivery(f: Fields) -> None:
    p = "delivery."
    cesarean = _checked(f, p + "mode.cesarean_planned") or _checked(f, p + "mode.cesarean_emergency")
    vaginal = _checked(f, p + "mode.vaginal") or _checked(f, p + "mode.instrumental")
    if vaginal and not cesarean:
        _not_applicable(f, p + "cesarean_indication", "not_a_cesarean")
    if cesarean is False and _val(f, p + "cesarean_indication"):
        _flag(f, [p + "cesarean_indication"], "indication_without_cesarean")
    if _checked(f, p + "newborn_status.stillborn"):
        for fid in ("newborn.weight", "newborn.head_circumference"):
            if _val(f, p + fid) is None:
                _not_applicable(f, p + fid, "stillborn")
    w, ga = _num(f, p + "newborn.weight"), _num(f, p + "newborn.gest_age")
    if w is not None and ga is not None and ga >= 37 and w < 1500:
        _flag(f, [p + "newborn.weight", p + "newborn.gest_age"], "birth_weight_inconsistent_with_term")


def check_pp_mother(prefix: str) -> Callable[[Fields], None]:
    def rule(f: Fields) -> None:
        bp = _bp(f, prefix + "bp")
        if bp:
            s, d = bp
            if not (70 <= s <= 220 and 40 <= d <= 140 and s > d):
                _flag(f, [prefix + "bp"], "bp_implausible")
        if _checked(f, prefix + "cesarean") is False:
            _not_applicable(f, prefix + "scar_state", "not_a_cesarean")
        if _checked(f, prefix + "medication_intake") is False:
            _not_applicable(f, prefix + "medication_details", "no_medication")
    return rule


def check_pp_newborn(prefix: str) -> Callable[[Fields], None]:
    def rule(f: Fields) -> None:
        if _checked(f, prefix + "transfer") is False:
            _not_applicable(f, prefix + "referral_facility", "no_transfer")
    return rule


def check_exclusive_groups(page_type: PageType, f: Fields) -> None:
    from dayone.forms.layout import PAGE_FIELDS

    groups: dict[str, list[str]] = {}
    for spec in PAGE_FIELDS[page_type]:
        if spec.exclusive and spec.group:
            groups.setdefault(spec.group, []).append(spec.id)
    for fids in groups.values():
        ticked = [fid for fid in fids if fid in f and f[fid].value is True]
        if len(ticked) > 1:
            _flag(f, ticked, "several_options_ticked")


RULES: dict[PageType, Callable[[Fields], None]] = {
    PageType.PREGNANCY: check_pregnancy,
    PageType.HISTORY: check_history,
    PageType.DELIVERY: check_delivery,
    PageType.PP_EARLY_MOTHER: check_pp_mother("pp_early_mother."),
    PageType.PP_LATE_MOTHER: check_pp_mother("pp_late_mother."),
    PageType.PP_EARLY_NEWBORN: check_pp_newborn("pp_early_newborn."),
    PageType.PP_LATE_NEWBORN: check_pp_newborn("pp_late_newborn."),
}


def apply_rules(page_type: PageType, fields: Fields) -> None:
    check_exclusive_groups(page_type, fields)
    rule = RULES.get(page_type)
    if rule:
        rule(fields)
