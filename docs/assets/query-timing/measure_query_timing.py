"""Measure how long the ArchAI Q&A path takes to answer one question, end to end.

This is the provenance of the chart: it runs the deployed answer path (`qa.answer(..., rewrite="dynamic")`,
the one `archai.data_eval` and `archai.qa_eval` evaluate) once per question, sequentially, and writes one row per
question to `measurements.csv` plus the run's comparability facts to `measurements.meta.json`, both next to this file.

The question set comes from the repository itself: `archai.qa_eval.all_cases` builds the 380 templated questions
(the retrieval evaluation's set). A fixed, seeded sample keeps the run bounded; the sample and its size are recorded
in the metadata, so the numbers can always be attributed to that set.

Run it from the repository root, against any OpenAI-compatible Apertus endpoint:

    APERTUS_BASE_URL=... APERTUS_API_KEY=... APERTUS_MODEL=... \
      PYTHONPATH=src uv run --all-groups python docs/assets/query-timing/measure_query_timing.py

Without Apertus (or with `--no-llm`) the path still runs, but only deterministically: the timings are then the
retrieval-and-rules floor and the chart says so through the model field in the metadata.
"""
import csv
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))  # the project is not installed as a package; the Justfile exports the same src/

from archai import llm, qa, qa_eval  # noqa: E402

HERE = Path(__file__).resolve().parent
SEED = 11
STAPFER_SAMPLE = 12
CONSULTATION_SAMPLE = 6
DEFAULT_BUDGET = 2700  # stop starting new questions after this many seconds, so a run still ends with usable data


def _sample(idx):
    """A fixed, seeded sample of the repository's own question set: 18 Stapfer + 6 consultation questions."""
    cases = qa_eval.all_cases(idx)
    rnd = random.Random(SEED)
    stapfer = rnd.sample([c for c in cases if c["dataset"] == "stapfer"], STAPFER_SAMPLE)
    consultation = rnd.sample([c for c in cases if c["dataset"] == "consultation"], CONSULTATION_SAMPLE)
    return stapfer + consultation


def _instrument():
    """Count the model calls the path makes and the seconds they took, without touching the path itself."""
    calls = {"n": 0, "seconds": 0.0}
    for name in ("chat_json", "chat_text", "chat_tools"):
        original = getattr(llm, name)

        def wrapper(*a, _original=original, **kw):
            t0 = time.perf_counter()
            try:
                return _original(*a, **kw)
            finally:
                calls["n"] += 1
                calls["seconds"] += time.perf_counter() - t0

        setattr(llm, name, wrapper)
    return calls


def _machine():
    def sysctl(key):
        try:
            return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            return ""
    return {"platform": platform.platform(), "machine": sysctl("hw.model") or platform.machine(),
            "cpu": sysctl("machdep.cpu.brand_string") or platform.processor(), "cpu_count": os.cpu_count(),
            "ram_bytes": int(sysctl("hw.memsize") or 0)}


def _meta(scope, rows, llm_seconds, started, finished, machine, load_start):
    return {
        "chart": "query_timing.png, drawn by plot_query_timing.R from measurements.csv",
        "scope": scope,
        "measurement": "wall-clock seconds around one qa.answer call, after qa.Index() has been built; llm_seconds is "
                       "the share of that time spent inside archai.llm model calls",
        "endpoint": {"base_url": llm.BASE,
                     "kind": "local" if any(h in llm.BASE for h in ("127.0.0.1", "localhost", "::1")) else "hosted"},
        "model": llm.MODEL,
        "seed": SEED,
        "machine": machine,
        "python": platform.python_version(),
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "load_average_start": load_start,
        "load_average_end": [round(x, 2) for x in os.getloadavg()],
        "questions": len(rows), "total_seconds": round(sum(r[9] for r in rows), 1),
        "total_llm_seconds": round(llm_seconds, 1),
        "median_seconds": round(statistics.median(r[9] for r in rows), 1) if rows else None,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(finished)),
        "caveats": "one local machine under interactive load, a 4-bit 8B model served by oMLX; per-question latency "
                   "varies with question type (the data engine reads and verifies every contributing record) and with "
                   "the endpoint's throughput, so the numbers compare the path's question types, not the model's quality",
    }


def main(argv):
    args, no_llm, limit, budget = list(argv), False, None, DEFAULT_BUDGET
    while args:
        a = args.pop(0)
        if a == "--no-llm":
            no_llm = True
        elif a == "--budget":
            budget = int(args.pop(0))
        elif a.isdigit():
            limit = int(a)
        else:
            sys.exit(f"unknown argument {a}")
    os.chdir(ROOT)  # every stage reads data/out/ relative to the working directory
    idx = qa.Index()
    questions = _sample(idx)
    if limit:
        questions = questions[:limit]
    calls = _instrument()
    started, machine, load_start = time.time(), _machine(), [round(x, 2) for x in os.getloadavg()]
    scope = (f"the deployed answer path (qa.answer, dynamic rewrite{', no model calls' if no_llm else ''}) "
             f"on a fixed sample of qa_eval.all_cases: {STAPFER_SAMPLE} Stapfer + {CONSULTATION_SAMPLE} "
             f"consultation questions, seed {SEED}, one measurement per question, sequential")
    rows = []
    def write_meta(finished):
        meta = _meta(scope, rows, calls["seconds"], started, finished, machine, load_start)
        (HERE / "measurements.meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return meta
    with open(HERE / "measurements.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["question_id", "question", "dataset", "style", "intent", "route_dataset", "answer_source",
                    "llm_calls", "llm_seconds", "seconds"])
        for i, c in enumerate(questions, start=1):
            if time.time() - started > budget:  # keep the run bounded; the metadata records what was actually measured
                print(f"budget reached after {len(rows)} questions", flush=True)
                break
            before, before_s = calls["n"], calls["seconds"]
            t0 = time.perf_counter()
            out = qa.answer(idx, c["q"], 6, not no_llm, "dynamic")
            seconds = time.perf_counter() - t0
            row = [i, c["q"], c["dataset"], c.get("style") or "", c.get("intent") or "",
                   (out.get("route") or {}).get("dataset") or "", out.get("answer_source") or "",
                   calls["n"] - before, round(calls["seconds"] - before_s, 1), round(seconds, 1)]
            rows.append(row)
            w.writerow(row)
            f.flush()
            write_meta(time.time())  # a killed run still leaves a consistent CSV and metadata behind
            print(f'{i}/{len(questions)}  {seconds:6.1f}s  llm {row[7]} calls {row[8]:6.1f}s  {row[6]:14s} {c["q"][:60]}', flush=True)
    meta = write_meta(time.time())
    print(json.dumps({k: v for k, v in meta.items() if k != "caveats"}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
