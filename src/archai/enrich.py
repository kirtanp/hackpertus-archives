"""Stage 4: Apertus enrichment (title, summary, entities, legibility) via any OpenAI-compatible endpoint.

Default: local llama.cpp server running the Apertus v1.5 8B GGUF. Set APERTUS_BASE_URL / APERTUS_API_KEY /
APERTUS_MODEL to use a hosted Apertus endpoint instead. Every output row records model + prompt version.
"""
import json, os, random, sys, time, concurrent.futures as cf
from pathlib import Path
import urllib.request

from .llm import BASE, KEY, extract_json
MODEL = os.environ.get("APERTUS_MODEL", "apertus-v1.5-8b-text-q4_k_m")
PROMPT_VERSION = "v2"  # v2: legibility judged from garbled words only; "[…]" gaps no longer appear (they were dashes)
SYSTEM = "You are a careful Swiss archivist. Use only facts present in the text. Reply with JSON only."
USER = """Record from the Stapfer-Enquête (1799 Helvetic Republic school survey); answers are prefixed with the question number.
Return JSON with keys:
 title (in the document's language, max 12 words),
 summary_en (max 50 words),
 persons (list of names exactly as written),
 places (list of place names exactly as written),
 dates (list, as written),
 topics (max 5 short English tags),
 legibility ("good" | "partly_illegible" | "mostly_illegible", judge from garbled or fragmentary words).

Text:
{text}"""


def chat(text, max_tokens=320):
    body = {"model": MODEL, "temperature": 0, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER.format(text=text)}]}
    req = urllib.request.Request(BASE + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    t0 = time.time()
    for a in range(4):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.load(r)
            break
        except Exception:
            if a == 3:
                raise
            time.sleep(2 ** a)
    return d["choices"][0]["message"]["content"], time.time() - t0, d.get("usage", {})


def enrich_doc(doc, max_chars=1800):
    text = "\n".join(filter(None, [doc.get("letterhead_clean"), doc["text_clean"]]))[:max_chars]
    raw, secs, usage = chat(text)
    out = extract_json(raw); ok = out is not None
    out = out if ok else {"_raw": raw}
    return {"doc_id": doc["doc_id"], "archive_signature": doc["provenance"]["archive_signature"], "llm": out,
            "json_valid": ok, "seconds": round(secs, 1), "usage": usage, "model": MODEL,
            "prompt_version": PROMPT_VERSION, "input_chars": len(text), "truncated": len(text) >= max_chars}


def out_path(model=None):
    m = model or MODEL
    return Path("data/out/apertus_enrichment.jsonl" if m == "apertus-v1.5-8b-text-q4_k_m" else f"data/out/apertus_enrichment.{m}.jsonl")


def main(n=60, workers=2, seed=7):
    docs = [json.loads(l) for l in open("data/out/stapfer_documents.jsonl", encoding="utf-8")]
    sample = random.Random(seed).sample(docs, min(n, len(docs)))
    out = out_path()
    done = {json.loads(l)["doc_id"] for l in open(out)} if out.exists() else set()
    todo = [d for d in sample if d["doc_id"] not in done]
    with cf.ThreadPoolExecutor(workers) as ex, open(out, "a", encoding="utf-8") as f:
        for r in ex.map(enrich_doc, todo):
            f.write(json.dumps(r, ensure_ascii=False) + "\n"); f.flush()
            print(r["doc_id"], r["seconds"], r["json_valid"], flush=True)


if __name__ == "__main__":
    main(*(int(x) for x in sys.argv[1:]))
