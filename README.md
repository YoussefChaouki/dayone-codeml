# DayOne — a midwife, a phone and an AI

[![CI](https://github.com/YoussefChaouki/dayone-codeml/actions/workflows/ci.yml/badge.svg)](https://github.com/YoussefChaouki/dayone-codeml/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

🇫🇷 [Version française](README.fr.md) · Hackathon project (CodeML, 3–4 October 2026)

▶️ **[Demo video (3 min 40, real WhatsApp)](https://github.com/YoussefChaouki/dayone-codeml/releases/download/v1.0/DayOne-demo-x1.25.mp4)**
— offline capture with identifiers masked, connectivity back, AI reading with an uncertain field
reviewed, patient code confirmed, and the patient-match decision on a second visit.

**An offline-first, WhatsApp-style agent that turns photos of the paper maternal registry
into a structured, verified and longitudinal digital record — 100 % local AI, nothing sent
to a third party.**

> **In short** — The midwife keeps filling in her paper registry. She photographs it with her
> phone, even with no network: the photo is checked, the page recognised, personal data masked,
> and everything encrypted on the phone. When the network returns, *local* AI models read each
> field; the agent shows her what it doubts (with the reason, another possible reading and the
> image), she confirms or corrects, then attaches the visit to the right patient. Nothing is
> lost, nothing is duplicated, and no decision is taken in her place.

## Results (test split, held-out patients)

Full tables: [docs/en/RESULTS.md](docs/en/RESULTS.md). Protocol, pre-registered before the
numbers: [docs/en/EVALUATION.md](docs/en/EVALUATION.md).

| Test split: 40 pages of 5 held-out patients (same 5 handwriting fonts as calibration) | |
|---|---|
| **Extraction accuracy**, handwritten fields, before any review (n = 3480; unaltered renders + simulated mild + medium photos) | **93.4 %** (95 % CI 91.5–95.6) |
| … medium photos only (WhatsApp-like; FR + AR + EN) | 87.8 % (95 % CI 83.9–91.0) |
| … unaltered render / mild / medium (French specimen) | 99.3 % / 99.2 % / 89.1 % |
| … medium photos: Arabic-script / Latin-script fields | 69.1 % / 93.5 % |
| … medium photos in 2 handwriting fonts never seen in development | 83.8 % (95 % CI 80.5–88.5) (same pages in the original fonts: 89.1 %) |
| Handwritten fields accepted without any question | 93.4 % |
| **Wrong among the values accepted without a question** | **1.9 %** (95 % CI 1.3–2.6); medium photos 3.3 %; Arabic-script fields 14.3 % |
| Tick boxes / blank fields recognised | 99.8 % / 4724 of 4725 |
| Page type recognised | 100.0 % (thresholds set on all pages, optimistic) |

Acceptance threshold τ = 0.50, chosen on the calibration patients 1-5 (it is the floor of the search grid; without the floor the rule gives τ = 0.42 and 2.2 % on test). The 2 % silent-error target is **not demonstrated** on test (its interval crosses 2 %). Full tables, calibration and risk–coverage: [docs/en/RESULTS.md](docs/en/RESULTS.md).

## What it does (challenge tasks → where)

| Task | Implementation |
|---|---|
| 1. Field schema & status model | 601 typed fields over 8 page types, 6 statuses — [`forms/layout.py`](src/dayone/forms/layout.py), [`schema.py`](src/dayone/schema.py), [docs/en/DESIGN.md §1-2](docs/en/DESIGN.md) |
| 2. Extraction with status + confidence per field, evaluated | registration + pixel reading + local OCR (qwen3.5:9b, glm-ocr) + rules + calibrated confidence — [`extraction/`](src/dayone/extraction), evaluation in [`evaluation/`](src/dayone/evaluation) |
| 3. Conversational review (confirm / edit / retake, follow-up questions, manual entry) | deterministic agent, FR/EN — [`device/agent.py`](src/dayone/device/agent.py) |
| 4. Offline mode | encrypted store, persistent outbox, idempotent sync, crash recovery — [`device/store.py`](src/dayone/device/store.py), [`device/sync.py`](src/dayone/device/sync.py) |
| 5. Record lifecycle | explicit state machine with history — [docs/en/LIFECYCLE.md](docs/en/LIFECYCLE.md) |
| 6. Patient linking | code (always confirmed) + OCR-confusion tolerance + non-identifying attributes, never auto-create — [`linking.py`](src/dayone/linking.py) |
| Continuous profile | *📁 Dossiers* / *dossier <code>*: the patient's longitudinal record (visits with date, GA, weight, BP; tests; delivery; postpartum) — [`records.py`](src/dayone/records.py) |
| 7. Multi-page sessions & re-digitisation | one record per registry, field-by-field diff — [`records.py`](src/dayone/records.py) |
| 8. Original image kept | redacted photo, encrypted, linked to record id, capture date, midwife id, processing status; role-based access with audit — [`server/app.py`](src/dayone/server/app.py) |
| Bonus | quality check before accepting a capture, Arabic/multilingual robustness (evaluated), anonymised dashboard, FR/EN interface, **real WhatsApp** (Cloud API, tested in real conditions — [docs/en/WHATSAPP.md](docs/en/WHATSAPP.md)) |

## Architecture

```mermaid
flowchart LR
    subgraph Phone["Phone — works offline"]
        UI["WhatsApp-style chat"] --> Agent["Conversation agent<br/>(state machine, FR/EN)"]
        Agent --> Q["Quality gate<br/>page recognition<br/>PII masking"]
        Agent --> Store[("Encrypted store<br/>AES-GCM, PIN key")]
        Store --> Outbox["Outbox<br/>idempotent jobs"]
    end
    subgraph Server["District server — local AI"]
        API["Pages / records API"] --> Worker["Extraction pipeline"]
        Worker --> Models["Ollama: qwen3.5:9b + glm-ocr"]
        API --> Registry[("Registry<br/>images encrypted at rest")]
        Registry --> Dash["Anonymised dashboard"]
    end
    Outbox <-->|when connectivity returns| API
```

Two processes: the **phone** (simulated in the browser, port 8000) and the **processing
server** (port 8100). Only AI reading and synchronisation need the network; capture,
quality check, page recognition, PII masking, encrypted storage, review of already-read
records, manual entry and patient matching all run on the phone.

## Quick start

Requirements: macOS/Linux, Python 3.12 with [uv](https://docs.astral.sh/uv/),
[Ollama](https://ollama.com) ≥ 0.19 running locally, ~10 GB of disk for the models.
The organisers' data must be in `data/` (`data/Paper Registry/…`, `data/maternal_registry_synthetic.csv`).

```bash
make install        # Python environment
make models         # ollama pull qwen3.5:9b && ollama pull glm-ocr
make prepare        # ground truth from the specimen PDF + blank templates (≈ 30 s)
make test           # 87 tests, no AI model needed (≈ 40 s)
make demo-offline   # phone http://127.0.0.1:8000 (starts offline) + server http://127.0.0.1:8100/dashboard
```

Demo walkthrough: [docs/en/DEMO.md](docs/en/DEMO.md). Real WhatsApp:
[docs/en/WHATSAPP.md](docs/en/WHATSAPP.md) (`make demo-whatsapp` + `make tunnel`).

The tests and the evaluation read the organisers' specimen registry, which is not redistributed
in this repository; CI therefore runs the linter and test collection only.

Reproduce the evaluation:

```bash
make dataset        # 416 simulated captures (seeded), incl. Arabic / English / mixed pages
make eval           # pipeline on every capture — resumable, ≈ 3 h on an Apple M4 Pro
make calibrate      # confidence model + acceptance threshold, calibration split only
make unseen         # supplementary experiment: 2 handwriting fonts never seen (≈ 30 min)
make report         # docs/RESULTS.md (French) and the results block of README.fr.md
```

## Repository layout

```
src/dayone/
  schema.py              statuses, field specs, extraction results
  forms/                 registry layout (601 fields, PII zones) and blank templates
  extraction/            quality gate, registration, pixel reading, OCR, parsing, rules, confidence, pipeline
  records.py linking.py pii.py
  device/                phone: encrypted store, lifecycle, sync/outbox, agent, i18n, web simulator
  server/                processing server, role-based image access, dashboard
  channels/whatsapp.py   WhatsApp Cloud API adapter (off by default)
  evaluation/            ground truth, capture simulator, multilingual pages, dataset, metrics, report
tests/                   87 tests (offline chaos, end-to-end conversation, pixel pipeline, WhatsApp, ...)
docs/                    DESIGN, LIFECYCLE, EVALUATION (protocol), RESULTS, DEMO, WHATSAPP (French)
docs/en/                 the same documents in English
```

Code, comments and commit messages are in English; the conversational agent speaks French
(and English: `language en`), and the documentation exists in both languages.

## Known limitations

* **Arabic is the weak point** (medium photos, font-rendered — real handwriting is harder):
  fields written in Arabic letters 69.1 % accuracy with 14.3 % of the values accepted without a
  question wrong; values written in **Eastern-Arabic digits (٠-٩) are essentially unreadable**
  (2 % accuracy) and most of them are accepted wrongly — do not deploy with Eastern-Arabic
  digits before adding a dedicated reader. Latin-script fields: 93.5 %.
* **New handwriting costs accuracy**: the test patients reuse the calibration's five
  handwriting fonts; on two fonts never seen in development, medium-photo accuracy drops from
  89.1 % to 83.8 % and silent errors rise from 2.9 % to 4.4 % (same pages).
* **The 2 % silent-error target is not demonstrated** on the test patients (1.9 %, 95 % CI
  1.3-2.6 %; 3.3 % on medium photos).
* **Forced bad photos are dangerous**: on `severe` captures the quality gate asks for a
  retake 97.5 % of the time; if the midwife forces one through anyway, 62.5 % of the values
  accepted without a question are wrong. Next step (not evaluated): accept nothing
  automatically on a capture the quality gate rejected.

* **One layout.** Templates exist for the specimen booklet only. The organisers' real
  photos of the official booklet (`1-*.jpg`) have a different layout: they are recognised
  as unknown pages and *not stored* (their identifier zones cannot be masked); the midwife
  can type the page in. Supporting a new booklet = one blank template + its field
  coordinates (`forms/layout.py`); the rest of the pipeline is layout-agnostic.
* **Simulated field conditions.** The provided PNGs are clean renders; captures are
  simulated (perspective, blur, shadows, low light, JPEG). Arabic and English
  handwriting is simulated with fonts.
* **Some development thresholds saw all pages** (registration, tick boxes, quality gate): see
  [EVALUATION.md §6](docs/en/EVALUATION.md). Confidence and τ were fitted on the calibration
  split only.
* **Speed.** ≈ 0.8 s per handwritten field on an M4 Pro: a dense visits page takes 1-2 min.
  Processing is asynchronous, so the midwife is not blocked, but a weaker server would be
  slower.
* **Real WhatsApp** moves the agent to a gateway: capture still works offline (WhatsApp
  queues photos), manual entry does not. The Meta test token is temporary.
* **Security is a prototype**: demo tokens instead of an identity provider, the PIN is fixed
  for the demo, no key rotation or remote wipe.
* **Gaps that come from the specimen form**: it has no **hepatitis C** row (it records HIV,
  syphilis and Ag HBs; hepatitis C appears in the dashboard only, from the reference CSV), no
  **temperature** in the antenatal visits table (postpartum only), and no **village** (region
  and province are recorded).
* **Out of scope by design**: no risk prediction, triage, diagnosis or treatment advice.
  Consistency rules only check that what is written is internally consistent.
