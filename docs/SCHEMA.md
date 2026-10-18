# Record schema (`schema_version` 1.1)

One JSON object per line in `data/out/records.jsonl` (flattened copy: `records.csv`). The same top-level keys are used for
every dataset; a field that does not apply is an empty list or `null`, never missing. Records that fail validation go to
`records_invalid.jsonl` instead (currently 0).

| Field | Type | Stapfer-Enquête 1799 | Federal Consultations 1960–1991 |
|---|---|---|---|
| `record_id` | str | `stapfer:transkription:<id>` | `consultation:<row>` |
| `dataset` | str | `stapfer-enquete-1799` | `federal-consultations-1960-1991` |
| `kind` | str | `school_survey_response` | `federal_consultation` |
| `title` | str | `Stapfer-Enquête 1799 – Schule <place>` | consultation title as published |
| `language` | str | `de` / `fr` / `it` (detected) | `de` |
| `text` | str | cleaned transcription, `[question_no] answer` blocks | title + procedure + linked dossiers |
| `text_raw_available` | bool | raw markup kept in `stapfer_answers.parquet` (`text_raw`) | — |
| `dates[]` | `{iso, source, …}` | date written (database) + `normalize_dates` | consultation start/end when parsed |
| `places[]` | `{place_id, name, municipality_today, lat, lon, source}` | gazetteer of the Bern database, WGS84 from LV03 | — |
| `persons[]` | `{person_id, name, role, source}` | teachers (database) | — |
| `organisations[]` | `{org_id?, name, type?/abbr?, role?}` | schools | lead department |
| `quantities` | `{pupils[], money[]}` | `extract_quantities` (counts, 1799 currencies) | — |
| `archive_units[]` | linked OLR dossiers | — | `{unit_id, signature, title, begin, end, level, access, url, path}` (OLR archive unit + hierarchy path) |
| `provenance` | object | `archive_signature`, `repository`, `is_original`, `facsimile_file`, `viewer_url`, `source_dataset`, `source_tables` | `source_dataset`, `source_file`, `row_nr`, `linked_dataset`, `archive_signature`, `viewer_url` |
| `quality` | object | `handwritten`, `uncertain_readings`, `markup_errors_in_source`, `pages_in_transcription`, `legibility_rule` (+ definition) | `archive_refs`, `archive_refs_linked`, `dates_parsed` |
| `enrichment` | object | `source` (`deterministic` / `apertus:enrich` / `apertus:agent`), `model`, `prompt_version`, `title_generated`, `summary_en`, `topics`, `legibility_model`, `rejected_ungrounded[]` | same |
| `places_mentioned[]` | str | grounded place names found by Apertus | — |
| `schema_version` | str | `1.1` | `1.1` |

Answer-level table `stapfer_answers.parquet`: `answer_id, doc_id, question_no, question_de, question_fr, text_raw,
text_clean, annotations, uncertain_readings, dash_marks, markup_errors, margin_note, editor_comment, page_start, page_end`.

**Changes 1.0 → 1.1**
- `quality.illegible_gaps` → `quality.uncertain_readings`: 1.0 counted the edition's dash markup `°-°°` as gaps; 1.1 counts
  transcriber-marked uncertain readings `#…##` (Editionsregeln §15), once per distinct answer text.
- New `quality.pages_in_transcription`; answers carry `page_start` / `page_end`.
- `provenance.viewer_url` now points to `…/db/transkriptions/view/<id>` (the 1.0 URL was a soft 404).

Apertus output is never the only source of a field: every model value sits under `enrichment` with model and prompt
version, and entities not literally present in the source text are moved to `rejected_ungrounded`.

The challenge mentions a shared specification across submissions; it was not available to us. Field names follow the
plain conventions above so a mapping is a rename, not a restructure.
