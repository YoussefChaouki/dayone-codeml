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
    return "—" if v is None else f"{100 * v:.1f} %".replace(".", ",")


def _num(v: float, digits: int = 2) -> str:
    """French decimal comma for the generated (French) reports."""
    return f"{v:.{digits}f}".replace(".", ",")


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
        ax.plot([0, 1], [0, 1], color="#999", lw=1, ls="--", label="calibration parfaite")
        ax.plot([r["mean_confidence"] for r in rel], [r["accuracy"] for r in rel], marker="o", color="#2a6f97",
                label="pipeline")
        ax.set_xlabel("confiance")
        ax.set_ylabel("exactitude observée")
        ax.set_title("Fiabilité (test, champs OCR)")
        ax.legend(loc="lower right", frameon=False)
        fig.tight_layout()
        fig.savefig(out_dir / "reliability.png", dpi=150)
        plt.close(fig)
        files.append("reliability.png")
    if rc:
        fig, ax = plt.subplots(figsize=(4.8, 3.6))
        ax.plot([r["coverage"] for r in rc], [100 * r["silent_error_rate"] for r in rc], color="#2a6f97")
        ax.set_xlabel("part des champs manuscrits acceptés sans question")
        ax.set_ylabel("erreurs parmi les acceptés (%)")
        ax.set_title("Risque–couverture (test)")
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
    return f" (IC 95 % {_num(100 * ci[0], 1)}–{_num(100 * ci[1], 1)})" if ci else ""


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
        "| Test : 40 pages de 5 patientes tenues à l'écart (mêmes 5 polices manuscrites que la calibration) | |",
        "|---|---|",
        f"| **Exactitude de l'extraction**, champs manuscrits, avant toute révision (n = {hw['n']} ; rendus "
        f"d'origine + photos simulées légères et moyennes) | **{_pct(hw['accuracy'])}**{_ci_txt(r.get('ci_accuracy'))} |",
        f"| … photos moyennes seules (type WhatsApp ; FR + AR + EN) | {_pct(med['summary']['handwritten']['accuracy'])}"
        f"{_ci_txt(med.get('ci_accuracy'))} |",
        f"| … rendu d'origine / photo légère / photo moyenne (spécimen français) | "
        f"{_pct(lv.get('clean', {}).get('accuracy'))} / {_pct(lv.get('mild', {}).get('accuracy'))} / "
        f"{_pct(lv.get('medium', {}).get('accuracy'))} |",
        f"| … photos moyennes : champs en lettres arabes / en écriture latine | "
        f"{_pct(sc.get('arabic_script', {}).get('accuracy'))} / {_pct(sc.get('latin', {}).get('accuracy'))} |",
    ]
    uf = r.get("unseen_fonts")
    if uf:
        rows.append(f"| … photos moyennes avec 2 polices manuscrites jamais vues pendant le développement | "
                    f"{_pct(uf['unseen']['summary']['accuracy'])}{_ci_txt(uf['unseen']['ci_accuracy'])} (mêmes pages "
                    f"dans les polices d'origine : {_pct(uf['original_fonts_same_pages']['summary']['accuracy'])}) |")
    rows += [
        f"| Champs manuscrits acceptés sans aucune question | {_pct(hw['auto_accepted_share'])} |",
        f"| **Valeurs fausses parmi celles acceptées sans question** | **{_pct(o['silent_error_rate'])}**"
        f"{_ci_txt(r.get('ci_silent'))} ; photos moyennes {_pct(med['summary']['silent_error_rate'])} ; "
        f"champs en lettres arabes {_pct(sc.get('arabic_script', {}).get('silent_error_rate'))} |",
        f"| Cases à cocher / champs vides reconnus | {_pct(o['checkbox']['accuracy'])} / "
        f"{o['blank']['n'] - o['blank']['errors']} sur {o['blank']['n']} |",
        f"| Type de page reconnu | {_pct(r['page_classification_accuracy'])} (seuils réglés sur toutes les pages, "
        "optimiste) |",
    ]
    tau_note = f"Seuil d'acceptation τ = {_num(r['accept_threshold'])}, choisi sur les patientes de calibration 1-5 " \
        "(c'est le plancher de la grille de recherche ; "
    tf = r.get("tau_without_floor")
    tau_note += (f"sans ce plancher, la règle donne τ = {_num(tf['tau'])} et {_pct(tf['silent_error_rate'])} "
                 "en test). " if tf else "). ")
    block = "<!-- RESULTS:START -->\n" + "\n".join(rows) + "\n\n" + tau_note + \
        "L'objectif de 2 % d'erreurs silencieuses n'est **pas démontré** en test (son intervalle franchit 2 %). " \
        "Tableaux complets, calibration et risque–couverture : [docs/RESULTS.md](docs/RESULTS.md).\n<!-- RESULTS:END -->"
    text = readme.read_text()
    a, b = text.find("<!-- RESULTS:START -->"), text.find("<!-- RESULTS:END -->")
    if a >= 0 and b > a:
        readme.write_text(text[:a] + block + text[b + len("<!-- RESULTS:END -->"):])


LEVEL_NAMES = {"clean": "rendu d'origine (non altéré)", "mild": "photo légère", "medium": "photo moyenne",
               "severe": "photo très dégradée"}
SCRIPT_NAMES = {"arabic_script": "lettres arabes", "eastern_digits": "chiffres arabes orientaux seuls",
                "latin": "écriture latine"}
STRATUM_NAMES = {"medium": "photos moyennes", "arabic_script_medium": "lettres arabes, photos moyennes",
                 "clean": "rendus d'origine"}
ROW_HEADER = "| n | Exactitude | Acceptés sans question | Erreurs silencieuses |"
PAGE_NAMES = {"cover": "Couverture / établissement", "history": "Identification et antécédents",
              "pregnancy": "Grossesse actuelle", "delivery": "Accouchement",
              "pp_early_mother": "Post-partum précoce — mère", "pp_early_newborn": "Post-partum précoce — nouveau-né",
              "pp_late_mother": "Post-partum tardif — mère", "pp_late_newborn": "Post-partum tardif — nouveau-né"}
VALUE_TYPE_NAMES = {"bool": "oui / non", "bp": "tension", "code": "code patiente", "date": "date",
                    "enum": "vocabulaire fermé", "float": "nombre décimal", "gest_age": "âge gestationnel",
                    "int": "nombre entier", "text": "texte libre"}
LANGUAGE_NAMES = {"ar": "arabe", "en": "anglais", "fr": "français"}


def render_markdown(r: dict, figs: list[str]) -> str:
    o = r["overall_excluding_severe"]
    hw = o["handwritten"]
    cal = o.get("calibration", {})
    med = r["medium_only"]
    tf = r.get("tau_without_floor")
    nan = float("nan")
    L = [
        "# Résultats",
        "",
        "> Généré par `make report` à partir des prédictions enregistrées — ne pas modifier à la main.",
        f"> Jeu de test : patientes 6-10 ({r['n_items']} captures de 40 pages). Modèle de confiance `{r['model']}`, "
        f"seuil d'acceptation τ = {_num(r['accept_threshold'])} choisi hors échantillon sur les patientes de "
        "calibration 1-5.",
        "> Intervalles : bootstrap percentile à 95 % sur les pages (2 000 tirages, graine 0) — les champs d'une même "
        "page sont corrélés.",
        "",
        "**À lire d'abord.** Les patientes de test sont de nouvelles *valeurs* écrites dans les *mêmes cinq polices "
        "manuscrites* que les patientes de calibration (une patiente = une police, appariées entre les deux jeux) ; "
        "voir l'expérience « écriture jamais vue » plus bas. Les captures `clean` sont les rendus des organisateurs, "
        "non altérés. Les pages arabes et anglaises sont rendues avec des polices.",
        "",
        "## Synthèse (rendus d'origine + photos légères + photos moyennes ; français / arabe / anglais)",
        "",
        "| Mesure | Valeur |",
        "|---|---|",
        f"| Champs manuscrits évalués | {hw['n']} (40 pages, 5 patientes) |",
        f"| **Exactitude de l'extraction** (bonne valeur, avant révision) | **{_pct(hw['accuracy'])}**"
        f"{_ci_txt(r.get('ci_accuracy'))} |",
        f"| Acceptés sans question | {_pct(hw['auto_accepted_share'])} |",
        f"| **Valeurs fausses parmi celles acceptées sans question** (erreurs silencieuses) | "
        f"**{_pct(o['silent_error_rate'])}**{_ci_txt(r.get('ci_silent'))} |",
        f"| Erreurs silencieuses sur tous les champs (texte, vides, tirets, cases) | "
        f"{_pct(o['all_fields_silent_error_rate'])} |",
        f"| Champs sur lesquels la sage-femme est interrogée (tous champs) | {_pct(o['all_fields_review_share'])} |",
        f"| Valeurs manuscrites lues à tort comme vides | {_pct(hw['missed_as_blank_share'])} |",
        f"| Champs vides reconnus comme vides | {o['blank']['n'] - o['blank']['errors']} sur {o['blank']['n']} |",
        f"| Tirets reconnus comme « non applicable » | {_pct(o['dash']['accuracy'])} (n={o['dash']['n']}) |",
        f"| Cases à cocher | {_pct(o['checkbox']['accuracy'])} (n={o['checkbox']['n']}) |",
        f"| Reconnaissance du type de page | {_pct(r['page_classification_accuracy'])} (seuils réglés sur les "
        "80 pages : optimiste) |",
        f"| Calibration de la confiance, champs OCR : ECE / Brier / AUROC | {_num(cal.get('ece', nan), 3)} / "
        f"{_num(cal.get('brier', nan), 3)} / {_num(cal.get('auroc') or nan, 3)} |",
        "",
        "**Photos moyennes seules** (la condition proche de WhatsApp, FR + AR + EN) : exactitude "
        f"{_pct(med['summary']['handwritten']['accuracy'])}{_ci_txt(med.get('ci_accuracy'))}, erreurs silencieuses "
        f"{_pct(med['summary']['silent_error_rate'])}{_ci_txt(med.get('ci_silent'))}.",
        "",
        "**Seuil.** La règle pré-enregistrée prend le plus petit τ qui respecte ≤ 2 % d'erreurs silencieuses hors "
        "échantillon, mais la grille de recherche commençait à 0,50 (un plancher non déclaré, voir EVALUATION.md §7). "
        + (f"Sans ce plancher, la règle donne τ = {_num(tf['tau'])} : {_pct(tf['silent_error_rate'])} d'erreurs "
           f"silencieuses et {_pct(tf['auto_accepted_share'])} acceptés en test. " if tf else "")
        + "Dans les deux cas, l'objectif de 2 % n'est **pas démontré** sur les patientes de test.",
        "",
        "## Par niveau de capture (pages du spécimen, français)",
        "",
        "| Niveau | n manuscrits | Exactitude | Acceptés sans question | Erreurs silencieuses | Tous champs | "
        "Reprise demandée |",
        "|---|---|---|---|---|---|---|",
    ]
    for lvl in ("clean", "mild", "medium", "severe"):
        b = r["by_level_specimen"].get(lvl)
        if b:
            L.append(f"| {LEVEL_NAMES[lvl]} | {b['n_handwritten']} | {_pct(b['accuracy'])} | "
                     f"{_pct(b['auto_accepted_share'])} | {_pct(b['silent_error_rate'])} | "
                     f"{_pct(b['all_fields_accuracy'])} | {_pct(r['retake_requested_share_by_level'].get(lvl))} |")
    L += ["", "Les photos très dégradées (`severe`) sont renvoyées par le contrôle qualité du téléphone ; leur ligne "
          "montre ce qui arriverait si la sage-femme les forçait (la plupart des valeurs fausses, beaucoup acceptées : "
          "c'est le contrôle qualité qui protège).", "",
          "## Par écriture (photos moyennes)", "",
          "Ce qui est réellement écrit : lettres arabes, chiffres arabes orientaux seuls, ou écriture latine (mots "
          "français/anglais et chiffres occidentaux, y compris sur les pages « arabes »).", "",
          "| Écriture " + ROW_HEADER, "|---|---|---|---|---|"]
    for k, b in r["by_script_medium"].items():
        ci = r["ci_by_script_medium"].get(k, {})
        L.append(f"| {SCRIPT_NAMES.get(k, k)} | {b['n_handwritten']} | {_pct(b['accuracy'])}{_ci_txt(ci.get('accuracy'))} | "
                 f"{_pct(b['auto_accepted_share'])} | {_pct(b['silent_error_rate'])}{_ci_txt(ci.get('silent'))} |")
    L += ["", "Les champs anglais forment un petit vocabulaire fermé (None, Normal, Negative…) plus des nombres, générés "
          "à partir des vocabulaires que connaît l'analyseur : leur score dit peu de choses sur l'écriture anglaise.",
          "", "## Par langue de la page (photos moyennes, pour information)", "",
          "| Langue " + ROW_HEADER, "|---|---|---|---|---|"]
    for k, b in r["by_language"].items():
        L.append(f"| {LANGUAGE_NAMES.get(k, k)} | {b['n_handwritten']} | {_pct(b['accuracy'])} | "
                 f"{_pct(b['auto_accepted_share'])} | "
                 f"{_pct(b['silent_error_rate'])} |")
    uf = r.get("unseen_fonts")
    L += ["", "## Écriture jamais vue (complémentaire, pré-enregistrée dans EVALUATION.md §8)", ""]
    if uf:
        u, s0 = uf["unseen"], uf["original_fonts_same_pages"]
        L += ["| Mêmes 40 pages de test, photos moyennes | Exactitude | Erreurs silencieuses |", "|---|---|---|",
              f"| polices d'origine (vues pendant le développement) | {_pct(s0['summary']['accuracy'])}"
              f"{_ci_txt(s0['ci_accuracy'])} | {_pct(s0['silent'])}{_ci_txt(s0['ci_silent'])} |",
              f"| Indie Flower / Homemade Apple (jamais vues) | {_pct(u['summary']['accuracy'])}"
              f"{_ci_txt(u['ci_accuracy'])} | {_pct(u['silent'])}{_ci_txt(u['ci_silent'])} |", "",
              f"{uf['dropped_fields']} champ(s) ne tenai(en)t pas dans leur case avec les nouvelles polices et sont "
              "exclus."]
    else:
        L.append("Pas encore lancée (`make unseen`).")
    L += ["", "## Par type de page (rendus d'origine + photos légères + photos moyennes)", "",
          "| Page " + ROW_HEADER, "|---|---|---|---|---|"]
    for k, b in r["by_page_type"].items():
        L.append(f"| {PAGE_NAMES.get(k, k)} | {b['n_handwritten']} | {_pct(b['accuracy'])} | "
                 f"{_pct(b['auto_accepted_share'])} | "
                 f"{_pct(b['silent_error_rate'])} |")
    L += ["", "## Par type de valeur (rendus d'origine + photos légères + photos moyennes)", "",
          "| Type " + ROW_HEADER, "|---|---|---|---|---|"]
    for k, b in r["by_value_type"].items():
        if b["n_handwritten"]:
            L.append(f"| {VALUE_TYPE_NAMES.get(k, k)} | {b['n_handwritten']} | {_pct(b['accuracy'])} | "
                     f"{_pct(b['auto_accepted_share'])} | "
                     f"{_pct(b['silent_error_rate'])} |")
    L += ["", "Avec 40 pages, des écarts de quelques points entre types de page ou de valeur restent dans le bruit.",
          "", "## Références triviales", ""]
    bl = r.get("baselines", {})
    if bl:
        L += ["| Famille de champs | Référence | Pipeline |", "|---|---|---|",
              f"| Vocabulaires fermés et oui/non (n={bl['n_enum_bool']}) | réponse la plus fréquente par champ "
              f"(apprise sur la calibration) : {_pct(bl['enum_bool_majority_accuracy'])} | "
              f"{_pct(bl['enum_bool_pipeline_accuracy'])} |",
              f"| Cases à cocher | tout décoché : {_pct(bl['checkbox_all_unticked_accuracy'])} | "
              f"{_pct(bl['checkbox_pipeline_accuracy'])} |",
              "| Champs vides | « tout vide » fait aussi 100 % sur les vides — à lire avec « valeurs manuscrites lues "
              "à tort comme vides » plus haut | |"]
    L += ["", "## Calibration", "", "Tous les champs OCR (rendus d'origine + photos légères + photos moyennes) :", "",
          "| Tranche de confiance | n | Confiance moyenne | Exactitude observée |", "|---|---|---|---|"]
    for row in cal.get("reliability", []):
        L.append(f"| {row['bin'].replace('.', ',')} | {row['n']} | {_num(row['mean_confidence'])} | "
                 f"{_num(row['accuracy'])} |")
    L += ["", "Par strate (l'ECE globale est dominée par la tranche 0,9-1,0 des rendus d'origine et des photos "
          "légères) :", "", "| Strate | n | ECE | AUROC |", "|---|---|---|---|"]
    for k, v in r.get("calibration_by_stratum", {}).items():
        if v:
            L.append(f"| {STRATUM_NAMES.get(k, k)} | {v.get('n')} | {_num(v.get('ece', nan), 3)} | "
                     f"{_num(v.get('auroc') or nan, 3)} |")
    L += ["", "## Risque–couverture", "",
          "Seuil de confiance seul, sur les champs OCR (la règle déployée force aussi une révision sur les valeurs "
          "réparées et les alertes de cohérence : elle n'est donc pas exactement sur cette courbe).", "",
          "| τ | Acceptés | Erreurs parmi les acceptés |", "|---|---|---|"]
    for row in r["risk_coverage"][::3]:
        L.append(f"| {_num(row['threshold'])} | {_pct(row['coverage'])} | {_pct(row['silent_error_rate'])} |")
    L += ["", "Exactitude sur les captures acceptées par le contrôle qualité : "
          f"{_pct(r['accuracy_by_quality_gate'].get('accepted'))} ; sur celles qu'il a refusées : "
          f"{_pct(r['accuracy_by_quality_gate'].get('rejected'))}.", ""]
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
