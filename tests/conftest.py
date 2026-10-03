from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "Paper Registry"
PDF = DATA / "dossiers_specimen_10_patientes.pdf"
sys.path.insert(0, str(ROOT / "src"))

from dayone.forms.templates import TEMPLATE_DIR, build_templates  # noqa: E402
from dayone.schema import FieldResult, FieldStatus, PageExtraction, PageType  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _chdir_and_templates():
    import os

    os.chdir(ROOT)
    if not (TEMPLATE_DIR / "templates.json").exists():
        build_templates(PDF, TEMPLATE_DIR)


@pytest.fixture(scope="session")
def registrar():
    from dayone.extraction.register import Registrar

    return Registrar()


@pytest.fixture(scope="session")
def ground_truth() -> list[dict]:
    from dayone.evaluation.groundtruth import build

    return build(PDF, DATA)


def page_png(page_number: int) -> np.ndarray:
    path = sorted(DATA.glob(f"dossiers_specimen_10_patientes-{page_number:02d}*.png"))[0]
    return cv2.imread(str(path))


class FakeExtractor:
    """Deterministic stand-in for the AI pipeline (no Ollama in tests)."""

    def __init__(self, fail_first: int = 0) -> None:
        self.calls = 0
        self.fail_first = fail_first

    def extract(self, image, expected=None, check_quality=True) -> PageExtraction:
        from dayone.extraction.ocr import OcrUnavailable

        self.calls += 1
        if self.calls <= self.fail_first:
            raise OcrUnavailable("model server down (test)")
        fields = {
            "pregnancy.lmp_date": FieldResult(field_id="pregnancy.lmp_date", status=FieldStatus.KNOWN,
                                              value="2025-04-26", confidence=0.97, source="ocr", raw_text="26/04/2025"),
            "pregnancy.edd": FieldResult(field_id="pregnancy.edd", status=FieldStatus.KNOWN, value="2026-01-31",
                                         confidence=0.96, source="ocr", raw_text="31/01/2026"),
            "pregnancy.visit.t1v2.bp": FieldResult(field_id="pregnancy.visit.t1v2.bp", status=FieldStatus.NEEDS_REVIEW,
                                                   value="109/74", confidence=0.62, source="ocr", raw_text="109/74",
                                                   alternatives=["104/74", "109/24"],
                                                   features={"second_agree": 0.0}),
            "pregnancy.visit.t1v2.hemoglobin": FieldResult(field_id="pregnancy.visit.t1v2.hemoglobin",
                                                           status=FieldStatus.ILLEGIBLE, value=None, confidence=0.1,
                                                           source="ocr", raw_text=""),
            "pregnancy.visit.t1v2.weight": FieldResult(field_id="pregnancy.visit.t1v2.weight", status=FieldStatus.KNOWN,
                                                       value=58.8, confidence=0.95, source="ocr", raw_text="58.8"),
            "pregnancy.visit.t1v1.weight": FieldResult(field_id="pregnancy.visit.t1v1.weight",
                                                       status=FieldStatus.NOT_PROVIDED, confidence=0.97, source="ink"),
            "pregnancy.blood_group.B": FieldResult(field_id="pregnancy.blood_group.B", status=FieldStatus.KNOWN,
                                                   value=True, confidence=0.99, source="checkbox"),
        }
        return PageExtraction(page_type=expected or PageType.PREGNANCY, page_type_confidence=1.0, layout="template",
                              fields=fields)


@pytest.fixture
def server(tmp_path):
    from dayone.server.app import create_app

    extractor = FakeExtractor()
    app = create_app(tmp_path / "server", extractor_factory=lambda: extractor)
    client = TestClient(app)
    client.extractor = extractor
    return client


def wait_processed(client, page_ids, timeout=10.0):
    import time

    t0 = time.time()
    while time.time() - t0 < timeout:
        rows = client.app.state.registry.execute(
            f"SELECT status FROM pages WHERE id IN ({','.join('?' * len(page_ids))})", tuple(page_ids))
        if rows and all(r[0] in ("done", "failed") for r in rows) and len(rows) == len(page_ids):
            return
        time.sleep(0.05)
    raise TimeoutError("server did not process pages")


def jpeg_bytes(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def dump(obj) -> str:
    return json.dumps(obj, default=str)
