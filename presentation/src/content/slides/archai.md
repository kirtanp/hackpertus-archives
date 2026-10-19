---
title: "ArchAI"
description: "Seven slides: the challenge, the search and the source it leads to, the goal, the product with its demo link, and the cost in tokens."
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

<p class="deck-lead">Deciphering Swiss Archives with Apertus: Making Swiss archives answerable using Apertus model family</p>

</div>

---

<h2 class="deck-heading">Challenge</h2>

<p class="deck-lead">How can we make archives easily searchable?</p>

Note:
Introduce — spoken budget ~1:30 (the sketch writes "1:30 mins"; it may mean 30 minutes — the captain can correct this
one line). This slide asks the question; the three after it answer it in pictures — the archive's own search, the
transcription that search leads to, and the handwritten original it was typed from — before the goal slide.

---

<div class="deck-shot-full">
  <img class="deck-shot" src="/archive-search-bern.png" alt="A German archive search for Bern: a result table of school places and municipalities listing nine transcriptions with links to each fragment." />
</div>

---

<div class="deck-shot-full">
  <img class="deck-shot" src="/stapfer-questionnaire-answers.png" alt="A detail of one 1799 transcription: German questionnaire rows III.11.e to IV.14 with the teacher's answers beside them, the answer about 82 schoolchildren highlighted." />
</div>

---

<h2 class="deck-heading">The goal: archives that are easily digestible</h2>

<div class="deck-shot-full">
  <img class="deck-shot" src="/stapfer-original-scan.png" alt="A scan of the handwritten 1798 original in Kurrent script: the archival page this entry was transcribed from, showing the answer about 82 schoolchildren." />
</div>

---

<h2 class="deck-heading">Our product</h2>

<p class="deck-demo"><a href="https://github.com/kirtanp/hackpertus-archives/tree/main/static/site">Product demo</a></p>

<p class="deck-demo deck-demo--address">http://localhost:8791/site</p>

Note:
Product — spoken budget 3 min. This is the demo moment: open the app, ask one question, then show the map and
"guess the school".

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

Note:
Business — spoken budget 2 min, all of it on this slide: the comparison of the three artifacts and the measured
tokens. The earlier provenance and future-plans slides were removed at the captain's request; their figures are in
data/out/metrics.json if a question comes up.
