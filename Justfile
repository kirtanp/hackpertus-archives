# Task runner for the ArchAI repository. Dependencies are managed by uv from pyproject.toml + uv.lock:
# `just setup` builds .venv from the committed lock, and every recipe runs through `uv run --all-groups` so the
# environment never flips between recipe subsets.
#
# `set dotenv-load` reads .env when it is present, so APERTUS_API_KEY and friends reach `just run` and `just eval`
# without an explicit `set -a; . ./.env`.

set dotenv-load := true

# src/ is on the import path so `python -m archai.<module>` works from the repository root; the project is not
# installed as a package (pyproject.toml carries no [build-system] table), so nothing else puts it there.
export PYTHONPATH := "src"

# Interpreter for .venv. The runtime is Python 3.12 (Dockerfile `python:3.12-slim`, CI `python-version: "3.12"`),
# so the local environment is built with 3.12 too; override with `UV_PYTHON=3.13 just setup`.
export UV_PYTHON := env("UV_PYTHON", "3.12")

# The port `just run` listens on; override with `PORT=9000 just run`.
port := env("PORT", "8791")

# List available recipes.
default:
    @just --list

# Create .venv and install the locked dependencies (every group).
setup:
    uv sync --all-groups

# Web demo on http://127.0.0.1:{{port}} (reads .env if present).
run:
    uv run --all-groups uvicorn app:app --host 127.0.0.1 --port {{port}}

# Unit tests (no network, no API key needed).
test:
    uv run --all-groups python -m pytest -q tests

# Lint, then all tests incl. JSON Schema / Croissant / JSON-LD validation.
check:
    uv run --all-groups ruff check .
    uv run --all-groups python -m pytest -q tests

# Execute notebooks/demo.ipynb in place.
notebook:
    uv run --all-groups python -c "import nbformat; from nbclient import NotebookClient as C; nb=nbformat.read('notebooks/demo.ipynb',4); C(nb, timeout=600, resources={'metadata': {'path': 'notebooks'}}).execute(); nbformat.write(nb,'notebooks/demo.ipynb')"

# Build and run the container (same as docker compose up).
docker:
    docker compose up --build

# Retrieval + answer evaluation (needs APERTUS_API_KEY); interop last: croissant.json hashes metrics.json.
eval:
    uv run --all-groups python -m archai.qa_eval
    uv run --all-groups python -m archai.qa_eval --confidence
    uv run --all-groups python -m archai.data_eval
    uv run --all-groups python -m archai.evaluate > /dev/null
    uv run --all-groups python -m archai.interop

# Build the DuckDB datasource for Superset from the corpus parquet (gitignored build artifact).
statssql:
    uv run --all-groups python -m archai.statssql

# Onboard the consultations table generically and compare with the hand-built records.
onboard-example:
    uv run --all-groups python -m archai.onboard data/out/consultations.csv --id example-consultations --no-llm \
      --source-url https://opendata.swiss/de/dataset/federal-consultations-1960-1991 --licence "see opendata.swiss" \
      --compare data/out/records.jsonl consultation:

# Remove the virtualenv and the caches.
clean:
    rm -rf .venv .pytest_cache
    find . -name __pycache__ -type d -prune -exec rm -rf {} +
