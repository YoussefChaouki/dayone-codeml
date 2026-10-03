# Connecting the real WhatsApp (bonus — after the 100 % local validation)

The agent is channel-agnostic: the browser simulator and the WhatsApp Business Platform
(Cloud API) drive the same engine. The adapter is
[`channels/whatsapp.py`](../src/dayone/channels/whatsapp.py) and is **off by default**.

> ⚠️ With the real WhatsApp, messages (and photos) go through Meta's servers. Use it only
> with the synthetic specimen data. Field-crop images are not sent unless
> `DAYONE_WHATSAPP_SEND_IMAGES=1`.

## What changes in the architecture

| | Simulated phone app (default) | Real WhatsApp |
|---|---|---|
| Where the agent runs | on the phone | on a gateway (district server) |
| Offline capture | yes, encrypted store on the phone | WhatsApp itself queues the photos until the network is back |
| Manual entry offline | yes | no (needs the network to reach the gateway) |
| Local encryption | phone store (PIN-derived key) | gateway store; phone side is WhatsApp's |

The simulator demonstrates the full offline-first design required by the challenge; the
WhatsApp channel shows the same conversation in the midwives' everyday tool.

## Setup (Meta test number)

1. Create a Meta app with the WhatsApp product, get a **test phone number id** and a
   temporary **access token**; add your own number as a test recipient.
2. Expose the phone app publicly over HTTPS (e.g. a tunnel to `127.0.0.1:8000`).
3. Start it with:

```bash
export DAYONE_WHATSAPP_TOKEN=...            # access token
export DAYONE_WHATSAPP_PHONE_ID=...         # phone number id
export DAYONE_WHATSAPP_VERIFY_TOKEN=choose-a-secret
export DAYONE_WHATSAPP_APP_SECRET=...       # to check X-Hub-Signature-256
export DAYONE_WHATSAPP_SENDERS="2126XXXXXXXX=sf-amina"   # allowed numbers -> midwife id
make demo
```

4. In the Meta app, set the webhook URL to `https://<tunnel>/whatsapp/webhook` with the
   verify token above and subscribe to `messages`.

Message mapping: ≤ 3 buttons → interactive reply buttons (titles cut at 20 characters),
4-10 buttons → interactive list, otherwise plain text. Messages from numbers not in
`DAYONE_WHATSAPP_SENDERS` are ignored and never stored. Verified against the Cloud API
documentation (Graph API v25.0) on 2026-10-03; covered by `tests/test_whatsapp_channel.py`
(payload shapes, webhook parsing, signature check). Not tested against Meta's live servers.
