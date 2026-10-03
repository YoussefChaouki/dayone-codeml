# Demo script (≈ 5 minutes)

Covers the four required moments: offline capture, return of connectivity, review of an
uncertain field, match decision — plus crash recovery, privacy and the dashboard.

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
| 8 | Match: first time *➕ Créer la patiente*; then photograph `spec_p03_medium.jpg` + `spec_p01_medium.jpg` of the same patient again | Second time: **[Patiente 1] [Aucune, créer] [Je ne sais pas]** with reasons ("même code, DPA cohérente"); if values differ, the re-digitisation diff lets the midwife keep old or take new |
| 9 | Watch `ENREGISTRÉ` → `SYNCHRONISÉ` | "Fiche synchronisée avec le serveur ✔️" |
| 10 | Backstage "Image d'origine — accès par rôle" | author midwife ✅, other midwife ⛔ 403, supervisor ✅, epidemiologist ⛔ — all audited |
| 11 | Open http://127.0.0.1:8100/dashboard | Anonymised aggregates, cells < 5 shown as "<5" |
| 12 | Type `language en`, or `saisie` for manual entry | Bilingual interface; full manual entry without AI |

Timing: on an M4 Pro, a simple page is read in 10-25 s, the dense visits table in
1-2 min (≈ 280 cells). Readings are cached by crop, so replaying a demo is faster.
