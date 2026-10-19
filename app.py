"""Web demo for ArchAI: cited Q&A over the Stapfer-Enquête 1799 and the Federal Consultations 1960-1991.

The qa.Index is built once, in a background thread at startup (1-2 min), so the page is reachable while it warms up."""
import json, os, random, re, sys, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # qa.py reads data/out relative to the working directory
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from archai import qa, llm, stats

OUT = ROOT / "data" / "out"
REWRITE = os.environ.get("QA_REWRITE", "dynamic")  # dynamic | apertus | gaps | off; measured in data/out/qa_eval.json
state = {"idx": None, "error": None, "seconds": None}
RECORDS = {}
PLACES = []  # one point per geocoded Stapfer sheet, for the map


def _load():
    t0 = time.time()
    try:
        meta = {}  # filter fields not carried in records.jsonl
        for l in open(OUT / "stapfer_documents.jsonl", encoding="utf-8"):
            d = json.loads(l)
            meta[d["doc_id"]] = {"conf": (d["schools"] or [{}])[0].get("confession"),
                                 "district": (d["places"] or [{}])[0].get("district_today")}
        for l in open(OUT / "records.jsonl", encoding="utf-8"):
            r = json.loads(l); RECORDS[r["record_id"]] = r
            pl = next((p for p in r.get("places") or [] if p.get("lat")), None)
            if pl:
                org = (r.get("organisations") or [{}])[0]
                pupils = [x["count"] for x in r["quantities"]["pupils"]]
                PLACES.append({"id": r["record_id"], "name": pl["name"], "today": pl.get("municipality_today"), "school": org.get("name"),
                               "type": org.get("type"), "lang": r["language"], "pupils": max(pupils) if pupils else None,
                               **meta.get(r["record_id"], {}), "lat": pl["lat"], "lon": pl["lon"], "url": r["provenance"]["viewer_url"]})
        for r in qa.onboarded_records():  # datasets added with archai.onboard --publish
            RECORDS[r["record_id"]] = r
        state["idx"] = qa.Index()
    except Exception as e:  # surfaced through /api/health instead of killing the server
        state["error"] = f"{type(e).__name__}: {e}"
    state["seconds"] = round(time.time() - t0, 1)
    print(f"[archai] index ready in {state['seconds']}s, {len(RECORDS)} records, error={state['error']}", flush=True)


app = FastAPI(title="ArchAI – Deciphering Swiss archives with Apertus")


@app.on_event("startup")
def startup():
    threading.Thread(target=_load, daemon=True).start()


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=500)


@app.get("/api/health")
def health():
    return {"ready": state["idx"] is not None, "error": state["error"], "build_seconds": state["seconds"],
            "records": len(RECORDS), "apertus_configured": llm.KEY not in ("", "none"), "model": llm.MODEL}


@app.post("/api/ask")
async def ask(body: Ask):
    if state["idx"] is None:
        raise HTTPException(503, state["error"] or "Index is still loading, try again in a minute.")
    use_llm = llm.KEY not in ("", "none")  # no key -> extractive cited answer, no wasted retries
    out = await run_in_threadpool(qa.answer, state["idx"], body.question.strip(), 6, use_llm, REWRITE)
    for h in out["sources"]:  # coordinates for the map, from the record (database gazetteer)
        pl = next((p for p in (RECORDS.get(h["doc_id"]) or {}).get("places") or [] if p.get("lat")), None)
        h["geo"] = {"name": pl["name"], "lat": pl["lat"], "lon": pl["lon"]} if pl else None
    return out


@app.get("/api/places")
def places():
    return PLACES


# ---- game: guess where a 1799 school was from its own answers (place names masked) ----
CLUE_SECTIONS = {"III.12": "Pupils", "IV.16": "Teacher's pay", "II.7": "School books", "II.5": "What is taught",
                 "II.6": "When school is held", "IV.15": "Schoolhouse", "III.11.d": "Teacher's age", "III.11.e": "Teacher's family",
                 "II.9": "Hours per day", "IV.14": "School fees"}
BLOCK = re.compile(r"\[([IVX]+\.[^\]]*)\] (.*?)(?=\n\n\[|\Z)", re.S)


def _mask(text, names):
    for n in sorted({n for n in names if n and len(n) >= 3}, key=len, reverse=True):
        text = re.sub(re.escape(n), "▇▇▇", text, flags=re.I)
        for part in re.split(r"[\s,/()-]+", n):  # "Lausanne, Saint Laurent" -> also mask the parts
            if len(part) >= 4:
                text = re.sub(rf"\b{re.escape(part)}\b", "▇▇▇", text, flags=re.I)
    return text


def game_round(rid):
    r = RECORDS[rid]
    pl = next((p for p in r["places"] if p.get("lat")), None)
    names = [p["name"] for p in r["places"]] + [p.get("municipality_today") for p in r["places"]] + \
            [o["name"] for o in r.get("organisations") or []] + [x["name"] for x in r.get("persons") or []]
    clues, seen = [], set()
    for qno, text in BLOCK.findall(r["text"]):
        sec = next((k for k in CLUE_SECTIONS if qno == k or qno.startswith(k + ".")), None)
        t = " ".join(text.split())
        if not sec or sec in seen or not (12 <= len(t) <= 260):
            continue
        seen.add(sec)
        clues.append({"topic": CLUE_SECTIONS[sec], "question_no": qno, "text": _mask(t, names)})
    if len(clues) < 3 or not pl:
        return None
    pupils = r["quantities"]["pupils"]
    return {"id": rid, "language": r["language"], "clues": clues[:4],
            "reveal": {"lat": pl["lat"], "lon": pl["lon"], "name_1799": pl["name"], "municipality_today": pl.get("municipality_today"),
                       "school": (r.get("organisations") or [{}])[0].get("name"), "teacher": ", ".join(x["name"] for x in r.get("persons") or []),
                       "pupils": max(x["count"] for x in pupils) if pupils else None,  # largest count stated (sheets list winter/summer/total)
                       "signature": r["provenance"]["archive_signature"], "url": r["provenance"]["viewer_url"]}}


class Round(BaseModel):
    ids: list[str] = []      # pool from the map filters; empty = all schools
    exclude: list[str] = []  # sheets already played this game


@app.post("/api/game/round")
def game(body: Round):
    if not PLACES:
        raise HTTPException(503, "Records are still loading.")
    skip = set(body.exclude)
    pool = [i for i in body.ids if i in RECORDS and i not in skip] if body.ids else [p["id"] for p in PLACES if p["id"] not in skip]
    for rid in random.sample(pool, len(pool)):
        if g := game_round(rid):
            return g
    raise HTTPException(404, "No playable sheet in this selection: widen the filters.")


@app.get("/api/record/{record_id:path}")
def record(record_id: str):
    if not RECORDS and state["idx"] is None:
        raise HTTPException(503, "Records are still loading.")
    if record_id not in RECORDS:
        raise HTTPException(404, f"No record {record_id!r}")
    return RECORDS[record_id]


@app.get("/api/metrics")
def metrics():
    return {"metrics": json.loads((OUT / "metrics.json").read_text()),
            "bern_eval": json.loads((OUT / "bern_eval.json").read_text())}


@app.get("/api/site")
async def site_data():
    """Real examples for every tab of the presentation site (archai.site); the simulator's example answers join once
    they have run through the answer path in the background."""
    if state["idx"] is None:
        raise HTTPException(503, state["error"] or "Index is still loading, try again in a minute.")
    from archai import site
    out = await run_in_threadpool(site.build, state["idx"], RECORDS, game_round, json.loads((OUT / "metrics.json").read_text()))
    return out | {"sim": site.sim_answers(state["idx"], llm.KEY not in ("", "none"))}


@app.get("/api/stats")
def corpus_stats(query: str = "all", section: str | None = None, page: int | None = None):
    """Aggregates over the whole corpus in SQL (stats.py / DuckDB); each block names the rows it counted."""
    try:
        return stats.summary(query=query, section=section, page=page)
    except stats.CorpusMissing as e:
        raise HTTPException(503, str(e)) from e
    except (stats.UnsupportedQuery, stats.InvalidFilter) as e:
        raise HTTPException(400, str(e)) from e


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


# the ArchAI presentation site (5 tabs); its Q&A simulator calls /api/ask and its evidence tab /api/metrics
app.mount("/site", StaticFiles(directory=ROOT / "static" / "site", html=True), name="site")
