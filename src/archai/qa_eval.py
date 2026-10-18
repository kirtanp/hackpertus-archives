"""Retrieval accuracy of the Q&A router: rules only vs. Apertus query rewriting.

Stapfer cases are templated questions (DE/FR/EN) about 10 sampled places; gold = the passages of that place in the
questionnaire sections of the asked intent, taken from the database, not from a model. Each intent has one phrasing the
rules know and three paraphrases they do not. Consultation cases are hand-written English/French/German paraphrases of
14 sampled consultation titles; gold = that consultation. Writes data/out/qa_eval.json and qa_eval_cases.jsonl."""
import json, random, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from . import qa, llm

OUT = Path("data/out")
MODES = ["off", "gaps", "apertus"]
TEMPLATES = {  # intent: [(style, question)]; "rules" = wording the regex router knows
    "pupils": [("rules", "Wie viele Kinder besuchten die Schule in {p}?"), ("para", "How many kids went to class in {p}?"),
               ("para", "Combien d'écoliers fréquentaient l'école de {p}?"), ("para", "Wie gross war die Klasse in {p}?")],
    "salary": [("rules", "Was verdiente der Schulmeister in {p}?"), ("para", "What were the wages of the schoolmaster in {p}?"),
               ("para", "Combien gagnait le maître d'école à {p}?"), ("para", "Wie viel bekam der Lehrer in {p} im Jahr?")],
    "books": [("rules", "Welche Bücher wurden in {p} benutzt?"), ("para", "What did pupils read from in {p}?"),
              ("para", "Quels ouvrages utilisait-on à l'école de {p}?"), ("para", "Welche Lesestoffe hatte man in {p}?")],
    "teacher": [("rules", "Wer war der Lehrer in {p}?"), ("para", "Who ran the school in {p}?"),
                ("para", "Qui faisait la classe à {p}?"), ("para", "Wer unterrichtete die Jugend in {p}?")],
    "schoolhouse": [("rules", "Wie war das Schulhaus in {p}?"), ("para", "Where were lessons held in {p}?"),
                    ("para", "Dans quel local se tenait l'école de {p}?"), ("para", "In welchem Raum fand der Unterricht in {p} statt?")],
    "schedule": [("rules", "Wurde in {p} auch im Winter Schule gehalten?"), ("para", "During which months was school held in {p}?"),
                 ("para", "À quelle saison l'école était-elle ouverte à {p}?"), ("para", "Wann ging man in {p} zum Unterricht?")],
}
CONSULTATIONS = [  # (question, gold doc_id) — paraphrases written by hand from the titles, avoiding the title wording
    ("Which consultation proposed changing the 1905 food law?", "consultation:262"),
    ("When did the government consult on reforming the army's organisation?", "consultation:368"),
    ("Quelle consultation portait sur la réforme de l'assurance maladie?", "consultation:543"),
    ("Was there a consultation about violence by spectators at sports events?", "consultation:477"),
    ("Which consultation set the legal blood alcohol limit for drivers?", "consultation:256"),
    ("When was the double taxation agreement with Germany revised?", "consultation:54"),
    ("Which consultation dealt with limiting the number of foreign workers?", "consultation:161"),
    ("Which proposal wanted a brake on federal spending?", "consultation:481"),
    ("Wann wurde über die inländische Zuckerproduktion vernehmlasst?", "consultation:253"),
    ("Which consultation concerned the popular initiative to abolish animal experiments?", "consultation:390"),
    ("Which consultation proposed lowering the age of majority and the marriage age?", "consultation:557"),
    ("Which law was meant to promote housing construction and home ownership?", "consultation:105"),
    ("Quelle consultation concernait la protection des données personnelles?", "consultation:418"),
    ("Which consultation dealt with handing federal tasks to other bodies?", "consultation:222"),
]


def stapfer_cases(idx, n_places=10, seed=3):
    by_place = {}
    for p in idx.P:
        if p["kind"] == "stapfer":
            for pl in p["places"]:
                by_place.setdefault(pl, set()).add(p["doc_id"])
    docs = {json.loads(l)["doc_id"]: json.loads(l) for l in open(OUT / "stapfer_documents.jsonl", encoding="utf-8")}
    cands = []  # places named by exactly one or two sheets, written the way the database writes them
    for d in docs.values():
        for pl in d["places"][:1]:
            a = qa._ascii(pl["name_1799"])
            if a in idx.gaz and len(by_place.get(a, ())) <= 2 and len(a) >= 4 and " " not in a:
                cands.append((pl["name_1799"], a, d["language"]))
    cands = sorted(set(cands))
    rnd = random.Random(seed)
    de = rnd.sample([c for c in cands if c[2] == "de"], 6); fr = rnd.sample([c for c in cands if c[2] == "fr"], 4)
    cases = []
    for name, a, _ in de + fr:
        for intent, tpls in TEMPLATES.items():
            secs = tuple(qa.INTENTS[intent][1])
            gold = {p["id"] for p in idx.P if p["kind"] == "stapfer" and a in p["places"] and p["qno"].startswith(secs)}
            if intent == "salary":  # the router's documented fallback when a sheet has no IV.16 answer
                gold |= {p["id"] for p in idx.P if p["kind"] == "stapfer" and a in p["places"] and p["qno"].startswith(("IV.13", "IV.14"))}
            if not gold:
                continue
            for style, t in tpls:
                cases.append({"q": t.format(p=name), "dataset": "stapfer", "intent": intent, "place": a, "style": style, "gold": sorted(gold)})
    return cases


def run_case(idx, c, mode):
    t0 = time.time()
    hits, r = idx.search(c["q"], 6, mode)
    ids = [h["id"] if c["dataset"] == "stapfer" else h["doc_id"] for h in hits]
    gold = set(c["gold"])
    return {"q": c["q"], "mode": mode, "dataset": c["dataset"], "style": c["style"], "intent": c.get("intent"),
            "hit1": bool(ids[:1]) and ids[0] in gold, "hit6": any(i in gold for i in ids),
            "intent_ok": (c.get("intent") or "consultation") in r["intents"],
            "place_ok": c.get("place") in r["places"] if c["dataset"] == "stapfer" else None,
            "rewrite_called": "rewrite" in r, "rewrite_failed": "failed" in r.get("rewrite", {}),
            "route": {k: v for k, v in r.items() if k != "rewrite"}, "rewrite": r.get("rewrite"), "seconds": round(time.time() - t0, 2)}


def summarise(rows):
    def agg(rs):
        n = len(rs)
        if not n:
            return None
        out = {"cases": n, "hit_at_1": round(sum(r["hit1"] for r in rs) / n, 3), "hit_at_6": round(sum(r["hit6"] for r in rs) / n, 3),
               "intent_correct": round(sum(r["intent_ok"] for r in rs) / n, 3)}
        pl = [r for r in rs if r["place_ok"] is not None]
        if pl:
            out["place_correct"] = round(sum(r["place_ok"] for r in pl) / len(pl), 3)
        out["apertus_calls"] = sum(r["rewrite_called"] for r in rs)
        out["apertus_failures"] = sum(r["rewrite_failed"] for r in rs)
        out["seconds_median"] = sorted(r["seconds"] for r in rs)[n // 2]
        return out
    rep = {}
    for m in MODES:
        rm = [r for r in rows if r["mode"] == m]
        rep[m] = {"all": agg(rm),
                  "stapfer_rules_wording": agg([r for r in rm if r["dataset"] == "stapfer" and r["style"] == "rules"]),
                  "stapfer_paraphrased": agg([r for r in rm if r["dataset"] == "stapfer" and r["style"] == "para"]),
                  "consultations_paraphrased": agg([r for r in rm if r["dataset"] == "consultation"])}
    return rep


def mode_runner(idx, mode):
    """Per-case callable for one evaluation mode. `mode` is bound now, as a default argument, so the pool can never
    run a callable that reads a loop variable which has already moved on to the next mode."""
    return lambda c, mode=mode: run_case(idx, c, mode)


def main(workers=4):
    idx = qa.Index()
    cases = stapfer_cases(idx) + [{"q": q, "dataset": "consultation", "style": "para", "gold": [g]} for q, g in CONSULTATIONS]
    rows = []
    for m in MODES:
        with ThreadPoolExecutor(workers if m != "off" else 1) as ex:
            rows += list(ex.map(mode_runner(idx, m), cases))
    n_st = sum(c["dataset"] == "stapfer" for c in cases)
    n_pl = len({c["place"] for c in cases if c["dataset"] == "stapfer"})
    rep = {"scope": f"{len(cases)} questions: {n_st} templated Stapfer questions ({n_pl} sampled places, seed 3; 6 intents; "
                    f"1 rules-wording + 3 paraphrases each, DE/FR/EN) + {len(CONSULTATIONS)} hand-written consultation paraphrases",
           "caveat": "paraphrases were written to avoid the rule keywords, so rules-only scores on them are a floor, not typical use",
           "model": llm.MODEL,
           "gold": "Stapfer: passages of that place in the intent's questionnaire sections (database, not a model); consultations: the consultation the question was written from",
           "metric_definitions": {"hit_at_1": "top passage is a gold passage", "hit_at_6": "a gold passage is among the 6 passages Apertus answers from",
                                  "intent_correct": "router intent includes the asked intent", "place_correct": "router place is the asked place"},
           "modes": {"off": "rules only (deployed before)", "gaps": "Apertus rewrites only when the rules find no intent or no place; rules win",
                     "apertus": "Apertus rewrites every question; its intents win, rules are the fallback"},
           "results": summarise(rows)}
    json.dump(rep, open(OUT / "qa_eval.json", "w"), indent=1, ensure_ascii=False)
    with open(OUT / "qa_eval_cases.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    return rep


def answers(workers=4):
    """Full answers (Apertus rewrite + Apertus answer) for every case: how often the number check rejects an answer."""
    idx = qa.Index()
    cases = stapfer_cases(idx) + [{"q": q, "dataset": "consultation", "style": "para", "gold": [g]} for q, g in CONSULTATIONS]
    def one(c):
        o = qa.answer(idx, c["q"], 6, True, "apertus")
        return {"q": c["q"], "dataset": c["dataset"], "answer_source": o["answer_source"], "reason": o.get("llm_rejected_reason"),
                "number_failures": (o.get("number_check") or {}).get("failures", []), "answer": o.get("llm_rejected") or o["answer"],
                "llm_errors": (o.get("llm_meta") or {}).get("errors")}
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, cases))
    n = len(rows)
    got = [r for r in rows if not r["llm_errors"] or r["answer_source"] == "apertus"]
    rep = {"scope": f"full answers (Apertus rewrite + Apertus 70B answer) for the {n} retrieval-eval questions",
           "answers": n, "accepted": sum(r["answer_source"] == "apertus" for r in rows),
           "rejected_number_not_in_cited_source": sum(r["reason"] == "number_not_in_cited_source" for r in rows),
           "rejected_uncited_or_no_information": sum(r["reason"] == "uncited_or_no_information" for r in rows),
           "llm_unavailable": n - len(got),
           "number_check_definition": "every number written in digits in a sentence must occur in the sources that sentence cites (as shown to the model, digits or FR/DE number words) or in the question; numbers the answer writes as words are not checked"}
    json.dump(rep, open(OUT / "qa_answer_check.json", "w"), indent=1, ensure_ascii=False)
    with open(OUT / "qa_answer_check_cases.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps(rep, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    if "--answers" in sys.argv:
        answers()
    else:
        main(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
