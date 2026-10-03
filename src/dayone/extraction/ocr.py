"""Local OCR through Ollama (no data leaves the machine).

The primary reader is ``glm-ocr`` (small, fast, strong on handwriting) called on one
field crop at a time. Token log-probabilities are kept: they feed the confidence model
and produce alternative readings ("I read 104/74, or maybe 109/74?") for the review flow.
A second, larger vision-language model (``qwen3.5:9b``) can re-read doubtful fields.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import httpx
import numpy as np

log = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("DAYONE_OLLAMA_URL", "http://localhost:11434")
PRIMARY_MODEL = os.environ.get("DAYONE_OCR_MODEL", "glm-ocr")
SECOND_MODEL = os.environ.get("DAYONE_SECOND_MODEL", "qwen3.5:9b")
CACHE_DIR = Path(os.environ.get("DAYONE_OCR_CACHE", "artifacts/cache/ocr"))


class OcrUnavailable(RuntimeError):
    """The local model server cannot be reached (AI unavailable: manual entry takes over)."""


@dataclass
class Reading:
    text: str
    model: str
    token_logprobs: list[float] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)  # other plausible strings, best first

    @property
    def mean_logprob(self) -> float:
        return float(np.mean(self.token_logprobs)) if self.token_logprobs else 0.0

    @property
    def min_logprob(self) -> float:
        return float(min(self.token_logprobs)) if self.token_logprobs else 0.0

    @property
    def seq_prob(self) -> float:
        return float(math.exp(sum(self.token_logprobs))) if self.token_logprobs else 1.0


PROMPTS = {
    "glm-ocr": "Text Recognition:",
    "default": ("This image is one handwritten field from a French maternal health registry. "
                "Transcribe exactly what is written (French, Arabic or English), nothing else. "
                "If the field is empty, answer with nothing."),
}


def _encode(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("cannot encode image")
    return base64.b64encode(buf.tobytes()).decode()


def _alternatives(logprobs: list[dict], max_alts: int = 3) -> list[str]:
    """Readings obtained by swapping the least certain token(s) for their runner-up."""
    tokens = [t["token"] for t in logprobs]
    swaps = []
    for i, t in enumerate(logprobs):
        for alt in t.get("top_logprobs", [])[1:]:
            if alt["token"].strip() and alt["token"] != t["token"]:
                swaps.append((t["logprob"] - alt["logprob"], i, alt["token"]))  # small gap = plausible
    swaps.sort()
    out = []
    for _, i, tok in swaps:
        cand = "".join(tokens[:i] + [tok] + tokens[i + 1:]).strip()
        if cand and cand not in out and cand != "".join(tokens).strip():
            out.append(cand)
        if len(out) >= max_alts:
            break
    return out


class OcrEngine:
    def __init__(self, model: str = PRIMARY_MODEL, url: str = OLLAMA_URL, timeout: float = 300.0,
                 cache_dir: Path | None = CACHE_DIR) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        try:
            r = self.client.get(f"{self.url}/api/tags", timeout=3.0)
        except httpx.HTTPError:
            return False
        return r.status_code == 200 and any(m["name"].split(":")[0] == self.model.split(":")[0]
                                            for m in r.json().get("models", []))

    def read(self, crop_bgr: np.ndarray, prompt: str | None = None) -> Reading:
        prompt = prompt or PROMPTS.get(self.model.split(":")[0], PROMPTS["default"])
        image_b64 = _encode(crop_bgr)
        key = hashlib.sha256(f"{self.model}|{prompt}|{image_b64}".encode()).hexdigest()
        cache = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cache and cache.exists():
            data = json.loads(cache.read_text())
        else:
            body = {"model": self.model, "stream": False, "logprobs": True, "top_logprobs": 3,
                    "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
                    "options": {"temperature": 0, "num_predict": 64}}
            if self.model.startswith("qwen"):
                body["think"] = False
            try:
                r = self.client.post(f"{self.url}/api/chat", json=body)
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise OcrUnavailable(f"OCR model {self.model} unreachable: {e}") from e
            payload = r.json()
            data = {"text": payload["message"]["content"], "logprobs": payload.get("logprobs") or []}
            if cache:
                cache.write_text(json.dumps(data, ensure_ascii=False))
        lps = [t["logprob"] for t in data["logprobs"] if t["token"].strip()]
        return Reading(text=data["text"].strip(), model=self.model, token_logprobs=lps,
                       alternatives=_alternatives(data["logprobs"]))
