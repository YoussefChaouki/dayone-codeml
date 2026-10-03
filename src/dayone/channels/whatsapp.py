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
import logging
import os
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, HTTPException, Request
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
                sender, typ = msg.get("from"), msg.get("type")
                if typ == "text":
                    out.append((sender, {"type": "text", "text": msg["text"]["body"]}))
                elif typ == "interactive":
                    inter = msg["interactive"]
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    out.append((sender, {"type": "button", "id": reply.get("id"), "title": reply.get("title")}))
                elif typ == "image":
                    out.append((sender, {"type": "image", "media_id": msg["image"]["id"], "text": "📷 photo"}))
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

    def download(self, media_id: str) -> bytes:
        meta = self.http.get(f"{GRAPH}/{media_id}", params={"phone_number_id": self.cfg.phone_number_id}, headers=self.headers)
        meta.raise_for_status()
        data = self.http.get(meta.json()["url"], headers=self.headers)
        data.raise_for_status()
        return data.content


def router(cfg: WhatsAppConfig, agent_for, client: CloudClient | None = None) -> APIRouter:
    """``agent_for(midwife_id)`` returns the Agent of that midwife."""
    client = client or CloudClient(cfg)
    r = APIRouter()

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
            raise HTTPException(401, "bad signature")
        for sender, event in parse_webhook(await request.json()):
            midwife = cfg.allowed_senders.get(sender)
            if midwife is None:  # unknown number: never processed, never stored
                log.warning("message from an unregistered number ignored")
                continue
            if event["type"] == "image":
                event["data"] = client.download(event.pop("media_id"))
            for msg in agent_for(midwife).handle(event)[1:]:
                if msg.get("image") and not cfg.send_images:
                    msg = msg | {"text": msg["text"] + "\n(image visible dans l'application)"}
                for payload in to_cloud_payloads(sender, msg):
                    client.send(payload)
        return {"ok": True}

    return r
