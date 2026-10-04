"""WhatsApp Business Platform (Cloud API) channel — same agent, real WhatsApp.

Disabled by default: the prototype is 100 % local, and WhatsApp means Meta's servers see
the messages. Enable it only with synthetic data, by setting the environment variables
listed in ``WhatsAppConfig.from_env`` (see docs/WHATSAPP.md).

Translation rules (Cloud API limits, Graph API v25.0):
* agent message with <= 3 buttons -> interactive *reply buttons* (title <= 20 chars, body <= 1024);
* 4 to 10 buttons -> interactive *list* (row title <= 24 chars, description <= 72);
* text only -> text message (<= 4096 chars); images (field crops) are not sent by default.
Incoming: text, button_reply / list_reply (-> button event), image (-> downloaded bytes).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

log = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com/v25.0"


@dataclass
class WhatsAppConfig:
    token: str
    phone_number_id: str
    verify_token: str
    app_secret: str
    allowed_senders: dict[str, str] = field(default_factory=dict)  # WhatsApp number -> midwife id
    send_images: bool = False

    @classmethod
    def from_env(cls) -> WhatsAppConfig | None:
        token = os.environ.get("DAYONE_WHATSAPP_TOKEN")
        if not token:
            return None
        senders = dict(pair.split("=", 1) for pair in os.environ.get("DAYONE_WHATSAPP_SENDERS", "").split(",") if "=" in pair)
        return cls(
            token,
            os.environ["DAYONE_WHATSAPP_PHONE_ID"],
            os.environ["DAYONE_WHATSAPP_VERIFY_TOKEN"],
            os.environ["DAYONE_WHATSAPP_APP_SECRET"],
            senders,
            os.environ.get("DAYONE_WHATSAPP_SEND_IMAGES") == "1",
        )


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def to_cloud_payloads(to: str, message: dict) -> list[dict]:
    """Agent message -> Cloud API message bodies."""
    text = message.get("text") or " "
    buttons = message.get("buttons") or []
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to}
    if not buttons:
        return [base | {"type": "text", "text": {"body": _cut(text, 4096), "preview_url": False}}]
    if len(text) > 1024:  # interactive bodies are limited: full text first, then the buttons
        head = base | {"type": "text", "text": {"body": _cut(text, 4096), "preview_url": False}}
        return [head] + to_cloud_payloads(to, message | {"text": "👇"})
    if len(buttons) <= 3:
        return [
            base
            | {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": _cut(text, 1024)},
                    "action": {
                        "buttons": [{"type": "reply", "reply": {"id": b["id"], "title": _cut(b["title"], 20)}} for b in buttons]
                    },
                },
            }
        ]
    rows = [
        {"id": b["id"], "title": _cut(b["title"], 24), **({"description": _cut(b["title"], 72)} if len(b["title"]) > 24 else {})}
        for b in buttons[:10]
    ]
    return [
        base
        | {
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": _cut(text, 4096)},
                "action": {"button": "Choisir", "sections": [{"title": "Options", "rows": rows}]},
            },
        }
    ]


def parse_webhook(body: dict) -> list[tuple[str, dict]]:
    """Cloud API webhook -> [(sender number, agent event)]. Images carry a ``media_id`` to download."""
    out = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                sender, typ, mid = msg.get("from"), msg.get("type"), msg.get("id")
                if typ == "text":
                    out.append((sender, {"type": "text", "text": msg["text"]["body"], "message_id": mid}))
                elif typ == "interactive":
                    inter = msg["interactive"]
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    out.append((sender, {"type": "button", "id": reply.get("id"), "title": reply.get("title"),
                                         "message_id": mid}))
                elif typ == "image":
                    out.append((sender, {"type": "image", "media_id": msg["image"]["id"], "text": "📷 photo",
                                         "message_id": mid}))
    return out


def valid_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


class CloudClient:
    def __init__(self, cfg: WhatsAppConfig, http: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.http = http or httpx.Client(timeout=30.0)
        self.headers = {"Authorization": f"Bearer {cfg.token}"}

    def send(self, payload: dict) -> None:
        r = self.http.post(f"{GRAPH}/{self.cfg.phone_number_id}/messages", json=payload, headers=self.headers)
        r.raise_for_status()

    def upload_image(self, data: bytes) -> str:
        """Upload a JPEG to Meta's media store, return its media id."""
        r = self.http.post(f"{GRAPH}/{self.cfg.phone_number_id}/media", headers=self.headers,
                           data={"messaging_product": "whatsapp", "type": "image/jpeg"},
                           files={"file": ("image.jpg", data, "image/jpeg")})
        r.raise_for_status()
        return r.json()["id"]

    def download(self, media_id: str) -> bytes:
        meta = self.http.get(f"{GRAPH}/{media_id}", params={"phone_number_id": self.cfg.phone_number_id}, headers=self.headers)
        meta.raise_for_status()
        data = self.http.get(meta.json()["url"], headers=self.headers)
        data.raise_for_status()
        return data.content


def deliver(cfg: WhatsAppConfig, client: CloudClient, to: str, messages: list[dict], image_bytes=None) -> None:
    """Send agent messages to a WhatsApp number (images only when explicitly allowed)."""
    for msg in messages:
        if msg.get("role") == "user":
            continue
        if msg.get("image"):
            data = image_bytes(msg["image"]) if (cfg.send_images and image_bytes) else None
            if data:
                # the image carries the details (e.g. "identifiers masked"), the message keeps the main line
                head, _, rest = (msg.get("text") or "").partition("\n")
                media_id = client.upload_image(data)
                client.send({"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                             "type": "image", "image": {"id": media_id, "caption": _cut(rest or head, 1024)}})
                if not msg.get("buttons") and not rest:
                    continue
                msg = msg | {"text": head}
            else:
                msg = msg | {"text": (msg.get("text") or "") + "\n(image visible dans l'application)"}
        for payload in to_cloud_payloads(to, msg):
            client.send(payload)


def router(cfg: WhatsAppConfig, agent_for, client: CloudClient | None = None, image_bytes=None) -> APIRouter:
    """``agent_for(midwife_id)`` returns the Agent of that midwife; ``image_bytes(url)`` resolves agent images."""
    client = client or CloudClient(cfg)
    r = APIRouter()
    seen: dict[str, None] = {}  # ids of messages already handled (insertion-ordered, bounded)

    @r.get("/whatsapp/webhook", response_class=PlainTextResponse)
    def verify(request: Request) -> str:
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == cfg.verify_token:
            return q.get("hub.challenge", "")
        raise HTTPException(403)

    @r.post("/whatsapp/webhook")
    async def receive(request: Request) -> dict:
        raw = await request.body()
        if not valid_signature(cfg.app_secret, raw, request.headers.get("X-Hub-Signature-256")):
            log.warning("whatsapp webhook REFUSED: bad signature (check DAYONE_WHATSAPP_APP_SECRET)")
            raise HTTPException(401, "bad signature")
        events = parse_webhook(json.loads(raw))
        log.warning("whatsapp webhook received: %d message(s)", len(events))
        # reading a photo takes seconds: never block the web server's event loop
        await run_in_threadpool(handle_events, events)
        return {"ok": True}

    def handle_events(events: list[tuple[str, dict]]) -> None:
        for sender, event in events:
            mid = event.pop("message_id", None)
            if mid is not None and mid in seen:  # Meta re-delivers webhooks: handle each message once
                continue
            midwife = cfg.allowed_senders.get(sender)
            if midwife is None:  # unknown number: never processed, never stored
                log.warning("whatsapp message from an unregistered number ignored (ends with %s)", (sender or "")[-4:])
                continue
            if event["type"] == "image":
                event["data"] = client.download(event.pop("media_id"))
            deliver(cfg, client, sender, agent_for(midwife).handle(event)[1:], image_bytes)
            if mid is not None:  # only once handled: a failure lets Meta's re-delivery try again
                seen[mid] = None
                while len(seen) > 5000:
                    seen.pop(next(iter(seen)))

    return r
