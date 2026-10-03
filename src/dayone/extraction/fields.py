"""Pixel-level field reading on a registered page (no AI, runs on device).

* ``ink_map``: handwriting ink = pixels notably darker than their neighbourhood that are
  not explained by the printed template (dilated, to absorb small misalignments).
* ``checkbox_fill``: share of a tick box interior covered by new ink.
* ``field_ink``: amount of new ink in a text field, used to decide "blank" without OCR.
* ``crop_for_ocr``: field crop with the printed template ink erased (table borders and
  dotted leaders otherwise get read as "|" or "....").
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from dayone.forms.layout import BOX as BOX_PT
from dayone.forms.templates import SCALE, Template, pt_to_px
from dayone.schema import FieldSpec

REL_DARKNESS = 0.22  # pixel at least 22 % darker than its local background counts as ink
MIN_COMPONENT_PX = 6  # connected components smaller than this are noise


@dataclass
class InkMap:
    ink: np.ndarray  # uint8 {0,1}: new ink (template removed)
    raw: np.ndarray  # uint8 {0,1}: all ink
    background: np.ndarray  # local paper brightness (gray)


def ink_map(warped_bgr: np.ndarray, template: Template) -> InkMap:
    gray = cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2GRAY)
    bg = cv2.medianBlur(gray, 41).astype(np.float32)
    rel = (bg - gray.astype(np.float32)) / np.maximum(bg, 1.0)
    raw = (rel > REL_DARKNESS).astype(np.uint8)
    new = raw & (1 - template.ink)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(new, connectivity=8)
    small = np.where(stats[:, cv2.CC_STAT_AREA] < MIN_COMPONENT_PX)[0]
    if len(small) > 1:
        new[np.isin(labels, small[small > 0])] = 0
    return InkMap(new, raw, bg)


def _box_kernel() -> np.ndarray:
    side = int(round(BOX_PT * SCALE))
    k = np.zeros((side, side), np.float32)
    t = max(2, int(round(0.8 * SCALE)))
    k[:t, :] = k[-t:, :] = k[:, :t] = k[:, -t:] = 1.0
    return k


_KERNEL = _box_kernel()


def locate_box(ink: InkMap, spec: FieldSpec, search_pt: float = 4.0) -> tuple[int, int, int, int]:
    """Pixel rect of the tick box near its layout position.

    The registry boxes are hand-drawn (each one shifts by 1–2 pt), so the printed square
    is searched for in a small window by matching a hollow-square kernel on the ink map.
    """
    x0, y0, x1, y1 = pt_to_px(spec.region, pad=search_pt)
    h, w = ink.raw.shape
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    roi = ink.raw[y0:y1, x0:x1].astype(np.float32)
    kh, kw = _KERNEL.shape
    if roi.shape[0] < kh or roi.shape[1] < kw:
        return pt_to_px(spec.region)
    res = cv2.matchTemplate(roi, _KERNEL, cv2.TM_CCORR)
    _, _, _, (bx, by) = cv2.minMaxLoc(res)
    return x0 + bx, y0 + by, x0 + bx + kw, y0 + by + kh


def checkbox_fill(ink: InkMap, spec: FieldSpec, shrink_pt: float = 1.5) -> float:
    """Fraction of the located box interior covered by ink (the border is excluded)."""
    x0, y0, x1, y1 = locate_box(ink, spec)
    m = int(round(shrink_pt * SCALE))
    inner = ink.raw[y0 + m:y1 - m, x0 + m:x1 - m]
    return float(inner.mean()) if inner.size else 0.0


def value_box(ink: InkMap, spec: FieldSpec, search_pt: float = 5.0) -> tuple[int, int, int, int] | None:
    """Pixel box of the handwritten value of ``spec``, or None if the field holds no new ink.

    Registration leaves 1-3 pt of error, enough for a field crop to catch the value of the
    neighbouring row or a column header. So the value is located from the ink itself: in a
    window slightly taller than the field, horizontal bands of new ink are found, and the
    bands lying mostly inside the field are kept (a neighbour's band overlaps only its edge).
    """
    rx0, ry0, rx1, ry1 = pt_to_px(spec.region)
    h, w = ink.ink.shape
    rx0, rx1 = max(rx0, 0), min(rx1, w)
    wy0, wy1 = max(ry0 - int(search_pt * SCALE), 0), min(ry1 + int(search_pt * SCALE), h)
    win = ink.ink[wy0:wy1, rx0:rx1].copy()
    if win.size == 0:
        return None
    # Table borders shifted by the residual misregistration are not explained by the template:
    # drop long straight strokes (most of the window's height or width) before looking for text.
    win[:, win.mean(axis=0) > 0.6] = 0
    win[win.mean(axis=1) > 0.6, :] = 0
    active = win.sum(axis=1) >= 2
    gap = int(1.5 * SCALE)
    bands, start, last = [], None, None
    for y, a in enumerate(active):
        if a:
            if start is None:
                start = y
            elif y - last > gap:
                bands.append((start, last + 1))
                start = y
            last = y
    if start is not None:
        bands.append((start, last + 1))
    keep = []
    for b0, b1 in bands:
        if win[b0:b1].sum() < 2 * MIN_COMPONENT_PX:
            continue
        inside = max(0, min(b1, ry1 - wy0) - max(b0, ry0 - wy0))
        if inside / (b1 - b0) >= 0.5:
            keep.append((b0, b1))
    if not keep:
        return None
    y0, y1 = min(b[0] for b in keep), max(b[1] for b in keep)
    cols = np.where(win[y0:y1].any(axis=0))[0]
    return rx0 + int(cols[0]), wy0 + y0, rx0 + int(cols[-1]) + 1, wy0 + y1


def field_ink(ink: InkMap, spec: FieldSpec) -> dict[str, float]:
    box = value_box(ink, spec)
    if box is None:
        return {"ink_px": 0.0, "ink_cols": 0.0, "box": None}
    x0, y0, x1, y1 = box
    roi = ink.ink[y0:y1, x0:x1]
    return {"ink_px": float(roi.sum()), "ink_cols": float(roi.any(axis=0).sum()) / SCALE,  # extent in points
            "box": [x0, y0, x1, y1]}


def enhance_for_ocr(crop_bgr: np.ndarray, min_height: int = 64) -> np.ndarray:
    """Flat-field, remove ruling lines, stretch contrast: dark ink on a white background.

    Recognition models are trained on clean documents; photos of pink paper in poor light
    are brought closer to that domain. Long straight strokes (table borders, underlines)
    are removed so that they are not read as "1", "|" or "_".
    """
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = gray.shape
    k = max(9, (min(h, w) // 3) | 1)
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    norm = np.clip(gray / np.maximum(bg, 1.0) * 255.0, 0, 255).astype(np.uint8)
    dark = (norm < 200).astype(np.uint8)
    horiz = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(int(w * 0.6), 15), 1)))
    vert = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(int(h * 0.7), 15))))
    lines = cv2.dilate(horiz | vert, np.ones((3, 3), np.uint8)) > 0
    norm[lines] = 255
    # Gentle contrast stretch: darkest ink to ~40, paper stays white (hard stretching makes strokes blobby).
    lo = float(np.percentile(norm, 0.5))
    if lo > 40:
        f = (255.0 - 40.0) / max(255.0 - lo, 1.0)
        norm = np.clip(255.0 - (255.0 - norm.astype(np.float32)) * f, 0, 255).astype(np.uint8)
    if h < min_height:
        f = min_height / h
        norm = cv2.resize(norm, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    return cv2.cvtColor(norm, cv2.COLOR_GRAY2BGR)


def crop_for_ocr(warped_bgr: np.ndarray, template: Template, spec: FieldSpec, box: list[int] | None = None,
                 margin_pt: float = 2.5, enhance: bool = False) -> np.ndarray:
    """Crop sent to the reader: the located value (``box``) with a margin, else the whole field."""
    h, w = warped_bgr.shape[:2]
    if box is not None:
        m = int(margin_pt * SCALE)
        fx0, _, fx1, _ = pt_to_px(spec.region, pad=2.0)
        x0, y0, x1, y1 = max(box[0] - m, fx0), box[1] - m, min(box[2] + m, fx1), box[3] + m
    else:
        x0, y0, x1, y1 = pt_to_px(spec.region, pad=2.0)
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    crop = warped_bgr[y0:y1, x0:x1].copy()
    if not enhance:
        return crop
    # Printed template ink (labels, dotted leaders) is painted over before enhancement.
    mask = template.ink[y0:y1, x0:x1].astype(bool)
    paper = cv2.medianBlur(crop, 21)
    crop[mask] = paper[mask]
    return enhance_for_ocr(crop)
