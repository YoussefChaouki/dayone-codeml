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
    assert events[0]["type"] == "text" and events[0]["text"] == "menu"
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


def test_redelivered_webhook_is_handled_once():
    cfg = WhatsAppConfig("t", "pid", "verify", "secret", {"2126": "sf-amina"})
    cloud = FakeCloud()
    app = FastAPI()
    app.include_router(router(cfg, lambda midwife: FakeAgent(), client=cloud))
    c = TestClient(app)
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.1", "from": "2126", "type": "text", "text": {"body": "menu"}}]}}]}]}).encode()
    headers = {"X-Hub-Signature-256": "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest(),
               "Content-Type": "application/json"}
    c.post("/whatsapp/webhook", content=body, headers=headers)
    c.post("/whatsapp/webhook", content=body, headers=headers)  # Meta retries the same delivery
    assert len(cloud.sent) == 1


class FakeCloudWithMedia(FakeCloud):
    def upload_image(self, data):
        self.sent.append({"uploaded": len(data)})
        return "MEDIA42"


def test_images_are_sent_only_when_allowed():
    from dayone.channels.whatsapp import deliver

    msg = [{"role": "agent", "text": "Zone lue", "image": "/api/crops/p/f", "buttons": []}]
    cloud = FakeCloudWithMedia()
    deliver(WhatsAppConfig("t", "pid", "v", "s", send_images=False), cloud, "2126", msg, lambda url: b"jpeg")
    assert len(cloud.sent) == 1 and cloud.sent[0]["type"] == "text" and "application" in cloud.sent[0]["text"]["body"]
    cloud = FakeCloudWithMedia()
    deliver(WhatsAppConfig("t", "pid", "v", "s", send_images=True), cloud, "2126", msg, lambda url: b"jpeg")
    assert cloud.sent[0] == {"uploaded": 4} and cloud.sent[1]["image"]["id"] == "MEDIA42"


def test_background_notifications_are_pushed(tmp_path, server, registrar):
    from dayone.device.app import Device
    from dayone.device.sync import Network

    device = Device(tmp_path / "phone", "2468", "sf-amina", "token-sf-amina", "http://unused", Network(online=True),
                    http=server, registrar=registrar, background=False)
    pushed = []
    device.push = pushed.extend
    rid = device.store.create_record("sf-amina", {"fields": {}})
    device._notify("synced", {"record_id": rid})
    assert pushed and "synchronisée" in pushed[0]["text"]
