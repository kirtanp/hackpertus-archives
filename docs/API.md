# ArchAI web API

FastAPI app in `app.py`. Interactive docs (OpenAPI) are served at `/docs` and `/redoc`.

```bash
.venv/bin/uvicorn app:app --port 8791            # local
docker build -t archai-demo . && docker run -p 7860:7860 --env-file ~/.config/archai/apertus.env archai-demo
```

Configuration (environment, all optional): `APERTUS_BASE_URL`, `APERTUS_MODEL`, `APERTUS_API_KEY`, `APERTUS_TIMEOUT`.
Without a key the app answers extractively (top cited passages); nothing else changes.
The search index is built once at startup in a background thread (10–25 s); requests before that return `503`.

## `GET /api/health`
```json
{"ready": true, "error": null, "build_seconds": 17.1, "records": 2819, "apertus_configured": true, "model": "apertus-v1-5-70b"}
```

## `POST /api/ask`
Body: `{"question": "Wie viele Kinder besuchten die Schule in Hörndli?"}` (2–500 chars, German/French/English; `422` otherwise).

| Field | Meaning |
|---|---|
| `answer` | Apertus answer with `[n]` citations, the extractive fallback, or the pupil total computed from the sources |
| `answer_source` | `apertus` (generated, cited, checked) · `aggregate` (a pupil total computed by code from the cited sheets, never by the model) · `extractive` (the top passages, when the generated answer failed a check) · `not_found` (Apertus says the archives do not answer it: a fixed message in the question's language; `sources` are the closest passages, not an answer) · `invalid_input` (no word in the question: no search, no model call) |
| `confidence` | `{percent, meaning, calibration}`: calibrated estimate that the cited sources answer the question (only for `apertus` / `extractive`). Fitted by `python -m archai.qa_eval --confidence` on answers with known gold sources; calibration in `data/out/confidence_eval.json` |
| `number_check` | `{ok, failures}`: every number in a sentence must occur in the sources it cites |
| `llm_meta.token_confidence` | final-layer token probabilities of the generated answer (mean, minimum, minimum on numbers and on citations) |
| `cited` | source numbers cited in the answer |
| `route` | deterministic router output: `intents`, `places`, `filtered_pool`, optional `fallback` |
| `sources[]` | `id`, `doc_id` (= `record_id`), `kind` (`stapfer`/`consultation`), `qno`, `text`, `cite` (archive signature, page, question), `page`, `url` (archive viewer), `facsimile`, `score` |
| `llm_meta` | model, attempts, errors |
| `llm_rejected` | present when Apertus answered without citations or with a blanket "no information": the answer is discarded |
| `number_check` | `ok` and the numbers of the answer that the cited sources do not contain |

Example `cite`: `BAR B0 1000/1483, Nr. 1421, fol. 228-229v, S. 2, Frage III.12`.
Consultations cite the linked dossier signature (e.g. `E6100C#1998/106#1039*`) or the metadata row.

### Pupil totals (`answer_source: aggregate`)
A question that asks for a number of pupils ("Wie viele Schülerinnen gab es total in Bern?") is answered by
`archai.qa.aggregate_pupils`, not by the model: `archai.tools.extract_quantities` reads the counts out of the retrieved
answers, the counts are grouped by sheet and by sex, and only unambiguous figures are summed. The answer lists every
sheet with its own value and citation, names the sheets outside the total with the reason (a range, several figures, no
figure for that sex), and states the total as computed from those sources. Boys and girls are never added together.
Without a place the question is not aggregated, and a route selecting more than `qa.AGG_SOURCES_MAX` sources is left to
the normal answer path. The whole routed pool is returned in `sources[]` for the aggregate, so `[n]` stays resolvable.

## `GET /api/record/{record_id}`
One record of `data/out/records.jsonl`, e.g. `/api/record/stapfer:transkription:16`, `/api/record/consultation:417`.
`404` if unknown. Schema: `docs/SCHEMA.md`.

## `GET /api/metrics`
`{"metrics": <data/out/metrics.json>, "bern_eval": <data/out/bern_eval.json>}`. Every block carries its own `scope`;
none of the numbers is an overall accuracy.

## Query rewriting (`QA_REWRITE`)
`dynamic` (default): Apertus reads the real Stapfer questionnaire (60 questions, German/French) and returns the dataset,
the question numbers that answer the question, the places, years and search keywords. Code validates every pick:
question numbers must exist, places must match the gazetteer, years 1960–1991, a year written in the question is read
by code. One feedback round: if a place is unknown (Apertus gets the closest gazetteer names) or the place's sheets do
not answer the chosen questions (Apertus gets the list they do answer), it revises once. If nothing matches, the search
widens step by step — related questions, then the whole sheet — and `route.fallback` says so; it never switches to a
different place. When Apertus chooses no question, or calls the question unrelated, the validated 9-intent rewrite
(`apertus`) routes it instead; the answer step, not the rewrite, decides whether the archives answer it.
`route` shows `dataset`, `sections`, `places`, `years`, `routed_by` and `rewrite.rounds`.
Other modes: `apertus` (9 fixed intents), `off` (rules only). Measured in `data/out/qa_eval.json`.

The same aggregates are also available as SQL, for BI tools that cannot chart a JSON envelope: `make statssql`
materialises them into a DuckDB database file (`src/archai/statssql.py`) that Apache Superset reads as a
datasource. See `deploy/superset/README.md`.

## `GET /api/places`
One point per geocoded Stapfer sheet (2,249): `id, name` (1799), `today` (municipality), `school, type, lang, conf`
(confession), `pupils` (largest count stated), `district, lat, lon, url`. Feeds the map and its filters.
Sources in `/api/ask` carry `geo: {name, lat, lon}` for the map.

## `POST /api/game/round`
Body `{"ids": [...], "exclude": [...]}`, both optional: `ids` restricts the pool (the map filters), `exclude` lists
sheets already played. Returns `clues[]` (`topic, question_no, text` — up to 4 of the sheet's own answers, place,
school and person names masked as ▇▇▇) and `reveal` (place 1799 and today, school, teacher, largest pupil count,
signature, viewer URL, coordinates). `404` if no sheet in the pool has enough clues.
