import numpy as np

from dayone.extraction.validators import apply_rules
from dayone.forms.layout import PII_ZONES
from dayone.forms.templates import SCALE
from dayone.linking import PLAUSIBLE, find_candidates, normalise_code, plausible
from dayone.pii import MASK, redact_capture, scrub_text
from dayone.schema import FieldResult, FieldStatus, PageType

PATIENTS = {
    "a" * 32: {"codes": ["2026-823-001"], "quasi": {"age": 31, "edd": "2026-01-31", "gravidity": 3, "parity": 1},
               "records": ["r1"]},
    "b" * 32: {"codes": ["2026-823-017"], "quasi": {"age": 24, "edd": "2026-05-02"}, "records": ["r2"]},
}


def test_code_normalisation_absorbs_ocr_confusions():
    assert normalise_code("2O26-823-OO1") == normalise_code("2026-823-001")
    assert normalise_code(" cm: 164125 ") == normalise_code("CM164125")


def test_exact_code_is_a_plausible_match():
    cands = find_candidates({"code": "2026-823-001", "age": 31, "edd": "2026-02-01"}, PATIENTS)
    assert cands[0].patient_id == "a" * 32 and cands[0].score >= 0.85 and plausible(cands)


def test_one_character_off_is_still_proposed():
    cands = find_candidates({"code": "2026-823-007", "age": 31}, PATIENTS)
    assert plausible(cands)  # midwife must decide: no automatic creation


def test_unrelated_code_and_data_is_not_plausible():
    cands = find_candidates({"code": "1999-111-555", "age": 40, "edd": "2026-09-01"}, PATIENTS)
    assert not any(c.score >= PLAUSIBLE for c in cands)


def test_scrub_text_masks_phone_and_national_id():
    assert scrub_text("rappeler au 06 12 34 56 78") == f"rappeler au {MASK}"
    assert MASK in scrub_text("CIN AB123456 vue")


def test_redaction_blacks_out_identifier_zones():
    img = np.full((2339, 1654, 3), 220, np.uint8)
    out, names = redact_capture(img, np.eye(3), PageType.HISTORY)
    assert set(names) == {"national_id", "address", "phone", "husband_name"}
    x0, y0, x1, y1 = PII_ZONES[PageType.HISTORY][0][1]
    cx, cy = int((x0 + x1) / 2 * SCALE), int((y0 + y1) / 2 * SCALE)
    assert (out[cy, cx] == 0).all() and (out[5, 5] == 220).all()


def _f(fid, value, status=FieldStatus.KNOWN, source="ocr"):
    return FieldResult(field_id=fid, status=status, value=value, source=source, confidence=0.9)


def test_edd_inconsistent_with_lmp_is_flagged():
    fields = {"pregnancy.lmp_date": _f("pregnancy.lmp_date", "2025-04-26"),
              "pregnancy.edd": _f("pregnancy.edd", "2026-03-31")}
    apply_rules(PageType.PREGNANCY, fields)
    assert "edd_inconsistent_with_lmp" in fields["pregnancy.edd"].flags


def test_blank_cesarean_indication_after_vaginal_birth_is_not_applicable():
    fields = {"delivery.mode.vaginal": _f("delivery.mode.vaginal", True, source="checkbox"),
              "delivery.mode.cesarean_planned": _f("delivery.mode.cesarean_planned", False, source="checkbox"),
              "delivery.mode.cesarean_emergency": _f("delivery.mode.cesarean_emergency", False, source="checkbox"),
              "delivery.cesarean_indication": _f("delivery.cesarean_indication", None, FieldStatus.NOT_PROVIDED, "ink")}
    apply_rules(PageType.DELIVERY, fields)
    assert fields["delivery.cesarean_indication"].status == FieldStatus.NOT_APPLICABLE


def test_two_ticks_in_a_single_choice_group_are_flagged():
    fields = {"delivery.newborn_status.alive": _f("delivery.newborn_status.alive", True, source="checkbox"),
              "delivery.newborn_status.stillborn": _f("delivery.newborn_status.stillborn", True, source="checkbox")}
    apply_rules(PageType.DELIVERY, fields)
    assert "several_options_ticked" in fields["delivery.newborn_status.alive"].flags


from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dayone.extraction.normalize import parse_value  # noqa: E402
from dayone.forms.layout import PAGE_FIELDS  # noqa: E402

READINGS = st.one_of(st.text(max_size=12), st.sampled_from(["-/-", "12O/8O", "TA: ?/80", "?", "—", "31/02/2025",
                                                             "1e9", "١٢/٨", "/", "120/", "/80", "999999999"]))


@settings(max_examples=60, deadline=None)
@given(page=st.sampled_from(list(PAGE_FIELDS)), readings=st.lists(READINGS, min_size=1, max_size=40))
def test_rules_never_crash_on_any_reading(page, readings):
    fields = {}
    for spec, text in zip(PAGE_FIELDS[page], readings * (len(PAGE_FIELDS[page]) // len(readings) + 1), strict=False):
        p = parse_value(spec, text)
        fields[spec.id] = FieldResult(field_id=spec.id, status=p.status, value=p.value, source="ocr", flags=p.flags)
    apply_rules(page, fields)  # must not raise


def test_profile_shows_the_most_recently_confirmed_code():
    from dayone.records import update_profile

    profile = update_profile(None, "r1", {"code": "2026-63-007"}, 1.0)
    profile = update_profile(profile, "r2", {"code": "2026-163-007"}, 2.0)  # code corrected on a re-digitisation
    assert profile["codes"][-1] == "2026-163-007"
    profile = update_profile(profile, "r3", {"code": "2026-63-007"}, 3.0)  # an older code confirmed again
    assert profile["codes"] == ["2026-163-007", "2026-63-007"]  # no duplicate, latest last


def test_ocr_variant_of_a_known_code_does_not_replace_it():
    from dayone.records import update_profile

    profile = update_profile(None, "r1", {"code": "2026-823-001"}, 1.0)
    profile = update_profile(profile, "r2", {"code": "2O26-823-OO1"}, 2.0)
    assert profile["codes"] == ["2026-823-001"]
