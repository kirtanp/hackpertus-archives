# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Working in this repository

- Run the tests the way the Makefile and CI do: `make test` (= `PYTHONPATH=src .venv/bin/python -m pytest -q tests`). CI (`.github/workflows/tests.yml`) additionally boots `app.py` and greps the JSON of `/api/ask` for a citation signature, so a change that drops `sources[]` breaks it even when the tests pass.
- Everything in `src/archai` reads `data/out/` relative to the *working directory*: run from the repository root (`app.py` `chdir`s to its own root itself). The venv is `.venv`; `make setup` builds it.
- `data/out/` is committed but *generated* from the 3.6 GB raw dumps that are not in the repository. Changing a pipeline stage (`ingest`, `stapfer_clean`, `enrich`, `tools`) leaves the committed outputs stale; regenerating them needs the raw data (README, "Rebuild from the raw data"). Do not hand-edit `data/out/`.
- Lint with `ruff check src tests` (config lives in `pyproject.toml`, which is deliberately lint-only). CI does not run ruff.

## Q&A answer path (`src/archai/qa.py`)

- The path is deterministic-first: rule router (`route`) → hybrid TF-IDF (`Index.search`) → Apertus answer → `check_numbers` grounds every digit against the cited source. The model is never told to calculate; do not relax that prompt.
- Question asking for a *number of pupils* ("Wie viele Schülerinnen gab es total in Bern?") is answered by code, not the model: `aggregate_pupils` + `render_pupil_totals` group `tools.extract_quantities` over the whole routed pool (`Index.pool`), keep boys and girls apart, and only sum sheets that state one unambiguous figure. `answer_source` is then `aggregate`. Numbers the aggregate computes are admitted to `check_numbers` solely through its `extra=` argument; keep that exception confined to the aggregate.
- `tools.extract_quantities` is the one parser for pupil counts and 1799 money; its pattern tables encode transcription realities that cost real time to rediscover (count before the sex word, the "(Knaben/Mädchen)" questionnaire header, `Mägdchen`/`Mägdlein`, `Knaben und 21 Mädchen`). Extend those tables instead of adding a second extractor, and take test strings from real rows in `data/out/stapfer_answers.parquet`.

## Data questions (`src/archai/dataq.py`)

- Questions that count, sum, average or list over records go through one general engine, never a per-question code
  path: Apertus plans, reads and phrases; code validates every plan value against the data, verifies every quote and
  number, applies the counting policy (`resolve`) and does all arithmetic. Add a new kind of question by extending the
  plan vocabulary or a verification rule, not by adding a branch for it.
- Apertus labels figures inconsistently from run to run (season, catechism, "and more", skipped answers). Whatever the
  text itself settles is set by code in `verify_figure` (season from the sub-question's wording, `_OTHER_GROUP`, ages
  via `NOT_PUPILS_AFTER`, the sex a quote names); keep moving judgements there rather than adding prompt rules.
- Reference figures to test against: 1799 Bern has 9 schools (Bümpliz and Oberbottigen are today's Bern only) and
  782 to 836 "and more" pupils in winter without the catechism classes, checked sheet by sheet by hand.

## Corpus statistics and the Superset datasource

- `src/archai/stats.py` owns the corpus aggregates and their SQL; `tests/test_stats.py` re-derives every figure it returns with pandas, so copy that pattern for a new aggregate instead of asserting SQL against itself. `statssql.py` (the Apache Superset datasource, `make statssql`) consumes the same SQL: it builds its DuckDB tables from `stats.aggregate_sql()` / `SECTION` / `EMPTY` / `TRIVIAL` / `PLAIN`, so a new aggregate belongs in `stats.py` and never as fresh SQL in `statssql.py`.
- `data/out/stapfer_stats.duckdb` is a derived, gitignored build artifact (`.gitignore`: `data/out/*.duckdb`); the committed corpus stays `data/out/stapfer_answers.parquet`.
- Superset is a separate application under `deploy/superset/`, deliberately not imported by `app.py`. How to run it and the Superset 6.1 traps it hit (flask-caching pin, the removed `bar` chart type, a chart `query_context` that the API does not compute) are recorded in `deploy/superset/README.md`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
