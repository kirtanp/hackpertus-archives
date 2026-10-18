"""Tool-calling benchmark for Apertus on this project's tools, modelled on the Berkeley Function Calling Leaderboard
(BFCL) categories, scored the BFCL "AST" way (function name exact, required arguments equal after normalisation).

    PYTHONPATH=src python -m archai.tool_bench [model ...]      # default: apertus-v1-5-70b apertus-v1-5-8b

Categories (single request each, all 8 tools offered every time):
  simple             one instruction -> one correct call
  parallel           the same tool on several values in one turn
  parallel_multiple  different tools in one turn
  irrelevance        nothing a tool can do -> the correct answer is no call
Formats: "native" = OpenAI tools API of the gateway; "json" = the tool schemas in the prompt, reply {"calls": [...]}.
Values come from the data (place names, signatures, record text); instructions are DE/FR/EN templates.
Writes data/out/tool_bench.json and tool_bench_cases.jsonl."""
import json, random, re, sys, time, unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from . import llm
from .agent import tool_specs, TEXT_BOUND

OUT = Path("data/out")
SYSTEM = "You are an assistant for Swiss archival records. Use the tools when they help; call several at once when the request needs several. If no tool fits, answer without calling one."


def _n(v):
    return unicodedata.normalize("NFKC", str(v)).strip().casefold()


def build_cases(seed=5):
    rnd = random.Random(seed)
    docs = [json.loads(l) for l in open(OUT / "stapfer_documents.jsonl", encoding="utf-8")]
    cons = [json.loads(l) for l in open(OUT / "consultations.jsonl", encoding="utf-8")]
    places = sorted({p["name_1799"] for d in docs for p in d["places"] if p["name_1799"] and " " not in p["name_1799"] and len(p["name_1799"]) > 3})
    bar = sorted({d["provenance"]["archive_signature"] for d in docs if d["provenance"]["archive_signature"]})
    fed = sorted({a["signature"] for c in cons for a in c["archive_refs"] if a.get("signature")})
    texts = [d["text_clean"][:500] for d in rnd.sample(docs, 30)]
    C = []
    def add(cat, lang, q, gold, record=None):
        C.append({"id": f"{cat}-{len(C)}", "category": cat, "lang": lang, "q": q, "record": record, "gold": gold})
    T = {  # (lang, template)
        "lookup_place": [("de", "Wo liegt der Ort {v}? Gib mir die Koordinaten."), ("fr", "Où se trouve le lieu {v} ? Donne ses coordonnées."), ("en", "Find the modern municipality and coordinates of {v}.")],
        "parse_signature": [("de", "Zerlege die Archivsignatur {v} in ihre Teile."), ("en", "Split the archive signature {v} into fonds, number and folio.")],
        "lookup_archive_unit": [("de", "Suche die Signatur {v} in der Archivdatenbank des Bundesarchivs."), ("fr", "Cherche la cote {v} dans la base de données des Archives fédérales.")],
        "normalize_dates": [("de", "Finde alle Datumsangaben in diesem Dokument."), ("en", "Extract the dates from this record as ISO dates.")],
        "extract_quantities": [("de", "Wie viele Schüler und welche Geldbeträge nennt dieses Dokument?"), ("fr", "Combien d'élèves et quelles sommes d'argent ce document mentionne-t-il ?")],
        "clean_markup": [("en", "Strip the transcription markup from this record and keep the annotations.")],
        "verify_in_source": [("en", "Check whether the names {v} literally occur in this record.")],
    }
    for _ in range(2):
        for lang, t in T["lookup_place"]:
            v = rnd.choice(places); add("simple", lang, t.format(v=v), [["lookup_place", {"name": v}]])
        for lang, t in T["parse_signature"]:
            v = rnd.choice(bar + fed); add("simple", lang, t.format(v=v), [["parse_signature", {"sig": v}]])
        for lang, t in T["lookup_archive_unit"]:
            v = rnd.choice(fed); add("simple", lang, t.format(v=v), [["lookup_archive_unit", {"signature": v}]])
        for tool in ("normalize_dates", "extract_quantities", "clean_markup"):
            for lang, t in T[tool]:
                add("simple", lang, t, [[tool, {}]], record=rnd.choice(texts))
        for lang, t in T["verify_in_source"]:
            rec = rnd.choice(docs); names = [x["name"] for x in rec["teachers"][:1]] + [rnd.choice(places)]
            add("simple", lang, t.format(v=" and ".join(names)), [["verify_in_source", {"entities": names}]], record=rec["text_clean"][:500])
    for k in (2, 3, 3, 4):
        for lang, t in (("de", "Wo liegen die Orte {v}?"), ("fr", "Où se trouvent les lieux {v} ?"), ("en", "Look up the places {v}.")):
            vs = rnd.sample(places, k); add("parallel", lang, t.format(v=", ".join(vs)), [["lookup_place", {"name": v}] for v in vs])
    for k in (2, 3):
        for lang, t in (("de", "Suche diese Signaturen in der Archivdatenbank: {v}"), ("en", "Look up these signatures in the Archive Database: {v}")):
            vs = rnd.sample(fed, k); add("parallel", lang, t.format(v="; ".join(vs)), [["lookup_archive_unit", {"signature": v}] for v in vs])
    for _ in range(4):
        p, s, f = rnd.choice(places), rnd.choice(bar), rnd.choice(fed)
        add("parallel_multiple", "de", f"Wo liegt {p}, und zerlege die Signatur {s}.", [["lookup_place", {"name": p}], ["parse_signature", {"sig": s}]])
        add("parallel_multiple", "en", f"Look up {f} in the Archive Database and find the place {p}.", [["lookup_archive_unit", {"signature": f}], ["lookup_place", {"name": p}]])
        add("parallel_multiple", "fr", "Donne les dates et les quantités (élèves, argent) de ce document.", [["normalize_dates", {}], ["extract_quantities", {}]], record=rnd.choice(texts))
    for lang, q in [("en", "Translate this record into English."), ("de", "Fasse dieses Dokument in zwei Sätzen zusammen."),
                    ("fr", "Quel temps fait-il à Berne aujourd'hui ?"), ("en", "Write a short poem about a village school."),
                    ("de", "Wer war Philipp Albert Stapfer?"), ("en", "What is the capital of Switzerland?"),
                    ("fr", "Traduis ce document en allemand moderne."), ("en", "Who won the 1984 football World Cup?"),
                    ("de", "Wie spät ist es jetzt?"), ("en", "Rate the handwriting quality of this teacher.")]:
        add("irrelevance", lang, q, [], record=rnd.choice(texts) if "record" in q or "document" in q.lower() or "Dokument" in q else None)
    return C


def _calls_native(msg):
    out = []
    for c in (msg or {}).get("tool_calls") or []:
        try:
            a = json.loads(c["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            a = {"_invalid_json": c["function"].get("arguments")}
        out.append([c["function"]["name"], a if isinstance(a, dict) else {}])
    return out


def ask(case, model, fmt, specs):
    llm.MODEL = model
    user = case["q"] + (f"\n\nRecord:\n{case['record']}" if case["record"] else "")
    t0 = time.time()
    if fmt == "native":
        msg, meta = llm.chat_tools([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], specs, max_tokens=400)
        calls, err = _calls_native(msg), meta.get("errors")
    else:
        prompt = (f"{SYSTEM}\nTools (JSON schema):\n{json.dumps(specs, ensure_ascii=False)}\n"
                  'Reply with JSON only: {"calls": [{"tool": "...", "args": {...}}]} — an empty list if no tool fits.\n\n' + user)
        obj, meta = llm.chat_json(prompt, max_tokens=400)
        calls = [[c.get("tool"), c.get("args") if isinstance(c.get("args"), dict) else {}] for c in (obj or {}).get("calls", []) if isinstance(c, dict)] if isinstance(obj, dict) else []
        err = meta.get("errors") if obj is None else None
    return calls, err, round(time.time() - t0, 2)


def _key(name, args, required):
    keep = {k: (sorted(_n(x) for x in v) if isinstance(v, list) else _n(v)) for k, v in (args or {}).items() if k in required}
    return name, json.dumps(keep, sort_keys=True, ensure_ascii=False)


def score(case, calls, specs):
    req = {s["function"]["name"]: s["function"]["parameters"]["required"] for s in specs}
    gold = Counter(_key(n, a, req.get(n, [])) for n, a in case["gold"])
    got = Counter(_key(n, a, req.get(n, [])) for n, a in calls if n != "finish")
    names_known = all(n in req for n, _ in calls)
    tp = sum((gold & got).values())
    return {"exact": gold == got, "tool_names_known": names_known,
            "precision": round(tp / sum(got.values()), 3) if got else (1.0 if not gold else 0.0),
            "recall": round(tp / sum(gold.values()), 3) if gold else (1.0 if not got else 0.0),
            "right_tools": Counter(n for n, _ in case["gold"]) == Counter(n for n, _ in calls if n != "finish"), "n_calls": len(calls)}


def run(models=("apertus-v1-5-70b", "apertus-v1-5-8b"), formats=("native", "json"), workers=4):
    specs = tool_specs()
    cases = build_cases()
    rows = []
    for model in models:
        for fmt in formats:
            with ThreadPoolExecutor(workers) as ex:
                outs = list(ex.map(lambda c: ask(c, model, fmt, specs), cases))
            for c, (calls, err, sec) in zip(cases, outs):
                rows.append({"model": model, "format": fmt, **{k: c[k] for k in ("id", "category", "lang", "q", "gold")}, "calls": calls,
                             "error": err, "seconds": sec, **score(c, calls, specs)})
    rep = {"scope": f"{len(cases)} single-turn cases on this project's 8 tools (BFCL-style categories: "
                    + ", ".join(f"{k} {v}" for k, v in Counter(c['category'] for c in cases).items()) + "); seed 5",
           "scoring": "exact = the multiset of (tool, required arguments) equals gold after case/space normalisation (BFCL AST style); "
                      "irrelevance is exact only with no call; optional arguments are ignored", "results": {}}
    for model in models:
        for fmt in formats:
            R = [r for r in rows if r["model"] == model and r["format"] == fmt]
            by = {}
            for cat in ("simple", "parallel", "parallel_multiple", "irrelevance"):
                rc = [r for r in R if r["category"] == cat]
                by[cat] = {"cases": len(rc), "exact": round(sum(r["exact"] for r in rc) / len(rc), 3),
                           "right_tools": round(sum(r["right_tools"] for r in rc) / len(rc), 3),
                           "call_recall": round(sum(r["recall"] for r in rc) / len(rc), 3)}
            by["overall_exact"] = round(sum(r["exact"] for r in R) / len(R), 3)
            by["by_language_exact"] = {l: round(sum(r["exact"] for r in R if r["lang"] == l) / max(1, sum(r["lang"] == l for r in R)), 3) for l in ("de", "fr", "en")}
            by["unknown_tool_names"] = sum(not r["tool_names_known"] for r in R)
            by["request_errors"] = sum(bool(r["error"]) for r in R)
            by["seconds_median"] = sorted(r["seconds"] for r in R)[len(R) // 2]
            rep["results"][f"{model} / {fmt}"] = by
    json.dump(rep, open(OUT / "tool_bench.json", "w"), indent=1, ensure_ascii=False)
    with open(OUT / "tool_bench_cases.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    return rep


if __name__ == "__main__":
    run(tuple(sys.argv[1:]) or ("apertus-v1-5-70b", "apertus-v1-5-8b"))
