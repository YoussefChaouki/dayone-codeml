"""Build the evaluation set: simulated field photos with exact per-field ground truth.

Split **by patient** (never by page): patients 1-5 are the *calibration* split (used to fit
the confidence model and choose thresholds), patients 6-10 the *test* split, touched only
for the final report. Exact duplicates among the organisers' PNGs are removed upstream
(groundtruth.py hashes the files).

Items:
* ``specimen``: each of the 80 specimen pages x {clean, mild, medium, severe} captures;
* ``synth``: Arabic / English / mixed rewrites of the pages of patients 1, 2 (calibration)
  and 6, 7 (test), as ``medium`` captures.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pymupdf

from dayone.evaluation.degrade import LEVELS, degrade
from dayone.evaluation.synth import render_page

CALIB_PATIENTS = {1, 2, 3, 4, 5}
TEST_PATIENTS = {6, 7, 8, 9, 10}
SYNTH_PATIENTS = {1, 2, 6, 7}
SYNTH_MODES = ("ar", "en", "mixed")
SYNTH_LEVEL = "medium"


def split_of(patient: int) -> str:
    return "calib" if patient in CALIB_PATIENTS else "test"


def build(gt_path: Path, png_dir: Path, pdf_path: Path, out: Path) -> list[dict]:
    pages = json.loads(gt_path.read_text())
    img_dir = out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for page in pages:
        src = cv2.imread(str(png_dir / page["images"][0]))
        for k, level in enumerate(LEVELS):
            item_id = f"spec_p{page['page_number']:02d}_{level}"
            seed = 1000 * page["page_number"] + k
            photo, params = degrade(src, level, seed)
            cv2.imwrite(str(img_dir / f"{item_id}.jpg"), photo, [cv2.IMWRITE_JPEG_QUALITY, 95])
            items.append({"id": item_id, "kind": "specimen", "split": split_of(page["patient"]),
                          "patient": page["patient"], "page_number": page["page_number"],
                          "page_type": page["page_type"], "level": level, "language": "fr",
                          "image": f"images/{item_id}.jpg", "seed": seed,
                          "degradation": {k2: v for k2, v in params.items() if k2 != "page_to_photo"},
                          "fields": page["fields"], "languages": {}})
    doc = pymupdf.open(pdf_path)
    for page in pages:
        if page["patient"] not in SYNTH_PATIENTS:
            continue
        for m, mode in enumerate(SYNTH_MODES):
            seed = 5000 + 10 * page["page_number"] + m
            img, fields, langs = render_page(doc, page["page_number"] - 1, page, mode, seed)
            photo, params = degrade(img, SYNTH_LEVEL, seed)
            item_id = f"synth_p{page['page_number']:02d}_{mode}_{SYNTH_LEVEL}"
            cv2.imwrite(str(img_dir / f"{item_id}.jpg"), photo, [cv2.IMWRITE_JPEG_QUALITY, 95])
            items.append({"id": item_id, "kind": "synth", "split": split_of(page["patient"]),
                          "patient": page["patient"], "page_number": page["page_number"],
                          "page_type": page["page_type"], "level": SYNTH_LEVEL, "language": mode,
                          "image": f"images/{item_id}.jpg", "seed": seed,
                          "degradation": {k2: v for k2, v in params.items() if k2 != "page_to_photo"},
                          "fields": fields, "languages": langs})
    (out / "dataset.json").write_text(json.dumps(items, ensure_ascii=False, indent=1))
    return items


def main() -> None:
    import argparse
    from collections import Counter

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gt", type=Path, default=Path("artifacts/groundtruth/specimen.json"))
    ap.add_argument("--png-dir", type=Path, default=Path("data/Paper Registry"))
    ap.add_argument("--pdf", type=Path, default=Path("data/Paper Registry/dossiers_specimen_10_patientes.pdf"))
    ap.add_argument("--out", type=Path, default=Path("artifacts/eval"))
    args = ap.parse_args()
    items = build(args.gt, args.png_dir, args.pdf, args.out)
    print(len(items), "items", dict(Counter((i["split"], i["kind"], i["level"]) for i in items)))


if __name__ == "__main__":
    main()
