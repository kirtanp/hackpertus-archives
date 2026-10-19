// Print the built deck to one PDF page per slide.
//
// It drives the browser that is already serving the deck over CDP (this machine
// has no /Applications/Google Chrome.app, and other headless routes fail here),
// using reveal's `?print-pdf` export. Usage:
//
//   bun run build                                   # ./dist
//   python3 -m http.server 8600 --directory dist &  # file:// blocks the deck's module script
//   "<chrome>" --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/cdp about:blank &
//   bun scripts/pdf.mjs http://127.0.0.1:8600/slides/archai/ archai-deck.pdf
//
// The two stylesheet overrides this export needs live in src/assets/app.css
// (see "PDF" in README.md); without them the pages come out blank.
import { writeFileSync } from "node:fs";

const [deckUrl, outFile] = process.argv.slice(2);
if (!deckUrl || !outFile) {
  console.error("usage: bun scripts/pdf.mjs <deck-url> <out.pdf>");
  process.exit(2);
}
const port = process.env.CDP_PORT ?? "9333";

const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const target = list.find((t) => t.type === "page");
if (!target) {
  console.error(`no page target on the CDP browser at 127.0.0.1:${port}`);
  process.exit(1);
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve);
  ws.addEventListener("error", reject);
});

let id = 0;
const pending = new Map();
ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
  }
});
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const messageId = ++id;
    pending.set(messageId, { resolve, reject });
    ws.send(JSON.stringify({ id: messageId, method, params }));
  });
const evaluate = async (expression) => {
  const res = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (res.exceptionDetails) throw new Error(JSON.stringify(res.exceptionDetails));
  return res.result.value;
};

await send("Page.enable");
await send("Runtime.enable");
await send("Page.navigate", { url: `${deckUrl}?print-pdf` });

// reveal builds one .pdf-page per slide; wait for them all
let pages = 0;
for (let attempt = 0; attempt < 60; attempt++) {
  await new Promise((resolve) => setTimeout(resolve, 500));
  pages = await evaluate(`document.querySelectorAll('.pdf-page').length`);
  if (pages > 0 && pages === (await evaluate(`document.querySelectorAll('.reveal .slides > section').length`))) break;
}

// make sure every image is decoded before the snapshot
await evaluate(`Promise.all([...document.images].map((i) => i.decode().catch(() => {}))).then(() => true)`);
await new Promise((resolve) => setTimeout(resolve, 1500));

const box = await evaluate(`(() => {
  const page = document.querySelector('.pdf-page');
  const rect = page.getBoundingClientRect();
  return { width: rect.width, height: rect.height, ratio: rect.width / rect.height };
})()`);

const pdf = await send("Page.printToPDF", {
  landscape: false,
  printBackground: true,
  paperWidth: box.width / 96, // the slide box in px at 96 dpi, so 1:1
  paperHeight: box.height / 96,
  marginTop: 0,
  marginBottom: 0,
  marginLeft: 0,
  marginRight: 0,
  preferCSSPageSize: false,
  scale: 1,
});

writeFileSync(outFile, Buffer.from(pdf.data, "base64"));
console.log(`${pages} page(s) at ${box.width}x${box.height} px (ratio ${box.ratio.toFixed(4)}) -> ${outFile}`);
ws.close();
