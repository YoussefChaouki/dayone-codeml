"""Encrypted local store of the phone (SQLite, durable writes).

What is stored in clear: ids, lifecycle states, timestamps, page types, capture-quality
metrics — the metadata needed to drive the queue. Everything else (page images, extracted
values, review decisions, the patient index, the conversation) is an AES-GCM blob.

Durability: WAL journal with ``synchronous=FULL``; every multi-step change (state
transition + outbox job, page + record update) happens in a single transaction, so a
crash or a dead battery never leaves a record half-written.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dayone.device.crypto import Cipher, WrongPin, new_salt
from dayone.device.lifecycle import RecordState, check_transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value BLOB);
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY, midwife_id TEXT NOT NULL, state TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, patient_id TEXT,
    version INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
    payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id), page_type TEXT,
    captured_at REAL NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    quality TEXT, registration TEXT, image BLOB NOT NULL, extraction BLOB);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL, ts REAL NOT NULL,
    from_state TEXT, to_state TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, ref TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT, done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS patients (id TEXT PRIMARY KEY, updated_at REAL NOT NULL, payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS chat (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, payload BLOB NOT NULL);
"""

PIN_CHECK = b"dayone-pin-check"


def new_id() -> str:
    """Random internal identifier (never derived from personal information)."""
    return uuid.uuid4().hex


@dataclass
class Record:
    id: str
    midwife_id: str
    state: RecordState
    created_at: float
    updated_at: float
    patient_id: str | None
    version: int
    attempts: int
    last_error: str | None
    payload: dict[str, Any]


@dataclass
class Page:
    id: str
    record_id: str
    page_type: str | None
    captured_at: float
    status: str  # stored | uploaded | processed | failed | replaced
    attempts: int
    quality: dict
    registration: dict


@dataclass
class Job:
    id: int
    kind: str
    ref: str
    idempotency_key: str
    attempts: int
    last_error: str | None


class DeviceStore:
    def __init__(self, path: Path | str, pin: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()
        self.db.executescript(SCHEMA)
        row = self.db.execute("SELECT value FROM meta WHERE key='salt'").fetchone()
        if row is None:
            salt = new_salt()
            self.cipher = Cipher.from_pin(pin, salt)
            with self._tx():
                self.db.execute("INSERT INTO meta VALUES ('salt', ?)", (salt,))
                self.db.execute("INSERT INTO meta VALUES ('pin_check', ?)", (self.cipher.encrypt(PIN_CHECK),))
        else:
            self.cipher = Cipher.from_pin(pin, row[0])
            check = self.db.execute("SELECT value FROM meta WHERE key='pin_check'").fetchone()[0]
            if self.cipher.decrypt(check) != PIN_CHECK:
                raise WrongPin("wrong PIN")

    # -- helpers -------------------------------------------------------------
    def _tx(self):
        store = self

        class _Tx:
            def __enter__(self):
                store.lock.acquire()
                store.db.execute("BEGIN IMMEDIATE")

            def __exit__(self, exc_type, exc, tb):
                try:
                    store.db.execute("COMMIT" if exc_type is None else "ROLLBACK")
                finally:
                    store.lock.release()
                return False

        return _Tx()

    def _enc(self, obj: Any, aad: str) -> bytes:
        return self.cipher.encrypt(json.dumps(obj, ensure_ascii=False, default=str).encode(), aad.encode())

    def _dec(self, blob: bytes, aad: str) -> Any:
        return json.loads(self.cipher.decrypt(blob, aad.encode()))

    def close(self) -> None:
        with self.lock:
            self.db.close()

    # -- records ---------------------------------------------------------------
    def create_record(self, midwife_id: str, payload: dict | None = None) -> str:
        rid, now = new_id(), time.time()
        with self._tx():
            self.db.execute("INSERT INTO records (id, midwife_id, state, created_at, updated_at, payload) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (rid, midwife_id, RecordState.CAPTURED.value, now, now, self._enc(payload or {}, rid)))
            self.db.execute("INSERT INTO history (record_id, ts, from_state, to_state, actor, reason) "
                            "VALUES (?, ?, NULL, ?, ?, 'capture started')", (rid, now, RecordState.CAPTURED.value,
                                                                             midwife_id))
        return rid

    def _row_to_record(self, row) -> Record:
        return Record(row[0], row[1], RecordState(row[2]), row[3], row[4], row[5], row[6], row[7], row[8],
                      self._dec(row[9], row[0]))

    def get_record(self, rid: str) -> Record:
        with self.lock:
            row = self.db.execute("SELECT id, midwife_id, state, created_at, updated_at, patient_id, version, "
                                  "attempts, last_error, payload FROM records WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        return self._row_to_record(row)

    def list_records(self, states: set[RecordState] | None = None) -> list[Record]:
        with self.lock:
            rows = self.db.execute("SELECT id, midwife_id, state, created_at, updated_at, patient_id, version, "
                                   "attempts, last_error, payload FROM records ORDER BY created_at").fetchall()
        recs = [self._row_to_record(r) for r in rows]
        return [r for r in recs if states is None or r.state in states]

    def update_record(self, rid: str, payload: dict | None = None, patient_id: str | None = None,
                      bump_version: bool = False, attempts: int | None = None, last_error: str | None = None) -> None:
        sets, args = ["updated_at=?"], [time.time()]
        if payload is not None:
            sets.append("payload=?")
            args.append(self._enc(payload, rid))
        if patient_id is not None:
            sets.append("patient_id=?")
            args.append(patient_id)
        if bump_version:
            sets.append("version=version+1")
        if attempts is not None:
            sets.append("attempts=?")
            args.append(attempts)
        if last_error is not None:
            sets.append("last_error=?")
            args.append(last_error)
        with self._tx():
            self.db.execute(f"UPDATE records SET {', '.join(sets)} WHERE id=?", (*args, rid))

    def transition(self, rid: str, target: RecordState, actor: str, reason: str = "",
                   enqueue: tuple[str, str, str] | None = None) -> None:
        """Move a record to ``target`` (validated), log it, and optionally enqueue a job atomically."""
        with self._tx():
            cur = RecordState(self.db.execute("SELECT state FROM records WHERE id=?", (rid,)).fetchone()[0])
            if cur == target:
                return
            check_transition(cur, target)
            now = time.time()
            self.db.execute("UPDATE records SET state=?, updated_at=? WHERE id=?", (target.value, now, rid))
            self.db.execute("INSERT INTO history (record_id, ts, from_state, to_state, actor, reason) "
                            "VALUES (?, ?, ?, ?, ?, ?)", (rid, now, cur.value, target.value, actor, reason))
            if enqueue:
                self._enqueue(*enqueue)

    def history(self, rid: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT ts, from_state, to_state, actor, reason FROM history WHERE record_id=? "
                                   "ORDER BY id", (rid,)).fetchall()
        return [{"ts": r[0], "from": r[1], "to": r[2], "actor": r[3], "reason": r[4]} for r in rows]

    # -- pages -----------------------------------------------------------------
    def add_page(self, record_id: str, image_jpeg: bytes, page_type: str | None, quality: dict,
                 registration: dict) -> str:
        pid, now = new_id(), time.time()
        with self._tx():
            self.db.execute("INSERT INTO pages (id, record_id, page_type, captured_at, status, quality, registration, "
                            "image) VALUES (?, ?, ?, ?, 'stored', ?, ?, ?)",
                            (pid, record_id, page_type, now, json.dumps(quality), json.dumps(registration),
                             self.cipher.encrypt(image_jpeg, pid.encode())))
            self.db.execute("UPDATE records SET updated_at=? WHERE id=?", (now, record_id))
        return pid

    def _row_to_page(self, r) -> Page:
        return Page(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6] or "{}"), json.loads(r[7] or "{}"))

    def list_pages(self, record_id: str, include_replaced: bool = False) -> list[Page]:
        with self.lock:
            rows = self.db.execute("SELECT id, record_id, page_type, captured_at, status, attempts, quality, "
                                   "registration FROM pages WHERE record_id=? ORDER BY captured_at",
                                   (record_id,)).fetchall()
        pages = [self._row_to_page(r) for r in rows]
        return [p for p in pages if include_replaced or p.status != "replaced"]

    def get_page(self, pid: str) -> Page:
        with self.lock:
            r = self.db.execute("SELECT id, record_id, page_type, captured_at, status, attempts, quality, "
                                "registration FROM pages WHERE id=?", (pid,)).fetchone()
        if r is None:
            raise KeyError(pid)
        return self._row_to_page(r)

    def page_image(self, pid: str) -> bytes:
        with self.lock:
            blob = self.db.execute("SELECT image FROM pages WHERE id=?", (pid,)).fetchone()[0]
        return self.cipher.decrypt(blob, pid.encode())

    def set_page_status(self, pid: str, status: str, attempts: int | None = None) -> None:
        with self._tx():
            if attempts is None:
                self.db.execute("UPDATE pages SET status=? WHERE id=?", (status, pid))
            else:
                self.db.execute("UPDATE pages SET status=?, attempts=? WHERE id=?", (status, attempts, pid))

    def set_page_extraction(self, pid: str, extraction: dict, page_type: str | None) -> None:
        with self._tx():
            self.db.execute("UPDATE pages SET extraction=?, status='processed', page_type=COALESCE(?, page_type) "
                            "WHERE id=?", (self._enc(extraction, pid), page_type, pid))

    def page_extraction(self, pid: str) -> dict | None:
        with self.lock:
            blob = self.db.execute("SELECT extraction FROM pages WHERE id=?", (pid,)).fetchone()[0]
        return None if blob is None else self._dec(blob, pid)

    # -- outbox ------------------------------------------------------------------
    def _enqueue(self, kind: str, ref: str, key: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO outbox (kind, ref, idempotency_key, created_at) VALUES (?, ?, ?, ?)",
                        (kind, ref, key, time.time()))

    def enqueue(self, kind: str, ref: str, key: str) -> None:
        with self._tx():
            self._enqueue(kind, ref, key)

    def due_jobs(self, now: float | None = None) -> list[Job]:
        now = time.time() if now is None else now
        with self.lock:
            rows = self.db.execute("SELECT id, kind, ref, idempotency_key, attempts, last_error FROM outbox "
                                   "WHERE done=0 AND next_attempt_at<=? ORDER BY id", (now,)).fetchall()
        return [Job(*r) for r in rows]

    def pending_jobs(self) -> list[Job]:
        with self.lock:
            rows = self.db.execute("SELECT id, kind, ref, idempotency_key, attempts, last_error FROM outbox "
                                   "WHERE done=0 ORDER BY id").fetchall()
        return [Job(*r) for r in rows]

    def job_done(self, job_id: int) -> None:
        with self._tx():
            self.db.execute("UPDATE outbox SET done=1 WHERE id=?", (job_id,))

    def job_retry(self, job_id: int, error: str, delay_s: float, count_attempt: bool = True) -> int:
        with self._tx():
            if count_attempt:
                self.db.execute("UPDATE outbox SET attempts=attempts+1, last_error=?, next_attempt_at=? WHERE id=?",
                                (error, time.time() + delay_s, job_id))
            else:
                self.db.execute("UPDATE outbox SET last_error=?, next_attempt_at=? WHERE id=?",
                                (error, time.time() + delay_s, job_id))
            return self.db.execute("SELECT attempts FROM outbox WHERE id=?", (job_id,)).fetchone()[0]

    # -- patients (local, encrypted index of the midwife's patients) ---------------
    def upsert_patient(self, patient_id: str, payload: dict) -> None:
        with self._tx():
            self.db.execute("INSERT INTO patients (id, updated_at, payload) VALUES (?, ?, ?) "
                            "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, payload=excluded.payload",
                            (patient_id, time.time(), self._enc(payload, patient_id)))

    def list_patients(self) -> dict[str, dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, payload FROM patients").fetchall()
        return {r[0]: self._dec(r[1], r[0]) for r in rows}

    def get_patient(self, patient_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT payload FROM patients WHERE id=?", (patient_id,)).fetchone()
        return None if row is None else self._dec(row[0], patient_id)

    # -- small encrypted key/value (conversation state) and chat log -------------------
    def put(self, key: str, obj: Any) -> None:
        with self._tx():
            self.db.execute("INSERT INTO kv (key, payload) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                            "payload=excluded.payload", (key, self._enc(obj, key)))

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            row = self.db.execute("SELECT payload FROM kv WHERE key=?", (key,)).fetchone()
        return default if row is None else self._dec(row[0], key)

    def log_chat(self, message: dict) -> int:
        with self._tx():
            cur = self.db.execute("INSERT INTO chat (ts, payload) VALUES (?, ?)",
                                  (time.time(), self._enc(message, "chat")))
            return int(cur.lastrowid)

    def chat_log(self, after_id: int = 0) -> list[tuple[int, dict]]:
        with self.lock:
            rows = self.db.execute("SELECT id, payload FROM chat WHERE id>? ORDER BY id", (after_id,)).fetchall()
        return [(r[0], self._dec(r[1], "chat")) for r in rows]

    def raw_sample(self) -> dict[str, str]:
        """What an attacker reading the database file would see (for the demo)."""
        with self.lock:
            rec = self.db.execute("SELECT id, state, payload FROM records ORDER BY created_at DESC LIMIT 1").fetchone()
            pg = self.db.execute("SELECT id, page_type, image FROM pages ORDER BY captured_at DESC LIMIT 1").fetchone()
        out = {}
        if rec:
            out["record"] = f"id={rec[0]} state={rec[1]} payload={rec[2][:48].hex()}…"
        if pg:
            out["page"] = f"id={pg[0]} page_type={pg[1]} image={pg[2][:48].hex()}…"
        return out
