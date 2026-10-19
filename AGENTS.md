# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Working in this repository

- Run the tests the way the Justfile and CI do: `just test` (= `uv run --all-groups python -m pytest -q tests`; `pythonpath = ["src"]` in `pyproject.toml` replaces the old `PYTHONPATH=src`). CI (`.github/workflows/tests.yml`) additionally boots `app.py` and greps the JSON of `/api/ask` for a citation signature, so a change that drops `sources[]` breaks it even when the tests pass.
- Dependencies and tasks are `uv` + `just`: `pyproject.toml` (with the committed `uv.lock`) is the single source of dependency truth and `Justfile` the only runner — there is no `Makefile` and no `requirements*.txt`. `just setup` builds `.venv` from the lock with Python 3.12 (the runtime the Dockerfile and CI use); the recipes run through `uv run --all-groups` and export `PYTHONPATH=src`, because the project is not installed as a package.
- Everything in `src/archai` reads `data/out/` relative to the *working directory*: run from the repository root (`app.py` `chdir`s to its own root itself).
- `data/out/` is committed but *generated* from the 3.6 GB raw dumps that are not in the repository. Changing a pipeline stage (`ingest`, `stapfer_clean`, `enrich`, `tools`) leaves the committed outputs stale; regenerating them needs the raw data (README, "Rebuild from the raw data"). Do not hand-edit `data/out/`.
- Lint with `ruff check .` (config lives in `pyproject.toml`, alongside the uv dependency, dependency-group and pytest settings). `just check` is ruff plus the whole test suite, which is what CI runs.

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
- Reference figures: `src/archai/data_eval.py` holds the hand-checked answers (1799 Bern: 9 schools, 782 to 836 "and
  more" pupils in winter without catechism; Bümpliz and Oberbottigen are today's Bern only). Brunngasse's figures may
  be past ones ("sonst ... darunter waren"), so both readings are accepted. Run it after any change to dataq or to the
  routing; it measures run-to-run variance, which the unit tests (fake Apertus) cannot.
- Do not put a data/lookup label into the dynamic rewrite prompt (`qa.DYNAMIC`): it cost consultation retrieval
  (top-1 7-8 of 20 down to 4-6 over three A/B runs). The engine's own planner decides (`{"task": "lookup"}`).

## Corpus statistics and the Superset datasource

- `src/archai/stats.py` owns the corpus aggregates and their SQL; `tests/test_stats.py` re-derives every figure it returns with pandas, so copy that pattern for a new aggregate instead of asserting SQL against itself. `statssql.py` (the Apache Superset datasource, `just statssql`) consumes the same SQL: it builds its DuckDB tables from `stats.aggregate_sql()` / `SECTION` / `EMPTY` / `TRIVIAL` / `PLAIN`, so a new aggregate belongs in `stats.py` and never as fresh SQL in `statssql.py`.
- `data/out/stapfer_stats.duckdb` is a derived, gitignored build artifact (`.gitignore`: `data/out/*.duckdb`); the committed corpus stays `data/out/stapfer_answers.parquet`.
- Superset is a separate application under `deploy/superset/`, deliberately not imported by `app.py`. How to run it and the Superset 6.1 traps it hit (flask-caching pin, the removed `bar` chart type, a chart `query_context` that the API does not compute) are recorded in `deploy/superset/README.md`.

## Presentation site (`static/site/`, `/site`)

- The app's only frontend: `/` redirects to `/site/` rather than serving the page, because it loads `./support.js` relative to `/site/` (a `FileResponse` at `/` would break it). It is a dc-runtime page (`support.js`, `{{ }}` bindings, a `text/x-dc` Component); its data constants are only fallbacks, replaced in place from `GET /api/site` (`src/archai/site.py`). Keep the array lengths the layout expects (5 tools, 4 entities, 5 geo rounds), and never show an invented passage under a real signature: take examples from `data/out` through `site.py`. Outside figures on the Impact tab (06) each carry their source URL in `SRC`; add none without one.

## Documentation site (`docs/`, Astro Starlight)

- The site is self-contained under `docs/` with its own bun toolchain, isolated from the Python/uv one: `cd docs && bun install --frozen-lockfile && bun run check && bun run build`. `.github/workflows/docs.yml` builds it and uploads `docs/dist` as an artifact; there is no deploy (private personal repository, no GitHub Pages). `docs/bun.lock` is committed; `docs/node_modules/`, `docs/dist/` and `docs/.astro/` are gitignored.
- Pages are `.mdx` under `docs/src/content/docs/`. `docsSchema()` requires `title` and `description` in every frontmatter, and the sidebar in `docs/astro.config.mjs` lists every page explicitly, so a new page needs a sidebar entry to stay reachable.
- Assets the site serves (the two architecture PNGs and their `.mmd` sources, the favicon) live in `docs/public/` and are referenced by absolute path (`/architecture/architecture-technical.png`). That is deliberate: the `.mmd` sources must stay downloadable beside their PNGs, which `src/assets/` would not do.
- MDX traps that cost real time: a literal `{`, `<` or `$` in prose is JSX/expression syntax, so write `\{`, `&lt;` or `\$` (the Kaggle write-ups carry all three), and LaTeX (`$…$`, `$$…$$`) needs `remark-math` + `rehype-katex` wired through `markdown.processor: unified({...})` from `@astrojs/markdown-remark` — the legacy `markdown.remarkPlugins` key is deprecated and fails on `.mdx`. The top-level `@astrojs/mdx` must track the version Starlight bundles (`^8.1.0`); otherwise `@astrojs/markdown-remark` hoists too old and the build refuses to render `.mdx`.
- `docs/record.schema.json` deliberately stays outside the site content: `tests/test_tools.py` loads it from that exact path.
- The root `README.md` keeps only orientation and links into the site by source path (`docs/src/content/docs/...`), because the built site has no published URL.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
