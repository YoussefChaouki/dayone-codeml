# Design choices

Each section states the decision, why, and what was measured or rejected. Numbers marked
*exploration* come from the calibration split (patients 1-5) during development; the
reported results are in [RESULTS.md](RESULTS.md) (test split, patients 6-10).

## 1. Start from the registry, not from OCR

The specimen is a fixed form ("Fiche de surveillance de la grossesse et du post-partum").
Every field is declared in [`forms/layout.py`](../src/dayone/forms/layout.py): page, section,
FR/EN label, kind (text or tick box), value type, unit, plausibility range, closed
vocabulary, single/multi-choice group, table row/column, and its position on the
reference page (PDF points).

| Page | Fields | Text | Tick boxes |
|---|---|---|---|
| Cover / facility | 21 | 5 | 16 |
| Identification and history | 76 | 67 | 9 |
| Current pregnancy (9 visits x 30 rows) | 280 | 274 | 6 |
| Delivery | 34 | 10 | 24 |
| Early postpartum — mother / newborn | 51 / 44 | 11 / 13 | 40 / 31 |
| Late postpartum — mother / newborn | 51 / 44 | 11 / 13 | 40 / 31 |
| **Total** | **601** | | |

Value types: `date` (ISO), `bp` ("120/80", cmHg "12/8" converted), `int`, `float`,
`gest_age` (weeks, "SA"), `bool`, `enum` (closed vocabularies with FR/EN/AR surface forms:
"Neg", "Négatif", "Negative", "سلبي" → `negative`), `code`, free `text`.

**Not in the schema, on purpose**: patient name, husband's name, national ID (CIN),
phone, address (direct identifiers) and professions (not needed). Their zones are
listed separately (`PII_ZONES`) to be blacked out.

## 2. Status model: missing information is a state

| Status | Meaning | Who sets it |
|---|---|---|
| `KNOWN` (CONNU) | value trusted: confidence ≥ τ, or confirmed/entered by the midwife | pipeline / midwife |
| `NEEDS_REVIEW` (À_RÉVISER) | a value was read but confidence < τ or a consistency rule failed | pipeline |
| `ILLEGIBLE` (ILLISIBLE) | ink is present but could not be read | pipeline / midwife |
| `NOT_PROVIDED` (NON_FOURNI) | the field is blank on paper | pixel check (no ink) / midwife |
| `NOT_APPLICABLE` (NON_APPLICABLE) | a dash is written, or the field is logically not applicable (cesarean indication after a vaginal birth, previous-delivery columns beyond the parity, vaccine date when not vaccinated…) | reader / rules / midwife |
| `UNKNOWN` (INCONNU) | "?", "NSP", "inconnu" written, or the midwife says it is unknown | reader / midwife |

Tick boxes: an unticked box is `KNOWN false` (the paper encodes "no" that way); a fill
ratio in the ambiguous band is `NEEDS_REVIEW`; two ticks in a single-choice group are
flagged.

Every value also keeps its provenance: `source` (`ocr`, `checkbox`, `ink`, `rule`,
`confirmed`, `corrected`, `manual`), page id, raw text, alternatives, flags and an audit
trail of human decisions.

## 3. Extraction pipeline

```
photo ─► quality gate ─► page outline ─► page type + registration ─► PII masking ─► (stored, encrypted)
                                                │
                          ┌─────────────────────┴───────────────────────┐
                    tick boxes (fill)      blank fields (no ink)     inked text fields
                          │                       │                       │
                          │                       │        qwen3.5:9b (prompted with label + format)
                          │                       │        glm-ocr (independent second reader)
                          └──────────► parse/normalise ► consistency rules ► calibrated confidence ► status
```

Everything left of the OCR box is classical computer vision and runs on the phone,
offline. Decisions and what they replaced:

* **Registration in two stages.** Feature matching alone (SIFT + RANSAC on the whole
  photo) failed on degraded captures of the visits table: the table is repetitive and
  header/footer are shared by every page, so a wrong homography still had 80+ inliers
  (exploration: 22/40 correct on severe captures). We now (1) segment the sheet from
  the background — on brightness *and* on the Lab a* channel, because the pink paper
  survives shadows better than brightness — and rectify its four corners, (2) refine with
  SIFT matches constrained to stay within 4 % of the page width of their rectified
  position, (3) refine densely with ECC, and (4) verify every candidate page type by
  correlating the warped capture with the whole blank template. Result: 320/320 pages
  correctly classified across clean/mild/medium/severe (exploration), median
  registration error 1-2 pt, ≈ 0.5 s per page on CPU.
* **Tick boxes** are read from pixels: the boxes are hand-drawn and each one is shifted by
  1-2 pt, so the square is first located by matching a hollow-square kernel near its
  expected position, then its interior fill is measured. Unticked boxes fill ≤ 0.09 even
  on severe captures, ticked ones ≥ 0.09 (median 0.5-0.8).
* **Blank fields** are decided from pixels: ink that is not explained by the printed
  template. A blank field costs no model call and is `NOT_PROVIDED` without any AI.
* **Reading** — models compared on the same field crops (*exploration*, medium captures,
  430 handwritten fields):

  | Reader | Accuracy | Time / field |
  |---|---|---|
  | glm-ocr, raw crop | 77.4 % | 0.09 s |
  | glm-ocr, contrast-enhanced crop | 66.0 % | 0.10 s |
  | **qwen3.5:9b, prompted with the field label and expected format** | **91.4 %** | 0.8 s |

  qwen3.5 also reads Arabic handwriting (glm-ocr does not). Enhancement (flat-field,
  line removal, contrast stretch) *hurt* both models and was dropped. glm-ocr is kept as a
  second, independent reader: agreement between two different models is a strong
  correctness signal, and its reading is offered as an alternative.
* **Parsing** turns text into canonical typed values (dates, BP, numbers with units,
  Eastern-Arabic digits, FR/EN/AR vocabularies with fuzzy matching). The same parser
  normalises ground truth and predictions.
* **Consistency rules** ([`validators.py`](../src/dayone/extraction/validators.py)) never
  change a value; they flag it: EDD = LMP + 280 d, post-term date = EDD + 7 d,
  gestational age vs visit date and LMP, chronological visits, implausible weight jumps
  between visits, gravidity ≥ parity, implausible BP, several ticks in a single-choice group.
  No clinical interpretation (no risk score, no triage: out of scope).

## 4. Confidence and when to ask

A VLM's own confidence is not calibrated. The confidence of a field is a logistic
regression over: mean/min token log-probability of the primary reader, agreement with the
second reader (after normalisation), whether the text parsed into the expected type,
distance to the closest vocabulary entry, number of rule flags, out-of-range flag, amount
of ink, registration similarity, capture sharpness, script (Arabic or not) and value type.
It is fitted on the calibration split only (`make calibrate`) and stored as JSON weights
(no pickled object is ever loaded).

The acceptance threshold τ is chosen **before** looking at test data, by a pre-registered
rule ([EVALUATION.md](EVALUATION.md)): the smallest τ whose silent error rate on the
calibration split is ≤ 2 %. Below τ, the agent asks the midwife — that is the only
mechanism that decides follow-up questions.

## 5. Conversation

The agent ([`device/agent.py`](../src/dayone/device/agent.py)) is a deterministic state
machine — no LLM in the dialogue — so it runs offline on the phone, cannot hallucinate,
and its behaviour is testable. Principles:

* **Zero change to the paper workflow**: the midwife fills the registry as today and takes
  photos; the agent never asks her to write differently. The registry code she already
  writes ("N° de la fiche") is the linking key.
* **Say when unsure, show why**: "Je lis *109/74*, mais je ne suis pas sûre (confiance
  62 %). Autre lecture possible : 104/74. Pourquoi : deux lectures différentes." Buttons:
  confirm, the alternative, edit, show the image crop, retake the photo. What is written
  is shown next to how it was understood (« Lycée » → Secondaire).
* **Review cost proportional to doubt**: doubtful fields one by one, confident fields
  summarised and confirmed in bulk (with a numbered detail to fix any one).
* **Manual entry of every field** when the AI is unavailable or the page is not recognised
  (special answers: *vide*, *illisible*, *inconnu*, *-*, *fin*).
* **Multi-page sessions**: pages of one registry form one record; a page photographed
  twice in a session replaces the previous photo; re-photographing a registry already
  registered shows the field-by-field differences and lets the midwife choose.
* Buttons follow WhatsApp limits (≤ 3 reply buttons, otherwise a list), so the same
  engine drives the real WhatsApp channel.
* French by default, English with *language en*; values accepted in FR/EN/AR.

## 6. Offline-first

* **Encrypted local store** (SQLite, WAL, `synchronous=FULL`): images, values, the
  patient index and the conversation are AES-256-GCM blobs; the key is derived from the
  midwife's PIN with scrypt; the record id is bound as associated data.
* **Outbox**: every network action is a persistent job written in the same transaction as
  the state change that requires it. Jobs are idempotent on the server (page id; record
  id + version), so a lost request, a lost response or a crash between the two is retried
  safely. On start-up, `recover()` re-creates any job a crash could have prevented.
* **Explicit lifecycle** ([LIFECYCLE.md](LIFECYCLE.md)) with an append-only history.
* Tested with fault injection and a property-based test (random sequences of network
  on/off, lost requests, lost responses, server errors and app crashes): every record
  ends synchronised or in an explicit failure state, and no page or record is duplicated.

## 7. Patient linking

Key: the code written on the registry, compared after OCR-confusion normalisation
(O/0, I/1, S/5, B/8, Z/2) with an edit-distance tolerance. Candidates are scored with
non-identifying attributes (age, LMP, EDD, gravidity/parity, province); conflicts are
shown. Any candidate scoring ≥ 0.35 must be decided by the midwife:
**[Patiente 1] [Patiente 2] [Aucune, créer] [Je ne sais pas]** — "Je ne sais pas" parks
the record (`MANUAL_REVIEW_REQUIRED`) without creating anything. Internal ids are random
uuid4. The server also reports profiles sharing a code across phones (possible duplicate)
for the supervisor.

## 8. Privacy

* Direct identifiers are never extracted (not in the schema), and their zones are blacked
  out on the photo **on the phone, before the photo is encrypted and stored**. The stored
  "original image" is this redacted photo. A page whose layout is not recognised is not
  stored at all (its identifier zones cannot be located); the midwife can type it in.
* Free-text values are scrubbed of phone and ID-number patterns.
* Original images on the server are encrypted at rest and served by role: the midwife who
  captured them and supervisors; other midwives and epidemiologists get 403; every
  access is audited.
* Aggregates for epidemiology suppress any cell under 5 women.
* All AI runs locally (Ollama); nothing is sent to a third party. The WhatsApp channel,
  which would send messages through Meta, is off unless explicitly configured.

## 9. Assumptions made without the organisers' answers

| Question | Assumption | Consequence |
|---|---|---|
| Format of the hidden test set | Photos of pages of the specimen layout (same form, new values), possibly degraded | Template registration is the main path; the evaluation simulates field captures |
| Cloud APIs allowed? | No: 100 % local models | qwen3.5:9b + glm-ocr through Ollama |
| "Keep the original image" vs "never store identifiers" | The original is the photo with identifier zones blacked out on the phone | Unrecognised layouts are not stored |
| Which code links visits | The "N° de la fiche" written by the midwife (asked if missing) | The midwife can type it during matching |
| Where the AI runs | A district-level machine reached when the phone has network | The phone does everything else offline |
