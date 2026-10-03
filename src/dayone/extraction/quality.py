"""On-device capture quality check, run before a photo is accepted (no AI, no network).

The midwife is asked to retake the photo when the page cannot be read reliably: page not
found, too small in the frame (low effective resolution), blurred, too dark, or glare.
Metrics are computed on the detected page area and normalised to a fixed resolution so
that thresholds do not depend on the phone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from dayone.extraction.register import find_page_quad

A4_HEIGHT_IN = 11.69

# Thresholds set from the simulated capture levels and the organisers' 5 real photos
# (docs/EVALUATION.md): every real photo passes (lowest: 92 dpi, sharpness 84), every
# "severe" capture (on which OCR accuracy collapses to ~4 %) is sent back (sharpness <= 37).
MIN_EFFECTIVE_DPI = 85.0
MIN_SHARPNESS = 40.0
MIN_BRIGHTNESS = 60.0
MAX_GLARE = 0.04

MESSAGES_FR = {
    "page_not_found": "Je ne vois pas la page entière : cadrez toute la feuille sur un fond sombre.",
    "low_resolution": "La page est trop petite dans la photo : rapprochez-vous.",
    "blurry": "La photo est floue : tenez le téléphone immobile et touchez l'écran pour faire la mise au point.",
    "too_dark": "La photo est trop sombre : rapprochez-vous d'une fenêtre ou allumez la lumière.",
    "glare": "Il y a un reflet sur la page : inclinez légèrement le téléphone.",
}
MESSAGES_EN = {
    "page_not_found": "I cannot see the whole page: frame the full sheet on a dark background.",
    "low_resolution": "The page is too small in the photo: move closer.",
    "blurry": "The photo is blurred: hold the phone still and tap the screen to focus.",
    "too_dark": "The photo is too dark: move closer to a window or switch the light on.",
    "glare": "There is glare on the page: tilt the phone slightly.",
}


@dataclass
class QualityReport:
    ok: bool
    issues: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def messages(self, lang: str = "fr") -> list[str]:
        table = MESSAGES_FR if lang == "fr" else MESSAGES_EN
        return [table[i] for i in self.issues]


def assess_capture(bgr: np.ndarray) -> QualityReport:
    quad = find_page_quad(bgr)
    if quad is not None:
        page_h = float((np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2)
        page_w = float((np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2)
        tw, th = int(round(page_w)), int(round(page_h))
        page = cv2.warpPerspective(bgr, cv2.getPerspectiveTransform(
            quad.astype(np.float32), np.float32([[0, 0], [tw, 0], [tw, th], [0, th]])), (tw, th))
        found = True
    else:  # no background around the sheet: assume the page fills the frame (scan / close-up)
        page, page_h, found = bgr, float(max(bgr.shape[:2])), False
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    dpi = page_h / A4_HEIGHT_IN
    # Sharpness measured at a fixed 100 dpi so it does not reward high resolution per se.
    f = 100.0 / dpi
    norm = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
    sharpness = float(cv2.Laplacian(norm, cv2.CV_64F).var())
    brightness = float(np.median(gray))
    glare = float((gray > 250).mean())
    metrics = {"effective_dpi": round(dpi, 1), "sharpness": round(sharpness, 1),
               "brightness": round(brightness, 1), "glare": round(glare, 4), "page_found": float(found)}
    issues = []
    if dpi < MIN_EFFECTIVE_DPI:
        issues.append("low_resolution")
    if sharpness < MIN_SHARPNESS:
        issues.append("blurry")
    if brightness < MIN_BRIGHTNESS:
        issues.append("too_dark")
    if glare > MAX_GLARE:
        issues.append("glare")
    return QualityReport(not issues, issues, metrics)
