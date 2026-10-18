"""One command, all stages:  python -m archai.pipeline [--llm-sample N] [--agent-sample N] [--force STAGE ...]

Stages are idempotent (skipped when outputs exist), resumable (LLM stages append and skip done records),
and isolated (an optional Apertus stage failing never breaks the deterministic dataset). A manifest records
each stage's status, runtime, outputs and the model configuration."""
import argparse, hashlib, importlib, json, platform, time, traceback
from pathlib import Path

OUT = Path("data/out")
STAGES = [  # name, module:function, outputs, optional
    ("ingest_stapfer", "archai.ingest:stapfer", ["data/interim/stapfer/t_transkription.parquet"], False),
    ("ingest_consultations", "archai.ingest:consultations", ["data/interim/consultations.parquet"], False),
    ("ingest_olr", "archai.ingest:olr", ["data/interim/olr/tbs_vrzng_enht.0000.parquet"], False),
    ("clean_stapfer", "archai.stapfer_clean:run", ["data/out/stapfer_documents.jsonl"], False),
    ("link_consultations", "archai.link:run", ["data/out/consultations.jsonl"], False),
    ("apertus_enrich", "archai.enrich:main", [], True),
    ("apertus_agent", "archai.agent:main", [], True),
    ("export", "archai.export:run", ["data/out/records.jsonl"], False),
    ("evaluate", "archai.evaluate:main", ["data/out/metrics.json"], False),
]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:16]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm-sample", type=int, default=0, help="Stapfer docs for plain Apertus enrichment (0=skip)")
    ap.add_argument("--agent-sample", type=int, default=0, help="Stapfer docs for Apertus tool-agent (0=skip)")
    ap.add_argument("--force", nargs="*", default=[], help="stage names to re-run even if outputs exist ('all')")
    a = ap.parse_args(argv)
    from . import llm
    manifest = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "host": platform.platform(),
                "apertus": {"base_url": llm.BASE, "model": llm.MODEL, "reachable": None}, "stages": []}
    OUT.mkdir(parents=True, exist_ok=True)
    for name, target, outs, optional in STAGES:
        mod, fn = target.split(":")
        args = ()
        if name == "apertus_enrich":
            args = (a.llm_sample,)
        if name == "apertus_agent":
            args = (a.agent_sample,)
        st = {"stage": name, "optional": optional}
        if optional and (not args[0]):
            st["status"] = "skipped:not_requested"
        elif optional and not (manifest["apertus"].__setitem__("reachable", llm.available()) or manifest["apertus"]["reachable"]):
            st["status"] = "skipped:apertus_unreachable"
        elif outs and all(Path(o).exists() for o in outs) and not ({name, "all"} & set(a.force)):
            st["status"] = "skipped:up_to_date"
        else:
            t0 = time.time()
            try:
                getattr(importlib.import_module(mod), fn)(*args)
                st["status"] = "ok"
            except Exception as e:
                st["status"] = "failed"; st["error"] = f"{type(e).__name__}: {e}"; st["trace"] = traceback.format_exc()[-1500:]
                if not optional:
                    manifest["stages"].append(st); _write(manifest); raise
            st["seconds"] = round(time.time() - t0, 1)
        st["outputs"] = {o: sha(o) for o in outs if Path(o).exists()}
        manifest["stages"].append(st); print(f"[{st['status']:>28}] {name} {st.get('seconds', '')}", flush=True)
    _write(manifest)


def _write(m):
    m["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    json.dump(m, open(OUT / "run_manifest.json", "w"), indent=1)


if __name__ == "__main__":
    main()
