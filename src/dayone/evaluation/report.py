"""Calibrate the confidence model and report metrics (docs/EVALUATION.md).

    python -m dayone.evaluation.report calibrate   # fit on the calibration split, choose τ
    python -m dayone.evaluation.report report      # metrics on the test split -> docs/RESULTS.md

Stored predictions are re-scored with the current confidence model (no OCR re-run).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from dayone.evaluation.metrics import FieldOutcome, breakdown, compare, risk_coverage, summarise
from dayone.extraction.confidence import DEFAULT_MODEL_PATH, ConfidenceModel, feature_vector, fit
from dayone.extraction.pipeline import PipelineConfig, rescore
from dayone.schema import PageExtraction

log = logging.getLogger(__name__)
TARGET_SILENT_ERROR = 0.02  # rule 2 of the protocol


def load_items(eval_dir: Path, run: str) -> list[tuple[dict, PageExtraction]]:
    items = json.loads((eval_dir / "dataset.json").read_text())
    out = []
    for item in items:
        path = eval_dir / "runs" / run / f"{item['id']}.json"
        if path.exists():
            out.append((item, PageExtraction.model_validate_json(path.read_text())))
    return out


def outcomes_for(pairs: list[tuple[dict, PageExtraction]], model: ConfidenceModel,
                 cfg: PipelineConfig) -> list[FieldOutcome]:
    out = []
    for item, pred in pairs:
        out += compare(item, rescore(pred, model, cfg))
    return out


def _readings(pairs: list[tuple[dict, PageExtraction]], cfg: PipelineConfig) -> tuple[np.ndarray, np.ndarray]:
    """Every OCR reading of ``pairs`` as (features, right/wrong)."""
    neutral = ConfidenceModel.default()
    xs, ys = [], []
    for item, pred in pairs:
        scored = rescore(pred, neutral, cfg)  # recomputes parse features and rule flags
        for o in compare(item, scored):
            f = scored.fields.get(o.field_id)
            if f is not None and f.source == "ocr":
                xs.append(feature_vector(f.features))
                ys.append(o.correct)
    return np.array(xs), np.array(ys)


def calibrate(eval_dir: Path, run: str, model_path: Path) -> ConfidenceModel:
    pairs = [(i, p) for i, p in load_items(eval_dir, run) if i["split"] == "calib"]
    if not pairs:
        raise SystemExit("no calibration predictions: run `make eval` first")
    cfg = PipelineConfig()
    x, y = _readings(pairs, cfg)
    model = fit(x, y)
    # Rule 2: smallest τ with silent error <= 2 % on calibration (clean/mild/medium). To avoid choosing
    # τ on readings the model was fitted on, τ is chosen on out-of-fold predictions: leave-one-patient-out.
    usable = [(i, p) for i, p in pairs if i["level"] != "severe"]
    patients = sorted({i["patient"] for i, _ in pairs})
    fold_models = {}
    for pat in patients:
        xt, yt = _readings([(i, p) for i, p in pairs if i["patient"] != pat], cfg)
        fold_models[pat] = fit(xt, yt)
    taus = np.round(np.arange(0.50, 0.995, 0.01), 2)
    chosen = None
    for tau in taus:
        outcomes = []
        for pat, fm in fold_models.items():
            outcomes += outcomes_for([(i, p) for i, p in usable if i["patient"] == pat], fm,
                                     PipelineConfig(accept_threshold=float(tau)))
        rate = summarise(outcomes)["silent_error_rate"]
        if rate is not None and rate <= TARGET_SILENT_ERROR:
            chosen = float(tau)
            break
    if chosen is None:
        log.warning("target silent error <= %.0f %% NOT reached on calibration even at τ=0.99", 100 * TARGET_SILENT_ERROR)
    model.accept_threshold = chosen if chosen is not None else 0.99
    model.target_reached = chosen is not None
    model.save(model_path)
    log.info("confidence model fitted on %d readings (%.1f%% correct); τ=%.2f chosen out-of-fold (%d patients) -> %s",
             len(y), 100 * y.mean(), model.accept_threshold, len(patients), model_path)
    return model


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{100 * v:.1f} %"


def _figures(test: list[FieldOutcome], rc: list[dict], out_dir: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    summ = summarise([o for o in test if o.level != "severe"])
    rel = summ.get("calibration", {}).get("reliability", [])
    if rel:
        fig, ax = plt.subplots(figsize=(4.2, 4.2))
        ax.plot([0, 1], [0, 1], color="#999", lw=1, ls="--", label="perfect calibration")
        ax.plot([r["mean_confidence"] for r in rel], [r["accuracy"] for r in rel], marker="o", color="#2a6f97",
                label="pipeline")
        ax.set_xlabel("confidence")
        ax.set_ylabel("observed accuracy")
        ax.set_title("Reliability (test split, OCR fields)")
        ax.legend(loc="lower right", frameon=False)
        fig.tight_layout()
        fig.savefig(out_dir / "reliability.png", dpi=150)
        plt.close(fig)
        files.append("reliability.png")
    if rc:
        fig, ax = plt.subplots(figsize=(4.8, 3.6))
        ax.plot([r["coverage"] for r in rc], [100 * r["silent_error_rate"] for r in rc], color="#2a6f97")
        ax.set_xlabel("share of handwritten fields auto-accepted")
        ax.set_ylabel("error among auto-accepted (%)")
        ax.set_title("Risk–coverage (test split)")
        fig.tight_layout()
        fig.savefig(out_dir / "risk_coverage.png", dpi=150)
        plt.close(fig)
        files.append("risk_coverage.png")
    return files


def report(eval_dir: Path, run: str, model_path: Path, out_md: Path) -> dict:
    model = ConfidenceModel.load(model_path)
    cfg = PipelineConfig()
    if model.accept_threshold is not None:
        cfg.accept_threshold = model.accept_threshold
    pairs = load_items(eval_dir, run)
    test_pairs = [(i, p) for i, p in pairs if i["split"] == "test"]
    test = outcomes_for(test_pairs, model, cfg)
    not_severe = [o for o in test if o.level != "severe"]
    res = {
        "run": run, "model": model.source, "accept_threshold": cfg.accept_threshold,
        "n_items": len(test_pairs),
        "overall_excluding_severe": summarise(not_severe),
        "by_level_specimen": breakdown([o for o in test if not o.item.startswith("synth")], "level"),
        "by_language": breakdown([o for o in test if o.item.startswith("synth") or o.level == "medium"], "language"),
        "by_page_type": breakdown(not_severe, "page_type"),
        "by_value_type": breakdown(not_severe, "value_type"),
        "risk_coverage": risk_coverage(not_severe),
    }
    # page classification and quality gate
    cls_ok, gate = [], {}
    for item, pred in test_pairs:
        cls_ok.append(pred.page_type is not None and pred.page_type.value == item["page_type"])
        gate.setdefault(item["level"], []).append(not pred.quality.get("ok", True))
    res["page_classification_accuracy"] = float(np.mean(cls_ok)) if cls_ok else None
    res["retake_requested_share_by_level"] = {k: float(np.mean(v)) for k, v in gate.items()}
    acc_gate = {}
    for item, pred in test_pairs:
        key = "accepted" if pred.quality.get("ok", True) else "rejected"
        acc_gate.setdefault(key, []).extend(compare(item, rescore(pred, model, cfg)))
    res["accuracy_by_quality_gate"] = {k: summarise(v)["handwritten"]["accuracy"] for k, v in acc_gate.items()}
    figs = _figures(test, res["risk_coverage"], out_md.parent / "figures")
    (eval_dir / f"report_{run}.json").write_text(json.dumps(res, indent=1))
    out_md.write_text(render_markdown(res, figs))
    return res


def render_markdown(r: dict, figs: list[str]) -> str:
    o = r["overall_excluding_severe"]
    hw = o["handwritten"]
    cal = o.get("calibration", {})
    lines = [
        "# Results",
        "",
        "> Generated by `make report` from the stored predictions — do not edit by hand.",
        f"> Test split (patients 6-10), {r['n_items']} captures, confidence model: `{r['model']}`, "
        f"acceptance threshold τ = {r['accept_threshold']:.2f} (chosen on the calibration split).",
        "",
        "## Headline (test split, clean + mild + medium captures, FR/AR/EN)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Handwritten fields evaluated | {hw['n']} |",
        f"| **Extraction accuracy** (value right, before review) | **{_pct(hw['accuracy'])}** |",
        f"| Auto-accepted without review | {_pct(hw['auto_accepted_share'])} |",
        f"| Accuracy of auto-accepted values | {_pct(hw['accuracy_when_auto_accepted'])} |",
        f"| **Silent error rate** (handwritten value wrong *and* no question asked) | **{_pct(o['silent_error_rate'])}** |",
        f"| Silent error rate over every field (text, blanks, dashes, tick boxes) | {_pct(o['all_fields_silent_error_rate'])} |",
        f"| Fields the midwife is asked about (all fields) | {_pct(o['all_fields_review_share'])} |",
        f"| Sent to the midwife for review / marked illegible | {_pct(hw['flagged_for_review_share'])} |",
        f"| Blank fields recognised as blank | {_pct(o['blank']['accuracy'])} (n={o['blank']['n']}) |",
        f"| Dashes recognised as not applicable | {_pct(o['dash']['accuracy'])} (n={o['dash']['n']}) |",
        f"| Tick boxes | {_pct(o['checkbox']['accuracy'])} (n={o['checkbox']['n']}) |",
        f"| All fields (text + boxes + blanks) | {_pct(o['all_fields_accuracy'])} (n={o['n_fields']}) |",
        f"| Page type classification | {_pct(r['page_classification_accuracy'])} |",
        f"| Confidence calibration: ECE / Brier / AUROC | {cal.get('ece', float('nan')):.3f} / "
        f"{cal.get('brier', float('nan')):.3f} / {cal.get('auroc') or float('nan'):.3f} |",
        "",
        "## By capture level (specimen pages, French)",
        "",
        "| Level | Handwritten n | Accuracy | Auto-accepted | Silent errors | All fields | Retake requested |",
        "|---|---|---|---|---|---|---|",
    ]
    for lvl in ("clean", "mild", "medium", "severe"):
        b = r["by_level_specimen"].get(lvl)
        if b:
            lines.append(f"| {lvl} | {b['n_handwritten']} | {_pct(b['accuracy'])} | {_pct(b['auto_accepted_share'])} | "
                         f"{_pct(b['silent_error_rate'])} | {_pct(b['all_fields_accuracy'])} | "
                         f"{_pct(r['retake_requested_share_by_level'].get(lvl))} |")
    lines += ["", "`severe` captures are sent back by the on-device quality check; their numbers show what would "
              "happen if the midwife forced them through.", "",
              "## By language (medium captures)", "",
              "| Language of the field | n | Accuracy | Auto-accepted | Silent errors |", "|---|---|---|---|---|"]
    for k, b in r["by_language"].items():
        lines.append(f"| {k} | {b['n_handwritten']} | {_pct(b['accuracy'])} | {_pct(b['auto_accepted_share'])} | "
                     f"{_pct(b['silent_error_rate'])} |")
    lines += ["", "## By page type", "", "| Page | n | Accuracy | Auto-accepted | Silent errors |", "|---|---|---|---|---|"]
    for k, b in r["by_page_type"].items():
        lines.append(f"| {k} | {b['n_handwritten']} | {_pct(b['accuracy'])} | {_pct(b['auto_accepted_share'])} | "
                     f"{_pct(b['silent_error_rate'])} |")
    lines += ["", "## By value type", "", "| Type | n | Accuracy | Auto-accepted | Silent errors |", "|---|---|---|---|---|"]
    for k, b in r["by_value_type"].items():
        if b["n_handwritten"]:
            lines.append(f"| {k} | {b['n_handwritten']} | {_pct(b['accuracy'])} | {_pct(b['auto_accepted_share'])} | "
                         f"{_pct(b['silent_error_rate'])} |")
    lines += ["", "## Calibration", "", "| Confidence bin | n | Mean confidence | Observed accuracy |", "|---|---|---|---|"]
    for row in cal.get("reliability", []):
        lines.append(f"| {row['bin']} | {row['n']} | {row['mean_confidence']:.2f} | {row['accuracy']:.2f} |")
    lines += ["", "## Risk–coverage", "", "| τ | Auto-accepted | Error among auto-accepted |", "|---|---|---|"]
    for row in r["risk_coverage"][::3]:
        lines.append(f"| {row['threshold']:.2f} | {_pct(row['coverage'])} | {_pct(row['silent_error_rate'])} |")
    lines += ["", f"Accuracy on captures accepted by the quality check: {_pct(r['accuracy_by_quality_gate'].get('accepted'))}; "
              f"on captures it rejected: {_pct(r['accuracy_by_quality_gate'].get('rejected'))}.", ""]
    for f in figs:
        lines.append(f"![{f}](figures/{f})")
    return "\n".join(lines) + "\n"


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["calibrate", "report"])
    ap.add_argument("--eval-dir", type=Path, default=Path("artifacts/eval"))
    ap.add_argument("--run", default="main")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    ap.add_argument("--out", type=Path, default=Path("docs/RESULTS.md"))
    args = ap.parse_args()
    if args.command == "calibrate":
        calibrate(args.eval_dir, args.run, args.model)
    else:
        r = report(args.eval_dir, args.run, args.model, args.out)
        o = r["overall_excluding_severe"]
        print(json.dumps({"handwritten": o["handwritten"], "silent_error_rate": o["silent_error_rate"],
                          "checkbox": o["checkbox"], "calibration": {k: v for k, v in o.get("calibration", {}).items()
                                                                     if k != "reliability"}}, indent=1))


if __name__ == "__main__":
    main()
