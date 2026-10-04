"""Supplementary experiment (docs/EVALUATION.md §8): handwriting never seen during development.

The 40 test-split pages are re-written in French with two fonts never used before, captured at
the ``medium`` level, and read with the frozen pipeline (same models, confidence model and τ).

    python -m dayone.evaluation.unseen_fonts build   # images + ground truth
    python -m dayone.evaluation.unseen_fonts run     # extraction (resumable)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import cv2
import pymupdf

from dayone.evaluation.dataset import TEST_PATIENTS
from dayone.evaluation.degrade import degrade
from dayone.evaluation.synth import render_page

log = logging.getLogger(__name__)
UNSEEN_FONTS = ["IndieFlower-Regular.ttf", "HomemadeApple-Regular.ttf"]
OUT = Path("artifacts/eval_unseen")


def build(gt_path: Path, pdf_path: Path, out: Path = OUT) -> list[dict]:
    pages = [p for p in json.loads(gt_path.read_text()) if p["patient"] in TEST_PATIENTS]
    doc = pymupdf.open(pdf_path)
    (out / "images").mkdir(parents=True, exist_ok=True)
    items = []
    for k, page in enumerate(pages):
        font = UNSEEN_FONTS[k % 2]
        seed = 9000 + page["page_number"]
        img, fields, langs = render_page(doc, page["page_number"] - 1, page, "fr", seed, latin_fonts=[font])
        photo, params = degrade(img, "medium", seed)
        item_id = f"unseen_p{page['page_number']:02d}_medium"
        cv2.imwrite(str(out / "images" / f"{item_id}.jpg"), photo, [cv2.IMWRITE_JPEG_QUALITY, 95])
        items.append(
            {
                "id": item_id,
                "kind": "unseen",
                "split": "test",
                "patient": page["patient"],
                "page_number": page["page_number"],
                "page_type": page["page_type"],
                "level": "medium",
                "language": "fr",
                "font": font,
                "image": f"images/{item_id}.jpg",
                "seed": seed,
                "fields": fields,
                "languages": {},
                "dropped_fields": langs.get("_dropped", []),
            }
        )
    (out / "dataset.json").write_text(json.dumps(items, ensure_ascii=False, indent=1))
    return items


def run(out: Path = OUT) -> None:
    from dayone.extraction.pipeline import Extractor, PipelineConfig

    items = json.loads((out / "dataset.json").read_text())
    runs = out / "runs" / "final"
    runs.mkdir(parents=True, exist_ok=True)
    extractor = Extractor(PipelineConfig())
    for k, item in enumerate(items):
        target = runs / f"{item['id']}.json"
        if target.exists():
            continue
        result = extractor.extract(cv2.imread(str(out / item["image"])))
        target.write_text(result.model_dump_json())
        log.info("[%d/%d] %s -> %s (%.1fs)", k + 1, len(items), item["id"], result.page_type, result.elapsed_s)


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["build", "run"])
    args = ap.parse_args()
    if args.command == "build":
        items = build(Path("artifacts/groundtruth/specimen.json"), Path("data/Paper Registry/dossiers_specimen_10_patientes.pdf"))
        print(len(items), "pages;", sum(len(i["dropped_fields"]) for i in items), "fields did not fit and were dropped")
    else:
        run()


if __name__ == "__main__":
    main()
