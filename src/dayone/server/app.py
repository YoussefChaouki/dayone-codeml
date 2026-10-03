"""Processing & registry server (district level), reached by phones when they have network.

* ``POST /v1/pages``   — idempotent upload of a (redacted) page photo; queued for AI processing.
* ``GET  /v1/pages/{id}`` — processing status and extraction.
* ``POST /v1/records`` — idempotent upsert of a validated record (key: record id + version).
* ``GET  /v1/images/{id}`` — original (redacted) photo, role-based access, every access audited.
* ``GET  /dashboard``  — anonymised aggregates (k-anonymity: cells under 5 women are suppressed).

The AI models run on this machine (Ollama): no data is sent to a third-party service.
Images are encrypted at rest with the server key.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from dayone.device.crypto import Cipher
from dayone.extraction.ocr import OcrUnavailable
from dayone.schema import PageType

log = logging.getLogger(__name__)

SERVER_DIR = Path(os.environ.get("DAYONE_SERVER_DIR", "artifacts/server"))
# token -> (user id, role). Demo accounts; in production: the health ministry's identity provider.
DEFAULT_TOKENS = {
    "token-sf-amina": ("sf-amina", "midwife"),
    "token-sf-hajar": ("sf-hajar", "midwife"),
    "token-sup-karim": ("sup-karim", "supervisor"),
    "token-epi-salma": ("epi-salma", "epidemiologist"),
}
K_ANONYMITY = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (id TEXT PRIMARY KEY, record_id TEXT, midwife_id TEXT, page_type_hint TEXT,
    captured_at REAL, received_at REAL, status TEXT, error TEXT, attempts INTEGER DEFAULT 0, image BLOB,
    extraction TEXT);
CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, version INTEGER, patient_id TEXT, midwife_id TEXT,
    state TEXT, received_at REAL, payload TEXT);
CREATE TABLE IF NOT EXISTS patients (id TEXT PRIMARY KEY, updated_at REAL, payload TEXT);
CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, response TEXT, at REAL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, user_id TEXT, role TEXT,
    action TEXT, target TEXT, allowed INTEGER);
"""


class Registry:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "server.db", check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        key_file = directory / "server.key"
        if not key_file.exists():
            key_file.write_bytes(os.urandom(32))
            key_file.chmod(0o600)
        self.cipher = Cipher(key_file.read_bytes())

    def execute(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def audit(self, user: tuple[str, str], action: str, target: str, allowed: bool) -> None:
        self.execute("INSERT INTO audit (at, user_id, role, action, target, allowed) VALUES (?, ?, ?, ?, ?, ?)",
                     (time.time(), user[0], user[1], action, target, int(allowed)))


class Processor:
    """Background worker running the extraction pipeline on queued pages."""

    def __init__(self, registry: Registry, extractor_factory) -> None:
        self.registry = registry
        self.factory = extractor_factory
        self.extractor = None
        self.queue: queue.Queue[str] = queue.Queue()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="dayone-processor")
        self.thread.start()
        for (pid,) in registry.execute("SELECT id FROM pages WHERE status IN ('queued', 'processing')"):
            self.queue.put(pid)  # resume work interrupted by a restart

    def submit(self, page_id: str) -> None:
        self.queue.put(page_id)

    def _loop(self) -> None:
        while True:
            pid = self.queue.get()
            try:
                self.process(pid)
            except Exception:  # keep the worker alive; the error is recorded on the page
                log.exception("processing %s crashed", pid)
                self.registry.execute("UPDATE pages SET status='failed', error='internal_error' WHERE id=?", (pid,))

    def process(self, pid: str) -> None:
        row = self.registry.execute("SELECT image, page_type_hint FROM pages WHERE id=?", (pid,))
        if not row:
            return
        self.registry.execute("UPDATE pages SET status='processing', attempts=attempts+1 WHERE id=?", (pid,))
        img = cv2.imdecode(np.frombuffer(self.registry.cipher.decrypt(row[0][0], pid.encode()), np.uint8),
                           cv2.IMREAD_COLOR)
        try:
            if self.extractor is None:
                self.extractor = self.factory()
            hint = PageType(row[0][1]) if row[0][1] else None
            result = self.extractor.extract(img, expected=hint, check_quality=False)
        except OcrUnavailable as e:
            self.registry.execute("UPDATE pages SET status='failed', error=? WHERE id=?", (f"ai_unavailable: {e}", pid))
            return
        status = "done" if result.page_type is not None else "failed"
        error = None if status == "done" else "page_not_recognised"
        self.registry.execute("UPDATE pages SET status=?, error=?, extraction=? WHERE id=?",
                              (status, error, result.model_dump_json(), pid))


def default_extractor_factory():
    from dayone.extraction.pipeline import Extractor

    return Extractor(ocr_cache=False)  # no plaintext readings on the server's disk


def create_app(directory: Path = SERVER_DIR, extractor_factory=default_extractor_factory,
               tokens: dict[str, tuple[str, str]] | None = None) -> FastAPI:
    registry = Registry(directory)
    processor = Processor(registry, extractor_factory)
    tokens = tokens or DEFAULT_TOKENS
    app = FastAPI(title="DayOne processing server")
    # The phone simulator (another local port) queries role-based image access for the demo.
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
                       allow_methods=["GET"], allow_headers=["Authorization"])
    app.state.registry = registry
    app.state.processor = processor

    def user(authorization: str = Header(default="")) -> tuple[str, str]:
        tok = authorization.removeprefix("Bearer ").strip()
        if tok not in tokens:
            raise HTTPException(401, "unknown token")
        return tokens[tok]

    def idempotent(key: str) -> dict | None:
        row = registry.execute("SELECT response FROM idempotency WHERE key=?", (key,))
        return json.loads(row[0][0]) if row else None

    def remember(key: str, response: dict) -> None:
        registry.execute("INSERT OR IGNORE INTO idempotency (key, response, at) VALUES (?, ?, ?)",
                         (key, json.dumps(response), time.time()))

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/v1/pages")
    def upload_page(page_id: str = Form(...), record_id: str = Form(...), captured_at: float = Form(...),
                    page_type_hint: str = Form(""), image: UploadFile = File(...),
                    who: tuple[str, str] = Depends(user)) -> dict:
        if who[1] != "midwife":
            raise HTTPException(403, "only midwives upload captures")
        prior = idempotent(f"page:{page_id}")
        if prior is not None:
            return prior | {"duplicate_request": True}
        data = image.file.read()
        registry.execute("INSERT OR IGNORE INTO pages (id, record_id, midwife_id, page_type_hint, captured_at, "
                         "received_at, status, image) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
                         (page_id, record_id, who[0], page_type_hint or None, captured_at, time.time(),
                          registry.cipher.encrypt(data, page_id.encode())))
        processor.submit(page_id)
        response = {"page_id": page_id, "status": "queued"}
        remember(f"page:{page_id}", response)
        return response

    @app.post("/v1/pages/{page_id}/retry")
    def retry_page(page_id: str, who: tuple[str, str] = Depends(user)) -> dict:
        rows = registry.execute("SELECT midwife_id, status FROM pages WHERE id=?", (page_id,))
        if not rows or rows[0][0] != who[0]:
            raise HTTPException(404)
        if rows[0][1] == "failed":
            registry.execute("UPDATE pages SET status='queued', error=NULL WHERE id=?", (page_id,))
            processor.submit(page_id)
        if rows[0][1] in ("done", "processing", "queued"):
            return {"page_id": page_id, "status": rows[0][1]}
        return {"page_id": page_id, "status": "queued"}

    @app.get("/v1/pages/{page_id}")
    def page_status(page_id: str, who: tuple[str, str] = Depends(user)) -> dict:
        rows = registry.execute("SELECT midwife_id, status, error, extraction, attempts FROM pages WHERE id=?",
                                (page_id,))
        if not rows:
            raise HTTPException(404, "unknown page")
        midwife, status, error, extraction, attempts = rows[0]
        if not (who[1] == "supervisor" or (who[1] == "midwife" and midwife == who[0])):
            raise HTTPException(404, "unknown page")  # field values are not for other roles
        return {"page_id": page_id, "status": status, "error": error, "attempts": attempts,
                "extraction": json.loads(extraction) if extraction else None}

    @app.post("/v1/records")
    def upsert_record(body: dict[str, Any], idempotency_key: str = Header(...),
                      who: tuple[str, str] = Depends(user)) -> dict:
        if who[1] != "midwife":
            raise HTTPException(403, "only midwives register records")
        prior = idempotent(f"record:{idempotency_key}")
        if prior is not None:
            return prior | {"duplicate_request": True}
        rid, version, pid = body["record_id"], int(body["version"]), body["patient_id"]
        current = registry.execute("SELECT version FROM records WHERE id=?", (rid,))
        if current and current[0][0] > version:
            raise HTTPException(409, "a newer version of this record is already registered")
        registry.execute("INSERT INTO records (id, version, patient_id, midwife_id, state, received_at, payload) "
                         "VALUES (?, ?, ?, ?, 'SYNCED', ?, ?) ON CONFLICT(id) DO UPDATE SET version=excluded.version, "
                         "patient_id=excluded.patient_id, received_at=excluded.received_at, payload=excluded.payload",
                         (rid, version, pid, who[0], time.time(), json.dumps(body.get("record", {}))))
        registry.execute("INSERT INTO patients (id, updated_at, payload) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE "
                         "SET updated_at=excluded.updated_at, payload=excluded.payload",
                         (pid, time.time(), json.dumps(body.get("patient", {}))))
        # Possible duplicates across phones: another profile with the same code (left to the supervisor).
        codes = set(body.get("patient", {}).get("codes", []))
        dups = []
        for other_id, payload in registry.execute("SELECT id, payload FROM patients WHERE id<>?", (pid,)):
            if codes & set(json.loads(payload).get("codes", [])):
                dups.append(other_id)
        response = {"record_id": rid, "version": version, "ok": True, "possible_duplicates": dups}
        remember(f"record:{idempotency_key}", response)
        return response

    @app.get("/v1/images/{page_id}")
    def image(page_id: str, who: tuple[str, str] = Depends(user)) -> Response:
        rows = registry.execute("SELECT midwife_id, image FROM pages WHERE id=?", (page_id,))
        allowed = bool(rows) and (who[1] == "supervisor" or (who[1] == "midwife" and rows[0][0] == who[0]))
        registry.audit(who, "view_image", page_id, allowed)
        if not allowed:
            raise HTTPException(403, "your role does not give access to this image")
        return Response(registry.cipher.decrypt(rows[0][1], page_id.encode()), media_type="image/jpeg")

    @app.get("/v1/audit")
    def audit(who: tuple[str, str] = Depends(user)) -> list[dict]:
        if who[1] != "supervisor":
            raise HTTPException(403)
        rows = registry.execute("SELECT at, user_id, role, action, target, allowed FROM audit ORDER BY id DESC LIMIT 200")
        return [dict(zip(("at", "user_id", "role", "action", "target", "allowed"), r, strict=True)) for r in rows]

    @app.get("/v1/dashboard")
    def dashboard_data(who: tuple[str, str] = Depends(user)) -> dict:
        if who[1] not in ("supervisor", "epidemiologist"):
            raise HTTPException(403)
        from dayone.server.dashboard import aggregates

        records = [(pid, json.loads(p)) for pid, p in registry.execute("SELECT patient_id, payload FROM records")]
        return aggregates(records, k=K_ANONYMITY)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_page() -> str:
        return (Path(__file__).parent / "dashboard.html").read_text()

    return app



def main() -> None:
    import argparse

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--dir", type=Path, default=SERVER_DIR)
    args = ap.parse_args()
    uvicorn.run(create_app(args.dir), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
