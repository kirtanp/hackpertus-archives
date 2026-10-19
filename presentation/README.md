# ArchAI presentation

A static slide deck about ArchAI: **eight slides**, Astro (static output) + reveal.js + Tailwind v4, no server, no data
fetched at view time, no analytics.

Build pattern: the deck is an Astro content collection entry (`src/content/slides/archai.md`), one `SlideLayout.astro`
turns it into a reveal.js deck, and `SINGLE_FILE=1` makes `scripts/single-file.mjs` emit **one self-contained HTML
file** (with the fonts inlined as data URIs) that opens straight from disk.

## Build it

```bash
bun install --frozen-lockfile
bun run build          # ./dist       - the site, deck at /slides/archai/
bun run build:single   # ./single     - one self-contained HTML file per page
```

With [`just`](https://just.systems/): `just build`, `just html` (single files), `just html-zip` (distributable zip).
`bun run setup` copies `node_modules/reveal.js` into `public/reveal/` (gitignored); every build script runs it first.

## Present it

- Open `dist/slides/archai/index.html` (or `single/archai.html`) and press `s` for reveal's speaker view; the section
  timings and the speaker notes are reveal presenter notes, not slide furniture.
- The deck is self-contained: `single/archai.html` works offline from a file path.

## Slides

| # | Section | Slide |
|---|---|---|
| 1 | — | Title |
| 2 | Introduce | Challenge: the question the introduction answers |
| 3 | Introduce | The problem, and what the product solves |
| 4 | Introduce | The goal: archives that are easily digestible |
| 5 | Product | The product: the working thing itself |
| 6 | Business | Provenance |
| 7 | Business | Pipelining, onboarding more datasets, a new customer base |
| 8 | Business | Cost: a comparison of the three artifacts, and tokens and calls |

The outline is the content plan; slides stay at that density. Timings (`Introduce`, `Product`, `Business`) are presenter
notes on the slide that opens the section.

## The header mark

The mark in the top-left of every slide is `src/assets/archai-mark.png`, prepared from the captain's `logo.webp`
(1024×572) once, by hand:

- **Tight crop** of the artwork (measured bounding box `(365,61)–(717,487)`, plus 16 px for the red glow), because the
  source frame is mostly empty margin.
- **No transparency key.** The source has an opaque cream background (`rgb(247,248,244)`) and the mark's white Swiss
  cross is only 7 levels away from it, so a colour key would have punched holes in the cross. Instead the measured
  background is mapped to pure white with a per-channel gain (1.0324 / 1.0282 / 1.0451), so the artwork's own panel
  matches the white slides instead of showing as a pale rectangle.
- Downscaled to 101×120 (3× the 36 px header height) and saved as PNG (16 KB).

`src/layouts/SlideLayout.astro` renders it fixed at the top-left of the viewport, so it shows on the title slide as on
content slides, and the slide padding (`3.4rem` top) keeps every title clear of it.

## Figures

Every number on a slide comes from this repository and keeps its stated scope; the small source line at the bottom of
each slide names the file: `README.md`, `data/out/metrics.json`, `qa_eval.json`, `qa_answer_check.json`,
`apertus_enrichment.apertus-v1-5-70b.jsonl`, and the documentation site under `docs/`.

## Layout

```
src/content/slides/archai.md   the deck: eight slides separated by ---, presenter notes after "Note:"
src/layouts/SlideLayout.astro  reveal.js wiring (markdown + notes plugin, palette, deck chrome)
src/assets/app.css             ArchAI palette, typography, slide content classes
src/pages/index.astro          small catalog page linking the deck
scripts/single-file.mjs        SINGLE_FILE build -> ./single/<deck>.html
```
