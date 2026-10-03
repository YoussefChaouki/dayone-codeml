"""Pixel-level pipeline on real specimen pages (no AI): registration, tick boxes, blank fields."""

import numpy as np
import pytest
from conftest import page_png

from dayone.evaluation.degrade import degrade
from dayone.extraction.ocr import Reading
from dayone.extraction.pipeline import Extractor, PipelineConfig
from dayone.forms.templates import SCALE
from dayone.schema import FieldStatus


@pytest.mark.parametrize("page_number,level", [(3, "medium"), (12, "mild"), (61, "medium"), (17, "severe")])
def test_registration_is_accurate_and_classifies(registrar, ground_truth, page_number, level):
    photo, params = degrade(page_png(page_number), level, seed=page_number)
    reg = registrar.register(photo)
    assert reg.ok and reg.page_type.value == ground_truth[page_number - 1]["page_type"]
    grid = np.array([[x, y, 1.0] for x in (80, 300, 520) for y in (120, 420, 760)]).T * np.array([[SCALE], [SCALE], [1]])
    q = reg.homography @ np.array(params["page_to_photo"]) @ grid
    err_pt = np.linalg.norm(q[:2] / q[2] - grid[:2], axis=0) / SCALE
    assert np.median(err_pt) < 4.0  # includes the generator's own 1-2 pt page jitter


class EchoOcr:
    """Fake OCR: always returns the same text (lets us test everything except reading)."""

    model = "fake"

    def __init__(self, text="Neg"):
        self.text, self.calls = text, 0

    def read(self, crop, prompt=None):
        self.calls += 1
        return Reading(self.text, "fake", [-0.01], [])


@pytest.mark.parametrize("page_number", [2, 3, 4, 5])
def test_ticks_and_blanks_without_ai(registrar, ground_truth, page_number):
    ocr = EchoOcr()
    ex = Extractor(PipelineConfig(second_model=None), registrar=registrar, primary=ocr)
    photo, _ = degrade(page_png(page_number), "mild", seed=7)
    result = ex.extract(photo)
    gt = ground_truth[page_number - 1]["fields"]
    boxes = [fid for fid, g in gt.items() if g["raw"] in ("X", "")]
    box_ok = [bool(result.fields[f].value) == bool(gt[f]["value"]) for f in boxes if result.fields[f].source == "checkbox"]
    assert np.mean(box_ok) >= 0.98
    blanks = [f for f, g in gt.items() if g["status"] == "NOT_PROVIDED" and result.fields[f].source != "checkbox"]
    blank_ok = [result.fields[f].status in (FieldStatus.NOT_PROVIDED, FieldStatus.NOT_APPLICABLE) for f in blanks]
    assert np.mean(blank_ok) >= 0.97
    inked = [f for f, g in gt.items() if g["status"] in ("KNOWN", "NOT_APPLICABLE") and g["raw"] not in ("X", "")]
    assert ocr.calls >= 0.95 * len(inked)  # every written field went to the reader, blank ones did not
