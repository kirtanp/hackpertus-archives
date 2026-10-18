# ArchAI – Deciphering Swiss archives with Apertus

Turns three open Swiss archive datasets into one **AI-ready, provenance-preserving** dataset and serves it through a
**cited Q&A demo**. Deterministic code does the reliable work; Apertus plans, enriches and answers, and a deterministic
validator has the final word on everything it produces.

| Dataset | Source | What we use |
|---|---|---|
| Stapfer-Enquête 1799 | [opendata.swiss](https://opendata.swiss/de/dataset/stapfer-enquete-schule-in-der-schweiz-1799) | 2,250 transcribed sheets, 92,524 answers, teachers/schools/places |
| Federal Consultations 1960–1991 | [opendata.swiss](https://opendata.swiss/de/dataset/federal-consultations-1960-1991) | 569 consultations, 858 archive references |
| Archive Database (OLR) | [opendata.swiss](https://opendata.swiss/de/dataset/swiss-archives-archive-database-of-the-swiss-federal-archives) | 4.5 M archive units + hierarchy, streamed from the 3.6 GB dump |

## Quick start
Everything the demo needs ships in `data/out/` (no raw dumps, no API key required).
```bash
git clone <this repo> && cd <repo>
make setup            # Python 3.12 venv + pinned requirements
make run              # http://127.0.0.1:8791
make test             # unit tests, no network
```
or with Docker: `docker compose up --build` → http://localhost:8791.

**Apertus (optional).** `cp .env.example .env` and set `APERTUS_API_KEY` (any OpenAI-compatible Apertus endpoint works;
default gateway `https://gateway.keera.ch/api/v1`, model `apertus-v1-5-70b`). Without a key the demo answers
extractively: the cited passages, no generated text. Never commit `.env`.

**What the demo does**
- Cited Q&A in German, French and English: Apertus rewrites the question → validated route → hybrid search → Apertus
  answer with `[n]` citations to archive signature, page and question number → deterministic citation and number checks.
- Map of all 2,249 geocoded schools on today's swisstopo map with a slider to the 1850s Dufour map; filters (language,
  confession, school type, pupils, name); cited schools highlighted.
- "Guess the school": read a teacher's own 1799 answers (place names masked) and find the village on the map.
- "Measured results": every number from `metrics.json`, in plain words, with its scope.

## Architecture
```
raw SQL/CSV ──► ingest (streaming SQL parser) ──► clean (markup → text + annotations, raw kept)
            ──► link (consultation refs → OLR units + archive path)
            ──► Apertus agent: plan tools ─► deterministic tools ─► compose ─► validate (grounding)
            ──► export records.jsonl/csv (one schema, provenance on every record) ──► metrics.json
            ──► Q&A: Apertus query rewrite (validated) + rule router → hybrid TF-IDF → Apertus answer with [n] citations
                 → citation + number check (extractive fallback)
any new table ──► onboard: profile → Apertus column mapping (validated) → transform → privacy screen → validate
```
Deterministic tools (`src/archai/tools.py`): `clean_markup`, `normalize_dates` (1799 month names: Hornung, Merz, …; French dates),
`lookup_place` (historical name → gazetteer id, today's municipality, WGS84 from LV03), `parse_signature`,
`lookup_archive_unit` (OLR), `extract_quantities` (pupils; fl, kr, Batzen, livres, sous…), `verify_in_source`.

Robustness: idempotent/resumable stages, run manifest with output hashes, retries + JSON repair + schema checks,
circuit breaker → deterministic-only output if Apertus is unreachable, schema-invalid records quarantined, unit tests.

## Rebuild from the raw data
Only needed to regenerate `data/out` from the opendata.swiss downloads (3.6 GB, not in this repository).
```bash
make setup
# raw files in data/raw/ (see links above); Apertus endpoint (any OpenAI-compatible):
mkdir -p ~/.config/archai && cat > ~/.config/archai/apertus.env <<'X'
APERTUS_BASE_URL=https://gateway.keera.ch/api/v1
APERTUS_API_KEY=...            # never commit
APERTUS_MODEL=apertus-v1-5-70b
X
PYTHONPATH=src python -m archai.pipeline --llm-sample 60 --agent-sample 30
PYTHONPATH=src python -m archai.qa "Was verdiente der Schulmeister in Fischenthal?"
```
Local alternative: llama.cpp server with `Colby/apertus-v1.5-8b-text-Q4_K_M-GGUF` on `http://127.0.0.1:8081/v1`.

## Outputs (`data/out/`)
- `records.jsonl` / `records.csv` – unified records: `text`, `dates`, `places` (ids + coordinates), `persons`, `quantities`,
  `archive_units`, `provenance` (signature, facsimile file, viewer URL, source dataset), `quality`, `enrichment` (model, prompt version, rejected entities)
- `stapfer_answers.parquet`, `stapfer_documents.jsonl`, `consultations.jsonl` – intermediate tables
- `agent_traces.jsonl` – every plan, tool call, result and validation
- `metrics.json` – every block states its scope; model numbers are sample measurements, not overall accuracy
- `run_manifest.json` – stages, status, runtime, output hashes, model endpoint

See `docs/ETHICS.md` (privacy, bias, limits) and `docs/KAGGLE_IDEAS.md` (what we reused from the Akkadian competition winners).

## Measured results (each line states its scope)

Full run from scratch (`python -m archai.pipeline --force all`, deterministic stages): **3 min 40 s, 2.2 GB peak RAM** on an 8-CPU VM.

| What | Scope | Result |
|---|---|---|
| Agreement with the Uni Bern reference export | 12,047 question answers over 2,245 sheets (2,754 sheet-level fields such as signatures/closing remarks not compared) | 100% matched on TransID+question, 100% archive signatures identical, 99.8% of texts ≥0.95 similar after documented normalisation (`data/out/bern_eval.json`) |
| Stapfer provenance | all 2,250 transcriptions | 100% archive signature, 2,248 facsimile refs, 2,249 geocoded places |
| Structural errors | all 2,250 transcriptions / 92,524 answers | **0.29% of answers** (7.9% of sheets) have unbalanced markup **in the source** — flagged, text kept unaltered; 1 of 92,524 answers keeps leftover markup after cleaning |
| Legibility / pagination | all 2,250 transcriptions | 205 sheets carry 534 transcriber-marked uncertain readings (`#…##`, counted once per distinct answer); 6,397 dashes restored (`°-°°`, previously mis-read as gaps); 2,128 sheets with page markers → page-level citations |
| Consultations → Archive DB | 858 archive signatures in 329 consultations | 857 linked (99.9%, exact match) |
| Unified export | Stapfer + consultations | 2,819 records, 0 schema errors |
| Apertus 70B enrichment | all 2,250 sheets (input cut to 1,800 chars) | 99.6% valid JSON; teacher surname recall 97.4% (1,064 scored), school place recall 92.1% (1,303) vs hand-entered Bern tables; 1.1% of 20,406 entities not literally in source text; median 3.8 s/sheet |
| Q&A retrieval: Apertus query rewriting | 230 questions with gold passages (216 templated Stapfer, 14 hand-written consultation paraphrases; paraphrases avoid the rule keywords) | paraphrased Stapfer questions: right top source 8.6% (rules only) → 100%; consultations: right one among 6 sources 14% → 57%; wording the rules know: 100% → 100%; ~1.2 s per question (`qa_eval.json`) |
| Q&A answers: number check | full Apertus answers to the same 230 questions | 185 shown as written; 6 stopped because a number was not in the cited source (all checked by hand: 4 computed/inferred, 2 mis-cited); 39 replaced by passages (no citation or "no information") (`qa_answer_check.json`) |
| Apertus tool agent v4 vs v3 | same 80 records (60 Stapfer, 20 consultations), both re-run in one environment | v4 (loop, parallel calls, argument repair, one feedback round): Apertus chose 79.3% of required tools (v3 65.4%), 97.7% of steps ran calls in parallel (up to 14), tool errors 3 (all self-corrected) vs 5, hallucination proxy 9.2% vs 11.4%; 15.1 s vs 6.7 s per record (`metrics.json` → `apertus_agent_v4`) |
| Tool-calling benchmark (BFCL-style) | 64 single-turn cases on the 8 tools: simple 26, parallel 16, parallel_multiple 12, irrelevance 10 | 70B: 81% exact with the JSON protocol, 50% with the gateway's native tools API (it returns one call per turn: parallel 0%); simple 100%, irrelevance 100%; repeating one tool on several values is the weak spot (44%). 8B: 59% (JSON), irrelevance 50% (`tool_bench.json`) |
| Apertus tool agent (agent-v3) | 80 records (60 Stapfer, 20 consultations) | 98.8% valid plans, planner picks 68.6% of required tools (the rest run anyway as baseline), 3 tool errors, 11.1% unsupported-entity proxy |

Model numbers are measurements on the stated scope, not an "overall accuracy".

## Onboarding a new dataset
`src/archai/onboard.py` turns any tabular open-data file into records of the same schema (1.1), with provenance on every
record and the original row kept in `source_row`. Download the file from opendata.swiss yourself (no crawling) and pass
its dataset URL and licence:
```bash
PYTHONPATH=src python -m archai.onboard file.csv --id my-dataset --source-url https://opendata.swiss/de/dataset/… \
    --licence "CC BY 4.0" --description "one line about the data" [--mask-pii] [--enrich 50] [--publish]
# review data/onboard/my-dataset/mapping.json, edit it if needed, then:
PYTHONPATH=src python -m archai.onboard --id my-dataset --from transform [--publish]
```
| Stage | What happens | Who decides |
|---|---|---|
| acquire | CSV/TSV/JSON/JSONL/XLSX/Parquet; encoding (UTF-8, Windows-1252, Latin-1) and delimiter detected; SHA-256 recorded | code |
| profile | per column: empty rate, distinct values, samples, roles (date, signature, URL, unique id, long text, WGS84/LV03/LV95 coordinates) | code |
| map | which columns are id / title / text / dates / places / persons / organisations / signature / URL | **Apertus proposes** from the profile; code validates every column against the data (exists, dates parse, ids unique, title is readable text, signatures look like signatures) and falls back to heuristics; `mapping.json` lists every dropped proposal and why |
| transform | dates → ISO, places → gazetteer coordinates, coordinates → WGS84, signatures parsed, quantities | code (tools.py) |
| privacy | flags e-mail, phone, AHV number, IBAN, birth dates < 110 years, dates inside the 30-year protection period; `--mask-pii` redacts the first four everywhere, also in `source_row` | code |
| enrich | optional title / English summary / topics; names not literally in the source are rejected | Apertus, checked by code |
| validate | schema check, invalid records quarantined, `report.json` (coverage, every decision), `manifest.json` (hashes, timings) | code |

`--publish` makes the records searchable in the web Q&A: Apertus' query rewriting then gets one more route
("other_dataset", with the dataset descriptions); the rules router never routes there.

**Measured** (`data/onboard/test-consultations-*/comparison.json`): onboarding the 569-row consultations table generically
reproduces the hand-built consultation records — titles 100% identical (whitespace-normalised), 100% of reference dates found,
lead department found in 99.5%, 0 schema errors — with the heuristic mapping and with the Apertus mapping. Apertus' first
proposal used the id column as title; validation rejected it (that rule was added after this test showed the gap).
Robustness tests (`tests/test_onboard.py`): Windows-1252 + semicolons, LV95 coordinates, signatures, PII flagging and masking,
rejection of a bad mapping proposal.

## Editing decisions (provenance)
Raw text is never overwritten: every answer keeps `text_raw` next to `text_clean` and its `annotations`.

| Source markup | Meaning (Editionsregeln of stapferenquete.ch) | What we do |
|---|---|---|
| `#…##` | probable reading, not certain (§15) | keep the text, annotate `uncertain_reading`, count per sheet |
| `° - °°` | dash / filler line drawn by the writer (checked on facsimiles, e.g. Töss "Detnau – welcher", sheet 1416 "Mädchen – 13") | render as `–`, count as `dash_marks`; **not** an illegible gap (versions before schema 1.1 mis-counted these as 847 "documents with illegible gaps") |
| `\|\|[Seite N]` | page break in the transcription | removed from text, kept as `page_break` annotation; each answer gets `page_start`/`page_end` (walked in questionnaire order) |
| `$…$$`, `¥…¥¥`, `€…€€`, `£…££`, `*…**` | Latin script, heading, underline, deletion/correction, note | text kept, markup moved to annotations |
| unbalanced pairs | transcription error in the source | counted in `markup_errors_in_source`, never "repaired" |

Counts per sheet use distinct answer texts: the database stores the same text under several sub-questions.
Re-derive all cleaned fields from `text_raw` without the raw dumps: `PYTHONPATH=src python -m archai.stapfer_clean --reclean`.
Record schema: `docs/SCHEMA.md`. Web API: `docs/API.md`.

## Tool agent (`src/archai/agent.py`, agent-v4)
No agent framework: plain OpenAI-compatible HTTP calls (`llm.py`). Tool schemas are generated from the Python signatures in
`tools.py`; text tools are bound to the record, so the model never has to pass the text.
1. Apertus calls tools — JSON protocol by default (it batches calls), or the gateway's native tools API (`--mode native`).
2. Calls of one step run in parallel; repeated calls are served from a cache; near-miss argument names are repaired
   (`place` → `name`), types coerced, and anything still wrong goes back to the model as an error listing the expected
   arguments, so it can correct itself.
3. The model sees the results and may call more (max 6 steps / 14 calls). If it finishes with required tools missing, the
   code names them once and the model decides; anything still missing runs anyway (`origin: baseline`).
4. Compose → deterministic grounding check, as before. Every step is in `agent_traces_v4*.jsonl`.
```bash
make setup && PYTHONPATH=src .venv/bin/python -m archai.agent 60 4          # 80 records, needs APERTUS_API_KEY
PYTHONPATH=src .venv/bin/python -m archai.tool_bench                         # BFCL-style benchmark, 70B + 8B
```
Gateway quirks found and handled: the native API returns at most one call per turn, and rejects a conversation that
contains an earlier call with empty arguments (`{}`), so history calls are recorded with a placeholder argument.

## Web demo and deployment
- API: `docs/API.md` (also interactive at `/docs`). Endpoints: `/api/ask`, `/api/record/{id}`, `/api/metrics`,
  `/api/places`, `/api/game/round`, `/api/health`.
- The search index is built once at startup in a background thread (10–25 s, ~1.5 GB RAM).
- Docker: `Dockerfile` (port 7860) or `docker compose up`. Hugging Face Space: `python deploy_hf.py <user>/<space>`
  (needs `pip install huggingface_hub` and `hf auth login`), then add `APERTUS_API_KEY` as a Space secret.
- Map tiles: © swisstopo (Dufour map 1845–1865, national map). Facsimiles are linked on stapferenquete.ch, never copied.

## Repository layout
```
app.py, static/index.html     web demo (FastAPI + one HTML page, Leaflet)
src/archai/                   pipeline: ingest, stapfer_clean, link, enrich, agent, export, evaluate, bern_eval,
                              qa (retrieval + answers), qa_eval (accuracy), onboard (new datasets), tools, llm
data/out/                     AI-ready dataset + metrics (committed)
data/onboard/                 onboarding runs (test: consultations table vs hand-built records)
notebooks/demo.ipynb          executed walkthrough      docs/  API, SCHEMA, ETHICS, KAGGLE_IDEAS
```
