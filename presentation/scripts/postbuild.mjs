// Make ./dist portable, because the built folder is opened straight from disk
// (Finder) as well as served from a web root.
//
// Astro emits root-absolute URLs (`/favicon.svg`, `/_astro/…`, `/reveal/…`),
// and its CSS points at the bundled fonts the same way. Those resolve against
// the server root, so under `file://` they hit the filesystem root instead. This
// script rewrites them to page-relative ones (and stylesheet-relative ones in
// CSS), which resolve identically from a host root and from disk.
//
// It also drops the app's static page in as `demo/`, so the deck's `demo/` link
// opens `dist/demo/index.html` from the host root and from disk alike.
//
// The deck's bundled module script cannot be made portable by rewriting: Chrome
// refuses `file://` module scripts outright ("blocked by CORS policy", origin
// `null`), so it is inlined instead, which runs from the page itself.
import { cpSync, existsSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";

const DIST = "dist";
const DEMO_SRC = "../static/site";
const DEMO_DST = join(DIST, "demo");

/** Path prefix that walks from `dir` back up to the build root. */
function prefixFor(dir) {
  const depth = relative(DIST, dir).split(sep).filter((part) => part && part !== ".").length;
  return depth === 0 ? "./" : "../".repeat(depth);
}

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

function inlineModuleScripts(html, file) {
  return html.replace(
    /<script([^>]*?)\bsrc="([^"]+)"([^>]*)><\/script>/g,
    (match, before, src, after) => {
      if (!/type="module"/.test(before + after)) return match;
      const abs = src.startsWith("/") ? join(DIST, src) : join(dirname(file), src);
      if (!existsSync(abs)) return match;
      return `<script type="module">${readFileSync(abs, "utf8")}</script>`;
    },
  );
}

function relativizeHtml(file) {
  const prefix = prefixFor(dirname(file));
  let html = inlineModuleScripts(readFileSync(file, "utf8"), file);

  // `href="/x"`, `src="/x"`, and the deck body's escaped `src=\"/x\"` (the body
  // is injected into the Reveal textarea as a JS string, so its quotes are
  // escaped and it resolves against the page like any other attribute).
  html = html.replace(
    /\b(href|src)=(\\?)"\/(?!\/)/g,
    (_match, attr, escape) => `${attr}=${escape}"${prefix}`,
  );

  // The same deck body also renders at /slides/<id>/, one level deeper, so its
  // own relative link into the copied app needs that page's prefix as well.
  html = html.replace(
    /\b(href)=(\\?)"demo\//g,
    (_match, attr, escape) => `${attr}=${escape}"${prefix}demo/`,
  );

  writeFileSync(file, html);
  return html;
}

function relativizeCss(file) {
  const prefix = prefixFor(dirname(file));
  const css = readFileSync(file, "utf8").replace(
    /url\(\s*(['"]?)\/(?!\/)/g,
    (_match, quote) => `url(${quote}${prefix}`,
  );
  writeFileSync(file, css);
}

const files = walk(DIST);
const htmlFiles = files.filter((file) => file.endsWith(".html"));
const cssFiles = files.filter((file) => file.endsWith(".css"));
for (const file of htmlFiles) relativizeHtml(file);
for (const file of cssFiles) relativizeCss(file);

// Fail loudly rather than ship a page that only works when served.
const offenders = htmlFiles
  .map((file) => ({
    file,
    hits: readFileSync(file, "utf8").match(/\b(?:href|src)=\\?"\/(?!\/)/g) ?? [],
  }))
  .filter(({ hits }) => hits.length > 0);
if (offenders.length > 0) {
  for (const { file, hits } of offenders) {
    console.error(`  ! ${file}: ${hits.length} absolute reference(s) left: ${hits.join(", ")}`);
  }
  throw new Error("dist still references the server root; the pages would break under file://");
}

console.log(
  `  relativized ${htmlFiles.length} page(s) and ${cssFiles.length} stylesheet(s) for file://`,
);

// The app is placed, never rewritten: its files are byte-identical copies, so
// it keeps its own root-absolute references (it is served by the Python app).
rmSync(DEMO_DST, { recursive: true, force: true });
cpSync(DEMO_SRC, DEMO_DST, { recursive: true });
console.log(`  ${DEMO_SRC}  ->  ${DEMO_DST}`);
