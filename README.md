# ArchAI – Deciphering Swiss archives with Apertus

Turns three open Swiss archive datasets into one **AI-ready, provenance-preserving** dataset and serves it through a
**cited Q&A demo**. Deterministic code does the reliable work; Apertus plans, enriches and answers, and a deterministic
validator has the final word on everything it produces.

| Dataset | Source | What we use |
|---|---|---|
| Stapfer-Enquête 1799 | [opendata.swiss](https://opendata.swiss/de/dataset/stapfer-enquete-schule-in-der-schweiz-1799) | 2,250 transcribed sheets, 92,524 answers, teachers/schools/places |
| Federal Consultations 1960–1991 | [opendata.swiss](https://opendata.swiss/de/dataset/federal-consultations-1960-1991) | 569 consultations, 858 archive references |
| Archive Database (OLR) | [opendata.swiss](https://opendata.swiss/de/dataset/swiss-archives-archive-database-of-the-swiss-federal-archives) | 4.5 M archive units + hierarchy, streamed from the 3.6 GB dump |

## Run it on your computer

Everything the demo needs is in this repository: no database, no downloads of raw data, no API key.
You need **git** and one of: **uv** (easiest) or **Docker**.

### Option A: uv (no Docker)
**1. Install uv, once** (a small tool that installs Python and everything else for you):
```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
Then open a **new** terminal so the `uv` command is found.

**2. Download and start ArchAI:**
```bash
git clone https://github.com/kirtanp/hackpertus-archives.git
cd hackpertus-archives
uv run app.py
```
**3. Open http://127.0.0.1:8791** in your browser.

The first start takes about a minute: uv downloads Python 3.12 and the libraries. After that, the app builds its
search index in about 20 seconds; until then, questions answer "Index is still loading". Stop it with **Ctrl+C**,
start it again with `uv run app.py`.

### Option B: Docker
Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it, then:
```bash
git clone https://github.com/kirtanp/hackpertus-archives.git
cd hackpertus-archives
docker compose up --build
```
Open **http://localhost:8791**. Stop with **Ctrl+C**.

### Optional: answers written by Apertus
Without a key, ArchAI answers with the cited passages themselves. For generated, cited answers from the Swiss
open model Apertus, copy the example settings and paste your key after `APERTUS_API_KEY=`:
```bash
cp .env.example .env      # Windows: copy .env.example .env
```
Then restart (`uv run app.py` or `docker compose up`). The start-up message says `Apertus: on`. Any
OpenAI-compatible Apertus endpoint works (default gateway `https://gateway.keera.ch/api/v1`, model
`apertus-v1-5-70b`). `.env` is ignored by git: never commit your key.

### If something goes wrong
| Problem | Fix |
|---|---|
| `uv: command not found` | Open a new terminal after installing uv. |
| Port 8791 is already in use | `PORT=9000 uv run app.py` (Windows PowerShell: `$env:PORT=9000; uv run app.py`), then open :9000. |
| "Index is still loading" | Wait about 20 seconds after the start and ask again. |
| The page looks unstyled | It loads fonts and React from public CDNs: you need an internet connection. |

### For developers
With [`just`](https://just.systems/) installed (`brew install just`, `cargo install just` or `winget install Casey.Just`):
```bash
just setup            # .venv with every dependency group (Python 3.12, from uv.lock)
just run              # the demo, reading .env
just test             # unit tests, no network
just check            # ruff + tests, what CI runs
```

**What the demo does**
- Cited Q&A in German, French and English: Apertus rewrites the question → validated route → hybrid search → Apertus
  answer with `[n]` citations to archive signature, page and question number → deterministic citation and number checks.
  A question that asks for a number of pupils ("Wie viele Schülerinnen gab es total in Bern?") is instead summed by
  `extract_quantities` over the retrieved sheets, per sex, with every contributing school and every exclusion listed
  (`answer_source: aggregate`, see the [web API](docs/src/content/docs/reference/api.mdx)).
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
    data questions (count/sum/average/list over records) ──► archai.dataq: Apertus plans → code validates and selects
                 → Apertus reads and labels every figure with its quote → code verifies, applies the counting policy and
                 computes → Apertus phrases → number check (no key: qa.aggregate_pupils, rules school counts)
any new table ──► onboard: profile → Apertus column mapping (validated) → transform → privacy screen → validate
```
Deterministic tools (`src/archai/tools.py`): `clean_markup`, `normalize_dates` (1799 month names: Hornung, Merz, …; French dates),
`lookup_place` (historical name → gazetteer id, today's municipality, WGS84 from LV03), `parse_signature`,
`lookup_archive_unit` (OLR), `extract_quantities` (pupils; fl, kr, Batzen, livres, sous…), `verify_in_source`.

Robustness: idempotent/resumable stages, run manifest with output hashes, retries + JSON repair + schema checks,
circuit breaker → deterministic-only output if Apertus is unreachable, schema-invalid records quarantined, unit tests.

### Diagrams
The two rendered diagrams - one engineering map for engineers, one plain-language overview - now live in the documentation site: [Technical architecture](docs/src/content/docs/diagrams/technical.mdx) and [Overview](docs/src/content/docs/diagrams/overview.mdx).
Mermaid sources move with them: [`docs/public/architecture/architecture-technical.mmd`](docs/public/architecture/architecture-technical.mmd) and [`docs/public/architecture/architecture-overview.mmd`](docs/public/architecture/architecture-overview.mmd), re-rendered with `mmdr -i docs/public/architecture/architecture-technical.mmd -o docs/public/architecture/architecture-technical.png -e png`.

## Documentation
The deep reference now lives in an [Astro Starlight](https://starlight.astro.build/) site under `docs/`; build it with `cd docs && bun install && bun run build` (CI builds it and uploads the result as a workflow artifact).
This README keeps the orientation, and each section below is a page in the site:
- [Rebuild from the raw data](docs/src/content/docs/reference/rebuild.mdx)
- [Outputs (data/out/)](docs/src/content/docs/reference/outputs.mdx)
- [Measured results](docs/src/content/docs/reference/measured-results.mdx)
- [Query timing](docs/src/content/docs/reference/query-timing.mdx)
- [Onboarding a new dataset](docs/src/content/docs/reference/onboarding.mdx)
- [Editing decisions (provenance)](docs/src/content/docs/reference/editing-decisions.mdx)
- [Tool agent (src/archai/agent.py, agent-v4)](docs/src/content/docs/reference/tool-agent.mdx)
- [Web demo and deployment](docs/src/content/docs/reference/web-demo.mdx)
- [Statistics in SQL (Apache Superset prototype)](docs/src/content/docs/reference/superset.mdx)

The site also carries the [architecture diagrams](docs/src/content/docs/diagrams/technical.mdx), the [web API](docs/src/content/docs/reference/api.mdx), the [record schema](docs/src/content/docs/reference/schema.mdx), [ethics, privacy and bias](docs/src/content/docs/reference/ethics.mdx), and the [Kaggle write-up ideas](docs/src/content/docs/kaggle/ideas.mdx) with all ten write-ups.

## Repository layout
```
app.py, static/site/          web app (FastAPI + the presentation site, served at /site; / redirects there)
src/archai/                   pipeline: ingest, stapfer_clean, link, enrich, agent, export, evaluate, bern_eval,
                              qa (retrieval + answers), qa_eval (accuracy), onboard (new datasets), tools, llm,
                              stats (corpus aggregates in DuckDB, /api/stats), statssql (the Superset datasource)
data/out/                     AI-ready dataset + metrics (committed)
data/onboard/                 onboarding runs (test: consultations table vs hand-built records)
deploy/server.sh              server deploy over SSH (`just deploy HOST`)
deploy/superset/              Superset prototype: datasource docs, provisioning script, dashboard export, compose
notebooks/demo.ipynb          executed walkthrough      docs/  Starlight documentation site (pages in src/content/docs/)
```
