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

from dayone.evaluation.metrics import (
    FieldOutcome,
    accuracy_stat,
    bootstrap_ci,
    breakdown,
    compare,
    rate,
    risk_coverage,
    silent_stat,
    summarise,
)
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


TAU_GRID = np.round(np.arange(0.50, 0.995, 0.01), 2)  # declared floor: EVALUATION.md §4 rule 2
FREE_GRID = np.round(np.arange(0.0, 0.995, 0.01), 2)  # sensitivity analysis without the floor


def oof_tau(pairs, cfg: PipelineConfig, grid) -> tuple[float | None, dict]:
    """Smallest τ of ``grid`` whose out-of-fold (leave-one-patient-out) silent error is <= target."""
    usable = [(i, p) for i, p in pairs if i["level"] != "severe"]
    patients = sorted({i["patient"] for i, _ in pairs})
    fold_models = {pat: fit(*_readings([(i, p) for i, p in pairs if i["patient"] != pat], cfg)) for pat in patients}
    curve = {}
    for tau in grid:
        outcomes = []
        for pat, fm in fold_models.items():
            outcomes += outcomes_for([(i, p) for i, p in usable if i["patient"] == pat], fm,
                                     PipelineConfig(accept_threshold=float(tau)))
        rate = summarise(outcomes)["silent_error_rate"]
        curve[float(tau)] = rate
        if rate is not None and rate <= TARGET_SILENT_ERROR:
            return float(tau), curve
    return None, curve


def calibrate(eval_dir: Path, run: str, model_path: Path) -> ConfidenceModel:
    pairs = [(i, p) for i, p in load_items(eval_dir, run) if i["split"] == "calib"]
    if not pairs:
        raise SystemExit("no calibration predictions: run `make eval` first")
    cfg = PipelineConfig()
    x, y = _readings(pairs, cfg)
    model = fit(x, y)
    chosen, _ = oof_tau(pairs, cfg, TAU_GRID)
    if chosen is None:
        log.warning("target silent error <= %.0f %% NOT reached on calibration even at τ=0.99", 100 * TARGET_SILENT_ERROR)
    model.accept_threshold = chosen if chosen is not None else 0.99
    model.target_reached = chosen is not None
    model.save(model_path)
    log.info("confidence model fitted on %d readings (%.1f%% correct); τ=%.2f chosen out-of-fold -> %s",
             len(y), 100 * y.mean(), model.accept_threshold, model_path)
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
    res["target_reached_on_calibration"] = model.target_reached
    res.update(extended_analyses(eval_dir, run, pairs, test, model, cfg))
    figs = _figures(test, res["risk_coverage"], out_md.parent / "figures")
    (eval_dir / f"report_{run}.json").write_text(json.dumps(res, indent=1))
    out_md.write_text(render_markdown(res, figs))
    update_readme(res, Path("README.md"))
    return res


def _ci(outcomes, stat) -> list[float] | None:
    ci = bootstrap_ci(outcomes, stat)
    return [round(ci[0], 4), round(ci[1], 4)] if ci else None


def extended_analyses(eval_dir: Path, run: str, pairs, test, model, cfg) -> dict:
    """Intervals, medium-only figures, script breakdown, τ sensitivity, strata, baselines, unseen fonts."""
    not_severe = [o for o in test if o.level != "severe"]
    medium = [o for o in test if o.level == "medium"]
    out = {
        "ci_accuracy": _ci(not_severe, accuracy_stat), "ci_silent": _ci(not_severe, silent_stat),
        "medium_only": {"summary": summarise(medium), "ci_accuracy": _ci(medium, accuracy_stat),
                        "ci_silent": _ci(medium, silent_stat)},
        "by_script_medium": breakdown(medium, "script"),
    }
    out["ci_by_script_medium"] = {k: {"accuracy": _ci([o for o in medium if o.script == k], accuracy_stat),
                                      "silent": _ci([o for o in medium if o.script == k], silent_stat)}
                                  for k in out["by_script_medium"]}
    # τ without the grid floor (sensitivity; the protocol's grid started at 0.50)
    calib = [(i, p) for i, p in pairs if i["split"] == "calib"]
    tau_free, _ = oof_tau(calib, cfg, FREE_GRID)
    if tau_free is not None:
        test_pairs = [(i, p) for i, p in pairs if i["split"] == "test"]
        alt = [o for o in outcomes_for(test_pairs, model, PipelineConfig(accept_threshold=tau_free))
               if o.level != "severe"]
        out["tau_without_floor"] = {"tau": tau_free, "silent_error_rate": summarise(alt)["silent_error_rate"],
                                    "auto_accepted_share": summarise(alt)["handwritten"]["auto_accepted_share"]}
    # calibration by stratum
    out["calibration_by_stratum"] = {
        name: {k: v for k, v in (summarise(group).get("calibration") or {}).items() if k != "reliability"}
        for name, group in (("medium", medium), ("arabic_script_medium", [o for o in medium if o.script == "arabic_script"]),
                            ("clean", [o for o in test if o.level == "clean"]))}
    # trivial baselines (majority answer learnt on the calibration ground truth)
    out["baselines"] = baselines(eval_dir, test)
    # supplementary: unseen handwriting fonts
    unseen_dir = Path("artifacts/eval_unseen")
    if (unseen_dir / "dataset.json").exists():
        upairs = load_items(unseen_dir, run)
        if upairs:
            uo = [o for o in outcomes_for(upairs, model, cfg)]
            pages = {i["page_number"] for i, _ in upairs}
            same = [o for o in medium if o.item.startswith("spec_") and o.page in pages]
            out["unseen_fonts"] = {
                "n_pages": len(upairs), "dropped_fields": sum(len(i.get("dropped_fields", [])) for i, _ in upairs),
                "unseen": {"summary": summarise(uo)["handwritten"], "silent": summarise(uo)["silent_error_rate"],
                           "ci_accuracy": _ci(uo, accuracy_stat), "ci_silent": _ci(uo, silent_stat)},
                "original_fonts_same_pages": {"summary": summarise(same)["handwritten"],
                                              "silent": summarise(same)["silent_error_rate"],
                                              "ci_accuracy": _ci(same, accuracy_stat), "ci_silent": _ci(same, silent_stat)}}
    return out


def baselines(eval_dir: Path, test) -> dict:
    from collections import Counter

    from dayone.forms.layout import ALL_FIELDS

    items = json.loads((eval_dir / "dataset.json").read_text())
    majority: dict[str, Counter] = {}
    for it in items:
        if it["split"] != "calib":
            continue
        for fid, g in it["fields"].items():
            spec = ALL_FIELDS[fid]
            if spec.value_type.value in ("enum", "bool") and g["status"] == "KNOWN":
                majority.setdefault(fid, Counter())[str(g["value"])] += 1
    hw = [o for o in test if o.kind == "text" and o.level != "severe" and o.value_type in ("enum", "bool")
          and o.gt_status == "KNOWN"]
    gt = {it["id"]: it["fields"] for it in items}
    maj_ok = [majority.get(o.field_id, Counter()).most_common(1)[0][0] == str(gt[o.item][o.field_id]["value"])
              if majority.get(o.field_id) else False for o in hw]
    boxes = [o for o in test if o.kind == "checkbox" and o.level != "severe"]
    unticked = [gt[o.item][o.field_id]["value"] is False for o in boxes]
    return {"enum_bool_majority_accuracy": rate(maj_ok), "enum_bool_pipeline_accuracy": rate([o.correct for o in hw]),
            "n_enum_bool": len(hw), "checkbox_all_unticked_accuracy": rate(unticked),
            "checkbox_pipeline_accuracy": rate([o.correct for o in boxes])}


def _ci_txt(ci) -> str:
    return f" (95 % CI {100 * ci[0]:.1f}–{100 * ci[1]:.1f})" if ci else ""


def update_readme(r: dict, readme: Path) -> None:
    """Write the headline numbers between the RESULTS markers of the README (never typed by hand)."""
    if not readme.exists():
        return
    o = r["overall_excluding_severe"]
    hw = o["handwritten"]
    lv = r["by_level_specimen"]
    med = r["medium_only"]
    sc = r["by_script_medium"]
    rows = [
        "| Test split: 40 pages of 5 held-out patients (same 5 handwriting fonts as calibration) | |",
        "|---|---|",
        f"| **Extraction accuracy**, handwritten fields, before any review (n = {hw['n']}; unaltered renders + "
        f"simulated mild + medium photos) | **{_pct(hw['accuracy'])}**{_ci_txt(r.get('ci_accuracy'))} |",
        f"| … medium photos only (WhatsApp-like; FR + AR + EN) | {_pct(med['summary']['handwritten']['accuracy'])}"
        f"{_ci_txt(med.get('ci_accuracy'))} |",
        f"| … unaltered render / mild / medium (French specimen) | {_pct(lv.get('clean', {}).get('accuracy'))} / "
        f"{_pct(lv.get('mild', {}).get('accuracy'))} / {_pct(lv.get('medium', {}).get('accuracy'))} |",
        f"| … medium photos: Arabic-script / Latin-script fields | {_pct(sc.get('arabic_script', {}).get('accuracy'))} / "
        f"{_pct(sc.get('latin', {}).get('accuracy'))} |",
    ]
    uf = r.get("unseen_fonts")
    if uf:
        rows.append(f"| … medium photos in 2 handwriting fonts never seen in development | "
                    f"{_pct(uf['unseen']['summary']['accuracy'])}{_ci_txt(uf['unseen']['ci_accuracy'])} (same pages "
                    f"in the original fonts: {_pct(uf['original_fonts_same_pages']['summary']['accuracy'])}) |")
    rows += [
        f"| Handwritten fields accepted without any question | {_pct(hw['auto_accepted_share'])} |",
        f"| **Wrong among the values accepted without a question** | **{_pct(o['silent_error_rate'])}**"
        f"{_ci_txt(r.get('ci_silent'))}; medium photos {_pct(med['summary']['silent_error_rate'])}; "
        f"Arabic-script fields {_pct(sc.get('arabic_script', {}).get('silent_error_rate'))} |",
        f"| Tick boxes / blank fields recognised | {_pct(o['checkbox']['accuracy'])} / "
        f"{o['blank']['n'] - o['blank']['errors']} of {o['blank']['n']} |",
        f"| Page type recognised | {_pct(r['page_classification_accuracy'])} (thresholds set on all pages, optimistic) |",
    ]
    tau_note = f"Acceptance threshold τ = {r['accept_threshold']:.2f}, chosen on the calibration patients 1-5 " \
        "(it is the floor of the search grid; "
    tf = r.get("tau_without_floor")
    tau_note += (f"without the floor the rule gives τ = {tf['tau']:.2f} and {_pct(tf['silent_error_rate'])} on test). "
                 if tf else "). ")
    block = "<!-- RESULTS:START -->\n" + "\n".join(rows) + "\n\n" + tau_note + \
        "The 2 % silent-error target is **not demonstrated** on test (its interval crosses 2 %). Full tables, " \
        "calibration and risk–coverage: [docs/RESULTS.md](docs/RESULTS.md).\n<!-- RESULTS:END -->"
    text = readme.read_text()
    a, b = text.find("<!-- RESULTS:START -->"), text.find("<!-- RESULTS:END -->")
    if a >= 0 and b > a:
        readme.write_text(text[:a] + block + text[b + len("<!-- RESULTS:END -->"):])


def render_markdown(r: dict, figs: list[str]) -> str:
    o = r["overall_excluding_severe"]
    hw = o["handwritten"]
    cal = o.get("calibration", {})
    med = r["medium_only"]
    tf = r.get("tau_without_floor")
    L = [
        "# Results",
        "",
        "> Generated by `make report` from the stored predictions — do not edit by hand.",
        f"> Test split: patients 6-10 ({r['n_items']} captures of 40 pages). Confidence model `{r['model']}`, "
        f"acceptance threshold τ = {r['accept_threshold']:.2f} chosen out-of-fold on the calibration patients 1-5.",
        "> Intervals: 95 % percentile bootstrap over pages (2,000 resamples, seed 0) — fields of a page are correlated.",
        "",
        "**Read this first.** The test patients are new *values* written in the *same five handwriting fonts* as the "
        "calibration patients (each patient = one font, paired across splits); see the unseen-font experiment below. "
        "`clean` captures are the organisers' renders, unaltered. Arabic and English pages are font-rendered.",
        "",
        "## Headline (clean + mild + medium captures, French / Arabic / English)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Handwritten fields evaluated | {hw['n']} (40 pages, 5 patients) |",
        f"| **Extraction accuracy** (value right, before review) | **{_pct(hw['accuracy'])}**{_ci_txt(r.get('ci_accuracy'))} |",
        f"| Accepted without a question | {_pct(hw['auto_accepted_share'])} |",
        f"| **Wrong among the values accepted without a question** (silent errors) | **{_pct(o['silent_error_rate'])}**"
        f"{_ci_txt(r.get('ci_silent'))} |",
        f"| Silent errors over every field (text, blanks, dashes, tick boxes) | {_pct(o['all_fields_silent_error_rate'])} |",
        f"| Fields the midwife is asked about (all fields) | {_pct(o['all_fields_review_share'])} |",
        f"| Handwritten values wrongly read as blank | {_pct(hw['missed_as_blank_share'])} |",
        f"| Blank fields recognised as blank | {o['blank']['n'] - o['blank']['errors']} of {o['blank']['n']} |",
        f"| Dashes recognised as not applicable | {_pct(o['dash']['accuracy'])} (n={o['dash']['n']}) |",
        f"| Tick boxes | {_pct(o['checkbox']['accuracy'])} (n={o['checkbox']['n']}) |",
        f"| Page type classification | {_pct(r['page_classification_accuracy'])} (thresholds set on all 80 pages: optimistic) |",
        f"| Confidence calibration, OCR fields: ECE / Brier / AUROC | {cal.get('ece', float('nan')):.3f} / "
        f"{cal.get('brier', float('nan')):.3f} / {cal.get('auroc') or float('nan'):.3f} |",
        "",
        "**Medium photos only** (the WhatsApp-like condition, FR + AR + EN): accuracy "
        f"{_pct(med['summary']['handwritten']['accuracy'])}{_ci_txt(med.get('ci_accuracy'))}, silent errors "
        f"{_pct(med['summary']['silent_error_rate'])}{_ci_txt(med.get('ci_silent'))}.",
        "",
        "**Threshold.** The pre-registered rule takes the smallest τ meeting ≤ 2 % silent errors out-of-fold, but the "
        "search grid started at 0.50 (an undeclared floor, see EVALUATION.md §7). "
        + (f"Without the floor the rule gives τ = {tf['tau']:.2f}: {_pct(tf['silent_error_rate'])} silent errors and "
           f"{_pct(tf['auto_accepted_share'])} accepted on test. " if tf else "")
        + "Either way the 2 % target is **not demonstrated** on the test patients.",
        "",
        "## By capture level (specimen pages, French)",
        "",
        "| Level | Handwritten n | Accuracy | Accepted w/o question | Silent errors | All fields | Retake requested |",
        "|---|---|---|---|---|---|---|",
    ]
    names = {"clean": "clean (unaltered render)", "mild": "mild", "medium": "medium", "severe": "severe"}
    for lvl in ("clean", "mild", "medium", "severe"):
        b = r["by_level_specimen"].get(lvl)
        if b:
            L.append(f"| {names[lvl]} | {b['n_handwritten']} | {_pct(b['accuracy'])} | {_pct(b['auto_accepted_share'])} | "
                     f"{_pct(b['silent_error_rate'])} | {_pct(b['all_fields_accuracy'])} | "
                     f"{_pct(r['retake_requested_share_by_level'].get(lvl))} |")
    L += ["", "`severe` captures are sent back by the on-device quality check; their row shows what would happen if "
          "the midwife forced them through (most values wrong, many accepted: the quality gate is the protection).", "",
          "## By script (medium photos)", "",
          "What is actually written: Arabic letters, Eastern-Arabic digits only, or Latin script (French/English "
          "words and Western digits, including on 'Arabic' pages).", "",
          "| Script | n | Accuracy | Accepted w/o question | Silent errors |", "|---|---|---|---|---|"]
    for k, b in r["by_script_medium"].items():
        ci = r["ci_by_script_medium"].get(k, {})
        L.append(f"| {k} | {b['n_handwritten']} | {_pct(b['accuracy'])}{_ci_txt(ci.get('accuracy'))} | "
                 f"{_pct(b['auto_accepted_share'])} | {_pct(b['silent_error_rate'])}{_ci_txt(ci.get('silent'))} |")
    L += ["", "English fields are a small closed vocabulary (None, Normal, Negative, …) plus numbers, generated from "
          "the same vocabularies the parser knows: their score says little about English handwriting.", "",
          "## Language of the page mode (medium photos, for reference)", "",
          "| Mode | n | Accuracy | Accepted w/o question | Silent errors |", "|---|---|---|---|---|"]
    for k, b in r["by_language"].items():
        L.append(f"| {k} | {b['n_handwritten']} | {_pct(b['accuracy'])} | {_pct(b['auto_accepted_share'])} | "
                 f"{_pct(b['silent_error_rate'])} |")
    uf = r.get("unseen_fonts")
    L += ["", "## Unseen handwriting (supplementary, pre-registered in EVALUATION.md §8)", ""]
    if uf:
        u, s0 = uf["unseen"], uf["original_fonts_same_pages"]
        L += ["| Same 40 test pages, medium photos | Accuracy | Silent errors |", "|---|---|---|",
              f"| original fonts (seen in development) | {_pct(s0['summary']['accuracy'])}{_ci_txt(s0['ci_accuracy'])} | "
              f"{_pct(s0['silent'])}{_ci_txt(s0['ci_silent'])} |",
              f"| Indie Flower / Homemade Apple (never seen) | {_pct(u['summary']['accuracy'])}{_ci_txt(u['ci_accuracy'])} | "
              f"{_pct(u['silent'])}{_ci_txt(u['ci_silent'])} |", "",
              f"{uf['dropped_fields']} field(s) did not fit their box in the new fonts and are excluded."]
    else:
        L.append("Not run yet (`python -m dayone.evaluation.unseen_fonts build && … run`).")
    L += ["", "## By page type (clean + mild + medium)", "",
          "| Page | n | Accuracy | Accepted w/o question | Silent errors |", "|---|---|---|---|---|"]
    for k, b in r["by_page_type"].items():
        L.append(f"| {k} | {b['n_handwritten']} | {_pct(b['accuracy'])} | {_pct(b['auto_accepted_share'])} | "
                 f"{_pct(b['silent_error_rate'])} |")
    L += ["", "## By value type (clean + mild + medium)", "",
          "| Type | n | Accuracy | Accepted w/o question | Silent errors |", "|---|---|---|---|---|"]
    for k, b in r["by_value_type"].items():
        if b["n_handwritten"]:
            L.append(f"| {k} | {b['n_handwritten']} | {_pct(b['accuracy'])} | {_pct(b['auto_accepted_share'])} | "
                     f"{_pct(b['silent_error_rate'])} |")
    L += ["", "With 40 pages, differences of a few points between page or value types are within the noise.", "",
          "## Trivial baselines", ""]
    bl = r.get("baselines", {})
    if bl:
        L += ["| Field family | Baseline | Pipeline |", "|---|---|---|",
              f"| Closed vocabularies and yes/no (n={bl['n_enum_bool']}) | most frequent answer per field (learnt on "
              f"calibration): {_pct(bl['enum_bool_majority_accuracy'])} | {_pct(bl['enum_bool_pipeline_accuracy'])} |",
              f"| Tick boxes | everything unticked: {_pct(bl['checkbox_all_unticked_accuracy'])} | "
              f"{_pct(bl['checkbox_pipeline_accuracy'])} |",
              "| Blank fields | \"everything blank\" also scores 100 % on blanks — read it with \"handwritten values "
              "read as blank\" above | |"]
    L += ["", "## Calibration", "", "All OCR fields (clean + mild + medium):", "",
          "| Confidence bin | n | Mean confidence | Observed accuracy |", "|---|---|---|---|"]
    for row in cal.get("reliability", []):
        L.append(f"| {row['bin']} | {row['n']} | {row['mean_confidence']:.2f} | {row['accuracy']:.2f} |")
    L += ["", "By stratum (the overall ECE is dominated by the 0.9-1.0 bin of clean and mild captures):", "",
          "| Stratum | n | ECE | AUROC |", "|---|---|---|---|"]
    for k, v in r.get("calibration_by_stratum", {}).items():
        if v:
            L.append(f"| {k} | {v.get('n')} | {v.get('ece', float('nan')):.3f} | {v.get('auroc') or float('nan'):.3f} |")
    L += ["", "## Risk–coverage", "",
          "Confidence threshold alone on OCR fields (the deployed rule also forces a review on repaired values and "
          "rule flags, so it does not lie exactly on this curve).", "",
          "| τ | Accepted | Error among accepted |", "|---|---|---|"]
    for row in r["risk_coverage"][::3]:
        L.append(f"| {row['threshold']:.2f} | {_pct(row['coverage'])} | {_pct(row['silent_error_rate'])} |")
    L += ["", f"Accuracy on captures accepted by the quality check: {_pct(r['accuracy_by_quality_gate'].get('accepted'))}; "
          f"on captures it rejected: {_pct(r['accuracy_by_quality_gate'].get('rejected'))}.", ""]
    for f in figs:
        L.append(f"![{f}](figures/{f})")
    return "\n".join(L) + "\n"


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["calibrate", "report"])
    ap.add_argument("--eval-dir", type=Path, default=Path("artifacts/eval"))
    ap.add_argument("--run", default="final")
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
