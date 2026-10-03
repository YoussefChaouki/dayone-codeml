"""The phone, simulated in a browser: WhatsApp-style chat + a "backstage" panel for the demo.

Runs entirely on the device: capture, quality check, page recognition, PII masking,
encrypted storage, the conversation, manual entry and patient matching. Only AI reading
and synchronisation need the processing server, reached through the simulated network
(on/off switch, injectable faults, app crash/restart).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from dayone.device.agent import Agent, field_crop
from dayone.device.lifecycle import STATE_LABEL_FR
from dayone.device.store import DeviceStore
from dayone.device.sync import Network, ServerClient, SyncEngine, SyncLoop
from dayone.extraction.register import Registrar
from dayone.records import review_queue

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
SAMPLE_DIRS = [Path("artifacts/eval/images"), Path("data/Paper Registry")]


class Device:
    def __init__(self, data_dir: Path, pin: str, midwife_id: str, token: str, server_url: str,
                 network: Network, http: httpx.Client | None = None, registrar: Registrar | None = None,
                 background: bool = True) -> None:
        self.data_dir, self.pin, self.midwife_id, self.token = data_dir, pin, midwife_id, token
        self.network = network
        self.http = http or httpx.Client(base_url=server_url, timeout=30.0)
        self.registrar = registrar or Registrar()
        self.background = background
        self._boot()

    def _boot(self) -> None:
        self.store = DeviceStore(self.data_dir / "device.db", self.pin)
        self.client = ServerClient(self.http, self.token, self.network)
        self.agent = Agent(self.store, self.registrar, self.midwife_id, is_online=lambda: self.network.online)
        self.engine = SyncEngine(self.store, self.client, notify=self.agent.notify)
        recovered = self.engine.recover()
        if recovered:
            log.info("recovered %d outbox job(s) after restart", recovered)
        self.loop = SyncLoop(self.engine, self.network) if self.background else None
        if self.loop:
            self.loop.start()

    def restart(self) -> None:
        """Simulate the app being killed and reopened: everything is rebuilt from the encrypted store."""
        if self.loop:
            self.loop.stop()
        self.store.close()
        self._boot()

    def snapshot(self) -> dict:
        recs = []
        for r in self.store.list_records():
            recs.append({"id": r.id, "short": r.id[:8], "state": r.state.value, "label": STATE_LABEL_FR[r.state],
                         "created_at": r.created_at, "version": r.version, "patient": (r.patient_id or "")[:8],
                         "pages": [{"id": p.id, "type": p.page_type, "status": p.status,
                                    "quality_ok": p.quality.get("ok"), "pii_masked": p.registration.get("pii_masked", [])}
                                   for p in self.store.list_pages(r.id, include_replaced=True)],
                         "to_review": len(review_queue(r.payload)), "n_fields": len(r.payload.get("fields", {})),
                         "history": self.store.history(r.id), "last_error": r.last_error})
        jobs = [{"id": j.id, "kind": j.kind, "ref": j.ref[:8], "attempts": j.attempts, "last_error": j.last_error}
                for j in self.store.pending_jobs()]
        return {"online": self.network.online, "faults": list(self.network.faults),
                "network_log": list(self.network.log)[-12:], "records": recs[::-1], "outbox": jobs,
                "patients": len(self.store.list_patients()), "encrypted_sample": self.store.raw_sample(),
                "midwife": self.midwife_id, "lang": self.agent.lang}


def create_app(device: Device) -> FastAPI:
    app = FastAPI(title="DayOne phone (simulated)")
    app.state.device = device
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text()

    @app.post("/api/send")
    def send(event: dict) -> list[dict]:
        if event.get("type") not in ("text", "button"):
            raise HTTPException(400, "type must be text or button")
        return device.agent.handle(event)

    @app.post("/api/photo")
    def photo(file: UploadFile = File(...)) -> list[dict]:
        data = file.file.read()
        return device.agent.handle({"type": "image", "data": data, "text": "📷 " + (file.filename or "photo")})

    @app.post("/api/sample/{name}")
    def send_sample(name: str) -> list[dict]:
        path = _sample_path(name)
        return device.agent.handle({"type": "image", "data": path.read_bytes(), "text": f"📷 {name}"})

    @app.get("/api/messages")
    def messages(after: int = 0) -> list[dict]:
        return [m | {"id": i} for i, m in device.store.chat_log(after)]

    @app.get("/api/state")
    def state() -> dict:
        return device.snapshot()

    @app.post("/api/network")
    def network(body: dict) -> dict:
        device.network.online = bool(body.get("online"))
        return {"online": device.network.online}

    @app.post("/api/fault")
    def fault(body: dict) -> dict:
        kind = body.get("kind")
        if kind not in ("drop_request", "drop_response", "server_error"):
            raise HTTPException(400, "unknown fault")
        device.network.faults.append(kind)
        return {"faults": list(device.network.faults)}

    @app.post("/api/restart")
    def restart() -> dict:
        device.restart()
        return {"restarted": True, "pending_jobs": len(device.store.pending_jobs())}

    @app.get("/api/pages/{page_id}/image")
    def page_image(page_id: str) -> Response:
        try:
            return Response(device.store.page_image(page_id), media_type="image/jpeg")
        except KeyError as e:
            raise HTTPException(404) from e

    @app.get("/api/crops/{page_id}/{field_id}")
    def crop(page_id: str, field_id: str) -> Response:
        return Response(field_crop(device.store, page_id, field_id), media_type="image/jpeg")

    @app.get("/api/samples")
    def samples() -> list[dict]:
        out = []
        for d in SAMPLE_DIRS:
            if not d.exists():
                continue
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in (".jpg", ".png") and _is_demo_sample(p.name):
                    out.append({"name": p.name, "group": _sample_group(p.name)})
        return out

    @app.get("/api/samples/{name}")
    def sample(name: str) -> FileResponse:
        return FileResponse(_sample_path(name))

    return app


def _is_demo_sample(name: str) -> bool:
    if name.startswith("1-"):
        return True  # the organisers' real photos (unsupported booklet layout: shows the refusal path)
    if name.startswith("spec_"):
        # patient 6 (test split) in every capture condition + a few others
        return any(f"_p{p:02d}_" in name for p in range(41, 49)) or name.endswith(("_medium.jpg",))
    return name.startswith("synth_p4") or name.startswith("synth_p5")


def _sample_group(name: str) -> str:
    if name.startswith("1-"):
        return "Photos réelles (livret officiel)"
    if name.startswith("synth"):
        return "Arabe / anglais / mixte"
    return "Registre spécimen (photos simulées)"


def _sample_path(name: str) -> Path:
    for d in SAMPLE_DIRS:
        p = d / name
        if p.exists() and p.parent == d:
            return p
    raise HTTPException(404, "unknown sample")


def main() -> None:
    import argparse

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data", type=Path, default=Path(os.environ.get("DAYONE_DEVICE_DIR", "artifacts/device")))
    ap.add_argument("--pin", default=os.environ.get("DAYONE_PIN", "2468"))
    ap.add_argument("--midwife", default="sf-amina")
    ap.add_argument("--token", default="token-sf-amina")
    ap.add_argument("--server", default="http://127.0.0.1:8100")
    ap.add_argument("--offline", action="store_true", help="start with the network switched off")
    args = ap.parse_args()
    device = Device(args.data, args.pin, args.midwife, args.token, args.server, Network(online=not args.offline))
    uvicorn.run(create_app(device), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
