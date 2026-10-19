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

## PDF

`presentation/archai-deck.pdf` is the deck as a PDF: one page per slide, in deck order, eight pages at the deck's 16:9
aspect (750 KB). It is printed from the built deck rather than assembled from screenshots, so the text stays selectable
and the pictures stay real. To regenerate it:

```bash
bun run build                                   # ./dist - the deck, not the single file
python3 -m http.server 8600 --directory dist    # file:// blocks the deck's module script
# print http://127.0.0.1:8600/slides/archai/?print-pdf with the local Chrome over CDP
#   Page.printToPDF, paper = the .pdf-page box (px / 96), printBackground, margins 0, scale 1
```

Reveal's `?print-pdf` export needs two overrides in `src/assets/app.css`, both in the `deck-print` layer and both scoped
to `html.print-pdf`, so the deck on screen is untouched. They cost real time to find, which is why they are here:

- Tailwind's preflight hides every print slide with `[hidden] { display: none !important }`; because an `!important` in
a cascade layer beats an unlayered one, the deck's own `!important` rule has to sit in a layer declared **before**
Tailwind's (hence `@layer deck-print;` at the top of the file).
- Reveal's print layout zeroes the slide padding and keeps the screen layout's `top` offset on each slide, which pushes a
tall slide outside its `.pdf-page` box, where the page's `overflow: hidden` clips it — a picture-only slide then prints
as a blank page. The layer restores the padding (from `--deck-slide-padding`) and pins every slide to its page origin.

## Slides

| # | Section | Slide |
|---|---|---|
| 1 | — | Title |
| 2 | Introduce | Challenge: the question the introduction answers |
| 3 | Introduce | The archive's own search for “Bern” — picture only |
| 4 | Introduce | One source entry in detail — picture only |
| 5 | Introduce | The goal: archives that are easily digestible — title and the handwritten original |
| 6 | Product | Our product — the “Product demo” link and the local address |
| 7 | Business | Cost: a comparison of the three artifacts, and tokens and calls |
| 8 | Try it | The live website: a link that opens `http://100.120.107.76:8791/site/` in a new tab (team Tailscale network) |

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

## The illustration images

The introduction slides carry three images from the captain, all in `public/` (files referenced from inside a
slide body live there, because the body is injected into the reveal textarea as a string and is never processed by
Astro — imported assets only work in the layout):

| File | Slide | What it is | Size on the slide |
|---|---|---|---|
| `archive-search-bern.png` | the problem slide | the archive's own search for “Bern”: a result table listing nine transcriptions with links to each fragment. Cropped from the 1484×988 screenshot to 1484×520 (the Bern block with the first transcription links), under the slide's text. | ~350×124 px |
| `stapfer-questionnaire-answers.png` | the problem slide | one source entry in detail: questionnaire rows III.11.e–IV.14 with the teacher's answers beside them, one highlighted. Used as supplied (1514×792), on the right. | ~390×205 px |
| `stapfer-original-scan.png` | the goal slide | the handwritten 1798 original in Kurrent script, the page that entry was typed from. Used as supplied (1296×694), on the right. | ~555×300 px |

All three are framed with a hairline border (`--color-line`), because they have their own (light) background and would
otherwise float on the white slides. The two slides use the `.deck-split` grid: the slide's own text in
`.deck-split__main` (which never shrinks), the search overview under it in a `.deck-shot-frame`, and the other image in
`.deck-split__aside`. The images take the room that is left, at their own aspect ratio, and the slides do not overflow.

## Figures

Every number on a slide comes from this repository and keeps its stated scope; the small source line at the bottom of
each slide names the file: `README.md`, `data/out/metrics.json`, `qa_answer_check.json`,
`apertus_enrichment.apertus-v1-5-70b.jsonl`, and the documentation site under `docs/`.

## Layout

```
src/content/slides/archai.md   the deck: eight slides separated by ---, presenter notes after "Note:"
src/layouts/SlideLayout.astro  reveal.js wiring (markdown + notes plugin, palette, deck chrome)
src/assets/app.css             ArchAI palette, typography, slide content classes
src/pages/index.astro          small catalog page linking the deck
scripts/single-file.mjs        SINGLE_FILE build -> ./single/<deck>.html
```
