# Evaluation protocol

> Pre-registered on 2026-10-03, **before** any number of the final run was computed.
> The decision rules below are not changed after looking at test results.

## 1. Question

How accurately does the pipeline turn a phone photo of a registry page into typed fields,
and does its confidence tell the midwife *which* fields to check?

## 2. Data and ground truth

* **Source**: the organisers' specimen PDF (10 fictitious patients x 8 pages). Ground truth
  is extracted automatically from the PDF itself: handwritten values are text set in
  handwriting fonts, tick marks are vector strokes in a pen colour
  (`src/dayone/evaluation/groundtruth.py`). 6,010 fields, 2,032 handwritten values,
  129 dashes, 306 ticked boxes. Every handwritten string is assigned to a field of the
  layout, except the direct identifiers (name, ID number, phone, address, husband) and
  professions, which are deliberately not part of the schema.
* **Duplicates**: 44 of the 132 provided PNG files are byte-identical copies (Drive
  suffixes). They are removed by hash before anything else.
* **Captures**: the PNGs are clean renders, so field conditions are simulated
  (`degrade.py`, seeded): `clean`, `mild` (camera photo ≈ 2000-2400 px long side),
  `medium` (WhatsApp-compressed ≈ 1600 px, shadows, low light — the organisers' real
  photos are 900x1600), `severe` (≈ 1200 px, strong blur and darkness).
* **Multilingual**: the specimen contains no Arabic or English, so pages of 4 patients
  are re-written in Arabic, English and mixed FR/AR/EN with open handwriting fonts
  (`synth.py`) and captured at `medium` level.
* **Split by patient**: patients 1-5 = *calibration* split (confidence model, thresholds),
  patients 6-10 = *test* split. All headline numbers are on the test split.

## 3. Metrics (per field, on the test split)

* **Extraction accuracy** — on fields that contain a handwritten value: the predicted
  canonical value equals the ground truth (dates as ISO, numbers as numbers, closed
  vocabularies as canonical tokens in any language; free text case/accent-insensitive).
  Reported by capture level, language, page type and value type.
* **Empty / dash handling** — blank fields predicted NOT_PROVIDED (or NOT_APPLICABLE by
  a consistency rule); dashes predicted NOT_APPLICABLE.
* **Tick boxes** — accuracy of ticked / not ticked.
* **Uncertainty**:
  * *auto-acceptance coverage*: share of handwritten fields accepted without review (KNOWN);
  * *silent error rate*: share of KNOWN fields whose value is wrong (the agent was wrong
    **and** did not say so) — the number the jury criterion "never hides its doubts" maps to;
  * *calibration*: expected calibration error (10 bins), Brier score, AUROC of the
    confidence for separating right from wrong readings, reliability table;
  * *risk-coverage curve* as the acceptance threshold varies.
* **Capture quality gate** — share of captures sent back for a retake, per level, and
  accuracy on accepted vs rejected captures.
* **Page classification** — share of pages assigned the right page type.

## 4. Decision rules (fixed in advance)

1. The confidence model is fitted on the calibration split only.
2. The acceptance threshold τ is the **smallest** threshold whose silent error rate on
   the calibration split (levels clean/mild/medium, all languages) is ≤ **2 %**.
   It is then frozen and applied to the test split.
3. The test split is used once for the final report; no parameter is changed afterwards.
   If a bug is found after the report, it is fixed, the whole calibration → test
   sequence is re-run and the change is logged in section 6.
4. `severe` captures are expected to be rejected by the quality gate; their accuracy is
   reported for transparency but is not a target.

## 5. Reproduce

```bash
make prepare      # ground truth + templates
make dataset      # simulated captures (seeded)
make eval         # extraction on every capture (local models, resumable, ~1.5 h on an M4 Pro)
make calibrate    # fit the confidence model + threshold on the calibration split
make report       # metrics on the test split -> docs/RESULTS.md
```

## 6. Known deviations (declared before the final run)

* **Development thresholds saw every page.** The registration / page-type thresholds
  (`MIN_SIMILARITY`, `MIN_MARGIN`), the tick-box fill band and the capture-quality
  thresholds were set while looking at statistics over all 80 specimen pages (both splits).
  They are geometric/image thresholds, not fitted to values, but the test-split numbers
  for **page classification**, **tick boxes**, **blank detection** and **retake requests**
  are therefore optimistic. The confidence model and τ (the uncertainty numbers) were fitted
  on the calibration split only.
* **One layout.** All test pages share the specimen layout; nothing here measures
  generalisation to another booklet.
* **Synthetic Arabic/English.** Fonts, not real handwriting.

## 7. Change log

* 2026-10-03 — first run interrupted twice by an Ollama runner hang (glm-ocr); the run is
  resumable, items already predicted were kept. The second reader was made optional
  (timeout + circuit breaker) during the run: items predicted before/after differ only if
  the second reader failed, which is recorded per field (`second_missing`).
