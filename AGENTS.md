# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Corpus statistics

- `src/archai/stats.py` is the only place that queries the corpus with DuckDB, and `tests/test_stats.py` re-derives every figure it returns with pandas: copy that pattern for new aggregates instead of asserting SQL against itself.
- `data/out/stapfer_answers.parquet` is committed and present in CI, so a test may read it directly without building the pipeline.
- DuckDB cannot open an in-memory database read-only ("Cannot launch in-memory database in read-only mode") and a read-only connection still allows `COPY ... TO` on the filesystem, so `src/archai/stats.py` opens read-only over an empty catalog in the system temp directory *and* refuses any statement that is not SELECT/WITH.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
