"""Core data model: field statuses, field specifications and extraction results.

Every field extracted from a registry page carries an explicit status, a value
(only when the status allows one), a calibrated confidence and its provenance.
Missing information is never collapsed into a single "N/A".
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FieldStatus(StrEnum):
    KNOWN = "KNOWN"  # value read and trusted (confidence >= threshold) or confirmed by the midwife
    UNKNOWN = "UNKNOWN"  # the midwife wrote that the information is unknown ("?", "NSP", "inconnu")
    NOT_PROVIDED = "NOT_PROVIDED"  # the field is blank on paper
    ILLEGIBLE = "ILLEGIBLE"  # ink is present but could not be read
    NOT_APPLICABLE = "NOT_APPLICABLE"  # a dash was written, or the field is logically not applicable
    NEEDS_REVIEW = "NEEDS_REVIEW"  # a value was read but confidence is low or a consistency rule failed


# French labels used in the conversation and in the documentation.
STATUS_LABEL_FR = {
    FieldStatus.KNOWN: "CONNU",
    FieldStatus.UNKNOWN: "INCONNU",
    FieldStatus.NOT_PROVIDED: "NON_FOURNI",
    FieldStatus.ILLEGIBLE: "ILLISIBLE",
    FieldStatus.NOT_APPLICABLE: "NON_APPLICABLE",
    FieldStatus.NEEDS_REVIEW: "À_RÉVISER",
}

# Statuses that carry a value.
VALUE_STATUSES = {FieldStatus.KNOWN, FieldStatus.NEEDS_REVIEW}


class FieldKind(StrEnum):
    TEXT = "text"  # handwritten / printed value in a region
    CHECKBOX = "checkbox"  # a single tick box


class ValueType(StrEnum):
    TEXT = "text"  # free text (lexicon-corrected when a vocabulary exists)
    CODE = "code"  # registry code written by the midwife (patient linking key)
    INT = "int"
    FLOAT = "float"
    DATE = "date"  # ISO yyyy-mm-dd
    BP = "bp"  # "systolic/diastolic"
    BOOL = "bool"  # yes / no written as text, or a tick box
    ENUM = "enum"  # closed vocabulary, canonical tokens
    GEST_AGE = "gest_age"  # gestational age in weeks (SA)


class PageType(StrEnum):
    COVER = "cover"
    HISTORY = "history"
    PREGNANCY = "pregnancy"
    DELIVERY = "delivery"
    PP_EARLY_MOTHER = "pp_early_mother"
    PP_EARLY_NEWBORN = "pp_early_newborn"
    PP_LATE_MOTHER = "pp_late_mother"
    PP_LATE_NEWBORN = "pp_late_newborn"


PAGE_TITLES_FR = {
    PageType.COVER: "Couverture / établissement",
    PageType.HISTORY: "Identification et antécédents",
    PageType.PREGNANCY: "Grossesse actuelle",
    PageType.DELIVERY: "Accouchement",
    PageType.PP_EARLY_MOTHER: "Post-partum précoce — mère",
    PageType.PP_EARLY_NEWBORN: "Post-partum précoce — nouveau-né",
    PageType.PP_LATE_MOTHER: "Post-partum tardif — mère",
    PageType.PP_LATE_NEWBORN: "Post-partum tardif — nouveau-né",
}
PAGE_TITLES_EN = {
    PageType.COVER: "Cover / facility",
    PageType.HISTORY: "Identification and history",
    PageType.PREGNANCY: "Current pregnancy",
    PageType.DELIVERY: "Delivery",
    PageType.PP_EARLY_MOTHER: "Early postpartum — mother",
    PageType.PP_EARLY_NEWBORN: "Early postpartum — newborn",
    PageType.PP_LATE_MOTHER: "Late postpartum — mother",
    PageType.PP_LATE_NEWBORN: "Late postpartum — newborn",
}


class FieldSpec(BaseModel):
    """One field of the registry, positioned on the reference template (PDF points)."""

    id: str
    page_type: PageType
    section: str
    label_fr: str
    label_en: str
    kind: FieldKind
    value_type: ValueType
    region: tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points (595 x 842 page)
    unit: str | None = None
    plausible: tuple[float, float] | None = None  # numeric plausibility range
    vocabulary: str | None = None  # name of a closed vocabulary (see normalize.VOCABULARIES)
    group: str | None = None  # checkbox group (single- or multi-choice)
    option: str | None = None  # canonical option of this checkbox inside its group
    exclusive: bool = False  # single-choice group
    row: str | None = None  # table row key (longitudinal visits, previous deliveries...)
    col: str | None = None  # table column key


class FieldResult(BaseModel):
    field_id: str
    status: FieldStatus
    value: Any = None
    raw_text: str | None = None  # what was read, before normalization
    confidence: float = 0.0  # calibrated probability that (status, value) is correct
    alternatives: list[str] = Field(default_factory=list)  # other plausible readings, best first
    source: str = "ocr"  # ocr | checkbox | ink | vlm | manual | rule
    flags: list[str] = Field(default_factory=list)  # validator / pipeline messages
    features: dict[str, float] = Field(default_factory=dict)  # confidence model inputs (debug)


class PageExtraction(BaseModel):
    page_type: PageType | None
    page_type_confidence: float
    layout: str  # "template" or "free" (template-free VLM fallback) or "failed"
    fields: dict[str, FieldResult] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    registration: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    model_versions: dict[str, str] = Field(default_factory=dict)
    elapsed_s: float = 0.0
