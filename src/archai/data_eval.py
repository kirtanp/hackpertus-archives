"""Data questions (archai.dataq) end to end: routing, plan, reading and computation against hand-checked answers.

Gold: counts, averages and groups are re-derived here from data/out with plain Python, not through the engine. Figures
read from the answers (pupils, girls, boys, pay) were checked sheet by sheet by hand for the 9 schools of 1799 Bern.
Brunngasse's "Knaben 36-40. Mädchen 60-70" follows "Diese Schul wurde sonst von 100 bis 130 Kindern besucht, darunter
waren": a present or a past figure, so both readings are accepted. Each question runs REPEATS times, because Apertus'
reading varies between runs. Writes data/out/data_eval.json and data_eval_cases.jsonl."""
import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from . import llm, qa

OUT = qa.OUT
REPEATS = 3


def _docs():
    return [json.loads(line) for line in open(OUT / "stapfer_documents.jsonl", encoding="utf-8")]


def _consultations():
    return [json.loads(line) for line in open(OUT / "consultations.jsonl", encoding="utf-8")]


def gold():
    d, co = _docs(), _consultations()
    hinwil = [int(t["age"]) for x in d if any(p.get("district_today") == "Hinwil" for p in x["places"])
              for t in x.get("teachers") or [] if str(t.get("age") or "").isdigit()]
    by_dep = {}
    for c in co:
        if str(c.get("year")) == "1984":
            dep = (c.get("department_abbr") or "?").split("\n")[0].strip()  # the engine groups by the first department
            by_dep[dep] = by_dep.get(dep, 0) + 1
    return {"lausanne": sum(any(p["name_1799"] == "Lausanne" for p in x["places"]) for x in d),
            "hinwil_age": round(statistics.mean(hinwil), 1),
            "female_teachers": sum(t.get("gender") == "Frau" for x in d for t in x.get("teachers") or []),
            "cons_1984": sum(str(c.get("year")) == "1984" for c in co), "by_dep_1984": by_dep}


def _result(text):
    """The numbers of the code-written result line ("Result: 782 to 836 and more (from 9 schools with a value)")."""
    m = re.search(r"^(?:Result|Ergebnis|Résultat)[^:\n]*:\s*(.+)$", text, re.M)
    if not m:
        return None, False
    line = m.group(1)
    head = line.split("(")[0]
    return [int(n) for n in re.findall(r"\d+", head)], bool(re.search(r"and more|und mehr|et plus", head))


def _listed(text):
    """The records listed as the result (the lines before 'without a usable value' / the result line)."""
    m = re.search(r"^(?:Plan|Vorgehen|Démarche): .*\n\n", text, re.M)
    body = re.split(r"\n(?:Without|Ohne|Sans|Cannot|Aus dem|Impossible) |\n\n", text[m.end():] if m else "")[0]
    return [ln for ln in body.splitlines() if ln.startswith("- ")]


def _range(accept):
    def check(o):
        nums, more = _result(o["answer"])
        ok = o["answer_source"] == "aggregate" and nums is not None and any(
            nums[:2] == [lo, hi] and more == m for lo, hi, m in accept)
        return ok, f"result {nums} more={more}"
    return check


def _count(n):
    def check(o):
        nums, _ = _result(o["answer"])
        return o["answer_source"] == "aggregate" and bool(nums) and nums[0] == n, f"result {nums}"
    return check


def _value(x, tol=0.05):
    def check(o):
        nums, _ = _result(o["answer"])
        got = float(re.search(r"[\d.]+", o["answer"].split("Result:")[-1]).group(0)) if "Result:" in o["answer"] else None
        return o["answer_source"] == "aggregate" and got is not None and abs(got - x) <= tol, f"result {got}"
    return check


def _groups(want):
    def check(o):
        got = dict(re.findall(r"^- ([^:\n]+): (\d+) ", o["answer"], re.M))
        bad = {k: (v, got.get(k)) for k, v in want.items() if str(v) != got.get(k)}
        return o["answer_source"] == "aggregate" and not bad, f"wrong groups {bad}" if bad else "all groups right"
    return check


def _lists(must, never=()):
    def check(o):
        rows = "\n".join(_listed(o["answer"]))
        miss = [m for m in must if m not in rows]
        extra = [n for n in never if n in rows]
        return o["answer_source"] == "aggregate" and not miss and not extra, f"missing {miss} wrong {extra}"
    return check


def _pay(o):
    """Each school's own pay as stated; an assistant's pay (Gehülfen) never added to the teacher's."""
    rows = {ln.split(":")[0]: ln for ln in _listed(o["answer"])}
    want = {"Mittleres Stadtquartier": "200 kr", "Stalden": "150 kr", "Bollwerk": "200 kr", "Mattenquartier (Knabenschule)": "375"}
    miss = [k for k, v in want.items() if not any(k in name and v in ln for name, ln in rows.items())]
    added = [name for name, ln in rows.items() if "= 250" in ln or "= 200 kr" in ln and "Stalden" in name]
    return o["answer_source"] == "aggregate" and not miss and not added, f"missing {miss} assistants added {added}"


def _lookup(o):
    return o["answer_source"] != "aggregate", f"answer_source {o['answer_source']}"


BOTH = lambda now, past: [now, past]  # noqa: E731  Brunngasse counted as a present figure / as a past one


def cases():
    g = gold()
    return [
        ("schools", "how many schools are there in the city of bern in 1799 ?", _count(9)),
        ("schools", "Combien d'écoles y avait-il à Lausanne en 1799 ?", _count(g["lausanne"])),
        ("teachers", "What was the average age of the teachers in the district of Hinwil?", _value(g["hinwil_age"], 0.6)),
        ("teachers", "How many female teachers were there in the Stapfer survey?", _count(g["female_teachers"])),
        ("consultations", "How many consultations were there in 1984?", _count(g["cons_1984"])),
        ("consultations", "How many consultations did each department run in 1984?", _groups(g["by_dep_1984"])),
        ("consultations", "How many consultations about data protection were there?", _count(1)),
        ("pupils", "how many students where there in the schoool in bern in 1799 ?", _range(BOTH((782, 836, True), (686, 726, True)))),
        ("pupils", "Wie viele Mädchen gingen 1799 in Bern zur Schule?", _range(BOTH((353, 383, True), (293, 313, True)))),
        ("pupils", "How many girls went to school in Bern in 1799?", _range(BOTH((353, 383, True), (293, 313, True)))),
        ("pupils", "Wie viele Knaben gingen 1799 in Bern zur Schule?", _range(BOTH((347, 371, True), (311, 331, True)))),
        ("condition", "Which schools in Bern had more than 100 pupils in 1799?", _lists(["Stalden", "Bollwerk"], ["Postgasse", "Aarbergergasse", "Mittleres"])),
        ("pay", "What was the teacher's pay in the schools of Bern?", _pay),
        ("lookup", "Wie alt war der Lehrer in Hörndli?", _lookup),
        ("lookup", "Wer war der Lehrer an der Postgasse in Bern?", _lookup),
        ("lookup", "Which consultation in 1984 dealt with data protection?", _lookup),
    ]


def rescore():
    """The checks again over the saved answers (data_eval_cases.jsonl), without calling Apertus."""
    checks = {q: (cat, check) for cat, q, check in cases()}
    rows = [json.loads(line) for line in open(OUT / "data_eval_cases.jsonl", encoding="utf-8")]
    for r in rows:
        r["ok"], r["detail"] = checks[r["q"]][1](r)
        r["ok"] = bool(r["ok"])
    return rows


def main(workers=4, repeats=REPEATS):
    if not llm.available():
        sys.exit("data_eval needs Apertus (APERTUS_BASE_URL, APERTUS_API_KEY)")
    idx = qa.Index()
    jobs = [(c, n) for c in cases() for n in range(repeats)]

    def one(job):
        (cat, q, check), n = job
        t0 = time.time()
        o = qa.answer(idx, q, 6, True, "dynamic")
        ok, detail = check(o)
        return {"category": cat, "q": q, "run": n, "ok": bool(ok), "detail": detail, "answer_source": o["answer_source"],
                "number_check_ok": (o.get("number_check") or {}).get("ok"), "seconds": round(time.time() - t0, 1),
                "plan": o.get("data_plan"), "engine": o["route"].get("data_engine"), "answer": o["answer"][:8000]}

    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, jobs))
    report(rows, repeats)


def report(rows, repeats=REPEATS):
    per_q = {}
    for r in rows:
        per_q.setdefault(r["q"], {"category": r["category"], "passed": 0, "runs": 0, "details": []})
        per_q[r["q"]]["passed"] += r["ok"]; per_q[r["q"]]["runs"] += 1; per_q[r["q"]]["details"].append(r["detail"])
    cats = {}
    for r in rows:
        c = cats.setdefault(r["category"], {"runs": 0, "passed": 0})
        c["runs"] += 1; c["passed"] += r["ok"]
    rep = {"scope": f"{len(per_q)} hand-checked data questions (counts, averages, groups, pupil totals, a condition, pay; "
                    f"3 lookups that must stay with the passage answer), each run {repeats} times end to end "
                    "(dynamic rewrite, data engine, Apertus 70B); 1799 Bern and Lausanne, Hinwil, consultations of 1984",
           "caveat": "a small hand-made set around the cases the engine was built on; a pass needs the exact gold result",
           "model": llm.MODEL,
           "runs": len(rows), "runs_passed": sum(r["ok"] for r in rows),
           "questions_passed_every_run": sum(v["passed"] == v["runs"] for v in per_q.values()),
           "by_category": cats, "questions": per_q,
           "seconds_median": sorted(r["seconds"] for r in rows)[len(rows) // 2]}
    json.dump(rep, open(OUT / "data_eval.json", "w"), indent=1, ensure_ascii=False)
    with open(OUT / "data_eval_cases.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({k: v for k, v in rep.items() if k != "questions"}, indent=1, ensure_ascii=False))
    for q, v in per_q.items():
        print(f'{v["passed"]}/{v["runs"]}  {q}  {v["details"]}')


if __name__ == "__main__":
    if "--rescore" in sys.argv:
        report(rescore())
    else:
        main(*(int(a) for a in sys.argv[1:3]))
