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
Needs [`uv`](https://docs.astral.sh/uv/) and [`just`](https://just.systems/); the recipes are in `Justfile`.
```bash
git clone <this repo> && cd <repo>
just setup            # Python 3.12 venv + locked dependencies (uv, pyproject.toml + uv.lock)
just run              # http://127.0.0.1:8791
just test             # unit tests, no network
```
or with Docker: `docker compose up --build` → http://localhost:8791.

**Apertus (optional).** `cp .env.example .env` and set `APERTUS_API_KEY` (any OpenAI-compatible Apertus endpoint works;
default gateway `https://gateway.keera.ch/api/v1`, model `apertus-v1-5-70b`). Without a key the demo answers
extractively: the cited passages, no generated text. Never commit `.env`.

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
- [Onboarding a new dataset](docs/src/content/docs/reference/onboarding.mdx)
- [Editing decisions (provenance)](docs/src/content/docs/reference/editing-decisions.mdx)
- [Tool agent (src/archai/agent.py, agent-v4)](docs/src/content/docs/reference/tool-agent.mdx)
- [Web demo and deployment](docs/src/content/docs/reference/web-demo.mdx)
- [Statistics in SQL (Apache Superset prototype)](docs/src/content/docs/reference/superset.mdx)

The site also carries the [architecture diagrams](docs/src/content/docs/diagrams/technical.mdx), the [web API](docs/src/content/docs/reference/api.mdx), the [record schema](docs/src/content/docs/reference/schema.mdx), [ethics, privacy and bias](docs/src/content/docs/reference/ethics.mdx), and the [Kaggle write-up ideas](docs/src/content/docs/kaggle/ideas.mdx) with all ten write-ups.

## Repository layout
```
app.py, static/index.html     web demo (FastAPI + one HTML page, Leaflet)
src/archai/                   pipeline: ingest, stapfer_clean, link, enrich, agent, export, evaluate, bern_eval,
                              qa (retrieval + answers), qa_eval (accuracy), onboard (new datasets), tools, llm,
                              stats (corpus aggregates in DuckDB, /api/stats), statssql (the Superset datasource)
data/out/                     AI-ready dataset + metrics (committed)
data/onboard/                 onboarding runs (test: consultations table vs hand-built records)
deploy/server.sh              server deploy over SSH (`just deploy HOST`)
deploy/superset/              Superset prototype: datasource docs, provisioning script, dashboard export, compose
notebooks/demo.ipynb          executed walkthrough      docs/  Starlight documentation site (pages in src/content/docs/)
```
