PY ?= python3.12
VENV := .venv
BIN := $(VENV)/bin
PORT ?= 8791
export PYTHONPATH := src

.PHONY: setup run test check notebook docker eval onboard-example statssql clean

setup:            ## create the virtual env and install pinned dependencies
	$(PY) -m venv $(VENV) && $(BIN)/pip install -q -r requirements.txt

run:              ## web demo on http://127.0.0.1:$(PORT) (reads .env if present)
	set -a; [ -f .env ] && . ./.env; set +a; $(BIN)/uvicorn app:app --host 127.0.0.1 --port $(PORT)

test:             ## unit tests (no network, no API key needed)
	$(BIN)/python -m pytest -q tests

notebook:         ## execute notebooks/demo.ipynb in place
	$(BIN)/pip install -q nbclient ipykernel && $(BIN)/python -c "import nbformat; from nbclient import NotebookClient as C; nb=nbformat.read('notebooks/demo.ipynb',4); C(nb, timeout=600, resources={'metadata': {'path': 'notebooks'}}).execute(); nbformat.write(nb,'notebooks/demo.ipynb')"

docker:           ## build and run the container (same as docker compose up)
	docker compose up --build

eval:             ## retrieval + answer evaluation (needs APERTUS_API_KEY); interop last: croissant.json hashes metrics.json
	set -a; [ -f .env ] && . ./.env; set +a; $(BIN)/python -m archai.qa_eval && $(BIN)/python -m archai.qa_eval --confidence && $(BIN)/python -m archai.evaluate > /dev/null && $(BIN)/python -m archai.interop

check:            ## lint + all tests incl. JSON Schema / Croissant / JSON-LD validation
	$(BIN)/pip install -q -r requirements-dev.txt && $(BIN)/ruff check . && $(BIN)/python -m pytest -q tests

statssql:         ## build the DuckDB datasource for Superset from the corpus parquet (gitignored build artifact)
	$(BIN)/python -m archai.statssql

onboard-example:  ## onboard the consultations table generically and compare with the hand-built records
	$(BIN)/python -m archai.onboard data/out/consultations.csv --id example-consultations --no-llm \
	  --source-url https://opendata.swiss/de/dataset/federal-consultations-1960-1991 --licence "see opendata.swiss" \
	  --compare data/out/records.jsonl consultation:

clean:
	rm -rf $(VENV) .pytest_cache **/__pycache__
