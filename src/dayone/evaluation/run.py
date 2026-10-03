"""Run the extraction pipeline on the evaluation set and store one JSON per item.

Resumable: items whose prediction already exists are skipped, and OCR readings are cached
by crop hash, so a re-run after a code change only pays for what changed.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

import cv2

from dayone.extraction.ocr import OcrUnavailable
from dayone.extraction.pipeline import Extractor, PipelineConfig

log = logging.getLogger(__name__)


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-dir", type=Path, default=Path("artifacts/eval"))
    ap.add_argument("--run", default="main")
    ap.add_argument("--split", choices=["calib", "test", "all"], default="all")
    ap.add_argument("--levels", default="clean,mild,medium,severe")
    ap.add_argument("--kinds", default="specimen,synth")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    items = json.loads((args.eval_dir / "dataset.json").read_text())
    levels, kinds = set(args.levels.split(",")), set(args.kinds.split(","))
    items = [i for i in items if (args.split == "all" or i["split"] == args.split)
             and i["level"] in levels and i["kind"] in kinds]
    # interleave so that partial runs already cover every level and page type
    items.sort(key=lambda i: (i["page_number"] % 8, i["patient"], i["level"]))
    if args.limit:
        items = items[: args.limit]
    out = args.eval_dir / "runs" / args.run
    out.mkdir(parents=True, exist_ok=True)
    extractor = Extractor(PipelineConfig())
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "src"], capture_output=True, text=True).stdout.strip())
    meta = out / "_meta.json"
    runs = json.loads(meta.read_text()) if meta.exists() else []
    runs.append({"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "git_commit": commit, "src_dirty": dirty,
                 "primary_model": extractor.primary.model,
                 "second_model": extractor.second.model if extractor.second else None})
    meta.write_text(json.dumps(runs, indent=1))
    if dirty:
        log.warning("src/ has uncommitted changes: this run is not reproducible from a commit")
    t0 = time.time()
    done, failed = 0, []
    for k, item in enumerate(items):
        target = out / f"{item['id']}.json"
        if target.exists():
            continue
        img = cv2.imread(str(args.eval_dir / item["image"]))
        try:
            result = extractor.extract(img)
        except OcrUnavailable as e:  # model server overloaded: skip, the next run resumes this item
            log.error("%s skipped: %s", item["id"], e)
            failed.append(item["id"])
            continue
        target.write_text(result.model_dump_json())
        done += 1
        log.info("[%d/%d] %s -> %s (%d fields, %.1fs, quality ok=%s)", k + 1, len(items), item["id"],
                 result.page_type, len(result.fields), result.elapsed_s, result.quality.get("ok"))
    log.info("finished: %d new predictions in %.0fs", done, time.time() - t0)
    if failed:
        raise SystemExit(f"{len(failed)} item(s) skipped because the OCR server was unreachable: re-run to resume")


if __name__ == "__main__":
    main()
