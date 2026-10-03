"""Page-type classification and photo-to-template registration (no AI, runs on device).

Two stages:
1. **Page outline**: the sheet is segmented from the background and its four corners give
   a perspective rectification onto the A4 template frame (robust to blur and low light,
   independent of the page content).
2. **Fine alignment**: SIFT features of the rectified page are matched to each candidate
   template, with matches constrained to stay near their rectified position (tables are
   repetitive, unconstrained matching happily aligns the wrong row). A RANSAC homography
   refines the rectification.

When no outline is found (page fills the frame, corners cut off) the capture is matched
globally with SIFT. Each candidate alignment is verified by correlating the warped capture
with the whole template; the best correlation gives the page type. Early/late postpartum
pages share a layout and are told apart on their title band.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from dayone.forms.templates import Template, load_templates, pt_to_px
from dayone.schema import PageType

log = logging.getLogger(__name__)

WORK_SCALE = 0.5  # template resolution used for feature matching (fraction of template DPI)
GLOBAL_CAPTURE_SIDE = 1600  # captures are resized to this long side for global matching
VERIFY_SCALE = 0.25  # resolution of the whole-page verification
ECC_SCALE = 0.5  # resolution of the dense ECC refinement
MIN_SIMILARITY = 0.15  # below this the capture is not considered a registry page
MIN_MARGIN = 0.05  # best layout must beat every other layout by this much
GOOD_SIMILARITY = 0.30  # outline alignment this good skips the (slower) global matching
LOCAL_RADIUS = 0.04  # max displacement (fraction of page width) of a match after rectification
TITLE_BAND = (190, 20, 310, 52)  # PDF points: the words PRÉCOCE / TARDIF
SIBLINGS = {
    PageType.PP_EARLY_MOTHER: PageType.PP_LATE_MOTHER, PageType.PP_LATE_MOTHER: PageType.PP_EARLY_MOTHER,
    PageType.PP_EARLY_NEWBORN: PageType.PP_LATE_NEWBORN, PageType.PP_LATE_NEWBORN: PageType.PP_EARLY_NEWBORN,
}


@dataclass
class Registration:
    ok: bool
    page_type: PageType | None
    confidence: float  # page-type confidence in [0, 1]
    similarity: float  # NCC between the registered capture and its template
    method: str = ""  # outline+local | outline | global
    scores: dict[str, float] = field(default_factory=dict)
    homography: np.ndarray | None = None  # capture pixels -> template pixels
    warped: np.ndarray | None = None  # capture in template frame (BGR)
    reason: str = ""

    def summary(self) -> dict:
        return {"ok": self.ok, "page_type": self.page_type.value if self.page_type else None,
                "confidence": round(self.confidence, 3), "similarity": round(self.similarity, 3),
                "method": self.method, "scores": self.scores, "reason": self.reason}


def _prep(gray: np.ndarray) -> np.ndarray:
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)


def _order_corners(pts: np.ndarray) -> np.ndarray:
    pts = pts.reshape(4, 2).astype(np.float32)
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


def _quad_from_channel(chan: np.ndarray, a4_ratio: float) -> tuple[np.ndarray, float] | None:
    """Largest A4-like quadrilateral separating the sheet from the background in one channel."""
    border = np.concatenate([chan[:5].ravel(), chan[-5:].ravel(), chan[:, :5].ravel(), chan[:, -5:].ravel()])
    hh, ww = chan.shape
    center = chan[hh // 4: 3 * hh // 4, ww // 4: 3 * ww // 4]
    if abs(float(np.median(border)) - float(np.median(center))) < 12:
        return None  # no contrast between sheet and background in this channel
    _, mask = cv2.threshold(chan, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.median(border) > np.median(center):
        mask = 255 - mask
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(cnt)
    if area < 0.2 * mask.size or area > 0.97 * mask.size:
        return None
    hull = cv2.convexHull(cnt)
    for eps in np.linspace(0.01, 0.08, 15):
        approx = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
        if len(approx) == 4:
            quad = _order_corners(approx)
            side_w = np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])
            side_h = np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])
            if 0.7 * a4_ratio <= side_h / max(side_w, 1e-6) <= 1.3 * a4_ratio:
                return quad, area
            return None
    return None


def find_page_quad(bgr: np.ndarray, a4_ratio: float = 841.89 / 595.28) -> np.ndarray | None:
    """Corners (tl, tr, br, bl) of the sheet in ``bgr`` pixels, or None if no plausible sheet.

    Tried on brightness and on the Lab a* channel (the registry paper is pink, which survives
    shadows and low light better than brightness does); the largest valid outline wins.
    """
    h, w = bgr.shape[:2]
    s = 800 / max(h, w)
    small = cv2.GaussianBlur(cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), (7, 7), 0)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    a_star = cv2.normalize(lab[:, :, 1], None, 0, 255, cv2.NORM_MINMAX)
    found = [q for q in (_quad_from_channel(c, a4_ratio) for c in (gray, a_star)) if q is not None]
    if not found:
        return None
    quad, _ = max(found, key=lambda qa: qa[1])
    return quad / s


class Registrar:
    def __init__(self, templates: dict[PageType, Template] | None = None) -> None:
        self.templates = templates or load_templates()
        self.sift = cv2.SIFT_create(nfeatures=6000)
        self.matcher = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {"checks": 64})
        self.features: dict[PageType, tuple[np.ndarray, np.ndarray]] = {}
        self.verify_tpl: dict[PageType, np.ndarray] = {}
        for pt, tpl in self.templates.items():
            small = cv2.resize(tpl.gray, None, fx=WORK_SCALE, fy=WORK_SCALE, interpolation=cv2.INTER_AREA)
            kp, desc = self.sift.detectAndCompute(_prep(small), None)
            self.features[pt] = (np.float32([k.pt for k in kp]), desc)
            v = cv2.resize(tpl.gray, None, fx=VERIFY_SCALE, fy=VERIFY_SCALE, interpolation=cv2.INTER_AREA)
            self.verify_tpl[pt] = cv2.GaussianBlur(_prep(v), (0, 0), 1.5).astype(np.float32)
        self.ecc_tpl = {
            pt: cv2.GaussianBlur(_prep(cv2.resize(tpl.gray, None, fx=ECC_SCALE, fy=ECC_SCALE,
                                                  interpolation=cv2.INTER_AREA)), (0, 0), 1.0).astype(np.float32)
            for pt, tpl in self.templates.items()}
        th, tw = next(iter(self.templates.values())).gray.shape
        self.tpl_size = (tw, th)

    # -- geometry helpers ---------------------------------------------------------
    def _match(self, kp_c: np.ndarray, desc_c: np.ndarray, pt: PageType,
               radius: float | None = None) -> np.ndarray | None:
        """Homography capture(work px) -> template(work px), optionally with a displacement limit."""
        kp_t, desc_t = self.features[pt]
        pairs = self.matcher.knnMatch(desc_c, desc_t, k=2)
        good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.8 * n.distance]
        if len(good) < 12:
            return None
        src = kp_c[[m.queryIdx for m in good]]
        dst = kp_t[[m.trainIdx for m in good]]
        if radius is not None:
            keep = np.linalg.norm(src - dst, axis=1) < radius
            src, dst = src[keep], dst[keep]
            if len(src) < 12:
                return None
        h, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0, maxIters=5000, confidence=0.999)
        if h is None or mask.sum() < 12:
            return None
        return h

    def _similarity(self, capture_gray: np.ndarray, h_full: np.ndarray, pt: PageType) -> float:
        """NCC between the capture warped onto template ``pt`` and the template (low resolution)."""
        tpl = self.verify_tpl[pt]
        h = np.diag([VERIFY_SCALE, VERIFY_SCALE, 1.0]) @ h_full
        warped = cv2.warpPerspective(capture_gray, h, (tpl.shape[1], tpl.shape[0]),
                                     flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)
        warped = cv2.GaussianBlur(_prep(warped), (0, 0), 1.5).astype(np.float32)
        return float(cv2.matchTemplate(warped, tpl, cv2.TM_CCOEFF_NORMED)[0, 0])

    # -- candidate alignments -----------------------------------------------------
    def _outline_candidates(self, gray: np.ndarray, quad: np.ndarray,
                            candidates: list[PageType]) -> dict[PageType, tuple[np.ndarray, str]]:
        tw, th = self.tpl_size
        corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]])
        h0 = cv2.getPerspectiveTransform(quad.astype(np.float32), corners)  # capture -> template px
        work = (int(tw * WORK_SCALE), int(th * WORK_SCALE))
        h0_work = np.diag([WORK_SCALE, WORK_SCALE, 1.0]) @ h0
        rect = cv2.warpPerspective(gray, h0_work, work, flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)
        kp, desc = self.sift.detectAndCompute(_prep(rect), None)
        out = {}
        for pt in candidates:
            h_fine = None
            if desc is not None and len(kp) >= 30:
                h_fine = self._match(np.float32([k.pt for k in kp]), desc, pt, radius=LOCAL_RADIUS * work[0])
            if h_fine is not None:
                h = np.diag([1 / WORK_SCALE, 1 / WORK_SCALE, 1.0]) @ h_fine @ h0_work
                out[pt] = (h, "outline+local")
            else:
                out[pt] = (h0, "outline")
        return out

    def _global_candidates(self, gray: np.ndarray,
                           candidates: list[PageType]) -> dict[PageType, tuple[np.ndarray, str]]:
        s_c = min(1.0, GLOBAL_CAPTURE_SIDE / max(gray.shape))
        small = cv2.resize(gray, None, fx=s_c, fy=s_c, interpolation=cv2.INTER_AREA)
        kp, desc = self.sift.detectAndCompute(_prep(small), None)
        if desc is None or len(kp) < 50:
            return {}
        kp_c = np.float32([k.pt for k in kp])
        out = {}
        for pt in candidates:
            h_work = self._match(kp_c, desc, pt)
            if h_work is not None:
                out[pt] = (np.diag([1 / WORK_SCALE, 1 / WORK_SCALE, 1.0]) @ h_work @ np.diag([s_c, s_c, 1.0]),
                           "global")
        return out

    # -- public API ---------------------------------------------------------------
    def register(self, capture_bgr: np.ndarray, expected: PageType | None = None) -> Registration:
        """Classify and register a capture. ``expected`` (retake of a given page) is *checked*, never
        assumed: the page is always classified among every layout, and a different page is refused
        (registering it on the wrong template would mask the wrong zones and leak identifiers)."""
        gray = cv2.cvtColor(capture_bgr, cv2.COLOR_BGR2GRAY)
        candidates = list(self.templates)
        quad = find_page_quad(capture_bgr)
        aligned: dict[PageType, tuple[np.ndarray, str]] = {}
        if quad is not None:
            aligned = self._outline_candidates(gray, quad, candidates)
        sims = {pt: self._similarity(gray, h, pt) for pt, (h, _) in aligned.items()}
        if not sims or max(sims.values()) < GOOD_SIMILARITY:
            glob = self._global_candidates(gray, candidates)
            for pt, (h, method) in glob.items():
                sim = self._similarity(gray, h, pt)
                if sim > sims.get(pt, -1.0):
                    aligned[pt], sims[pt] = (h, method), sim
        scores = {pt.value: round(v, 3) for pt, v in sims.items()}
        if not sims:
            return Registration(False, None, 0.0, 0.0, reason="no_alignment", scores=scores)
        best = max(sims, key=sims.get)
        others = [v for pt, v in sims.items() if pt not in (best, SIBLINGS.get(best, best))]
        margin = sims[best] - max(others, default=0.0)
        if sims[best] < MIN_SIMILARITY or margin < MIN_MARGIN:
            return Registration(False, None, 0.0, sims[best], scores=scores,
                                reason=f"weak_match(sim={sims[best]:.2f}, margin={margin:.2f})")
        h, method = aligned[best]
        h, refined = self._refine_ecc(gray, h, best)
        if refined:
            method += "+ecc"
        warped = cv2.warpPerspective(capture_bgr, h, self.tpl_size, flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REPLICATE)
        page_type = best
        if best in SIBLINGS:
            page_type = self._disambiguate(warped, best, SIBLINGS[best])
        confidence = float(np.clip(margin / 0.2, 0.0, 1.0))
        if expected is not None and page_type == SIBLINGS.get(expected):
            # same layout and same identifier zones; the title band that tells them apart may be masked
            page_type = expected
        if expected is not None and page_type != expected:
            return Registration(False, page_type, confidence, sims[best], method, scores,
                                reason=f"unexpected_page:{page_type.value}")
        return Registration(True, page_type, confidence, sims[best], method, scores, h, warped)

    def _refine_ecc(self, capture_gray: np.ndarray, h: np.ndarray, pt: PageType) -> tuple[np.ndarray, bool]:
        """Refine ``h`` with ECC (dense intensity alignment) against the template at half resolution.

        Feature homographies leave a few pixels of residual error, enough to push a short
        handwritten value out of its table cell. Returns the refined homography and whether
        ECC converged (if not, ``h`` is kept).
        """
        s = ECC_SCALE
        tpl = self.ecc_tpl[pt]
        hs = np.diag([s, s, 1.0]) @ h
        cur = cv2.warpPerspective(capture_gray, hs, (tpl.shape[1], tpl.shape[0]), flags=cv2.INTER_AREA,
                                  borderMode=cv2.BORDER_REPLICATE)
        cur = cv2.GaussianBlur(_prep(cur), (0, 0), 1.0).astype(np.float32)
        warp = np.eye(3, dtype=np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5)
        try:
            _, warp = cv2.findTransformECC(tpl, cur, warp, cv2.MOTION_HOMOGRAPHY, criteria, None, 5)
        except cv2.error as e:  # did not converge: keep the feature-based homography
            log.debug("ECC did not converge: %s", e)
            return h, False
        # warp maps template(work) -> current(work); the refined capture->template map is inv(warp) o h.
        w_full = np.diag([1 / s, 1 / s, 1.0]) @ warp.astype(np.float64) @ np.diag([s, s, 1.0])
        refined = np.linalg.inv(w_full) @ h
        shift = np.abs(w_full[:2, 2]).max()
        if shift > 40:  # implausible jump: ECC locked onto something else
            return h, False
        return refined / refined[2, 2], True

    def _disambiguate(self, warped: np.ndarray, a: PageType, b: PageType) -> PageType:
        x0, y0, x1, y1 = pt_to_px(TITLE_BAND)
        band = cv2.cvtColor(warped[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)

        def ncc(pt: PageType) -> float:
            m = 12  # allow a few pixels of residual misalignment
            ref = self.templates[pt].gray[y0 + m:y1 - m, x0 + m:x1 - m].astype(np.float32)
            return float(cv2.matchTemplate(band, ref, cv2.TM_CCOEFF_NORMED).max())

        return a if ncc(a) >= ncc(b) else b
