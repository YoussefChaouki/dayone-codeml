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

## Setup (Meta test number, about 20 min)

1. On developers.facebook.com: *Create app*, use case **"Connect with customers through
   WhatsApp"** (the WhatsApp product is added). In *WhatsApp > API Setup*: note the **test
   phone number id**, click *Generate access token* (temporary token), and add **your own
   WhatsApp number** as a recipient (a code confirms it).
2. In *App settings > Basic*: note the **App secret**.
3. `cp whatsapp.env.example whatsapp.env` and fill it in (git-ignored; never paste tokens in a
   chat or a commit).
4. Terminal 1: `make demo-whatsapp` (server + phone/gateway with the channel on).
   Terminal 2: `make tunnel` (after `brew install cloudflared`), note the
   `https://....trycloudflare.com` URL.
5. In *WhatsApp > Configuration > Webhook*: callback URL
   `https://....trycloudflare.com/whatsapp/webhook`, verify token = `DAYONE_WHATSAPP_VERIFY_TOKEN`,
   *Verify and save*, then subscribe to the **messages** field.
6. From your phone, send "menu" to the test number. Photos sent in WhatsApp go through the same
   pipeline; "record read" / "synchronised" notifications are pushed back to WhatsApp; with
   `DAYONE_WHATSAPP_SEND_IMAGES=1` the masked page thumbnail and field crops are sent too.

The test number can reply freely within 24 h of your last message (WhatsApp's service window).
The temporary token expires: regenerate it in *API Setup* if messages stop.

Message mapping: ≤ 3 buttons → interactive reply buttons (titles cut at 20 characters),
4-10 buttons → interactive list, otherwise plain text. Messages from numbers not in
`DAYONE_WHATSAPP_SENDERS` are ignored and never stored. Verified against the Cloud API
documentation (Graph API v25.0) on 2026-10-03; covered by `tests/test_whatsapp_channel.py`
(payload shapes, webhook parsing, signature check). Not tested against Meta's live servers.
