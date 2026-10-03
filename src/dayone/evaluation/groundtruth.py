"""Build per-field ground truth from the organisers' specimen PDF.

The PDF is generated: handwritten values are real text set in handwriting fonts
(Caveat, Gaegu, ...) while the form itself is Helvetica, and tick marks are blue
vector strokes. We therefore recover, for every page, which value sits in which field
region of the layout — no manual labelling.

Each specimen page is first aligned to the reference page of its type (the generator
jitters the whole page by 1–2 pt) using the printed labels as anchors.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf

from dayone.extraction.normalize import parse_value
from dayone.forms.layout import PAGE_FIELDS, PAGE_ORDER
from dayone.schema import FieldKind, FieldStatus, PageType

log = logging.getLogger(__name__)

HAND_FONTS = ("Caveat", "ShadowsIntoLight", "NanumPen", "Gaegu", "ReenieBeanie")
MISSING_GLYPH = "�"  # the generator's fonts lack some accented glyphs: they render as a gap
FORM_COLOR = (0.12, 0.08, 0.10)  # every printed line of the form; tick marks use another (pen) colour


@dataclass
class Char:
    c: str
    bbox: tuple[float, float, float, float]

    @property
    def center(self) -> tuple[float, float]:
        return (self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class PageContent:
    labels: list[tuple[str, tuple[float, float, float, float]]]
    hand: list[Char]
    marks: list[tuple[float, float, float, float]]


def read_page(page: pymupdf.Page) -> PageContent:
    labels, hand = [], []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["font"].split("+")[-1].startswith(HAND_FONTS):
                    for ch in span["chars"]:
                        c = MISSING_GLYPH if ch["c"] == "\x00" else ch["c"]
                        hand.append(Char(c, tuple(ch["bbox"])))
                else:
                    text = "".join(ch["c"] for ch in span["chars"]).strip()
                    if text and "fictive" not in text:
                        labels.append((text, tuple(span["bbox"])))
    marks = []
    for d in page.get_drawings():
        col = d.get("color")
        # Pens differ between patients (blue, dark blue, black): a mark is any stroke not drawn
        # in the form colour.
        if col and not np.allclose(col, FORM_COLOR, atol=0.01):
            marks.append(tuple(d["rect"]))
    return PageContent(labels, hand, marks)


def fit_alignment(src: PageContent, ref: PageContent) -> np.ndarray:
    """2x3 affine (least squares) mapping ``src`` coordinates to ``ref`` coordinates."""
    ref_index: dict[str, list] = {}
    for text, bb in ref.labels:
        ref_index.setdefault(text, []).append(bb)
    pts_src, pts_ref = [], []
    for text, bb in src.labels:
        cands = ref_index.get(text)
        if not cands:
            continue
        rb = min(cands, key=lambda r: abs(r[0] - bb[0]) + abs(r[1] - bb[1]))
        if abs(rb[0] - bb[0]) + abs(rb[1] - bb[1]) > 20:
            continue
        pts_src += [(bb[0], bb[1]), (bb[2], bb[3])]
        pts_ref += [(rb[0], rb[1]), (rb[2], rb[3])]
    a = np.hstack([np.array(pts_src), np.ones((len(pts_src), 1))])
    m, *_ = np.linalg.lstsq(a, np.array(pts_ref), rcond=None)
    return m.T  # 2x3


def _apply(m: np.ndarray, x: float, y: float) -> tuple[float, float]:
    return float(m[0, 0] * x + m[0, 1] * y + m[0, 2]), float(m[1, 0] * x + m[1, 1] * y + m[1, 2])


def _join(chars: list[Char]) -> str:
    """Join characters in reading order (lines top to bottom, then left to right)."""
    if not chars:
        return ""
    chars = sorted(chars, key=lambda c: c.center[1])
    lines: list[list[Char]] = [[chars[0]]]
    for ch in chars[1:]:
        if abs(ch.center[1] - np.mean([c.center[1] for c in lines[-1]])) < 6:
            lines[-1].append(ch)
        else:
            lines.append([ch])
    out = []
    for line in lines:
        line.sort(key=lambda c: c.center[0])
        out.append("".join(c.c for c in line))
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def _inside(pt: tuple[float, float], r: tuple[float, float, float, float], pad: float = 0.0) -> bool:
    return r[0] - pad <= pt[0] <= r[2] + pad and r[1] - pad <= pt[1] <= r[3] + pad


def page_ground_truth(content: PageContent, align: np.ndarray, page_type: PageType) -> tuple[dict, list[str]]:
    """Ground truth of one page and the list of handwritten strings that fell outside every field."""
    chars = [Char(c.c, (*_apply(align, c.bbox[0], c.bbox[1]), *_apply(align, c.bbox[2], c.bbox[3])))
             for c in content.hand]
    marks = [(*_apply(align, m[0], m[1]), *_apply(align, m[2], m[3])) for m in content.marks]
    used = [False] * len(chars)
    fields = {}
    for spec in PAGE_FIELDS[page_type]:
        if spec.kind == FieldKind.CHECKBOX:
            r = spec.region
            checked = any(_inside(((m[0] + m[2]) / 2, (m[1] + m[3]) / 2), r, pad=3) for m in marks)
            fields[spec.id] = {"status": FieldStatus.KNOWN.value, "value": checked, "raw": "X" if checked else ""}
            continue
        idx = [i for i, ch in enumerate(chars) if _inside(ch.center, spec.region)]
        for i in idx:
            used[i] = True
        raw = _join([chars[i] for i in idx])
        parsed = parse_value(spec, raw.replace(MISSING_GLYPH, ""))
        entry = {"status": parsed.status.value, "value": parsed.value, "raw": raw}
        if idx:  # where the value is written, in the page's own coordinates (used by synth.py)
            boxes = np.array([content.hand[i].bbox for i in idx])
            entry["bbox_page"] = [round(float(v), 1) for v in (*boxes[:, :2].min(axis=0), *boxes[:, 2:].max(axis=0))]
        fields[spec.id] = entry
    stray = _join([c for c, u in zip(chars, used, strict=True) if not u and c.c.strip()])
    return fields, ([stray] if stray else [])


def build(pdf_path: Path, png_dir: Path) -> list[dict]:
    """Ground truth for every page of the specimen PDF (8 pages per patient)."""
    doc = pymupdf.open(pdf_path)
    n_types = len(PAGE_ORDER)
    refs = {PAGE_ORDER[k]: read_page(doc[k]) for k in range(n_types)}
    # Several PNGs are byte-identical copies (Drive suffixes): keep one file per distinct content.
    pngs: dict[int, list[str]] = {}
    seen: set[str] = set()
    for p in sorted(png_dir.glob("dossiers_specimen_10_patientes-*.png")):
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        num = int(re.match(r"dossiers_specimen_10_patientes-(\d+)", p.stem).group(1))
        pngs.setdefault(num, []).append(p.name)
    pages = []
    for pno in range(doc.page_count):
        page_type = PAGE_ORDER[pno % n_types]
        content = read_page(doc[pno])
        align = fit_alignment(content, refs[page_type])
        fields, stray = page_ground_truth(content, align, page_type)
        if stray:
            log.warning("page %d (%s): handwriting outside any field: %s", pno + 1, page_type, stray)
        pages.append({
            "page_number": pno + 1,
            "patient": pno // n_types + 1,
            "page_type": page_type.value,
            "images": pngs.get(pno + 1, []),
            "unassigned_handwriting": stray,
            "fields": fields,
        })
    return pages


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path, default=Path("data/Paper Registry/dossiers_specimen_10_patientes.pdf"))
    ap.add_argument("--png-dir", type=Path, default=Path("data/Paper Registry"))
    ap.add_argument("--out", type=Path, default=Path("artifacts/groundtruth/specimen.json"))
    args = ap.parse_args()
    pages = build(args.pdf, args.png_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(pages, ensure_ascii=False, indent=1))
    n_fields = sum(len(p["fields"]) for p in pages)
    n_text = sum(1 for p in pages for f in p["fields"].values() if f["raw"] and f["raw"] != "X")
    n_stray = sum(1 for p in pages if p["unassigned_handwriting"])
    print(f"{len(pages)} pages, {n_fields} fields, {n_text} non-empty text fields, "
          f"{n_stray} pages with unassigned handwriting -> {args.out}")


if __name__ == "__main__":
    main()
