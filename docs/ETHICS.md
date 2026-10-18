# Ethics, privacy and bias assessment

## Data and legal basis
- All three sources are Open Government Data published on opendata.swiss by the Swiss Federal Archives (BAR) and the
  University of Bern (Stapfer-Enquête). We only use the open-data releases and keep their licence/terms links in every record's `provenance`.
- The Archive Database (OLR) dump includes the access status (`access`, e.g. "Frei zugänglich"). We copy it into each linked
  archive unit and **never** expose content of units that are not freely accessible; we only use catalogue metadata (signature, title, dates, hierarchy).

## Personal data
| Dataset | Personal data | Risk | Handling |
|---|---|---|---|
| Stapfer 1799 | teacher names, age, family, origin; pastors/authors | persons deceased >200 years; low | kept (historically essential), sourced from the hand-made database, flagged `source=database` |
| Consultations 1960–1991 | almost none (departments, organisations) | low | kept |
| OLR catalogue | dossier titles may name persons (e.g. case files) | medium: persons may still be alive | only units linked from consultations are exported; titles shown as catalogued by BAR; protection periods respected via `access` |

Rule in the code: model-generated person names are **only** added to a record if a deterministic check finds them in the source text
(`verify_in_source`); ungrounded names are logged in `enrichment.rejected_ungrounded`, not published as facts.

## Onboarding new datasets (`archai.onboard`)
- Every onboarded record runs through a privacy screen: e-mail addresses, phone numbers, AHV numbers, IBANs, birth dates
  less than 110 years old, and dates inside the 30-year protection period of the Archiving Act (BGA Art. 9) are flagged in
  `quality.privacy_flags`; `--mask-pii` redacts the first four everywhere, including the kept original row. Flags are a
  screen, not a legal clearance: a flagged dataset needs a human check of its access status before it is published.
- Records are only searchable in the demo after an explicit `--publish`.
- Licence and source URL are mandatory inputs and are written into every record's provenance. Files are read locally:
  the pipeline does not crawl or scrape (challenge rule: use public bulk open data).

## Model use (Apertus Charter)
- Model: Apertus 1.5 70B (`apertus-v1-5-70b`, Swiss AI Initiative) through an OpenAI-compatible gateway; the 8B model and a
  local llama.cpp GGUF build were used for comparison runs (see `metrics.json`). No other LLM produces published content.
- How the design follows the [Apertus Charter](https://apertus-ai.org/pages/charter/):
  - *"factual correctness placed above style or polish"* (§1): the model only proposes (query routes, column mappings,
    enrichment, answer text); deterministic code validates each proposal, and every field says which component made it.
    A number check rejects answers whose numbers are not in the cited source (6 of 230 test answers, all confirmed errors).
  - *"say that the answer is unknown rather than guess"* (§2): when Apertus finds no answer, or cites nothing, the demo
    shows the retrieved passages instead of a generated claim.
  - Privacy and minimal data (§8): the onboarding privacy screen and `--mask-pii`.
  - *"ultimate control and decision-making authority always remain with humans"* (§10): `mapping.json` is reviewed and
    editable before transform; nothing becomes searchable without `--publish`.

## Copyright of the Stapfer edition
- The Bundesarchiv states on stapferenquete.ch that the edition (transcriptions and facsimiles) is free for personal and
  scientific use, while publication or copying into other systems needs its consent. We use the open-data release; check
  the licence stated on opendata.swiss before publishing the derived dataset or a public demo, and cite as the edition asks.
- Facsimile scans are not part of this repository and are not served by the demo; it links to the edition's own viewer.

## Transparency and reproducibility
- Raw text is never overwritten: `text_raw`/`letterhead_raw` are kept next to cleaned text; every annotation removed by the cleaner is stored.
- Each record says how each field was produced (`source: database | normalize_dates | apertus`), with model name and prompt version.
- `run_manifest.json` records stages, runtimes, output hashes and the model endpoint; `agent_traces.jsonl` records every model plan, tool call and validation.

## Bias
- **Coverage bias:** the 1799 survey over-represents German-speaking, Reformed regions (language detected: ~77% de, ~23% fr, <1% it) and only schools that answered. Statistics derived from it describe respondents, not all of Switzerland.
- **Model bias:** Apertus is multilingual but our sample shows it renames or normalises historical spellings and occasionally invents place/person names; English summaries flatten French/Italian nuance. We therefore treat model text as *descriptive enrichment*, never as the authoritative value.
- **Historical language:** terms in 1799 sources reflect their time (confession, gender roles). We do not modernise or censor the source text; generated summaries are instructed to stay factual.
- **Retrieval bias:** TF-IDF favours German (larger share); the router, character n-grams and Apertus query rewriting (keywords in German and French) mitigate this; consultation retrieval stays weaker (57% of paraphrased test questions find the right consultation among 6 sources).
- **Map and game:** all schools of one town share one gazetteer point (e.g. six Lausanne schools); the game's distance score therefore rewards the town, not the school.

## Limits
- Apertus quality was measured on small random samples (see `metrics.json`, each block states its scope). These are not overall accuracy numbers.
- LV03→WGS84 conversion is the swisstopo approximation (~1 m).
