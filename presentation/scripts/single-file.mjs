// Turn the SINGLE_FILE build output (dist-single) into one self-contained HTML
// file per page under ./single: the deck must open from disk, offline, with its
// images and fonts intact.
//
// Astro already inlines the component <style> blocks, but the external assets
// are not reached yet: the <link rel="stylesheet"> files (Astro CSS, reveal.js
// CSS), the module scripts, the fonts and the images. This script inlines them:
//   - stylesheets -> <style>, with their url(...) resolved to data URIs
//   - <script type="module" src> -> bundled ESM, inlined
//   - fonts, images and other assets referenced by path -> data URIs
//   - sibling links (/slides/<id>/) -> the sibling single file (<id>.html)
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, extname, join, resolve } from "node:path";

const DIST = "dist-single";
const OUT = "single";
const ESBUILD = "node_modules/.bin/esbuild";

const MIME = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
};

const bundleCache = new Map();

function bundle(entry) {
  if (bundleCache.has(entry)) return bundleCache.get(entry);
  const code = execFileSync(
    ESBUILD,
    [entry, "--bundle", "--format=esm", "--minify", "--log-level=error"],
    { maxBuffer: 128 * 1024 * 1024 },
  ).toString();
  bundleCache.set(entry, code);
  return code;
}

function dataUri(absPath) {
  const mime = MIME[extname(absPath).toLowerCase()] ?? "application/octet-stream";
  return `data:${mime};base64,${readFileSync(absPath).toString("base64")}`;
}

/** Rewrite url(...) inside a stylesheet so it points at a data URI. */
function inlineCssUrls(css, cssPath) {
  return css.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, (match, _quote, url) => {
    if (url.startsWith("data:") || url.startsWith("http:") || url.startsWith("https:")) {
      return match;
    }
    const abs = url.startsWith("/") ? join(DIST, url) : resolve(dirname(cssPath), url);
    return existsSync(abs) ? `url(${dataUri(abs)})` : match;
  });
}

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

function inlineHtml(absPath) {
  let html = readFileSync(absPath, "utf8");

  // 1) stylesheets -> inline <style> (keep the file on disk: it is shared)
  html = html.replace(
    /<link\b[^>]*?rel="stylesheet"[^>]*?href="([^"]+)"[^>]*?>/g,
    (match, href) => {
      const abs = href.startsWith("/") ? join(DIST, href) : resolve(dirname(absPath), href);
      if (!existsSync(abs)) return match;
      return `<style type="text/css">\n${inlineCssUrls(readFileSync(abs, "utf8"), abs)}\n</style>`;
    },
  );

  // 2) module scripts -> inline bundled ESM
  html = html.replace(/<script([^>]*?)\bsrc="([^"]+)"([^>]*)><\/script>/g, (match, _pre, src) => {
    if (!src.startsWith("/")) return match;
    const abs = join(DIST, src);
    return existsSync(abs) ? `<script type="module">${bundle(abs)}</script>` : match;
  });

  // 3) modulepreload hints point at chunks that are now inlined
  html = html.replace(/<link\b[^>]*?rel="modulepreload"[^>]*?>/g, "");

  // 4) any remaining asset referenced by path -> data URI (also inside the deck
  //    body string, where the quotes are escaped as \")
  html = html.replace(
    /(["'(])(\/[^"'()\s\\]+\.(?:png|jpe?g|svg|webp|gif|ico|woff2?))(?=\\?["')])/g,
    (match, pre, path) => {
      const abs = join(DIST, path);
      return existsSync(abs) ? pre + dataUri(abs) : match;
    },
  );

  // 4b) no remote assets: the distributed file must not depend on the network
  const remotes = [
    ...new Set(html.match(/https?:\/\/[^"'\s)]+\.(?:png|jpe?g|svg|webp|gif|woff2?)/g) ?? []),
  ].filter((url) => !url.includes("${"));
  if (remotes.length > 0) {
    console.warn(`  ! ${absPath} still references remote asset(s):`);
    for (const url of remotes) console.warn(`      ${url}`);
  }

  // 5) internal navigation -> the sibling single files
  html = html.replace(/href="\/slides\/([A-Za-z0-9_-]+)\/?"/g, 'href="$1.html"');
  html = html.replace(/href="\/"/g, 'href="index.html"');

  return html;
}

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

const files = walk(DIST).filter(
  (file) => file.endsWith(".html") && !file.slice(DIST.length + 1).startsWith("reveal/"),
);

let count = 0;
for (const file of files) {
  const rel = file.slice(DIST.length + 1);
  const name = basename(rel) === "index.html" ? (dirname(rel) === "." ? "index" : basename(dirname(rel))) : basename(rel, ".html");
  const target = join(OUT, `${name}.html`);
  const html = inlineHtml(file);
  writeFileSync(target, html);
  count++;
  console.log(`  ${rel}  ->  ${target}  (${(html.length / 1024 / 1024).toFixed(2)} MB)`);
}
console.log(`\n${count} single-file page(s) written to ./${OUT}`);
