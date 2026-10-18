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

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
