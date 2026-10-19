"""Stage 6: metrics report. Every number states its scope; nothing is an 'overall accuracy'."""
import json, re, time, unicodedata
from pathlib import Path
import pandas as pd

OUT = Path("data/out")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s).split()


def found_in(entity, text_tokens):
    toks = [t for t in norm(entity) if len(t) > 2]
    return bool(toks) and all(t in text_tokens for t in toks)


def stapfer_metrics():
    docs = [json.loads(l) for l in open(OUT / "stapfer_documents.jsonl", encoding="utf-8")]
    a = pd.read_parquet(OUT / "stapfer_answers.parquet", columns=["text_raw", "text_clean", "markup_errors", "uncertain_readings", "dash_marks", "page_start"])
    leftover = a.text_clean.dropna().str.contains(r"[@$¢€¦£¥]|°°|##").sum()
    n = len(docs)
    return {
        "scope": "Stapfer-Enquête, all transcriptions in the open-data SQL dump",
        "documents": n, "answers": len(a),
        "documents_with_text": sum(bool(d["text_clean"]) for d in docs),
        "documents_with_archive_signature": sum(bool(d["provenance"]["archive_signature"]) for d in docs),
        "documents_with_facsimile_ref": sum(bool(d["provenance"]["facsimile_file"]) for d in docs),
        "documents_with_geocoded_place": sum(any(p["lat"] for p in d["places"]) for d in docs),
        "documents_with_teacher": sum(bool(d["teachers"]) for d in docs),
        "structural_error_rate_documents": round(sum(d["markup_errors"] > 0 for d in docs) / n, 4),
        "structural_error_definition": "share of documents whose raw transcription has >=1 unbalanced markup pair (source-side issue, kept and flagged)",
        "structural_error_rate_answers": round(float((a.markup_errors > 0).mean()), 4),
        "structural_error_rate_answers_definition": "share of answers whose raw text has >=1 unbalanced markup pair (source-side; flagged, text kept)",
        "answers_with_leftover_markup_after_cleaning": int(leftover),
        "documents_with_uncertain_readings": sum(d["uncertain_readings"] > 0 for d in docs),
        "uncertain_readings_distinct": int(sum(d["uncertain_readings"] for d in docs)),
        "uncertain_readings_definition": "transcriber-marked probable readings #..## (Editionsregeln §15), counted once per distinct answer text",
        "dash_marks_restored": int(sum(d["dash_marks"] for d in docs)),
        "dash_marks_note": "edition markup °-°° is a dash/filler line (verified on facsimiles); earlier versions mis-counted it as an illegible gap",
        "documents_with_page_markers": sum(d["pages"] > 1 for d in docs),
        "language_detected": pd.Series([d["language"] for d in docs]).value_counts(dropna=False).to_dict(),
    }


def bias_metrics(threshold=0.10):
    """Bias-aware preprocessing check: does each extraction step work equally well for every source language? Rates are
    per Stapfer sheet; a gap above `threshold` between German and French is flagged. "before_fix" are the rates of the
    German-only extractor (October 2026), kept for transparency."""
    recs = [json.loads(l) for l in open(OUT / "records.jsonl", encoding="utf-8")]
    st = [r for r in recs if r["dataset"] == "stapfer-enquete-1799"]
    checks = {"pupil_counts_extracted": lambda r: bool(r["quantities"]["pupils"]), "money_amounts_extracted": lambda r: bool(r["quantities"]["money"]),
              "dates_found": lambda r: bool(r["dates"]), "place_geocoded": lambda r: any(p.get("lat") for p in r["places"]),
              "teacher_named": lambda r: bool(r["persons"]), "apertus_enriched": lambda r: r["enrichment"]["source"] != "deterministic"}
    out = {"scope": "Stapfer sheets by detected language (it: 3 sheets, too few to compare)", "threshold": threshold,
           "rates": {}, "gaps_de_fr": {}, "flagged": [],
           "before_fix": {"pupil_counts_extracted": {"de": 0.77, "fr": 0.43}, "money_amounts_extracted": {"de": 0.78, "fr": 0.36}},
           "fix": "extract_quantities reads French and German number words, both writing orders, French pupil/currency words "
                  "and bare numbers in the pupils question (III.12); precision of newly added extractions 36/40 in a hand-checked sample"}
    for lang in ("de", "fr", "it"):
        rs = [r for r in st if r["language"] == lang]
        out["rates"][lang] = {"sheets": len(rs), **{k: round(sum(f(r) for r in rs) / len(rs), 3) for k, f in checks.items()}} if rs else {"sheets": 0}
    # dates: the sources themselves differ (French sheets are dated less often), so parity is measured on the sheets that
    # carry a year where the pipeline looks for the date of writing (letterhead + end of the sheet)
    docs = {json.loads(l)["doc_id"]: json.loads(l) for l in open(OUT / "stapfer_documents.jsonl", encoding="utf-8")}
    dated = lambda r: bool(re.search(r"\b1[78]\d\d\b", (docs[r["record_id"]]["letterhead_clean"] or "") + " " + docs[r["record_id"]]["text_clean"][-400:]))
    for lang in ("de", "fr"):
        rs = [r for r in st if r["language"] == lang]; d = [r for r in rs if dated(r)]
        out["rates"][lang]["sheets_with_a_year_in_source"] = round(len(d) / len(rs), 3)
        out["rates"][lang]["dates_found_where_source_dated"] = round(sum(bool(r["dates"]) for r in d) / len(d), 3)
    out["source_side_gaps"] = {"dates_found": "French sheets carry a date less often (year in letterhead/end of sheet: see "
                                              "sheets_with_a_year_in_source); parity is checked on dates_found_where_source_dated"}
    for k in list(checks) + ["dates_found_where_source_dated"]:
        gap = round(out["rates"]["de"][k] - out["rates"]["fr"][k], 3)
        out["gaps_de_fr"][k] = gap
        if abs(gap) > threshold and k not in out["source_side_gaps"]:
            out["flagged"].append(k)
    return out


def link_metrics():
    s = json.load(open(OUT / "link_stats.json"))
    recs = [json.loads(l) for l in open(OUT / "consultations.jsonl", encoding="utf-8")]
    s.update({"scope": "Federal Consultations 1960-1991 x OLR archive database (exact signature match)",
              "link_rate": round(s["linked_to_olr"] / s["archive_refs_with_signature"], 4),
              "consultations_with_any_archive_ref": sum(bool(r["archive_refs"]) for r in recs),
              "consultations_with_linked_dossier": sum(any("olr" in a for a in r["archive_refs"]) for r in recs),
              "start_date_parsed": sum(bool(r["consultation_start"]) for r in recs),
              "start_date_present": sum(bool(r["consultation_start_raw"]) for r in recs)})
    return s


def apertus_metrics(p=OUT / "apertus_enrichment.jsonl"):
    if not p.exists():
        return None
    from .enrich import enrich_doc  # noqa: F401  (same truncation rule)
    docs = {json.loads(l)["doc_id"]: json.loads(l) for l in open(OUT / "stapfer_documents.jsonl", encoding="utf-8")}
    rows = [json.loads(l) for l in open(p, encoding="utf-8")]
    g_hit = g_tot = pl_hit = pl_tot = ent_tot = ent_unsup = 0
    leg = []
    for r in rows:
        d = docs[r["doc_id"]]
        text = "\n".join(filter(None, [d.get("letterhead_clean"), d["text_clean"]]))[: r["input_chars"]]
        tt = set(norm(text)); llm = r["llm"] if r["json_valid"] else {}
        persons = [x for x in llm.get("persons", []) if isinstance(x, str)]
        places = [x for x in llm.get("places", []) if isinstance(x, str)]
        for t in d["teachers"]:
            sur = [x for x in norm(t["name"]) if len(x) > 2][-1:]
            if sur and sur[0] in tt:  # only score names that are actually in the model's input
                g_tot += 1; g_hit += any(sur[0] in norm(p) for p in persons)
        for pl in d["places"]:
            nm = [x for x in norm(pl["name_1799"]) if len(x) > 2]
            if nm and all(x in tt for x in nm):
                pl_tot += 1; pl_hit += any(set(nm) <= set(norm(p)) for p in places)
        for e in persons + places:
            ent_tot += 1; ent_unsup += not found_in(e, tt)
        leg.append((llm.get("legibility"), d["uncertain_readings"] > 0))
    n = len(rows)
    gap_docs = [l for l, g in leg if g]
    return {
        "scope": f"{rows[0]['model']} on a random sample (seed 7) of {n} Stapfer documents, input truncated to 1800 chars",
        "json_valid_rate": round(sum(r["json_valid"] for r in rows) / n, 3),
        "teacher_surname_recall_vs_db": round(g_hit / g_tot, 3) if g_tot else None, "teacher_names_scored": g_tot,
        "school_place_recall_vs_db": round(pl_hit / pl_tot, 3) if pl_tot else None, "places_scored": pl_tot,
        "unsupported_entity_rate": round(ent_unsup / ent_tot, 3) if ent_tot else None,
        "unsupported_entity_definition": "person/place strings whose tokens do not all occur in the input text (proxy for hallucination; also catches normalised spellings)",
        "entities_checked": ent_tot,
        "legibility_flag_on_docs_with_uncertain_readings": pd.Series(gap_docs).value_counts(dropna=False).to_dict(),
        "seconds_per_doc_median": float(pd.Series([r["seconds"] for r in rows]).median()),
        "note": "gold = hand-entered teacher/place tables of the Uni Bern Stapfer database (independent of the transcription text)",
    }


def runtime():
    import subprocess, sys
    t = {}
    for mod in ["archai.stapfer_clean", "archai.link"]:
        t0 = time.time(); subprocess.run([sys.executable, "-W", "ignore", "-m", mod], check=True, env={"PYTHONPATH": "src", "PATH": ""}, capture_output=True)
        t[mod] = round(time.time() - t0, 1)
    return t


def agent_metrics(p=OUT / "agent_traces.jsonl", version=None, note=None):
    if not p.exists():
        return None
    rows = [json.loads(l) for l in open(p, encoding="utf-8")]
    from .agent import PROMPT_VERSION
    version = version or PROMPT_VERSION
    rows = [r for r in rows if r.get("prompt_version") == version]
    if not rows:
        return None
    pl = [r["planner"] for r in rows]
    base = sum(len(x["baseline_tools"]) for x in pl)
    ents = sum(len(r["steps"][-1]["result"]) for r in rows)
    return {
        "scope": f"Apertus tool-agent ({'native tool calls in a loop' if version == 'agent-v4' else 'plan -> tools -> compose'} -> validate) on {len(rows)} records "
                 f"({sum(r['kind'] == 'stapfer' for r in rows)} Stapfer, {sum(r['kind'] == 'consultation' for r in rows)} consultations)" + (f"; {note}" if note else ""),
        "prompt_version": version,
        "valid_plan_rate": round(sum(x["valid_plan"] for x in pl) / len(rows), 3),
        "plan_truncated_rate": round(sum(r.get("plan_truncated", False) for r in rows) / len(rows), 3),
        "mean_tool_calls_planned": round(sum(x["n_calls"] for x in pl) / len(rows), 2),
        "unknown_tool_calls": sum(len(x["unknown_tools"]) for x in pl),
        "planner_recall_of_required_tools": round(sum(len(x["baseline_tools_chosen"]) for x in pl) / base, 3) if base else None,
        "planner_recall_definition": "share of tools the deterministic baseline policy requires that Apertus also chose on its own",
        "tool_execution_errors": sum(r["tool_errors"] for r in rows),
        "tool_errors_by_origin": {k: int(v) for k, v in pd.Series([x["origin"] for r in rows for st in r["steps"] if st["step"] == "tools"
                                                 for x in st["results"] if "error" in x], dtype=object).value_counts().items()},
        "tool_errors_note": "errors on Apertus-planned calls are mostly wrong argument names; baseline calls always run as well",
        "compose_valid_rate": round(sum(r["compose_valid"] for r in rows) / len(rows), 3),
        "unsupported_entity_rate": round(sum(len(r["unsupported"]) for r in rows) / ents, 3) if ents else None,
        "entities_checked": ents,
        "seconds_per_record_median": float(pd.Series([r["seconds"] for r in rows]).median()),
        **(loop_metrics(rows) if version == "agent-v4" else {}),
    }


def loop_metrics(rows):
    L = [r["loop"] for r in rows]
    per_step = [c for x in L for c in x["calls_per_step"]]
    return {"loop_steps_mean": round(sum(x["steps"] for x in L) / len(L), 2),
            "steps_with_parallel_calls": round(sum(c > 1 for c in per_step) / max(len(per_step), 1), 3),
            "max_calls_in_one_step": max(per_step, default=0),
            "planner_recall_before_feedback": round(sum(len(x.get("chosen_before_feedback", [])) for x in L) / max(sum(len(r["planner"]["baseline_tools"]) for r in rows), 1), 3),
            "feedback_rounds_used": sum(x.get("feedback_rounds", 0) for x in L),
            "argument_repairs": sum(len(x["repairs"]) for x in L),
            "argument_errors_returned_to_model": sum(x["arg_errors"] for x in L),
            "self_corrected_after_error": sum(x["self_corrected"] for x in L),
            "repeated_calls_served_from_cache": sum(x["duplicates"] for x in L),
            "invalid_tool_calls_from_model": sum(x.get("invalid_tool_calls", 0) for x in L),
            "records_native_api": sum(r["mode"] == "native" for r in rows), "records_json_protocol": sum(r["mode"] == "json" for r in rows),
            "records_native_fell_back_to_json": sum(r["mode"] == "native->json" for r in rows),
            "loop_definition": "Apertus calls tools (native API or JSON protocol), sees results, may call more (max 6 steps / 14 calls); calls of one step run "
                               "in parallel; when it finishes with required tools missing, the code names them once and the model decides; anything still "
                               "missing runs anyway (origin baseline). planner_recall_of_required_tools counts after that feedback round."}


def export_metrics():
    p = OUT / "export_stats.json"
    return dict(json.load(open(p)), scope="unified records.jsonl (Stapfer documents + consultations)") if p.exists() else None


def main(with_runtime=False):
    from . import bern_eval
    bern = bern_eval.run()
    if bern is None and (OUT / "bern_eval.json").exists():  # reference xlsx not present: keep the last measured comparison
        bern = dict(json.load(open(OUT / "bern_eval.json")), note="carried over from the last run with the Uni Bern reference file")
    rep = {"bern_reference": bern, "stapfer": stapfer_metrics(), "linking": link_metrics(), "export": export_metrics(),
           "apertus_enrichment": {f.name: apertus_metrics(f) for f in sorted(OUT.glob("apertus_enrichment*.jsonl"))}, "apertus_agent": agent_metrics()}
    if (OUT / "qa_eval.json").exists():  # written by archai.qa_eval (needs Apertus)
        rep["qa_retrieval"] = json.load(open(OUT / "qa_eval.json"))
    rep["bias_by_language"] = bias_metrics()
    if (OUT / "tool_bench.json").exists():  # written by archai.tool_bench (needs Apertus)
        rep["tool_calling_benchmark"] = json.load(open(OUT / "tool_bench.json"))
    if (OUT / "agent_traces_v4.jsonl").exists():  # same 80 records, same environment: v3 re-run locally vs v4
        rep["apertus_agent_v4_native_api"] = agent_metrics(OUT / "agent_traces_v4.jsonl", "agent-v4", "native tools API of the gateway (one call per turn)")
        if (OUT / "agent_traces_v4_json.jsonl").exists():
            rep["apertus_agent_v4"] = agent_metrics(OUT / "agent_traces_v4_json.jsonl", "agent-v4", "default mode: JSON tool protocol, the model batches calls")
        rep["apertus_agent_v3_same_run"] = agent_metrics(OUT / "agent_traces_v3_local.jsonl", "agent-v3",
                                                         "re-run next to v4; Archive Database not available locally, so lookup_archive_unit returns not found in both")
    if (OUT / "confidence_eval.json").exists():  # written by archai.qa_eval --confidence
        rep["qa_confidence"] = json.load(open(OUT / "confidence_eval.json"))
    if (OUT / "qa_answer_check.json").exists():
        rep["qa_answer_check"] = json.load(open(OUT / "qa_answer_check.json"))
    if (OUT / "data_eval.json").exists():  # written by archai.data_eval (needs Apertus)
        rep["data_questions"] = {k: v for k, v in json.load(open(OUT / "data_eval.json")).items() if k != "questions"}
    if with_runtime:
        rep["runtime_seconds_8cpu"] = runtime()
    json.dump(rep, open(OUT / "metrics.json", "w"), indent=1, ensure_ascii=False, default=str)
    print(json.dumps(rep, indent=1, ensure_ascii=False, default=str))
    return rep


if __name__ == "__main__":
    import sys
    main("--runtime" in sys.argv)
