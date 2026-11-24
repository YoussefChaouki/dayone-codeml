# Demo script

Two demonstrations cover the four required moments: offline capture, return of connectivity,
review of an uncertain field, match decision.

## A. Video on the real WhatsApp (the one submitted)

[Video (3 min 40)](https://github.com/YoussefChaouki/dayone-codeml/releases/download/v1.0/DayOne-demo-x1.25.mp4),
filmed on a phone ([WHATSAPP.md](WHATSAPP.md)). To show the queue, the gateway was started
**offline**:

```bash
set -a; . ./whatsapp.env; set +a; uv run python -m dayone.demo --offline --reset   # terminal 1
make tunnel                                                                          # terminal 2
```

then put back online about twenty seconds after the first record, with the network switch of the
backstage panel (http://127.0.0.1:8000, reachable locally only). Without `--offline`
(`make demo-whatsapp`), the record is read straight away.

The agent's messages are in French in the video (it also speaks English: `language en`).

| # | On the phone | What you see |
|---|---|---|
| 1 | `menu` → *Nouvelle fiche* → photo of the cover page | the photo comes back **with the name blacked out**: "Nom et identifiants masqués, photo chiffrée sur le téléphone" (name and identifiers masked, photo encrypted on the phone) |
| 2 | *Terminer* | "📴 Pas de réseau : la fiche est enregistrée et chiffrée… EN ATTENTE IA" (no network: the record is saved and encrypted, waiting for the AI) |
| 3 | (wait; network put back online backstage) | when the network returns, with nothing done on the phone: the record has been read, 21 fields, 20 read with confidence and 1 in doubt |
| 4 | *Vérifier la fiche* | health facility — the agent reads *CSy Ernanda* but is not sure (confidence 11 %), offers another reading and says why (two different readings) → *Corriger* → `CSU Ennahda` |
| 5 | *Tout confirmer* → code confirmation | the AI read `2026-63-007` (one digit lost); *Corriger le code* → `2026-163-007` → *Créer la patiente* → "Fiche synchronisée avec le serveur ✔️" |
| 6 | Second visit: same page, same review | **[Patiente 1] [Aucune, créer] [Je ne sais pas]** with the reason (same code, or code within one character) → *Patiente 1*; if values differ, the agent shows the differences to arbitrate (re-digitisation) |

## B. Local simulator (≈ 5 minutes, without WhatsApp)

```bash
ollama serve            # if not already running (models: make models)
make demo-offline       # = uv run python -m dayone.demo --offline   (add --reset for a clean start)
```

Open http://127.0.0.1:8000. Left: the midwife's WhatsApp-style chat. Right: the
"backstage" panel (network switch, faults, lifecycle, outbox, raw encrypted storage).

| # | Action | What to point at |
|---|---|---|
| 1 | Backstage shows **📴 Hors ligne**. Tap *📷 Nouvelle fiche*, then 📷 → pick `spec_p02_medium.jpg` (identification page) | "Page reçue … nom et identifiants masqués": the thumbnail shows CIN, address, phone and husband's name blacked out. Backstage: record `CAPTURÉ`, raw DB row is ciphertext |
| 2 | Add `spec_p01_medium.jpg` and `spec_p04_medium.jpg`, tap *Terminer* | "Pas de réseau : la fiche est enregistrée et chiffrée…", state `EN_ATTENTE_IA` |
| 3 | Optional: pick `spec_p41_severe.jpg` | Quality gate: "la photo est floue" → *Reprendre* / *Garder quand même* |
| 4 | Click **💥 Crash + redémarrage** | The app is rebuilt from the encrypted store; "3 tâche(s) en attente retrouvée(s)" |
| 5 | Switch the network **📶 En ligne** (optionally arm "réponse perdue" first) | Outbox empties: upload → server reads with the local models → `TRAITÉ_IA` → `À_RÉVISER`; a lost response is retried without duplicate |
| 6 | Tap *🔎 Vérifier maintenant* | Each doubtful field: value, confidence, *why* (e.g. "valeur hors des plages habituelles, deux lectures différentes"), alternative reading. Try *🖼️ Voir l'image* (the exact crop), then pick the alternative (e.g. birth weight read *36269 g* → *3626 g*) |
| 7 | *✅ Tout confirmer* | Summary of confident fields; `VALIDÉ` |
| 8 | Match: the agent first asks to confirm the code it read ("J'ai lu : 2026-…, est-ce bien le code ?"); then, the first time, *➕ Créer la patiente*; then photograph `spec_p03_medium.jpg` + `spec_p01_medium.jpg` of the same patient again | Second time: **[Patiente 1] [Aucune, créer] [Je ne sais pas]** with reasons ("même code, DPA cohérente"); if values differ, the re-digitisation diff lets the midwife keep old or take new |
| 9 | Watch `ENREGISTRÉ` → `SYNCHRONISÉ`, then *📁 Voir le dossier* (or type `dossier 2026-…`) | "Fiche synchronisée avec le serveur ✔️"; the patient's longitudinal record: visits (date, GA, weight, BP), tests, delivery, postpartum |
| 10 | Backstage "Image d'origine — accès par rôle" | author midwife ✅, other midwife ⛔ 403, supervisor ✅, epidemiologist ⛔ — all audited |
| 11 | Open http://127.0.0.1:8100/dashboard | Anonymised aggregates, cells < 5 shown as "<5" |
| 12 | Type `language en`, or `saisie` for manual entry | Bilingual interface; full manual entry without AI |

Timing: on an M4 Pro, a simple page is read in 10-25 s, the dense visits table in
1-3 min (≈ 280 cells). The server keeps no OCR cache (raw readings would sit in clear on disk), so a replay is read again.
