"""Simulate field photos of a registry page: perspective, skew, blur, shadows, low light, noise, JPEG.

The organisers' PNGs are clean renders; the hidden test set is described as degraded
phone photos. Every degradation is drawn from a seeded RNG so the evaluation set is
reproducible (``make dataset``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np

LEVELS = ("clean", "mild", "medium", "severe")


@dataclass
class DegradeParams:
    level: str
    corner_jitter: float  # fraction of page size
    rotation_deg: float
    out_height: int  # resolution of the "phone photo"
    blur_sigma: float
    motion_blur: int  # kernel length, 0 = none
    shadow_strength: float
    gamma: float
    brightness: float
    noise_sigma: float
    jpeg_quality: int


def sample_params(level: str, rng: np.random.Generator) -> DegradeParams:
    if level == "clean":
        return DegradeParams(level, 0, 0, 0, 0, 0, 0, 1.0, 1.0, 0, 95)
    # mild   ~ camera photo (long side 2000-2400 px), good light
    # medium ~ WhatsApp-compressed photo (long side ~1600 px, as in the organisers' real photos), shadow
    # severe ~ low-resolution, blurred, dark capture: the quality check should often ask for a retake
    k = {"mild": 0, "medium": 1, "severe": 2}[level]
    u = rng.uniform
    return DegradeParams(
        level=level,
        corner_jitter=u(0.0, (0.02, 0.045, 0.07)[k]),
        rotation_deg=u(-1, 1) * (2, 5, 9)[k],
        out_height=int(u(*((2000, 2400), (1500, 1700), (1150, 1350))[k])),
        blur_sigma=u(*((0.0, 0.5), (0.3, 0.8), (0.8, 1.3))[k]),
        motion_blur=int(rng.choice([0, 0, 3, 5][: 2 + k])),
        shadow_strength=u(*((0.0, 0.2), (0.2, 0.4), (0.35, 0.55))[k]),
        gamma=u(*((1.0, 1.2), (1.1, 1.4), (1.4, 1.8))[k]),
        brightness=u(*((0.9, 1.0), (0.75, 0.9), (0.55, 0.75))[k]),
        noise_sigma=u(*((1, 3), (2, 5), (4, 8))[k]),
        jpeg_quality=int(u(*((80, 92), (65, 80), (45, 60))[k])),
    )


def _background(h: int, w: int, rng: np.random.Generator) -> np.ndarray:
    base = rng.uniform(25, 90, size=3)
    bg = np.ones((h, w, 3), np.float32) * base
    bg += rng.normal(0, 6, size=(h, w, 1))  # fabric-like texture
    return np.clip(bg, 0, 255).astype(np.uint8)


def _shadow(h: int, w: int, strength: float, rng: np.random.Generator) -> np.ndarray:
    """Multiplicative shading map: a soft polygon shadow plus a lighting gradient."""
    mask = np.zeros((h, w), np.float32)
    pts = np.array([[rng.uniform(-0.2, 1.2) * w, rng.uniform(-0.2, 1.2) * h] for _ in range(4)], np.int32)
    cv2.fillConvexPoly(mask, cv2.convexHull(pts), 1.0)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=max(h, w) * 0.05)
    gx = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    gy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    a, b = rng.uniform(-1, 1, 2)
    grad = (a * gx + b * gy)
    grad = (grad - grad.min()) / (np.ptp(grad) + 1e-6)
    return 1.0 - strength * (0.7 * mask + 0.3 * grad)


def degrade(page_bgr: np.ndarray, level: str, seed: int) -> tuple[np.ndarray, dict]:
    """Return a simulated photo of ``page_bgr`` and the parameters used.

    ``params["page_to_photo"]`` is the exact homography from page pixels to photo pixels,
    which lets tests measure registration error.
    """
    rng = np.random.default_rng(seed)
    p = sample_params(level, rng)
    if level == "clean":
        return page_bgr.copy(), asdict(p) | {"page_to_photo": np.eye(3).tolist()}
    h, w = page_bgr.shape[:2]
    # Page placed on a table with a margin, then photographed in perspective.
    margin = int(0.12 * max(h, w))
    canvas_h, canvas_w = h + 2 * margin, w + 2 * margin
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    jit = rng.uniform(-1, 1, size=(4, 2)) * p.corner_jitter * np.array([w, h])
    dst = src + margin + jit
    # Rotate the destination quad around the canvas centre.
    c = np.array([canvas_w / 2, canvas_h / 2])
    th = np.deg2rad(p.rotation_deg)
    rot = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    dst = ((dst - c) @ rot.T + c).astype(np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    bg = _background(canvas_h, canvas_w, rng)
    warped = cv2.warpPerspective(page_bgr, m, (canvas_w, canvas_h), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    page_mask = cv2.warpPerspective(np.ones((h, w), np.uint8), m, (canvas_w, canvas_h)) > 0
    img = np.where(page_mask[..., None], warped, bg).astype(np.float32)
    # Crop roughly around the page like a phone frame, then resize to phone resolution.
    x0, y0 = np.maximum(dst.min(axis=0) - margin * 0.4, 0).astype(int)
    x1, y1 = np.minimum(dst.max(axis=0) + margin * 0.4, [canvas_w, canvas_h]).astype(int)
    img = img[y0:y1, x0:x1]
    scale = p.out_height / img.shape[0]
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    page_to_photo = np.diag([scale, scale, 1.0]) @ np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1.0]]) @ m
    hh, ww = img.shape[:2]
    # Lighting: shadow, exposure and gamma, warm colour cast.
    img *= _shadow(hh, ww, p.shadow_strength, rng)[..., None]
    img = 255.0 * np.power(np.clip(img / 255.0, 0, 1), p.gamma) * p.brightness
    img *= np.array([0.92, 1.0, 1.06], np.float32)  # BGR: warm indoor light
    if p.blur_sigma > 0.05:
        img = cv2.GaussianBlur(img, (0, 0), p.blur_sigma)
    if p.motion_blur:
        k = np.zeros((p.motion_blur, p.motion_blur), np.float32)
        k[p.motion_blur // 2, :] = 1.0 / p.motion_blur
        rk = cv2.getRotationMatrix2D((p.motion_blur / 2 - 0.5, p.motion_blur / 2 - 0.5), rng.uniform(0, 180), 1)
        img = cv2.filter2D(img, -1, cv2.warpAffine(k, rk, k.shape))
    img += rng.normal(0, p.noise_sigma, img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, p.jpeg_quality])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_COLOR), asdict(p) | {"page_to_photo": page_to_photo.tolist()}
