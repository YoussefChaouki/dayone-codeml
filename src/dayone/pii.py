"""Direct identifiers: never extracted, never stored.

* Images: the zones of the form that hold a name, ID number, phone, address or husband's
  name (``forms.layout.PII_ZONES``) are blacked out on the phone right after
  registration, **before** the photo is encrypted and stored. The stored "original
  image" is this redacted photo; the unredacted pixels exist only in camera memory.
* Text: free-text values are scrubbed of anything that looks like a phone number or a
  national ID number (a midwife may write one in a comment field).
"""

from __future__ import annotations

import re

import cv2
import numpy as np

from dayone.forms.layout import PII_ZONES
from dayone.forms.templates import SCALE
from dayone.schema import PageType

PHONE = re.compile(r"(?:\+?212|0)\s*[5-7](?:[\s.\-]*\d){8}")
NATIONAL_ID = re.compile(r"\b[A-Z]{1,2}\s?\d{5,7}\b", re.IGNORECASE)
MASK = "[masqué]"


def redact_capture(capture_bgr: np.ndarray, homography: np.ndarray, page_type: PageType,
                   margin_pt: float = 3.0) -> tuple[np.ndarray, list[str]]:
    """Black out the PII zones of ``page_type`` on the original capture.

    ``homography`` maps capture pixels to template pixels; zones are mapped back with its
    inverse. Returns the redacted copy and the names of the zones masked.
    """
    out = capture_bgr.copy()
    inv = np.linalg.inv(homography)
    names = []
    for name, (x0, y0, x1, y1) in PII_ZONES.get(page_type, []):
        x0, y0, x1, y1 = x0 - margin_pt, y0 - margin_pt, x1 + margin_pt, y1 + margin_pt
        corners = np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) * SCALE
        pts = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), inv).reshape(-1, 2)
        cv2.fillPoly(out, [np.round(pts).astype(np.int32)], (0, 0, 0))
        names.append(name)
    return out, names


def scrub_text(text: str | None) -> str | None:
    if text is None:
        return None
    text = PHONE.sub(MASK, text)
    return NATIONAL_ID.sub(MASK, text)
