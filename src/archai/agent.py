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


# ---------------- agent-v4: native tool calls, loop, parallel execution, argument repair ----------------
import difflib, inspect, re
PROMPT_VERSION_V4 = "agent-v4"
MAX_STEPS, MAX_CALLS, FEEDBACK_ROUNDS = 6, 14, 1
MISSING_HINT = {"normalize_dates": "the dates in the record (normalize_dates)", "extract_quantities": "pupil counts and money (extract_quantities)",
                "lookup_place": "each place the record names (lookup_place)", "parse_signature": "the archive signature (parse_signature)",
                "lookup_archive_unit": "each federal archive signature in the Archive Database (lookup_archive_unit)"}
TEXT_BOUND = {"normalize_dates", "extract_quantities", "clean_markup"}  # always read the record: no text argument exposed
_JT = {str: "string", int: "integer", float: "number", list: "array", bool: "boolean"}


def tool_specs():
    """JSON-schema tool specs generated from the Python signatures in tools.py."""
    specs = []
    for name, (fn, desc) in T.TOOLS.items():
        props, req = {}, []
        for pn, prm in inspect.signature(fn).parameters.items():
            if pn == "text" and name in TEXT_BOUND | {"verify_in_source"}:
                continue  # bound to the record
            t = _JT.get(prm.annotation, "string")
            props[pn] = {"type": t, **({"items": {"type": "string"}} if t == "array" else {})}
            if prm.default is inspect.Parameter.empty:
                req.append(pn)
        d = desc.split(" args:")[0] + (" Works on the record text." if name in TEXT_BOUND | {"verify_in_source"} else "")
        specs.append({"type": "function", "function": {"name": name, "description": d,
                      "parameters": {"type": "object", "properties": props, "required": req}}})
    specs.append({"type": "function", "function": {"name": "finish", "description": "Call when you have run every tool you need.",
                  "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": []}}})
    return specs


SPECS = None


def _params(name):
    return {s["function"]["name"]: s["function"]["parameters"] for s in (SPECS or tool_specs())}[name]


def repair_args(name, args):
    """Map near-miss argument names onto the schema; returns (args, repairs, error)."""
    if name not in T.TOOLS:
        return None, [], f"unknown tool '{name}'; available: {', '.join(T.TOOLS)}"
    sch = _params(name); props, req = set(sch["properties"]), sch["required"]
    args = args if isinstance(args, dict) else {}
    out, repairs = {}, []
    for k, v in args.items():
        if k in props:
            out[k] = v
        elif k == "text" and name in TEXT_BOUND | {"verify_in_source"}:
            continue  # the record text is supplied by the agent
        else:
            free, need = list(props - set(out)), [r for r in req if r not in out and r not in args]
            m = difflib.get_close_matches(k, free, n=1, cutoff=0.5) or (need if len(need) == 1 else []) or (free if len(free) == 1 else [])
            if m:
                out[m[0]] = v; repairs.append(f"{k}->{m[0]}")
            else:
                repairs.append(f"dropped:{k}")
    for k, v in list(out.items()):  # coerce to the schema type ("1" -> 1, "a; b" -> ["a", "b"])
        t = sch["properties"][k]["type"]
        try:
            if t == "integer" and not isinstance(v, int):
                out[k] = int(str(v).strip())
            elif t == "array" and isinstance(v, str):
                out[k] = [x.strip() for x in re.split(r"[;,]", v.strip("[]")) if x.strip().strip("'\"")]
            elif t == "string" and not isinstance(v, str):
                out[k] = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
        except ValueError:
            repairs.append(f"dropped:{k}(not {t})"); out.pop(k)
    missing = [r for r in req if r not in out]
    if missing:
        return None, repairs, f"missing argument(s) {missing}; expected {sch['properties']}"
    return out, repairs, None


def _run_tool(name, args, text):
    a = dict(args)
    if name in TEXT_BOUND or name == "verify_in_source":
        a["text"] = text
    try:
        return {"result": T.call(name, a)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


SYSTEM_V4 = """You prepare one archival record for an AI-ready dataset by calling deterministic tools.
Call the tools you need (several at once is fine); you will see their results and may call more.
Use literal strings from the record as arguments. parse_signature and lookup_archive_unit take archive signatures
(e.g. "BAR B0 1000/1483, Nr. 1421" or "E6100C#1998/106#1039*"), not question numbers like "I.1.a".
A record usually needs: its dates, its quantities, each place it names, and its archive signature(s) parsed and,
for federal signatures, looked up. When you have what you need, call finish."""
JSON_LOOP = """{system}
Tools (JSON schema):
{specs}
Reply with JSON only: {{"calls": [{{"tool": "...", "args": {{...}}}}]}} to call tools, or {{"calls": [{{"tool": "finish"}}]}} when finished.

Record ({kind}):
{text}{history}"""


def run_record_v4(rec_id, kind, text, meta, max_chars=1500, mode="json"):
    """mode "json" (default): tool schemas in the prompt, the model may batch calls; "native": the gateway's tools API, which
    returns one call per turn on this gateway (see data/out/tool_bench.json)."""
    global SPECS
    SPECS = SPECS or tool_specs()
    text = text[:max_chars]; t0 = time.time()
    tr = {"record": rec_id, "kind": kind, "model": llm.MODEL, "prompt_version": PROMPT_VERSION_V4, "steps": [], "mode": mode}
    msgs = [{"role": "system", "content": SYSTEM_V4}, {"role": "user", "content": f"Record ({kind}):\n{text}"}]
    cache, results, n_calls, history = {}, [], 0, ""
    stats = {"steps": 0, "calls_per_step": [], "repairs": [], "arg_errors": 0, "self_corrected": 0, "duplicates": 0, "feedback_rounds": 0}
    required = {n_ for n_, _ in baseline_calls(kind, meta)}
    chosen_before_feedback = None
    failed_tools = set()
    for step in range(MAX_STEPS):
        if mode == "native":
            msg, m = llm.chat_tools(msgs, SPECS)
            if msg is None and m.get("invalid_tool_call"):  # malformed call from the model: one retry
                stats["invalid_tool_calls"] = stats.get("invalid_tool_calls", 0) + 1
                msg, m = llm.chat_tools(msgs, SPECS)
            if msg is None and (m.get("tools_unsupported") or m.get("invalid_tool_call")):
                # no tool API, or malformed twice: same loop on the JSON protocol, carrying the results so far
                mode = tr["mode"] = "native->json" if step else "json"
                tr["fallback"] = {"step": step, "errors": m.get("errors")}
                history = "".join(f"\n{r['tool']}({json.dumps(r['args'], ensure_ascii=False)}) -> "
                                  + json.dumps({k: r[k] for k in ("result", "error") if k in r}, ensure_ascii=False, default=str)[:900] for r in results)
                history = ("\n\nResults so far:" + history) if history else ""
            elif msg is None:
                tr["steps"].append({"step": f"call_{step}", "error": m.get("errors")}); break
            else:
                calls = [(c["function"]["name"], c["function"].get("arguments"), c.get("id")) for c in msg.get("tool_calls") or []]
        if mode in ("json", "native->json"):
            obj, m = llm.chat_json(JSON_LOOP.format(system=SYSTEM_V4, specs=json.dumps(SPECS, ensure_ascii=False), kind=kind, text=text, history=history), max_tokens=400)
            calls = [(c.get("tool"), c.get("args"), None) for c in (obj or {}).get("calls", []) if isinstance(c, dict)] if isinstance(obj, dict) else []
            msg = None
        finishing = any(c[0] == "finish" for c in calls) or not calls
        calls = [c for c in calls if c[0] != "finish"]
        if finishing and not calls:
            missing = required - {r["tool"] for r in results if "error" not in r}
            if missing and stats["feedback_rounds"] < FEEDBACK_ROUNDS and step < MAX_STEPS - 1:
                # deterministic critic: say what is still missing once; the model decides what to call
                chosen_before_feedback = {r["tool"] for r in results if "error" not in r}
                stats["feedback_rounds"] += 1
                note = "Not done yet: " + "; ".join(MISSING_HINT[t] for t in sorted(missing)) + ". Call those tools now, or finish if they do not apply."
                tr["steps"].append({"step": f"call_{step}", "feedback": note})
                if mode == "native" and msg is not None:
                    msgs.append({"role": "assistant", "content": msg.get("content") or "Done."})
                    msgs.append({"role": "user", "content": note})
                else:
                    history += "\n\n" + note
                continue
            tr["steps"].append({"step": f"call_{step}", "done": True, "finish": True}); break
        if not calls:
            tr["steps"].append({"step": f"call_{step}", "done": True, "meta": {k: m.get(k) for k in ("seconds", "finish", "errors")}}); break
        stats["steps"] += 1; stats["calls_per_step"].append(len(calls))
        prepared = []
        for name, raw, cid in calls[: max(0, MAX_CALLS - n_calls)]:
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                args = {}
            a, rep, err = repair_args(name, args)
            stats["repairs"] += rep
            prepared.append((name, a, cid, err, rep))
        n_calls += len(prepared)
        def ex(p):
            name, a, cid, err, rep = p
            if err:
                return {"error": err}
            key = (name, json.dumps(a, sort_keys=True, default=str))
            if key in cache:
                stats["duplicates"] += 1; return dict(cache[key], cached=True)
            r = _run_tool(name, a, text); cache[key] = r; return r
        with cf.ThreadPoolExecutor(max(1, len(prepared))) as pool:  # calls of one turn run in parallel
            outs = list(pool.map(ex, prepared))
        step_res = []
        for (name, a, cid, err, rep), r in zip(prepared, outs):
            if "error" in r:
                stats["arg_errors"] += bool(err); failed_tools.add(name)
            elif name in failed_tools:
                stats["self_corrected"] += 1; failed_tools.discard(name)
            res = {"tool": name, "args": {k: str(v)[:80] for k, v in (a or {}).items()}, "origin": "apertus", "step": step, **({"repairs": rep} if rep else {}), **r}
            results.append(res); step_res.append(res)
        tr["steps"].append({"step": f"call_{step}", "calls": len(prepared), "results": step_res, "meta": {k: m.get(k) for k in ("seconds", "finish")}})
        if step_res and all(r.get("cached") for r in step_res):  # no new information: stop instead of looping
            tr["steps"][-1]["stopped"] = "only repeated calls"; break
        feed = lambda r: json.dumps({k: r[k] for k in ("result", "error") if k in r}, ensure_ascii=False, default=str)[:900]
        if mode == "native" and msg is not None:
            # the gateway rejects history calls with empty arguments ("{}"): record them with a placeholder
            norm = []
            for c in [c for c in msg["tool_calls"] if c["function"]["name"] != "finish"][: len(prepared)]:
                try:
                    a = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    a = {}
                norm.append(dict(c, function=dict(c["function"], arguments=json.dumps(a or {"text": "$RECORD"}, ensure_ascii=False))))
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": norm})
            msgs += [{"role": "tool", "tool_call_id": cid, "content": feed(r)} for (_, _, cid, _, _), r in zip(prepared, step_res)]
        else:
            history += "\n\nStep %d results:\n" % step + "\n".join(f"{r['tool']}({json.dumps(r['args'], ensure_ascii=False)}) -> {feed(r)}" for r in step_res)
        if n_calls >= MAX_CALLS:
            break
    planned = {r["tool"] for r in results if "error" not in r}
    base = baseline_calls(kind, meta)
    for n_, a_ in base:  # the guarantee: required tools run even if the model skipped them
        a2, _, err = repair_args(n_, {k: v for k, v in a_.items() if v != "$TEXT"})
        key = (n_, json.dumps(a2, sort_keys=True, default=str))
        if err or key in cache:
            continue
        r = _run_tool(n_, a2, text); cache[key] = r
        results.append({"tool": n_, "args": {k: str(v)[:80] for k, v in a2.items()}, "origin": "baseline", **r})
    tr["planner"] = {"valid_plan": stats["steps"] > 0 or any(st.get("done") for st in tr["steps"]), "n_calls": n_calls,
                     "unknown_tools": [r["tool"] for r in results if r.get("origin") == "apertus" and r["tool"] not in T.TOOLS],
                     "baseline_tools_chosen": sorted({n_ for n_, _ in base} & planned), "baseline_tools": sorted({n_ for n_, _ in base})}
    stats["chosen_before_feedback"] = sorted((chosen_before_feedback if chosen_before_feedback is not None else planned) & required)
    tr["loop"] = stats
    final, m2 = llm.chat_json(COMPOSE.format(text=text, results=json.dumps(
        [{k: r[k] for k in ("tool", "result") if k in r} for r in results], ensure_ascii=False, default=str)[:3500]), schema=SCHEMA, max_tokens=400)
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


def main(n=9, workers=4, seed=11, version=None, out=None, mode="json"):
    version = version or PROMPT_VERSION_V4
    run = run_record_v4 if version == PROMPT_VERSION_V4 else run_record
    out = Path(out) if out else OUT / "agent_traces.jsonl"
    done = set()
    if out.exists():
        done = {t["record"] for t in map(json.loads, open(out, encoding="utf-8")) if t.get("prompt_version") == version}
    todo = [j for j in jobs(n, seed) if j[0] not in done]
    with cf.ThreadPoolExecutor(workers) as ex, open(out, "a", encoding="utf-8") as f:
        for tr in ex.map(lambda j: run(*j, mode=mode) if run is run_record_v4 else run(*j), todo):
            f.write(json.dumps(tr, ensure_ascii=False, default=str) + "\n"); f.flush()
            print(tr["record"], tr["seconds"], tr["planner"]["n_calls"], tr["tool_errors"], len(tr["unsupported"]), flush=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int, nargs="?", default=9); ap.add_argument("workers", type=int, nargs="?", default=4)
    ap.add_argument("--seed", type=int, default=11); ap.add_argument("--version", choices=[PROMPT_VERSION, PROMPT_VERSION_V4], default=PROMPT_VERSION_V4)
    ap.add_argument("--out"); ap.add_argument("--mode", choices=["native", "json"], default="json")
    a = ap.parse_args()
    main(a.n, a.workers, a.seed, a.version, a.out, a.mode)
