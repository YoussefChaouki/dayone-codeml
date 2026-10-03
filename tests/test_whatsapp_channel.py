import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dayone.channels.whatsapp import WhatsAppConfig, parse_webhook, router, to_cloud_payloads, valid_signature


def test_three_buttons_become_reply_buttons_with_short_titles():
    msg = {
        "text": "Je lis 109/74",
        "buttons": [
            {"id": "f:confirm", "title": "✅ 109/74"},
            {"id": "f:alt:0", "title": "104/74"},
            {"id": "f:edit", "title": "✏️ Corriger une valeur très longue"},
        ],
    }
    (p,) = to_cloud_payloads("212600000000", msg)
    buttons = p["interactive"]["action"]["buttons"]
    assert p["interactive"]["type"] == "button" and len(buttons) == 3
    assert all(len(b["reply"]["title"]) <= 20 for b in buttons)


def test_more_than_three_buttons_become_a_list():
    msg = {"text": "Patiente ?", "buttons": [{"id": f"m:{k}", "title": f"Patiente {k}"} for k in range(4)]}
    (p,) = to_cloud_payloads("212600000000", msg)
    assert p["interactive"]["type"] == "list"
    assert len(p["interactive"]["action"]["sections"][0]["rows"]) == 4


def test_webhook_parsing():
    body = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": "2126", "type": "text", "text": {"body": "menu"}},
                                {
                                    "from": "2126",
                                    "type": "interactive",
                                    "interactive": {"type": "button_reply", "button_reply": {"id": "f:confirm", "title": "ok"}},
                                },
                                {"from": "2126", "type": "image", "image": {"id": "MEDIA1", "mime_type": "image/jpeg"}},
                            ]
                        }
                    }
                ]
            }
        ]
    }
    events = [e for _, e in parse_webhook(body)]
    assert events[0] == {"type": "text", "text": "menu"}
    assert events[1]["type"] == "button" and events[1]["id"] == "f:confirm"
    assert events[2]["media_id"] == "MEDIA1"


class FakeAgent:
    def handle(self, event):
        return [event, {"text": "Bonjour", "buttons": [{"id": "menu:new", "title": "Nouvelle fiche"}]}]


class FakeCloud:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)


def test_webhook_checks_signature_and_ignores_unknown_numbers():
    cfg = WhatsAppConfig("t", "pid", "verify", "secret", {"2126": "sf-amina"})
    cloud = FakeCloud()
    app = FastAPI()
    app.include_router(router(cfg, lambda midwife: FakeAgent(), client=cloud))
    c = TestClient(app)
    assert (
        c.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify", "hub.challenge": "42"}).text
        == "42"
    )
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {"from": "2126", "type": "text", "text": {"body": "menu"}},
                                    {"from": "9999", "type": "text", "text": {"body": "menu"}},
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()
    assert c.post("/whatsapp/webhook", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert valid_signature("secret", body, sig)
    assert (
        c.post(
            "/whatsapp/webhook", content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"}
        ).status_code
        == 200
    )
    assert len(cloud.sent) == 1 and cloud.sent[0]["to"] == "2126"  # the unknown number got nothing
