"""Apertus as orchestrator over deterministic tools.

1. plan:     Apertus picks tools + arguments for the record (logged).
2. baseline: a deterministic policy adds the tools every record of this kind must get, so a weak planner
             cannot silently skip work; planner-vs-baseline agreement is measured.
3. execute:  tools run in code; errors are recorded, never hidden.
4. compose:  Apertus writes metadata from the record + tool results only.
5. validate: deterministic grounding check; ungrounded entities are rejected in export."""
import json, random, sys, time, concurrent.futures as cf
from pathlib import Path
from . import tools as T, llm

OUT = Path("data/out")
PROMPT_VERSION = "agent-v3"
CATALOG = "\n".join(f"- {k}: {v[1]}" for k, v in T.TOOLS.items())

PLAN = """You process one archival record. Choose which deterministic tools to run (max 6 calls).
Tools:
{catalog}
Use literal strings from the record as arguments; pass "$TEXT" to mean the full record text.
Each tool is called at most once per distinct argument. Be brief.
Return compact JSON only: {{"calls": [{{"tool": "...", "args": {{...}}}}], "reason": "max 15 words"}}

Record ({kind}):
{text}"""

COMPOSE = """Compose AI-ready metadata for this archival record using ONLY the record and the tool results.
Return JSON with keys: title (max 12 words, record language), summary_en (max 50 words), persons (names as written),
places (names as written), topics (max 5 English tags), legibility ("good"|"partly_illegible"|"mostly_illegible").

Record:
{text}

Tool results:
{results}"""
SCHEMA = {"title": str, "summary_en": str, "persons": list, "places": list, "topics": list, "legibility": str}


def baseline_calls(kind, meta):
    if kind == "stapfer":
        calls = [("normalize_dates", {"text": "$TEXT"}), ("extract_quantities", {"text": "$TEXT"})]
        calls += [("lookup_place", {"name": p}) for p in meta.get("place_names", [])[:2]]
        calls += [("parse_signature", {"sig": meta["signature"]})] if meta.get("signature") else []
    else:
        calls = [("normalize_dates", {"text": "$TEXT"})]
        calls += [("lookup_archive_unit", {"signature": s}) for s in meta.get("signatures", [])[:3]]
    return calls


TEXT_TOOLS = {"normalize_dates", "extract_quantities", "clean_markup", "verify_in_source"}


def _exec(name, args, text):
    args = {k: (text if v == "$TEXT" else v) for k, v in (args or {}).items()} if isinstance(args, dict) else {}
    if name in TEXT_TOOLS:  # deterministic repair: text tools always read the record itself
        args = {k: v for k, v in args.items() if k in ("entities", "default_year")}
        args["text"] = text
    short = {k: str(v)[:80] for k, v in args.items()}
    try:
        return {"tool": name, "args": short, "result": T.call(name, args)}
    except Exception as e:
        return {"tool": name, "args": short, "error": f"{type(e).__name__}: {e}"}


def run_record(rec_id, kind, text, meta, max_chars=1500):
    text = text[:max_chars]; t0 = time.time()
    tr = {"record": rec_id, "kind": kind, "model": llm.MODEL, "prompt_version": PROMPT_VERSION, "steps": []}
    plan, m = llm.chat_json(PLAN.format(catalog=CATALOG, kind=kind, text=text), max_tokens=500)
    tr["steps"].append({"step": "plan", "output": plan, "meta": m})
    calls = plan if isinstance(plan, list) else (plan.get("calls") if isinstance(plan, dict) else None)
    if isinstance(plan, dict) and calls is None and "tool" in plan:
        calls = [plan]
    tr["plan_truncated"] = (m.get("usage") or {}).get("completion_tokens", 0) >= 500
    planned = [(c.get("tool"), c.get("args")) for c in calls if isinstance(c, dict)][:6] if isinstance(calls, list) else []
    base = baseline_calls(kind, meta)
    tr["planner"] = {"valid_plan": isinstance(calls, list), "n_calls": len(planned),
                     "unknown_tools": [n for n, _ in planned if n not in T.TOOLS],
                     "baseline_tools_chosen": sorted({n for n, _ in base} & {n for n, _ in planned}),
                     "baseline_tools": sorted({n for n, _ in base})}
    seen, results = set(), []
    for origin, cl in (("apertus", planned), ("baseline", base)):
        for n, a in cl:
            key = (n, json.dumps(a, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            r = _exec(n, a, text) if n in T.TOOLS else {"tool": n, "error": "unknown tool"}
            results.append(dict(r, origin=origin))
    tr["steps"].append({"step": "tools", "results": results})
    final, m2 = llm.chat_json(COMPOSE.format(text=text, results=json.dumps(
        [{k: r[k] for k in ("tool", "result") if k in r} for r in results], ensure_ascii=False, default=str)[:3500]),
        schema=SCHEMA, max_tokens=400)
    tr["steps"].append({"step": "compose", "output": final, "meta": m2})
    final = final if isinstance(final, dict) else {}
    ents = [e for k in ("persons", "places") for e in final.get(k) or [] if isinstance(e, str)]
    chk = T.verify_in_source(ents, text)
    tr["steps"].append({"step": "validate", "result": chk})
    tr.update(final=final, compose_valid=bool(final), unsupported=[c["entity"] for c in chk if not c["grounded"]],
              tools_used=[r["tool"] for r in results], tool_errors=sum("error" in r for r in results),
              seconds=round(time.time() - t0, 1))
    return tr


def jobs(n, seed=11):
    docs = [json.loads(l) for l in open(OUT / "stapfer_documents.jsonl", encoding="utf-8")]
    cons = [json.loads(l) for l in open(OUT / "consultations.jsonl", encoding="utf-8")]
    rnd = random.Random(seed)
    for d in rnd.sample(docs, n):
        yield (d["doc_id"], "stapfer", "\n".join(filter(None, [d["letterhead_clean"], d["text_clean"]])),
               {"place_names": [p["name_1799"] for p in d["places"]], "signature": d["provenance"]["archive_signature"]})
    for c in rnd.sample([c for c in cons if c["archive_refs"]], max(1, n // 3)):
        yield (c["doc_id"], "consultation",
               f'{c["title"]}\nDepartement: {c["department"]}\nFrist: {c["consultation_start_raw"]} - {c["consultation_end_raw"]}\n'
               + "\n".join(a["raw"] for a in c["archive_refs"]),
               {"signatures": [a["signature"] for a in c["archive_refs"] if a.get("signature")]})


def main(n=9, workers=4, seed=11):
    out = OUT / "agent_traces.jsonl"
    done = set()
    if out.exists():
        done = {t["record"] for t in map(json.loads, open(out, encoding="utf-8")) if t.get("prompt_version") == PROMPT_VERSION}
    todo = [j for j in jobs(n, seed) if j[0] not in done]
    with cf.ThreadPoolExecutor(workers) as ex, open(out, "a", encoding="utf-8") as f:
        for tr in ex.map(lambda j: run_record(*j), todo):
            f.write(json.dumps(tr, ensure_ascii=False, default=str) + "\n"); f.flush()
            print(tr["record"], tr["seconds"], tr["planner"]["n_calls"], tr["tool_errors"], len(tr["unsupported"]), flush=True)


if __name__ == "__main__":
    main(*(int(x) for x in sys.argv[1:]))
