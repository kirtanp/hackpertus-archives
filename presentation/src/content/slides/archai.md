---
title: "ArchAI"
description: "Eight slides: the challenge, the problem and the datasets, the goal, the product, provenance, future plans and the cost in tokens."
pubDate: 2026-10-19
order: 1
minutes: 7
presenter: Hackpertus
theme: white
transition: slide
controls: true
progress: true
fontScale: 1
---

<div class="deck-center">

<div class="deck-eyebrow">Hackpertus · Archives</div>

<h1 class="deck-heading">ArchAI</h1>

<p class="deck-lead">Deciphering Swiss archives with Apertus: three open Swiss archive datasets become one AI-ready,
provenance-preserving dataset, served as a cited Q&amp;A demo.</p>

</div>

---

<h2 class="deck-heading">Challenge</h2>

<p class="deck-lead">How can we make archives more easily digestible?</p>

Note:
Introduce — spoken budget ~1:30 (the sketch writes "1:30 mins"; it may mean 30 minutes — the captain can correct this
one line). This slide asks the question; the two after it carry the detail — first the problem, the material and what
the product solves, then the goal and how it is organised.

---

<div class="deck-eyebrow">Introduce · The problem and the material</div>

<h2 class="deck-heading">The problem, and what the product solves</h2>

<ul class="deck-list">
  <li>2,250 handwritten 1799 school sheets in German, French and Italian, with 92,524 answers; 569 federal consultations from 1960–1991, whose dossiers open only through a 4.5 M-unit archive database.</li>
  <li>Not centralised databases: the sources stay where they are, and only 329 of the 569 consultations point at a dossier at all — 858 archive references between them.</li>
  <li>The datasets become a PDF transcript plus data: the raw transcription is never overwritten, and beside it stand 2,819 records in one schema, each with its provenance.</li>
  <li>What the product solves: ask in plain language, and get the archive page the answer came from.</li>
</ul>

<p class="deck-source">data/out/metrics.json → stapfer, linking, export · README.md</p>

---

<div class="deck-eyebrow">Introduce · The goal and how it is organised</div>

<h2 class="deck-heading">The goal: archives that are easily digestible</h2>

<ul class="deck-list">
  <li>Ask in German, French or English, in plain language; the answer names the archive signature, the sheet and the question it came from. Every number is checked against the source it cites, and totals are computed by code, never by the model.</li>
  <li>Accessible to everyone: no key, no database, no raw dump. <span class="deck-mono">uv run app.py</span> reads data/out/, and without an Apertus key the app still answers — with the cited passages themselves.</li>
  <li>An AI framework is what organises it: Apertus rewrites the question, enriches the records and writes the answer; deterministic code validates the route, the citations and every number.</li>
</ul>

<p class="deck-cite">BAR B0 1000/1483, Nr. 1421, fol. 228-229v, S. 2, Frage III.12</p>

<p class="deck-source">docs/reference/api.mdx (citation format) · data/out/qa_answer_check.json (number check) · README.md</p>

---

<div class="deck-eyebrow">Product</div>

<h2 class="deck-heading">The product</h2>

<p class="deck-lead">The working thing itself.</p>

<ul class="deck-list">
  <li>Cited Q&amp;A in German, French and English: answer, citations, and a calibrated confidence. 92.4 % of the 380 eval questions put the right sheet first.</li>
  <li>A map of 2,249 geocoded schools on today's swisstopo map, with a slider to the 1850s Dufour map.</li>
  <li>“Guess the school”: read a teacher's own 1799 answers with the place names masked, then find the village.</li>
  <li>Measured results: every figure from metrics.json in plain words, each with the scope it was measured on.</li>
</ul>

<p class="deck-source">README.md · data/out/qa_eval.json (92.4 % of 380 questions, dynamic rewriting) · 2,249 geocoded places in data/out/metrics.json</p>

Note:
Product — spoken budget 3 min. This is the demo moment: open the app, ask one question, then show the map and
"guess the school".

---

<div class="deck-eyebrow">Business · Provenance</div>

<h2 class="deck-heading">Provenance</h2>

<p class="deck-lead">Every record keeps where it came from: the source dataset and its licence, the source file with its
SHA-256, the row number, the archive signature and the archive viewer URL.</p>

<ul class="deck-list">
  <li>100 % of the 2,250 transcriptions carry an archive signature; 2,248 also link their facsimile — linked on stapferenquete.ch, never copied.</li>
  <li>857 of 858 archive references were matched into the archive database, by exact match.</li>
  <li>0 of 2,819 exported records failed the schema; an invalid record is quarantined instead of exported.</li>
  <li>Against the University of Bern export: 100 % of 12,047 question answers matched on TransID and question, 99.8 % of texts ≥ 0.95 similar.</li>
</ul>

<p class="deck-source">data/out/metrics.json → stapfer, linking, export, bern_reference · docs/reference/outputs.mdx</p>

Note:
Business — spoken budget 2 min, across this slide and the two after it. Provenance first: this is what makes the
dataset defensible for an archive. Hold the two examples from the Introduce section in mind and show where each record's traceability lives.

---

<div class="deck-eyebrow">Business · Next</div>

<h2 class="deck-heading">Pipelining, onboarding more datasets, a new customer base</h2>

<ul class="deck-list">
  <li>Pipelining: idempotent, resumable stages and a run manifest with the hash of every output. The two Apertus stages are optional — without a key they are skipped and everything after them still runs.</li>
  <li>Onboarding more datasets: any tabular open-data file goes through acquire → profile → map → transform → privacy → validate. Apertus proposes the column mapping and code validates it; onboarding the 569-row consultations table this way reproduced the hand-built records.</li>
  <li>A new customer base: the same path makes another archive, library or research group searchable, without new pipeline code.</li>
</ul>

<p class="deck-source">README.md (robustness) · docs/reference/onboarding.mdx, docs/reference/measured-results.mdx</p>

---

<div class="deck-eyebrow">Business · Cost</div>

<h2 class="deck-heading">Cost: tokens and calls, not currency</h2>

<table>
  <thead>
    <tr><th>Artifact</th><th>How it is served</th><th>Tokens at query time</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>Starlight documentation site (docs/)</td>
      <td>static Astro build, built in CI</td>
      <td>0</td>
    </tr>
    <tr>
      <td>This deck</td>
      <td>Astro + reveal.js, <span class="deck-mono">SINGLE_FILE=1</span> → one HTML file that opens from disk</td>
      <td>0</td>
    </tr>
    <tr>
      <td>ArchAI Q&amp;A</td>
      <td>Python app, search index built at startup (10–25 s)</td>
      <td>only with an Apertus key; 0 without</td>
    </tr>
  </tbody>
</table>

<ul class="deck-list">
  <li>API cost of the enrichment run: 2,250 calls, one per sheet.</li>
  <li>Token cost of that run: 2,174,008 tokens (1,784,954 in, 389,054 out) — 966 tokens per sheet.</li>
  <li>One question: one validated rewrite call, plus one call to write the answer.</li>
  <li>No price per token is stated in this repository, and the gateway and model are configurable — so the honest unit here is tokens and calls, not currency.</li>
</ul>

<p class="deck-source">data/out/apertus_enrichment.apertus-v1-5-70b.jsonl (2,250 rows) · data/out/metrics.json · docs/reference/web-demo.mdx · docs/ (Starlight site)</p>
