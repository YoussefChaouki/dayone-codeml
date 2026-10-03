import pytest

from dayone.extraction.normalize import fold, parse_value
from dayone.forms.layout import ALL_FIELDS
from dayone.schema import FieldStatus

F = ALL_FIELDS


@pytest.mark.parametrize("raw,expected", [
    ("26/04/2025", "2025-04-26"), ("26-4-25", "2025-04-26"), ("٢٦/٠٤/٢٠٢٥", "2025-04-26"), ("2 6/04/2025", "2025-04-26"),
])
def test_dates(raw, expected):
    p = parse_value(F["pregnancy.lmp_date"], raw)
    assert p.status == FieldStatus.KNOWN and p.ok and p.value == expected


def test_invalid_date_is_kept_but_flagged():
    p = parse_value(F["pregnancy.lmp_date"], "31/02/2025")
    assert not p.ok and "date_unparsed" in p.flags


@pytest.mark.parametrize("raw,expected", [("104/74", "104/74"), ("12/8", "120/80"), ("10 4 / 74", "104/74")])
def test_blood_pressure(raw, expected):
    assert parse_value(F["pregnancy.visit.t1v2.bp"], raw).value == expected


def test_numbers_and_units():
    assert parse_value(F["delivery.newborn.weight"], "3587 g").value == 3587
    assert parse_value(F["pregnancy.visit.t1v2.platelets"], "186k").value == 186_000
    assert parse_value(F["pregnancy.visit.t1v2.hemoglobin"], "11,8 g/dL").value == 11.8
    assert parse_value(F["pregnancy.visit.t1v2.gest_age"], "12 SA").value == 12
    assert parse_value(F["pregnancy.visit.t1v2.gest_age"], "١٢ أسبوع").value == 12
    assert "out_of_range" in parse_value(F["pregnancy.visit.t1v2.weight"], "4").flags  # not repairable


@pytest.mark.parametrize("raw", ["Neg", "Négatif", "negative", "سلبي", "NEG"])
def test_multilingual_vocabulary(raw):
    p = parse_value(F["pregnancy.visit.t1v2.hiv"], raw)
    assert p.value == "negative" and p.ok


@pytest.mark.parametrize("raw,yes", [("Oui", True), ("Non", False), ("Yes", True), ("لا", False), ("نعم", True)])
def test_booleans(raw, yes):
    assert parse_value(F["pregnancy.visit.t1v2.edema"], raw).value is yes


@pytest.mark.parametrize("raw,status", [
    ("", FieldStatus.NOT_PROVIDED), ("   ", FieldStatus.NOT_PROVIDED), ("—", FieldStatus.NOT_APPLICABLE),
    ("-", FieldStatus.NOT_APPLICABLE), ("?", FieldStatus.UNKNOWN), ("inconnu", FieldStatus.UNKNOWN),
])
def test_missing_information_is_a_status(raw, status):
    assert parse_value(F["pregnancy.visit.t1v2.weight"], raw).status == status


def test_fold_is_accent_and_case_insensitive():
    assert fold("Céphalique") == fold("CEPHALIQUE") == "cephalique"


@pytest.mark.parametrize("fid,raw,expected", [
    ("pregnancy.visit.t1v2.weight", "791", 79.1),  # decimal separator lost
    ("delivery.newborn.weight", "35879", 3587),  # unit g read as 9
    ("pregnancy.visit.t1v2.bp", "137192", "137/92"),  # slash read as 1
    ("pp_early_mother.temperature", "372", 37.2),
])
def test_systematic_misreadings_are_repaired_and_flagged(fid, raw, expected):
    p = parse_value(F[fid], raw)
    assert p.value == expected and "repaired" in p.flags


def test_plausible_values_are_not_touched():
    assert parse_value(F["delivery.newborn.weight"], "3299 g").flags == []
