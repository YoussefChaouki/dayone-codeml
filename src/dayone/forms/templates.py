"""Blank template images, one per page type, rendered from the specimen PDF.

A template is the reference page with the handwriting removed (text redacted at the
PDF level, blue tick marks painted over). Templates drive photo registration, page-type
classification and "is there any ink in this field?" decisions.
"""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from dayone.evaluation.groundtruth import HAND_FONTS
from dayone.forms.layout import PAGE_FIELDS, PAGE_ORDER
from dayone.schema import FieldKind, PageType

log = logging.getLogger(__name__)

DPI = 200
SCALE = DPI / 72.0  # pixels per PDF point
PAGE_PT = (595.28, 841.89)
TEMPLATE_DIR = Path("artifacts/templates")


def pt_to_px(rect: tuple[float, float, float, float], pad: float = 0.0) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    return (int((x0 - pad) * SCALE), int((y0 - pad) * SCALE), int(round((x1 + pad) * SCALE)),
            int(round((y1 + pad) * SCALE)))


def render_blank(page: pymupdf.Page, page_type: PageType) -> np.ndarray:
    """Render ``page`` without its handwriting, as BGR at ``DPI``."""
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["font"].split("+")[-1].startswith(HAND_FONTS):
                    page.add_redact_annot(pymupdf.Rect(span["bbox"]), fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    pix = page.get_pixmap(dpi=DPI)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    # Paint blue ink (tick marks) with the paper colour.
    paper = np.median(img.reshape(-1, 3), axis=0)
    b, g, r = (img[:, :, k].astype(int) for k in range(3))
    blue = (b - r > 25) & (b - g > 15)
    blue = cv2.dilate(blue.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    img[blue] = paper.astype(np.uint8)
    # Painting the marks also erased parts of the ticked boxes' borders: redraw every box outline.
    for spec in PAGE_FIELDS[page_type]:
        if spec.kind == FieldKind.CHECKBOX:
            x0, y0, x1, y1 = pt_to_px(spec.region)
            cv2.rectangle(img, (x0, y0), (x1, y1), (26, 20, 31), 2)
    return img


def ink_mask(gray: np.ndarray) -> np.ndarray:
    """Dark-ink mask robust to uneven lighting: pixel notably darker than its neighbourhood."""
    bg = cv2.medianBlur(gray, 31)
    diff = bg.astype(np.int16) - gray.astype(np.int16)
    return (diff > 40).astype(np.uint8)


@dataclass
class Template:
    page_type: PageType
    image: np.ndarray  # BGR, DPI resolution
    gray: np.ndarray
    ink: np.ndarray  # printed-ink mask (dilated), to subtract from captures
    paper_bgr: np.ndarray


def build_templates(pdf_path: Path, out_dir: Path = TEMPLATE_DIR) -> None:
    doc = pymupdf.open(pdf_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {}
    for k, page_type in enumerate(PAGE_ORDER):
        img = render_blank(doc[k], page_type)
        cv2.imwrite(str(out_dir / f"{page_type.value}.png"), img)
        meta[page_type.value] = {"source_page": k + 1, "dpi": DPI, "size": img.shape[:2]}
        log.info("template %s <- page %d", page_type.value, k + 1)
    (out_dir / "templates.json").write_text(json.dumps(meta, indent=1))


@functools.cache
def load_templates(template_dir: Path = TEMPLATE_DIR) -> dict[PageType, Template]:
    if not (template_dir / "templates.json").exists():
        raise FileNotFoundError(f"templates missing in {template_dir}: run `make templates`")
    out = {}
    for page_type in PAGE_ORDER:
        img = cv2.imread(str(template_dir / f"{page_type.value}.png"))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        ink = cv2.dilate(ink_mask(gray), np.ones((5, 5), np.uint8))
        paper = np.median(img.reshape(-1, 3), axis=0)
        out[page_type] = Template(page_type, img, gray, ink, paper)
    return out


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path, default=Path("data/Paper Registry/dossiers_specimen_10_patientes.pdf"))
    ap.add_argument("--out", type=Path, default=TEMPLATE_DIR)
    args = ap.parse_args()
    build_templates(args.pdf, args.out)


if __name__ == "__main__":
    main()
