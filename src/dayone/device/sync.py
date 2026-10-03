"""Outbox processing: upload pages, collect AI results, push validated records.

The phone never assumes the network is there. Work is a persistent queue (``outbox``)
whose jobs are idempotent on the server side (page id, record id + version), so a request
lost on the way, a response lost on the way back, or the app killed in the middle can
all be retried safely: no record is lost, none is duplicated.

``Network`` simulates connectivity for the demo and the tests (on/off switch plus
injectable faults).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

import httpx

from dayone.device.lifecycle import RecordState
from dayone.device.store import DeviceStore, Job
from dayone.records import merge_extraction

log = logging.getLogger(__name__)

MAX_PROCESSING_ATTEMPTS = 4
POLL_DELAY_S = 1.5


class Offline(Exception):
    """No connectivity (or the request / response was lost)."""


class ServerError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status


class Network:
    """Simulated connectivity. ``faults`` are consumed one per request: drop_request, drop_response, server_error."""

    def __init__(self, online: bool = True) -> None:
        self.online = online
        self.faults: deque[str] = deque()
        self.log: deque[str] = deque(maxlen=50)

    def take_fault(self) -> str | None:
        return self.faults.popleft() if self.faults else None


class ServerClient:
    def __init__(self, http: httpx.Client, token: str, network: Network) -> None:
        self.http = http
        self.token = token
        self.network = network

    def _call(self, method: str, url: str, **kw) -> dict:
        if not self.network.online:
            raise Offline("offline")
        fault = self.network.take_fault()
        if fault == "drop_request":
            self.network.log.append(f"{method} {url}: request lost")
            raise Offline("request lost")
        headers = kw.pop("headers", {}) | {"Authorization": f"Bearer {self.token}"}
        try:
            r = self.http.request(method, url, headers=headers, **kw)
        except httpx.TransportError as e:
            raise Offline(str(e)) from e
        if fault == "drop_response":
            self.network.log.append(f"{method} {url}: response lost (server did the work)")
            raise Offline("response lost")
        if fault == "server_error":
            self.network.log.append(f"{method} {url}: server error injected")
            raise ServerError(503, "injected server error")
        if r.status_code >= 400:
            raise ServerError(r.status_code, r.text[:200])
        self.network.log.append(f"{method} {url}: {r.status_code}")
        return r.json()

    def upload_page(self, page_id: str, record_id: str, captured_at: float, page_type: str | None,
                    image: bytes) -> dict:
        return self._call("POST", "/v1/pages", data={"page_id": page_id, "record_id": record_id,
                                                      "captured_at": str(captured_at), "page_type_hint": page_type or ""},
                          files={"image": (f"{page_id}.jpg", image, "image/jpeg")})

    def page_status(self, page_id: str) -> dict:
        return self._call("GET", f"/v1/pages/{page_id}")

    def retry_page(self, page_id: str) -> dict:
        return self._call("POST", f"/v1/pages/{page_id}/retry")

    def push_record(self, body: dict, key: str) -> dict:
        return self._call("POST", "/v1/records", json=body, headers={"Idempotency-Key": key})


Notify = Callable[[str, dict], None]


class SyncEngine:
    def __init__(self, store: DeviceStore, client: ServerClient, notify: Notify | None = None,
                 actor: str = "sync") -> None:
        self.store = store
        self.client = client
        self.notify = notify or (lambda kind, data: None)
        self.actor = actor
        self.lock = threading.Lock()

    # -- recovery after a crash / restart --------------------------------------------
    def recover(self) -> int:
        """Re-create jobs that a crash may have prevented from being queued (idempotent)."""
        n = 0
        for rec in self.store.list_records({RecordState.PENDING_AI, RecordState.PROCESSING_FAILED}):
            for page in self.store.list_pages(rec.id):
                if page.status in ("stored", "uploaded"):
                    self.store.enqueue("process_page", page.id, f"process:{page.id}")
                    n += 1
        for rec in self.store.list_records({RecordState.REGISTERED, RecordState.SYNC_FAILED}):
            self.store.enqueue("sync_record", rec.id, f"sync:{rec.id}:{rec.version}")
            n += 1
        return n

    # -- main loop step -----------------------------------------------------------------
    def run_once(self) -> dict:
        done = {"processed": 0, "synced": 0, "errors": 0, "offline": False}
        with self.lock:
            for job in self.store.due_jobs():
                try:
                    if job.kind == "process_page":
                        done["processed"] += self._process_page(job)
                    elif job.kind == "sync_record":
                        done["synced"] += self._sync_record(job)
                    else:
                        self.store.job_done(job.id)
                except Offline as e:
                    self.store.job_retry(job.id, f"offline: {e}", 0.0, count_attempt=False)
                    done["offline"] = True
                    break  # no point trying the other jobs now
                except ServerError as e:
                    done["errors"] += 1
                    self._on_server_error(job, e)
        return done

    def _backoff(self, attempts: int) -> float:
        return min(60.0, 2.0 ** attempts)

    def _on_server_error(self, job: Job, e: ServerError) -> None:
        attempts = self.store.job_retry(job.id, str(e), self._backoff(job.attempts + 1))
        if job.kind == "sync_record":
            rec = self.store.get_record(job.ref)
            if rec.state == RecordState.REGISTERED:
                self.store.transition(rec.id, RecordState.SYNC_FAILED, self.actor, str(e))
                self.notify("sync_failed", {"record_id": rec.id, "error": str(e)})
        elif job.kind == "process_page" and attempts >= MAX_PROCESSING_ATTEMPTS:
            self._processing_failed(job, f"server error: {e}")

    def _processing_failed(self, job: Job, reason: str) -> None:
        page = self.store.get_page(job.ref)
        self.store.set_page_status(page.id, "failed")
        self.store.job_done(job.id)
        rec = self.store.get_record(page.record_id)
        if rec.state == RecordState.PENDING_AI:
            self.store.transition(rec.id, RecordState.PROCESSING_FAILED, self.actor, reason)
        rec = self.store.get_record(page.record_id)
        if rec.state == RecordState.PROCESSING_FAILED:
            self.store.transition(rec.id, RecordState.MANUAL_REVIEW_REQUIRED, self.actor,
                                  "AI processing failed repeatedly: manual entry")
        self.notify("processing_failed", {"record_id": rec.id, "page_id": page.id, "reason": reason})

    # -- jobs ------------------------------------------------------------------------------
    def _process_page(self, job: Job) -> int:
        page = self.store.get_page(job.ref)
        if page.status in ("replaced", "processed", "failed"):
            self.store.job_done(job.id)
            return 0
        rec = self.store.get_record(page.record_id)
        if page.status == "stored":
            self.client.upload_page(page.id, rec.id, page.captured_at, page.page_type, self.store.page_image(page.id))
            self.store.set_page_status(page.id, "uploaded")
        status = self.client.page_status(page.id)
        if status["status"] in ("queued", "processing"):
            self.store.job_retry(job.id, "processing on server", POLL_DELAY_S, count_attempt=False)
            return 0
        if status["status"] == "failed":
            error = status.get("error") or "failed"
            if error.startswith("ai_unavailable") and job.attempts + 1 < MAX_PROCESSING_ATTEMPTS:
                self.store.job_retry(job.id, error, self._backoff(job.attempts + 1))
                self.client.retry_page(page.id)
                return 0
            self._processing_failed(job, error)
            return 0
        extraction = status["extraction"]
        with self.store.lock:  # page result + record merge + transition: one consistent step
            self.store.set_page_extraction(page.id, extraction, extraction.get("page_type"))
            rec = self.store.get_record(page.record_id)
            payload = rec.payload
            merge_extraction(payload, page.id, extraction)
            self.store.update_record(rec.id, payload=payload)
            self.store.job_done(job.id)
            remaining = [p for p in self.store.list_pages(rec.id) if p.status not in ("processed", "failed")]
            if not remaining and rec.state == RecordState.PENDING_AI:
                self.store.transition(rec.id, RecordState.AI_PROCESSED, self.actor, "all pages read")
        if not remaining:
            self.notify("processed", {"record_id": rec.id})
        return 1

    def _sync_record(self, job: Job) -> int:
        rec = self.store.get_record(job.ref)
        if rec.state not in (RecordState.REGISTERED, RecordState.SYNC_FAILED):
            self.store.job_done(job.id)
            return 0
        patient = self.store.get_patient(rec.patient_id) if rec.patient_id else None
        body = {"record_id": rec.id, "version": rec.version, "patient_id": rec.patient_id,
                "record": {"fields": rec.payload.get("fields", {}), "page_types": rec.payload.get("page_types", {}),
                           "captured_at": rec.created_at},
                "patient": {"codes": (patient or {}).get("codes", []), "quasi": (patient or {}).get("quasi", {})}}
        resp = self.client.push_record(body, key=job.idempotency_key.removeprefix("sync:"))
        self.store.transition(rec.id, RecordState.SYNCED, self.actor, "server acknowledged")
        self.store.job_done(job.id)
        self.notify("synced", {"record_id": rec.id, "possible_duplicates": resp.get("possible_duplicates", [])})
        return 1


class SyncLoop:
    """Background thread: runs the engine while the (simulated) network is up."""

    def __init__(self, engine: SyncEngine, network: Network, period_s: float = 1.0) -> None:
        self.engine, self.network, self.period = engine, network, period_s
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="dayone-sync")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.network.online:
                try:
                    self.engine.run_once()
                except Exception:  # keep syncing; the failure is logged loudly
                    log.exception("sync step failed")
            time.sleep(self.period)
