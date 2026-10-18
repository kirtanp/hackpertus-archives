# What we took from the Kaggle "Deep Past" (Akkadian→English) winners

Write-ups read: 1st, 2nd, 3rd, 4th, 5th, 6th, 7th, 8th, 9th, 10th (copies in `docs/kaggle_writeups/`).

| Winner idea | Where | How we use it here |
|---|---|---|
| "Data quality dictates everything"; pre-process carefully, post-process minimally | 1st | Deterministic cleaner with structural-error counters; raw text kept; no LLM rewriting of the source |
| Strict set arithmetic to route records to different LLM strategies (Breaker/Fixer/Generator); the "Generator" (making things up from metadata) *hurt* | 2nd | Records are routed by what the deterministic layer already knows; Apertus never fabricates fields that the database lacks — it only adds summaries/titles and grounded entities |
| Normalising orthography across publications | 2nd, 5th | 1799 month names (Hornung, Merz, Brachmonat…), French dates, currencies (fl, kr, Batzen, livres, sous) normalised by `tools.py` |
| Anchors (numbers, names) to align texts | 1st | Grounding check: model entities must literally occur in the source; numbers come from `extract_quantities`, not the model |
| Validation step that re-sends failed extractions with an error description | 5th | `llm.chat_json` re-asks with "not valid JSON" and checks schema; circuit breaker falls back to deterministic output |
| 30+ cleaning passes with automated invariants as quality gates | 9th | `tests/test_tools.py` + schema validation in `export.py` (invalid records go to `records_invalid.jsonl`) |
| MBR / reward model: choose the consensus candidate to suppress hallucinations | 3rd, 4th, 9th | Deterministic validator acts as the "reward": ungrounded entities are rejected; agent traces measure how often |
| Held-out validation set kept out of everything | 4th, 10th | Gold = hand-entered Uni Bern teacher/place tables, never shown to the model |

Not transferred: ByT5 fine-tuning, synthetic data and ensembles — the challenge is curation of existing transcriptions, not translation,
and the local hardware (8 CPU, no GPU) runs Apertus 8B at ~45–100 s per document.
