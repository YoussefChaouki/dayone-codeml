"""Field-level comparison of predictions with ground truth, and summary metrics."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from dayone.evaluation.groundtruth import MISSING_GLYPH
from dayone.extraction.normalize import canonical, fold
from dayone.forms.layout import ALL_FIELDS
from dayone.schema import FieldKind, FieldResult, FieldStatus, PageExtraction, ValueType

VALUE = {FieldStatus.KNOWN, FieldStatus.NEEDS_REVIEW}
EMPTY = {FieldStatus.NOT_PROVIDED, FieldStatus.NOT_APPLICABLE}


def values_match(fid: str, gt: dict, pred_value) -> bool:
    spec = ALL_FIELDS[fid]
    if pred_value is None:
        return False
    if spec.value_type == ValueType.TEXT and MISSING_GLYPH in (gt.get("raw") or "") and isinstance(gt["value"], str):
        # the specimen font lacked some accented glyphs: they are a 0-2 char wildcard
        parts = [re.escape(fold(p)) for p in gt["raw"].split(MISSING_GLYPH)]
        return re.fullmatch(r".{0,2}".join(parts), fold(str(pred_value))) is not None
    return canonical(spec, pred_value) == canonical(spec, gt["value"])


@dataclass
class FieldOutcome:
    item: str
    field_id: str
    kind: str  # text | checkbox
    gt_status: str
    pred_status: str
    correct: bool  # value / emptiness / tick decision right
    accepted: bool  # the agent did not ask for a review (KNOWN, or a blank / dash / unknown decision)
    confidence: float
    level: str
    language: str  # fr | ar | en (field language for synthetic pages)
    page_type: str
    value_type: str
    split: str
    source: str


def compare(item: dict, pred: PageExtraction) -> list[FieldOutcome]:
    out = []
    page_ok = pred.page_type is not None and pred.page_type.value == item["page_type"]
    for fid, gt in item["fields"].items():
        spec = ALL_FIELDS[fid]
        p: FieldResult | None = pred.fields.get(fid) if page_ok else None
        gt_status = FieldStatus(gt["status"])
        if p is None:  # page not recognised (or misclassified): every field is missed
            pred_status, pval, conf, source = "MISSING", None, 0.0, "none"
        else:
            pred_status, pval, conf, source = p.status.value, p.value, p.confidence, p.source
        ps = FieldStatus(pred_status) if pred_status != "MISSING" else None
        if spec.kind == FieldKind.CHECKBOX:
            correct = ps is not None and bool(pval) == bool(gt["value"])
        elif gt_status in VALUE:
            correct = ps in VALUE and values_match(fid, gt, pval)
        elif gt_status == FieldStatus.NOT_PROVIDED:
            correct = ps in EMPTY
        else:  # NOT_APPLICABLE (dash) or UNKNOWN written on paper
            correct = ps == gt_status
        out.append(FieldOutcome(
            item=item["id"], field_id=fid, kind=spec.kind.value, gt_status=gt_status.value,
            pred_status=pred_status, correct=bool(correct),
            # accepted = the agent did not ask the midwife about this field
            accepted=p is not None and p.status not in (FieldStatus.NEEDS_REVIEW, FieldStatus.ILLEGIBLE)
            and "confirm_special" not in p.flags,
            confidence=conf, level=item["level"],
            language=item["languages"].get(fid, "fr") if item["kind"] == "synth" else "fr",
            page_type=item["page_type"], value_type=spec.value_type.value, split=item["split"], source=source))
    return out


def rate(xs: list[bool]) -> float | None:
    return float(np.mean(xs)) if xs else None


def ece(conf: np.ndarray, correct: np.ndarray, bins: int = 10) -> tuple[float, list[dict]]:
    edges = np.linspace(0, 1, bins + 1)
    table, total = [], 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (conf >= lo) & ((conf < hi) | ((hi == 1.0) & (conf <= 1.0)))
        if m.sum() == 0:
            continue
        acc, mc = float(correct[m].mean()), float(conf[m].mean())
        total += m.sum() / len(conf) * abs(acc - mc)
        table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()), "mean_confidence": round(mc, 3),
                      "accuracy": round(acc, 3)})
    return float(total), table


def auroc(conf: np.ndarray, correct: np.ndarray) -> float | None:
    pos, neg = conf[correct], conf[~correct]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def handwritten(outcomes: list[FieldOutcome]) -> list[FieldOutcome]:
    return [o for o in outcomes if o.kind == "text" and o.gt_status in ("KNOWN", "NEEDS_REVIEW")]


def summarise(outcomes: list[FieldOutcome]) -> dict:
    hw = handwritten(outcomes)
    blank = [o for o in outcomes if o.kind == "text" and o.gt_status == "NOT_PROVIDED"]
    dash = [o for o in outcomes if o.kind == "text" and o.gt_status == "NOT_APPLICABLE"]
    boxes = [o for o in outcomes if o.kind == "checkbox"]
    accepted = [o for o in hw if o.accepted]
    ocr = [o for o in outcomes if o.source == "ocr"]
    res = {
        "n_fields": len(outcomes),
        "all_fields_accuracy": rate([o.correct for o in outcomes]),
        "handwritten": {"n": len(hw), "accuracy": rate([o.correct for o in hw]),
                        "auto_accepted_share": rate([o.accepted for o in hw]),
                        "accuracy_when_auto_accepted": rate([o.correct for o in accepted]),
                        "flagged_for_review_share": rate([not o.accepted for o in hw]),
                        "missed_as_blank_share": rate([o.pred_status in ("NOT_PROVIDED", "NOT_APPLICABLE") for o in hw])},
        "silent_error_rate": rate([not o.correct for o in accepted]),
        "all_fields_silent_error_rate": rate([not o.correct for o in outcomes if o.accepted]),
        "all_fields_review_share": rate([not o.accepted for o in outcomes]),
        "blank": {"n": len(blank), "accuracy": rate([o.correct for o in blank])},
        "dash": {"n": len(dash), "accuracy": rate([o.correct for o in dash])},
        "checkbox": {"n": len(boxes), "accuracy": rate([o.correct for o in boxes]),
                     "silent_error_rate": rate([not o.correct for o in boxes if o.accepted])},
    }
    if ocr:
        conf = np.array([o.confidence for o in ocr])
        cor = np.array([o.correct for o in ocr])
        e, table = ece(conf, cor)
        res["calibration"] = {"n": len(ocr), "ece": round(e, 4), "brier": round(float(np.mean((conf - cor) ** 2)), 4),
                              "auroc": auroc(conf, cor), "reliability": table}
    return res


def breakdown(outcomes: list[FieldOutcome], key: str) -> dict[str, dict]:
    groups: dict[str, list[FieldOutcome]] = defaultdict(list)
    for o in outcomes:
        groups[getattr(o, key)].append(o)
    out = {}
    for k, v in sorted(groups.items()):
        hw = handwritten(v)
        acc = [o for o in hw if o.accepted]
        out[k] = {"n_handwritten": len(hw), "accuracy": rate([o.correct for o in hw]),
                  "auto_accepted_share": rate([o.accepted for o in hw]),
                  "silent_error_rate": rate([not o.correct for o in acc]),
                  "all_fields_accuracy": rate([o.correct for o in v])}
    return out


def risk_coverage(outcomes: list[FieldOutcome], thresholds: np.ndarray | None = None) -> list[dict]:
    hw = [o for o in handwritten(outcomes) if o.source == "ocr"]
    if not hw:
        return []
    thresholds = thresholds if thresholds is not None else np.round(np.linspace(0.0, 0.99, 34), 3)
    out = []
    for t in thresholds:
        acc = [o for o in hw if o.confidence >= t]
        out.append({"threshold": float(t), "coverage": len(acc) / len(hw),
                    "silent_error_rate": rate([not o.correct for o in acc]) if acc else 0.0})
    return out
