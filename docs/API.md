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

### Data questions (`answer_source: aggregate`, `data_plan`)
A question that counts, sums, averages, compares or lists over many records ("how many schools / pupils in Bern?",
"average age of the teachers in Hinwil", "which schools in Lausanne teach Latin?", "consultations per department in
1984") is stated by no single passage. `archai.dataq` answers it with Apertus at every step and code checking each:
1. **plan**: Apertus writes a data plan (dataset, unit, filters, measure, operation, condition, grouping) over the
   fields that exist, or answers that the question is a lookup of one record; code keeps only values that occur in the
   data (`data_plan.dropped` lists the rest) and sends a plan back once when it can tell it is wrong (a count of
   girls planned as a count of schools; `data_plan.revised`). Pay is listed per school unless a total is asked.
2. **select**: code selects the records from the metadata; a 1799 place and today's municipality of the same name are
   listed apart (Bümpliz is in today's Bern).
3. **read** (measures only the answer text holds): Apertus lists and labels every figure of each record, with its
   quote; the reader gets whole answers, the school's metadata and the questionnaire wording, packed by size (Apertus
   1.5 serves a 262k-token context), and old number words annotated with their value ("zwey und achtzig [=82]").
   Code keeps a figure only if its quote is in the record and its number in the quote; code sets what the text settles
   (season from the sub-question's wording, catechism classes, ages, the sex the quote names) and gives one correction
   round naming each failure, including an answer that holds a number but was skipped; a second round re-reads only
   the sheets still incomplete. Pay: the period (yearly, quarterly "fronfästlich", ...) is read from the sheet's words,
   payments in kind (Mütt, Klafter, ...) are kept as their own measure, an assistant's pay and the items or instalments
   of a stated total are left out.
4. **compute**: code applies the counting policy (one season, winter first; never another group or the past; girls'
   and boys' schools from the metadata) and counts, sums, averages, groups. Ranges stay ranges; currencies, measures
   in kind and periods are never added together.
5. **answer**: Apertus phrases the result; `check_numbers` rejects any number that is neither in a cited source nor
   computed. The audit listing (every record, its value and citation, what was left out and why) follows the summary.

The engine is tried for a question about a place with several schools, a pupil or school count, or data wording
("how many", "which", "average", ...) without a place, except "which consultation ..." (one record, found by the
search). It steps aside (`route.data_engine.reason`) when its planner reads the question as a lookup, the plan
selects nothing, or more than `dataq.MAX_READ` records would have to be read; the normal answer path then runs.
Measured by `python -m archai.data_eval` (`data/out/data_eval.json`): hand-checked questions, each run three times.

### Pupil totals without Apertus
Without a key, a question that asks for a number of pupils ("Wie viele Schülerinnen gab es total in Bern?") is answered by
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

## `GET /api/site` and the presentation site (`/site`)
The six-tab site in `static/site/` (served at `/site`; `/` stays the demo page) loads its examples from this endpoint
(`src/archai/site.py`), built once after the index: `pipeline` (the run manifest), `morph` (one real answer, raw edition
markup to clean text), `agent` (one Apertus agent-v4 trace: plan, tool calls, composed metadata, grounding check),
`dec`, `route`, `cal`, `geo`, `hal`, `rush` (game rounds from real answers, traces, `qa.route` and `qa.check_numbers`)
and `sim` (the simulator's example questions through the `/api/ask` path; `null` until they have run in the
background). Its simulator posts free questions to `/api/ask`, and its evidence tab reads `/api/metrics`. The built-in
examples in the page are only a fallback for when the server cannot be reached.

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
