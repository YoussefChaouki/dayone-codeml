"""Photo of a registry page -> structured, typed fields with status and confidence.

Steps (only step 4 needs the AI models; 1-3 run on the phone, offline):
1. capture quality check (retake if unreadable),
2. page-type classification + registration onto the blank template,
3. pixel reading: tick boxes, blank fields (no ink -> NOT_PROVIDED without any model call),
4. local OCR of inked text fields: primary reader (qwen3.5, prompted with the field's
   label and expected format) and a second, independent reader (glm-ocr),
5. parsing into typed canonical values, cross-field consistency rules,
6. calibrated confidence -> KNOWN if above the field's threshold, else NEEDS_REVIEW.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import re
import time
from dataclasses import dataclass

import numpy as np

from dayone.extraction.confidence import ConfidenceModel, checkbox_confidence
from dayone.extraction.fields import checkbox_fill, crop_for_ocr, field_ink, ink_map
from dayone.extraction.normalize import VOCABULARIES, canonical, parse_value
from dayone.extraction.ocr import CACHE_DIR, PRIMARY_MODEL, OcrEngine, OcrUnavailable, Reading
from dayone.extraction.quality import QualityReport, assess_capture
from dayone.extraction.register import Registrar
from dayone.extraction.validators import apply_rules
from dayone.forms.layout import PAGE_FIELDS
from dayone.pii import scrub_text
from dayone.schema import FieldKind, FieldResult, FieldSpec, FieldStatus, PageExtraction, PageType, ValueType

log = logging.getLogger(__name__)

_ARABIC = re.compile(r"[؀-ۿ]")


@dataclass
class PipelineConfig:
    primary_model: str = "qwen3.5:9b"
    second_model: str | None = "glm-ocr"
    workers: int = 2
    blank_ink_cols: float = 1.5  # less ink than this (horizontal extent, in points): the field is blank
    checkbox_on: float = 0.06  # box fill above which a box counts as ticked
    checkbox_review_band: tuple[float, float] = (0.03, 0.12)  # fills in this band are sent to review
    accept_threshold: float = 0.80  # calibrated confidence needed to auto-accept a value (KNOWN)
    illegible_threshold: float = 0.15  # below this and unparseable: ILLEGIBLE rather than NEEDS_REVIEW


def _hint(spec: FieldSpec) -> str:
    vt = spec.value_type
    if vt == ValueType.DATE:
        return "a date such as 12/03/2025"
    if vt == ValueType.BP:
        return "a blood pressure such as 120/80"
    if vt == ValueType.GEST_AGE:
        return "a gestational age such as 24 SA"
    if vt in (ValueType.INT, ValueType.FLOAT):
        return f"a number{f' ({spec.unit})' if spec.unit else ''}"
    if vt == ValueType.BOOL:
        return "Oui or Non (or Yes/No, نعم/لا)"
    if vt == ValueType.CODE:
        return "a registry code made of digits, letters and dashes"
    if spec.vocabulary and spec.vocabulary in VOCABULARIES:
        forms = [forms[0] for forms in VOCABULARIES[spec.vocabulary].values()][:4]
        return "a short word such as " + ", ".join(forms)
    return "a short text"


def ocr_prompt(spec: FieldSpec) -> str:
    return (f"Handwritten field «{spec.label_fr}» from a Moroccan maternal health registry. "
            f"Expected: {_hint(spec)}. The text may be in French, Arabic or English. "
            "Transcribe exactly what is written, nothing else. If the field only shows a dash, answer -.")


def parse_features(spec: FieldSpec, primary_text: str, second_text: str | None) -> dict[str, float]:
    """Features that depend on how the readings parse (recomputed whenever the parser changes)."""
    parsed = parse_value(spec, primary_text)
    agree = None
    if second_text is not None:
        sp = parse_value(spec, second_text)
        agree = float(sp.status == parsed.status and canonical(spec, sp.value) == canonical(spec, parsed.value))
    return {
        "second_agree": agree if agree is not None else 0.0,
        "second_missing": float(agree is None),
        "parse_ok": float(parsed.ok),
        "lexicon_score": parsed.lexicon_score,
        "out_of_range": float("out_of_range" in parsed.flags or "bp_implausible" in parsed.flags),
        "is_arabic": float(bool(_ARABIC.search(primary_text or ""))),
    }


def text_features(spec: FieldSpec, primary: Reading, second: Reading | None, ink: dict, reg_similarity: float,
                  quality: QualityReport | None) -> dict[str, float]:
    vt = spec.value_type
    return {
        "lp_mean": primary.mean_logprob,
        "lp_min": max(primary.min_logprob, -10.0),
        "n_tokens": float(len(primary.token_logprobs)),
        **parse_features(spec, primary.text, second.text if second is not None else None),
        "n_flags": 0.0,  # set by finalise() once the consistency rules have run
        "ink_cols": min(ink.get("ink_cols", 0.0), 200.0) / 100.0,
        "reg_similarity": reg_similarity,
        "sharpness": min((quality.metrics.get("sharpness", 0.0) if quality else 0.0), 1000.0) / 1000.0,
        "vt_date": float(vt == ValueType.DATE),
        "vt_bp": float(vt == ValueType.BP),
        "vt_number": float(vt in (ValueType.INT, ValueType.FLOAT, ValueType.GEST_AGE)),
        "vt_choice": float(vt in (ValueType.BOOL, ValueType.ENUM)),
        "vt_text": float(vt in (ValueType.TEXT, ValueType.CODE)),
    }


class Extractor:
    def __init__(self, config: PipelineConfig | None = None, registrar: Registrar | None = None,
                 primary: OcrEngine | None = None, second: OcrEngine | None = None,
                 confidence: ConfidenceModel | None = None, ocr_cache: bool = True) -> None:
        """``ocr_cache=False`` in production: the cache holds raw readings in clear (evaluation only)."""
        self.cfg = config or PipelineConfig()
        self.registrar = registrar or Registrar()
        cache = CACHE_DIR if ocr_cache else None
        self.primary = primary or OcrEngine(self.cfg.primary_model or PRIMARY_MODEL, cache_dir=cache)
        # The second reader is decided by the configuration (second_model=None disables it).
        if second is not None:
            self.second = second
        elif self.cfg.second_model:
            self.second = OcrEngine(self.cfg.second_model, timeout=60.0, cache_dir=cache)
        else:
            self.second = None
        self.confidence = confidence or ConfidenceModel.load()
        self._second_down_until = 0.0
        if self.confidence.accept_threshold is not None:
            self.cfg.accept_threshold = self.confidence.accept_threshold

    # ------------------------------------------------------------------
    def extract(self, image_bgr: np.ndarray, expected: PageType | None = None,
                check_quality: bool = True) -> PageExtraction:
        t0 = time.time()
        quality = assess_capture(image_bgr) if check_quality else None
        reg = self.registrar.register(image_bgr, expected)
        base = {"quality": {"ok": quality.ok, "issues": quality.issues, **quality.metrics} if quality else {},
                "registration": reg.summary(),
                "model_versions": {"primary": self.primary.model,
                                   "second": self.second.model if self.second else "",
                                   "confidence": self.confidence.source}}
        if not reg.ok:
            return PageExtraction(page_type=None, page_type_confidence=0.0, layout="failed",
                                  warnings=["page_not_recognised"], elapsed_s=time.time() - t0, **base)
        page_type = reg.page_type
        tpl = self.registrar.templates[page_type]
        ink = ink_map(reg.warped, tpl)
        fields: dict[str, FieldResult] = {}
        jobs: list[tuple[FieldSpec, dict]] = []
        for spec in PAGE_FIELDS[page_type]:
            if spec.kind == FieldKind.CHECKBOX:
                fields[spec.id] = self._checkbox(ink, spec)
                continue
            fi = field_ink(ink, spec)
            if fi["ink_cols"] < self.cfg.blank_ink_cols:
                conf = 0.97 if fi["ink_cols"] == 0 else 0.85
                fields[spec.id] = FieldResult(field_id=spec.id, status=FieldStatus.NOT_PROVIDED, confidence=conf,
                                              source="ink", features={"ink_cols": fi["ink_cols"]})
            else:
                jobs.append((spec, fi))
        crops = [crop_for_ocr(reg.warped, tpl, spec, box=fi["box"]) for spec, fi in jobs]
        with cf.ThreadPoolExecutor(self.cfg.workers) as ex:
            specs = [spec for spec, _ in jobs]
            primaries = list(ex.map(lambda a: self.primary.read(a[1], ocr_prompt(a[0])), zip(specs, crops, strict=True)))
        # The second reader only adds evidence: if it is unavailable the fields are marked
        # "second_missing" (lower confidence) instead of failing the page. Calls are sequential.
        seconds = [self._second_read(c) for c in crops]
        for (spec, fi), prim, sec in zip(jobs, primaries, seconds, strict=True):
            fields[spec.id] = self._text(spec, fi, prim, sec, reg.similarity, quality)
        apply_rules(page_type, fields)
        self._finalise(fields)
        warnings = []
        if quality is not None and not quality.ok:
            warnings.append("low_quality_capture:" + ",".join(quality.issues))
        return PageExtraction(page_type=page_type, page_type_confidence=reg.confidence, layout="template",
                              fields=fields, warnings=warnings, elapsed_s=time.time() - t0, **base)

    def _second_read(self, crop) -> Reading | None:
        if self.second is None or time.time() < self._second_down_until:
            return None
        try:
            return self.second.read(crop)
        except OcrUnavailable as e:
            # circuit breaker: do not wait for a dead reader on every remaining field
            self._second_down_until = time.time() + 120.0
            log.warning("second reader unavailable for 2 min, continuing without it: %s", e)
            return None

    # ------------------------------------------------------------------
    def _checkbox(self, ink, spec: FieldSpec) -> FieldResult:
        fill = checkbox_fill(ink, spec)
        lo, hi = self.cfg.checkbox_review_band
        ticked = fill >= self.cfg.checkbox_on
        status = FieldStatus.NEEDS_REVIEW if lo < fill < hi else FieldStatus.KNOWN
        return FieldResult(field_id=spec.id, status=status, value=ticked, raw_text="X" if ticked else "",
                           confidence=checkbox_confidence(fill, self.cfg.checkbox_on), source="checkbox",
                           features={"fill": round(fill, 4)})

    def _text(self, spec: FieldSpec, fi: dict, primary: Reading, second: Reading | None, reg_sim: float,
              quality: QualityReport | None) -> FieldResult:
        free_text = spec.value_type == ValueType.TEXT
        clean = (lambda t: scrub_text(t)) if free_text else (lambda t: t)  # no phone / ID number in comments
        primary_text = clean(primary.text)
        second_text = clean(second.text) if second is not None else None
        parsed = parse_value(spec, primary_text)
        feats = text_features(spec, primary, second, fi, reg_sim, quality)
        alternatives = [clean(a) for a in primary.alternatives if not a.startswith("<|")]
        if second_text and second_text != primary_text:
            alternatives.insert(0, second_text)
        status, value = parsed.status, parsed.value
        if status == FieldStatus.NOT_PROVIDED:
            # ink was seen but the reader returned nothing: that is not a blank field
            status, value = FieldStatus.ILLEGIBLE, None
        return FieldResult(field_id=spec.id, status=status, value=value, raw_text=primary_text,
                           second_text=second_text, alternatives=alternatives[:3], source="ocr",
                           flags=list(parsed.flags), features={k: round(v, 4) for k, v in feats.items()})

    def _finalise(self, fields: dict[str, FieldResult]) -> None:
        finalise(fields, self.confidence, self.cfg)


def finalise(fields: dict[str, FieldResult], model: ConfidenceModel, cfg: PipelineConfig) -> None:
    """Confidence (once consistency-rule flags are known) and the decision to ask the midwife.

    A value is accepted without review (KNOWN) only if its confidence reaches τ **and** nothing
    is wrong with it: an automatic repair or any consistency-rule flag always sends it to review.
    Dashes / question marks read by the OCR keep their status but are confirmed with the midwife
    when the reading is not confident ("confirm_special").
    """
    for f in fields.values():
        if f.source == "checkbox" and "several_options_ticked" in f.flags:
            f.status = FieldStatus.NEEDS_REVIEW
        if f.source != "ocr":
            continue
        rule_flags = [fl for fl in f.flags if not fl.startswith("not_applicable")]
        f.features["n_flags"] = float(len(rule_flags))
        f.confidence = round(model.predict(f.features), 4)
        if f.status == FieldStatus.KNOWN:
            if f.features["parse_ok"] == 0.0 and f.confidence < cfg.illegible_threshold:
                f.status, f.value = FieldStatus.ILLEGIBLE, None
            elif f.confidence < cfg.accept_threshold or rule_flags:
                f.status = FieldStatus.NEEDS_REVIEW
        elif f.status in (FieldStatus.NOT_APPLICABLE, FieldStatus.UNKNOWN) and f.confidence < cfg.accept_threshold:
            f.flags.append("confirm_special")


def rescore(extraction: PageExtraction, model: ConfidenceModel, cfg: PipelineConfig) -> PageExtraction:
    """Recompute statuses and confidences of a stored extraction with another model/threshold.

    Readings (raw text, features) are kept, so recalibration never re-runs OCR.
    """
    from dayone.forms.layout import ALL_FIELDS

    out = extraction.model_copy(deep=True)
    if out.page_type is None:
        return out
    for f in out.fields.values():
        if f.source == "ocr":
            spec = ALL_FIELDS[f.field_id]
            parsed = parse_value(spec, f.raw_text)
            f.status, f.value, f.flags = parsed.status, parsed.value, list(parsed.flags)
            f.features.update(parse_features(spec, f.raw_text or "", f.second_text))
            if f.status == FieldStatus.NOT_PROVIDED:
                f.status, f.value = FieldStatus.ILLEGIBLE, None
        elif f.source == "rule":  # blank field turned NOT_APPLICABLE by a rule: let rules decide again
            f.status, f.source, f.flags = FieldStatus.NOT_PROVIDED, "ink", []
    apply_rules(out.page_type, out.fields)
    finalise(out.fields, model, cfg)
    return out
