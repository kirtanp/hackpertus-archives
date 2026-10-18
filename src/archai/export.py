"""Unified AI-ready export: one record schema across datasets. Deterministic tools fill every record;
Apertus fields are merged only after deterministic grounding checks, and rejected values are kept for audit."""
import json
from pathlib import Path
import pandas as pd
from . import tools as T

OUT = Path("data/out")
SCHEMA_VERSION = "1.1"  # 1.1: quality.uncertain_readings / pages_in_transcription replace illegible_gaps; viewer_url fixed
REQUIRED = {"record_id": str, "dataset": str, "title": str, "text": str, "provenance": dict, "dates": list,
            "places": list, "persons": list, "quality": dict, "enrichment": dict}


def validate(rec):
    errs = [f"missing:{k}" for k in REQUIRED if k not in rec]
    errs += [f"type:{k}" for k, t in REQUIRED.items() if k in rec and not isinstance(rec[k], t)]
    p = rec.get("provenance", {})
    if not (p.get("archive_signature") or p.get("source_dataset")):
        errs.append("no_provenance")
    return errs


def _jsonl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")] if Path(p).exists() else []


def apertus_layer():
    """doc_id -> best Apertus output (agent trace preferred over plain enrichment)."""
    layer = {}
    files = sorted(OUT.glob("apertus_enrichment*.jsonl"), key=lambda f: ("70b" in f.name, "8b" in f.name and "q4" not in f.name))
    for r in (r for f in files for r in _jsonl(f)):  # later files (70B) override earlier ones
        if r.get("json_valid"):
            layer[r["doc_id"]] = {"source": "enrich", "out": r["llm"], "model": r["model"], "prompt_version": r["prompt_version"]}
    for r in _jsonl(OUT / "agent_traces.jsonl"):
        if r.get("compose_valid", True) and isinstance(r.get("final"), dict) and "_raw" not in r["final"]:
            layer[r["record"]] = {"source": "agent", "out": r["final"], "model": r["model"], "prompt_version": "agent-v1"}
    return layer


def merge_apertus(rec, a):
    o = a["out"]; text = rec["text"]
    persons = [p for p in o.get("persons") or [] if isinstance(p, str)]
    places = [p for p in o.get("places") or [] if isinstance(p, str) and not p.startswith("stapfer:")]
    chk = T.verify_in_source(persons + places, text + " " + rec["title"])
    ok = {c["entity"] for c in chk if c["grounded"]}
    rec["persons"] += [{"name": p, "source": "apertus", "grounded": True} for p in persons if p in ok]
    rec["places_mentioned"] = [p for p in places if p in ok]
    rec["enrichment"] = {"source": f"apertus:{a['source']}", "model": a["model"], "prompt_version": a["prompt_version"],
                         "title_generated": o.get("title") if isinstance(o.get("title"), str) else None,
                         "summary_en": o.get("summary_en") if isinstance(o.get("summary_en"), str) else None,
                         "topics": [t for t in o.get("topics") or [] if isinstance(t, str)][:5],
                         "legibility_model": o.get("legibility"),
                         "rejected_ungrounded": [c["entity"] for c in chk if not c["grounded"]]}


def stapfer_records():
    for d in _jsonl(OUT / "stapfer_documents.jsonl"):
        text = d["text_clean"] or ""
        head = (d["letterhead_clean"] or "").strip()
        place = d["places"][0]["name_1799"] if d["places"] else None
        dates = ([{"iso": d["date_written"], "source": "database"}] if d["date_written"] else [])
        dates += [dict(x, source="normalize_dates") for x in T.normalize_dates(head + "\n" + text[-400:])]
        q = T.extract_quantities(text)
        yield {
            "record_id": d["doc_id"], "dataset": d["dataset"], "kind": "school_survey_response",
            "title": f"Stapfer-Enquête 1799 – Schule {place}" if place else (head.split("\n")[0][:120] or d["doc_id"]),
            "language": d["language"], "text": text, "text_raw_available": True,
            "dates": dates,
            "places": [{"place_id": p["place_id"], "name": p["name_1799"], "municipality_today": p["municipality_today"],
                        "lat": p["lat"], "lon": p["lon"], "source": "database"} for p in d["places"]],
            "persons": [{"person_id": t["teacher_id"], "name": t["name"], "role": "teacher", "source": "database"} for t in d["teachers"]],
            "organisations": [{"org_id": s["school_id"], "name": s["name"], "type": s["type"]} for s in d["schools"]],
            "quantities": q,
            "provenance": d["provenance"],
            "quality": {"handwritten": d["handwritten"], "uncertain_readings": d["uncertain_readings"],
                        "markup_errors_in_source": d["markup_errors"], "pages_in_transcription": d["pages"],
                        "legibility_rule": "good" if d["uncertain_readings"] == 0 else ("partly_illegible" if d["uncertain_readings"] < 10 else "mostly_illegible"),
                        "legibility_rule_definition": "from transcriber-marked uncertain readings (#..##, Editionsregeln §15), counted once per distinct answer text"},
            "enrichment": {"source": "deterministic"},
        }


def consultation_records():
    for c in _jsonl(OUT / "consultations.jsonl"):
        units = [a["olr"] for a in c["archive_refs"] if "olr" in a]
        dates = [{"iso": c[k], "role": k, "source": "database"} for k in ("consultation_start", "consultation_end") if c[k]]
        yield {
            "record_id": c["doc_id"], "dataset": c["dataset"], "kind": "federal_consultation",
            "title": c["title"], "language": "de",
            "text": f'{c["title"]}. {c["type"]}, {c["department"]}. Adressaten: {", ".join(c["addressees"])}.',
            "dates": dates, "places": [], "persons": [],
            "organisations": [{"name": c["department"], "abbr": c["department_abbr"], "role": "lead_department"}]
                             + [{"name": a, "role": "addressee"} for a in c["addressees"]],
            "archive_units": [{k: u[k] for k in ("unit_id", "signature", "title", "begin", "end", "level", "access", "url")}
                              | {"path": [h["title"] for h in u["hierarchy"]]} for u in units],
            "provenance": dict(c["provenance"], archive_signature="; ".join(u["signature"] for u in units) or None,
                               viewer_url=units[0]["url"] if units else None),
            "quality": {"archive_refs": len(c["archive_refs"]), "archive_refs_linked": len(units),
                        "dates_parsed": len(dates)},
            "enrichment": {"source": "deterministic"},
        }


def run():
    layer = apertus_layer(); n = bad = merged = 0
    with open(OUT / "records.jsonl", "w", encoding="utf-8") as f, open(OUT / "records_invalid.jsonl", "w", encoding="utf-8") as fb:
        for rec in list(stapfer_records()) + list(consultation_records()):
            if rec["record_id"] in layer:
                merge_apertus(rec, layer[rec["record_id"]]); merged += 1
            rec["schema_version"] = SCHEMA_VERSION
            errs = validate(rec)
            (fb if errs else f).write(json.dumps(dict(rec, _errors=errs) if errs else rec, ensure_ascii=False, default=str) + "\n")
            n += 1; bad += bool(errs)
    flat = pd.read_json(OUT / "records.jsonl", lines=True)
    flat[["record_id", "dataset", "kind", "title", "language"]].assign(
        signature=flat.provenance.map(lambda p: p.get("archive_signature")),
        url=flat.provenance.map(lambda p: p.get("viewer_url")),
        enrichment=flat.enrichment.map(lambda e: e["source"])).to_csv(OUT / "records.csv", index=False)
    stats = {"records": n, "schema_invalid": bad, "apertus_merged": merged}
    json.dump(stats, open(OUT / "export_stats.json", "w"), indent=1)
    print(stats)


if __name__ == "__main__":
    run()
