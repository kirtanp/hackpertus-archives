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
| `answer` | Apertus answer with `[n]` citations, or the extractive fallback |
| `answer_source` | `apertus` (generated, cited, checked) · `extractive` (the top passages, when the generated answer failed a check) · `not_found` (Apertus says the archives do not answer it: a fixed message in the question's language; `sources` are the closest passages, not an answer) · `invalid_input` (no word in the question: no search, no model call) |
| `confidence` | `{percent, meaning, calibration}`: calibrated estimate that the cited sources answer the question (only for `apertus` / `extractive`). Fitted by `python -m archai.qa_eval --confidence` on answers with known gold sources; calibration in `data/out/confidence_eval.json` |
| `number_check` | `{ok, failures}`: every number in a sentence must occur in the sources it cites |
| `llm_meta.token_confidence` | final-layer token probabilities of the generated answer (mean, minimum, minimum on numbers and on citations) |
| `cited` | source numbers cited in the answer |
| `route` | deterministic router output: `intents`, `places`, `filtered_pool`, optional `fallback` |
| `sources[]` | `id`, `doc_id` (= `record_id`), `kind` (`stapfer`/`consultation`), `qno`, `text`, `cite` (archive signature, page, question), `page`, `url` (archive viewer), `facsimile`, `score` |
| `llm_meta` | model, attempts, errors |
| `llm_rejected` | present when Apertus answered without citations or with a blanket "no information": the answer is discarded |

Example `cite`: `BAR B0 1000/1483, Nr. 1421, fol. 228-229v, S. 2, Frage III.12`.
Consultations cite the linked dossier signature (e.g. `E6100C#1998/106#1039*`) or the metadata row.

## `GET /api/record/{record_id}`
One record of `data/out/records.jsonl`, e.g. `/api/record/stapfer:transkription:16`, `/api/record/consultation:417`.
`404` if unknown. Schema: `docs/SCHEMA.md`.

## `GET /api/metrics`
`{"metrics": <data/out/metrics.json>, "bern_eval": <data/out/bern_eval.json>}`. Every block carries its own `scope`;
none of the numbers is an overall accuracy.

## Query rewriting (`QA_REWRITE`)
Before retrieval, Apertus maps the question to the router's fields (intents, place, search keywords in the sources'
languages). Its output is validated: unknown intents are dropped and the place must match the gazetteer.
`QA_REWRITE=apertus` (default): Apertus intents win, rules are the fallback, places from both are kept.
`gaps`: Apertus only fills what the rules missed. `off`: rules only. The response's `route.rewrite` shows what Apertus
returned; `route.intents_from` / `places_from` say who decided. Accuracy of each mode: `data/out/qa_eval.json`
(`PYTHONPATH=src python -m archai.qa_eval`, needs Apertus).

## `GET /api/places`
One point per geocoded Stapfer sheet (2,249): `id, name` (1799), `today` (municipality), `school, type, lang, conf`
(confession), `pupils` (largest count stated), `district, lat, lon, url`. Feeds the map and its filters.
Sources in `/api/ask` carry `geo: {name, lat, lon}` for the map.

## `POST /api/game/round`
Body `{"ids": [...], "exclude": [...]}`, both optional: `ids` restricts the pool (the map filters), `exclude` lists
sheets already played. Returns `clues[]` (`topic, question_no, text` — up to 4 of the sheet's own answers, place,
school and person names masked as ▇▇▇) and `reveal` (place 1799 and today, school, teacher, largest pupil count,
signature, viewer URL, coordinates). `404` if no sheet in the pool has enough clues.
