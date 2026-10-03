"""Per-field confidence: features of a reading -> calibrated probability that it is correct.

Signals combined:
* token log-probabilities of the primary reader (mean, min),
* agreement between two independent readers (qwen3.5 vs glm-ocr) after normalisation,
* whether the text parsed into the expected type, closeness to the field vocabulary,
* consistency-rule flags (out of range, inconsistent dates, ...),
* image evidence (amount of ink, registration similarity, capture sharpness).

A logistic regression fitted on the calibration split maps features to a probability
(``make calibrate``); its weights are stored as JSON, so no pickled object is ever loaded.
Before calibration a conservative hand-set model is used.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

TEXT_FEATURES = [
    "lp_mean", "lp_min", "n_tokens", "second_agree", "second_missing", "parse_ok", "lexicon_score",
    "n_flags", "out_of_range", "ink_cols", "reg_similarity", "sharpness", "is_arabic",
    "vt_date", "vt_bp", "vt_number", "vt_choice", "vt_text",
]

DEFAULT_MODEL_PATH = Path("artifacts/models/confidence.json")


def feature_vector(feats: dict[str, float]) -> np.ndarray:
    return np.array([float(feats.get(k, 0.0)) for k in TEXT_FEATURES], dtype=np.float64)


@dataclass
class ConfidenceModel:
    coef: np.ndarray
    intercept: float
    mean: np.ndarray
    scale: np.ndarray
    source: str = "default"
    accept_threshold: float | None = None  # chosen on the calibration split (docs/EVALUATION.md, rule 2)

    def predict(self, feats: dict[str, float]) -> float:
        z = (feature_vector(feats) - self.mean) / self.scale
        return float(1.0 / (1.0 + math.exp(-(z @ self.coef + self.intercept))))

    def to_json(self) -> dict:
        return {"features": TEXT_FEATURES, "coef": self.coef.tolist(), "intercept": self.intercept,
                "mean": self.mean.tolist(), "scale": self.scale.tolist(), "source": self.source,
                "accept_threshold": self.accept_threshold}

    @classmethod
    def from_json(cls, data: dict) -> ConfidenceModel:
        if data["features"] != TEXT_FEATURES:
            raise ValueError("confidence model was trained on a different feature set: re-run `make calibrate`")
        return cls(np.array(data["coef"]), float(data["intercept"]), np.array(data["mean"]),
                   np.array(data["scale"]), data.get("source", "trained"), data.get("accept_threshold"))

    @classmethod
    def load(cls, path: Path = DEFAULT_MODEL_PATH) -> ConfidenceModel:
        if path.exists():
            return cls.from_json(json.loads(path.read_text()))
        return cls.default()

    @classmethod
    def default(cls) -> ConfidenceModel:
        """Hand-set, deliberately conservative weights (used until a calibrated model exists)."""
        w = dict.fromkeys(TEXT_FEATURES, 0.0)
        w.update(lp_mean=2.0, lp_min=0.8, second_agree=1.6, parse_ok=1.2, lexicon_score=0.8,
                 n_flags=-1.2, out_of_range=-1.5, reg_similarity=0.5)
        return cls(np.array([w[k] for k in TEXT_FEATURES]), 0.3, np.zeros(len(TEXT_FEATURES)),
                   np.ones(len(TEXT_FEATURES)), "default")

    def save(self, path: Path = DEFAULT_MODEL_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=1))


def checkbox_confidence(fill: float, on: float = 0.06) -> float:
    """Probability that the tick/no-tick decision is right, from the box fill ratio.

    Fills observed on the specimen: unticked boxes <= 0.09 even on severe captures, ticked
    boxes >= 0.09 (median 0.5-0.8). Confidence grows with the distance to the threshold.
    """
    d = abs(fill - on)
    return float(min(0.995, 0.5 + d / 0.08 * 0.5)) if d < 0.08 else 0.995


def fit(x: np.ndarray, y: np.ndarray, c: float = 1.0) -> ConfidenceModel:
    from sklearn.linear_model import LogisticRegression

    mean = x.mean(axis=0)
    scale = np.where(x.std(axis=0) > 1e-9, x.std(axis=0), 1.0)
    lr = LogisticRegression(C=c, max_iter=2000)
    lr.fit((x - mean) / scale, y)
    return ConfidenceModel(lr.coef_[0], float(lr.intercept_[0]), mean, scale, "trained")
