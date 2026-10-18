"""Cited question answering: deterministic router -> hybrid TF-IDF retrieval -> Apertus answer with [n] citations.

The router is rule-based and auditable: it maps the question to Stapfer questionnaire sections
(e.g. salary -> IV.16), to a place from the gazetteer, or to the consultations dataset. Apertus only
writes the final sentence; without Apertus the answer is extractive (top cited passages)."""
import json, re, sys, unicodedata
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel
from . import llm

OUT = Path("data/out")
FREE = "zusatz.fliesstext"
INTENTS = {  # intent: (multilingual trigger regex, questionnaire sections; None = consultations)
    "pupils": (r"pupil|sch(ü|ue)ler|schulkind|kinder|knaben|m(ä|ae)dchen|(é|e)l(è|e)ve|enfant|gar(ç|c)on|fille|children", ["III.12", "I.3.b"]),
    "salary": (r"salar|lohn|einkommen|besoldung|verdien|salaire|revenu|traitement|income|\bpay|paid|earn", ["IV.16"]),
    "books": (r"book|b(ü|ue)cher|buch\b|livre|manuel|lehrmittel", ["II.7"]),
    "subjects": (r"taught|gelehrt|subject|enseign|curriculum|lesen|schreiben|rechnen", ["II.5"]),
    "teacher": (r"teacher|lehrer|schulmeister|r(é|e)gent|ma(î|i)tre|instituteur", ["III.11"]),
    "schoolhouse": (r"schoolhouse|schulhaus|schulstube|building|b(â|a)timent|maison d", ["IV.15"]),
    "fund": (r"\bfund|fonds|stiftung|schulgeld|school fee|quelle|source|woher|bezahlt|paid by", ["IV.13", "IV.14", "IV.16.B"]),
    "schedule": (r"winter|summer|sommer|hiver|\b(é|e)t(é|e)\b|how long|wie lange|hours|stunden", ["II.6", "II.9"]),
    "consultation": (r"vernehmlassung|consultation|bundesgesetz|verordnung|gesetz|\bloi\b|ordonnance|dossier|departement|bundesrat|19[6-9]\d", None),
    "other_dataset": (r"(?!)", None),  # datasets added with archai.onboard --publish; only Apertus routes here
}


def _ascii(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def _pages(a):
    return f"S. {a.page_start}" if a.page_start == a.page_end else f"S. {a.page_start}–{a.page_end}"


def passages():
    import pandas as pd
    P, docs = [], {}
    for l in open(OUT / "stapfer_documents.jsonl", encoding="utf-8"):
        d = json.loads(l); docs[d["doc_id"]] = d
    ans = pd.read_parquet(OUT / "stapfer_answers.parquet", columns=["answer_id", "doc_id", "question_no", "question_de", "question_fr", "text_clean", "page_start", "page_end"])
    for a in ans.dropna(subset=["text_clean"]).itertuples(index=False):
        if len(re.sub(r"\W", "", a.text_clean)) < 3:  # empty / "O." answers carry nothing to retrieve
            continue
        d = docs[a.doc_id]
        place = ", ".join(p["name_1799"] for p in d["places"][:2])
        # towns like Lausanne have many schools: name school + teacher so answers from different sheets stay apart
        school = "; ".join(dict.fromkeys(s["name"] for s in d.get("schools") or [] if s.get("name")))
        teacher = "; ".join(dict.fromkeys(t["name"] for t in d.get("teachers") or [] if t.get("name")))
        label = school if school.startswith(place) else place + (f" – {school}" if school else "")
        label += f" (Lehrer: {teacher})" if teacher else ""
        q = a.question_fr if d.get("language") == "fr" and a.question_fr else a.question_de
        P.append({"id": a.answer_id, "doc_id": a.doc_id, "kind": "stapfer", "qno": a.question_no or "",
                  "places": {_ascii(p["name_1799"]) for p in d["places"]} | {_ascii(p["municipality_today"] or "") for p in d["places"]},
                  "text": f"{label} 1799. Frage {a.question_no} ({q}): {a.text_clean}",
                  "cite": f'{d["provenance"]["archive_signature"]}, {_pages(a)}, Frage {a.question_no}', "page": int(a.page_start),
                  "url": d["provenance"]["viewer_url"], "facsimile": d["provenance"]["facsimile_file"]})
    for l in open(OUT / "consultations.jsonl", encoding="utf-8"):
        d = json.loads(l)
        units = [a["olr"] for a in d["archive_refs"] if "olr" in a]
        text = (f'{d["title"]} ({d["type"]}, {d["department_abbr"]}, {d["year"]}); Vernehmlassung '
                f'{d["consultation_start"] or "?"} bis {d["consultation_end"] or "?"}; Adressaten: {", ".join(d["addressees"])}. '
                + " ".join(f'Dossier {u["signature"]}: {u["title"]} ({(u["begin"] or "?")[:4]}-{(u["end"] or "?")[:4]}).' for u in units))
        P.append({"id": d["doc_id"], "doc_id": d["doc_id"], "kind": "consultation", "qno": "", "places": set(), "text": text,
                  "cite": "; ".join(u["signature"] for u in units) or f'Metadatenbank Vernehmlassungen Nr. {d["provenance"]["row_nr"]}',
                  "url": units[0]["url"] if units else d["provenance"]["source_dataset"], "facsimile": None})
    for rec in onboarded_records():
        pv = rec["provenance"]
        P.append({"id": rec["record_id"], "doc_id": rec["record_id"], "kind": "onboarded", "qno": "",
                  "places": {_ascii(x["name"]) for x in rec["places"] if x.get("name")}, "text": f'{rec["title"]}. {rec["text"]}'[:1500],
                  "cite": pv.get("archive_signature") or f'{rec["dataset"]}, Zeile {pv["row_nr"]}',
                  "url": pv.get("viewer_url") or pv["source_dataset"], "facsimile": None})
    return P


ONBOARD = Path("data/onboard")


def published():
    """Reports of datasets onboarded with --publish (archai.onboard)."""
    out = []
    for rp in sorted(ONBOARD.glob("*/report.json")):
        rep = json.loads(rp.read_text())
        if rep.get("published_to_qa"):
            m = json.loads((rp.parent / "mapping.json").read_text())
            out.append({"dataset": rep["dataset"], "description": m.get("description") or rep["dataset"], "path": rp.parent / "records.jsonl"})
    return out


def onboarded_records():
    for d in published():
        for l in open(d["path"], encoding="utf-8"):
            yield json.loads(l)


def route(q, gazetteer):
    qa = _ascii(q)
    intents = [k for k, (rx, _) in INTENTS.items() if re.search(rx, q, re.I) or re.search(rx, qa, re.I)]
    toks = re.findall(r"\w{4,}", qa)
    gz = set(gazetteer)
    trigger = lambda t: any(re.fullmatch(rf"(?:{rx})\w*", t, re.I) for rx, _ in INTENTS.values())
    toks = [t for t in toks if not trigger(t)]  # "Lohn" is also a village: question words are not places
    places = {t for t in toks if t in gz} | {a + " " + b for a, b in zip(toks, toks[1:]) if a + " " + b in gz}
    if not places:  # historical spellings vary (Hörndli/Hörnli): fuzzy-match capitalised words only
        import difflib
        caps = [_ascii(w) for w in re.findall(r"\b[A-ZÄÖÜÉÈ][\wäöüéèàç-]{3,}", q)]
        for w in caps:
            if not trigger(w):
                places |= set(difflib.get_close_matches(w, gazetteer, n=1, cutoff=0.85))
    places = sorted(places)
    if "teacher" in intents and len([i for i in intents if i not in ("teacher", "consultation")]) > 0:
        intents.remove("teacher")  # "what did the teacher earn" is a salary question
    if "consultation" in intents and len(intents) > 1 and not places:
        intents = ["consultation"]
    return {"intents": intents, "places": places}


INTENT_HELP = {
    "pupils": "number of pupils / children attending", "salary": "the teacher's income or pay",
    "books": "school books / reading material", "subjects": "what was taught", "teacher": "who the teacher was (name, age, origin, family)",
    "schoolhouse": "the school building or room", "fund": "school funds, fees, who pays for the school",
    "schedule": "winter/summer school, months, hours per day",
    "consultation": "Swiss federal consultations 1960-1991 on laws, ordinances, treaties (not the 1799 school survey)",
    "other_dataset": "",  # filled with the published datasets' descriptions
}
REWRITE = """Turn this question into a search request for two Swiss archive datasets: the 1799 Stapfer school survey
(answers by village teachers) and the federal consultations 1960-1991. Reply with JSON only:
{{"intents": [one or two of: {intents}], "place": "the village/town asked about, or null",
 "keywords": ["up to 6 search words in German and French, phrased as the archival source would phrase them"]}}
Intents:
{help}
Question: {q}"""


def rewrite_query(q, gazetteer):
    """Apertus maps a free question to the router's fields. Deterministic validation: unknown intents are dropped,
    the place must match the gazetteer (exact or fuzzy), keywords are capped; nothing unvalidated reaches the search."""
    import difflib
    pub = published()  # the extra intent is only offered when a dataset was published, so the evaluated prompt is unchanged
    helps = {k: v for k, v in INTENT_HELP.items() if k != "other_dataset" or pub}
    if pub:
        helps["other_dataset"] = "anything that is not about the 1799 schools or the consultations, in particular: " + "; ".join(d["description"] for d in pub)
    prompt = REWRITE.format(intents=", ".join(helps), help="\n".join(f"- {k}: {v}" for k, v in helps.items()), q=q)
    obj, meta = llm.chat_json(prompt, schema={"intents": list, "keywords": list}, max_tokens=160)
    if not obj:
        return None, meta
    intents = list(dict.fromkeys(i for i in obj.get("intents", []) if isinstance(i, str) and i in INTENTS))[:2]
    places, place = [], obj.get("place")
    if isinstance(place, str) and place.strip() and place.strip().lower() not in ("null", "none"):
        p = _ascii(place.strip())
        places = [p] if p in set(gazetteer) else difflib.get_close_matches(p, gazetteer, n=1, cutoff=0.85)
    kw = [k.strip() for k in obj.get("keywords", []) if isinstance(k, str) and k.strip()][:6]
    return {"intents": intents, "places": places, "keywords": kw, "place_raw": place}, meta


def needs_rewrite(r):
    return not r["intents"] or (r["intents"] != ["consultation"] and not r["places"])


class Index:
    def __init__(self):
        self.P = passages()
        texts = [p["text"] for p in self.P]
        self.vc = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=2)
        self.vw = TfidfVectorizer(analyzer="word", sublinear_tf=True, min_df=1, token_pattern=r"(?u)\b\w\w+\b")
        self.Xc, self.Xw = self.vc.fit_transform(texts), self.vw.fit_transform(texts)
        self.gaz = sorted(set().union(*[p["places"] for p in self.P]) - {""})
        self.kind = np.array([p["kind"] for p in self.P]); self.qno = np.array([p["qno"] for p in self.P])

    def mask(self, r):
        m = np.ones(len(self.P), bool)
        if r["intents"] == ["consultation"]:
            return self.kind == "consultation"
        if "other_dataset" in r["intents"]:
            return self.kind == "onboarded"
        secs = [s for k in r["intents"] if INTENTS[k][1] for s in INTENTS[k][1]]
        if secs:
            m &= np.array([q.startswith(tuple(secs)) or q.startswith(FREE) for q in self.qno])
            if "salary" in r["intents"] and "fund" not in r["intents"]:  # prefer income totals over the per-source breakdown
                totals = m & np.array([not q.startswith("IV.16.B") for q in self.qno])
                if r["places"]:
                    totals &= np.array([bool(p["places"] & set(r["places"])) for p in self.P])
                if totals.sum() >= 3:
                    m = totals
        if r["places"]:
            at = np.array([bool(p["places"] & set(r["places"])) for p in self.P])
            m &= at
            if not m.any() and "salary" in r["intents"]:  # sheets without IV.16 state pay under endowment / school fee
                m = at & np.array([q.startswith(("IV.13", "IV.14")) for q in self.qno])
                r["fallback"] = "no IV.16 answer for this place; using IV.13/IV.14"
        return m

    def search(self, q, k=6, rewrite="off"):
        """rewrite: "off" = rules only; "gaps" = Apertus fills what the rules missed (rules win);
        "apertus" = Apertus intents win, rules are the fallback. Places: exact gazetteer match from rules first."""
        r = route(q, self.gaz)
        qx = q
        if rewrite != "off" and (rewrite == "apertus" or needs_rewrite(r)):
            rw, meta = rewrite_query(q, self.gaz)
            r["rewrite"] = rw or {"failed": meta.get("errors")}
            if rw:
                rule_intents = r["intents"]
                if rw["intents"] and (rewrite == "apertus" or not rule_intents):
                    r["intents"], r["intents_from"] = rw["intents"], "apertus"
                if rw["places"] and r["intents"] != ["consultation"]:
                    if not r["places"]:
                        r["places"], r["places_from"] = rw["places"], "apertus"
                    elif rewrite == "apertus" and set(rw["places"]) - set(r["places"]):
                        # rules can match a question word that is also a village ("gross"); keep both, both are gazetteer-checked
                        r["places"], r["places_from"] = sorted(set(r["places"]) | set(rw["places"])), "rules+apertus"
                if "teacher" in r["intents"] and len([i for i in r["intents"] if i not in ("teacher", "consultation")]) > 0:
                    r["intents"].remove("teacher")
                if "consultation" in r["intents"] and len(r["intents"]) > 1 and not r["places"]:
                    r["intents"] = ["consultation"]
                qx = q + " " + " ".join(rw["keywords"])  # query expansion in the sources' languages
        s = 0.5 * linear_kernel(self.vc.transform([qx]), self.Xc).ravel() + 0.5 * linear_kernel(self.vw.transform([qx]), self.Xw).ravel()
        m = self.mask(r)
        r["filtered_pool"] = int(m.sum())
        if m.any():
            s = np.where(m, s, -1.0)
        else:
            r["fallback"] = "no passage matched the route; searched everything"
        hits = [{k2: v for k2, v in self.P[i].items() if k2 != "places"} | {"score": round(float(s[i]), 3)} for i in s.argsort()[::-1][:k] if s[i] >= 0]
        return hits, r


NO_INFO = re.compile(r"keine (angaben|informationen?)|enthalten nicht|no information|do not contain|does not contain|"
                     r"aucune information|ne contiennent pas|ne mentionnent pas", re.I)

PROMPT = """Answer the question using ONLY the numbered archival sources. Cite every claim with [n].
If the sources do not contain the answer, say so. Answer in the language of the question.
Sources can come from different schools of the same place. If they give different values, list each school
with its own value and citation (or give the range); never pick a single value as if it were the only one.
Quote numbers exactly as written; never calculate, split or infer numbers that are not in the sources.

Sources:
{src}

Question: {q}"""


NUM = re.compile(r"\d+(?:[.,]\d+)?(?:/\d+)?")
SENT = re.compile(r"\n+|(?<=[.!?;])\s+(?=[A-ZÄÖÜÉÈÀ\[(])")
SRC_CHARS = 600  # what the model sees of each source; numbers are checked against exactly this


def _nums(t):
    t = re.sub(r"(?<=\d)['’](?=\d{3})", "", t or "")  # 1'000 -> 1000
    return {n.replace(",", ".") for n in NUM.findall(t)}


# number words in the sources (FR/DE), so "Quarante-un" in a source grounds "41" in an answer
_NW = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
       "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20, "vingts": 20, "trente": 30,
       "quarante": 40, "cinquante": 50, "soixante": 60, "septante": 70, "huitante": 80, "octante": 80, "nonante": 90,
       "cent": 100, "cents": 100, "mille": 1000,
       "ein": 1, "eins": 1, "eine": 1, "zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
       "zehn": 10, "elf": 11, "zwolf": 12, "dreizehn": 13, "vierzehn": 14, "funfzehn": 15, "sechzehn": 16, "siebzehn": 17,
       "achtzehn": 18, "neunzehn": 19, "zwanzig": 20, "dreissig": 30, "dreizig": 30, "vierzig": 40, "funfzig": 50, "sechzig": 60,
       "siebzig": 70, "achtzig": 80, "neunzig": 90, "hundert": 100, "tausend": 1000}
_DE_COMPOUND = re.compile(r"(ein|zwei|drei|vier|funf|sechs|sieben|acht|neun)und(zwanzig|dreissig|vierzig|funfzig|sechzig|siebzig|achtzig|neunzig)")


def word_numbers(t):
    """Values of FR/DE number words and runs of them ("quarante six" 46, "deux cent soixante" 260, "einundvierzig" 41)."""
    t = _ascii(t)
    out = {str(_NW[m.group(1)] + _NW[m.group(2)]) for m in _DE_COMPOUND.finditer(t)}
    cur, total, run = 0, 0, False
    for w in re.findall(r"[a-z]+", t) + [""]:
        v = _NW.get(w)
        if v is None:
            if w == "et" and run:  # "vingt et un"
                continue
            if run:
                out.add(str(total + cur))
            cur, total, run = 0, 0, False
            continue
        out.add(str(v)); run = True
        if v == 100:
            cur = (cur or 1) * 100
        elif v == 1000:
            total += (cur or 1) * 1000; cur = 0
        elif v == 20 and cur % 100 == 4:  # quatre-vingt(s) = 80
            cur += 76
        else:
            cur += v
    return out


def check_numbers(a, hits, q):
    """Deterministic grounding of numbers: every number written in digits in a sentence must occur in the sources that
    sentence cites (text as shown to the model + its citation, digits or FR/DE number words), or in the question.
    Uncited sentences are checked against all sources. Numbers the answer itself writes as words are not checked."""
    seen = [_nums(f"{h['cite']} {h['text'][:SRC_CHARS]}") | word_numbers(h["text"][:SRC_CHARS]) for h in hits]
    allowed_q, every = _nums(q), set().union(*seen) if seen else set()
    fails = []
    for seg in filter(None, (x.strip() for x in SENT.split(a))):
        refs = [int(n) for n in re.findall(r"\[(\d+)\]", seg) if 0 < int(n) <= len(hits)]
        pool = set().union(*(seen[n - 1] for n in refs)) if refs else every
        for n in _nums(re.sub(r"\[\d+\]", " ", seg)) - allowed_q - pool:
            fails.append({"number": n, "cited": refs, "sentence": seg[:200]})
    return fails


def answer(idx, q, k=6, use_llm=True, rewrite="off"):
    hits, r = idx.search(q, k, rewrite if use_llm else "off")
    out = {"question": q, "route": r, "sources": hits}
    src = "\n".join(f"[{i + 1}] ({h['cite']}) {h['text'][:SRC_CHARS]}" for i, h in enumerate(hits))
    a = None
    if use_llm:
        a, meta = llm.chat_text(PROMPT.format(src=src, q=q), max_tokens=300)
        out["llm_meta"] = meta
    if a:
        cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", a) if 0 < int(n) <= len(hits)})
        # a per-school list may say "no information" for one school; only reject answers that are a refusal overall
        fails = check_numbers(a, hits, q)
        out["number_check"] = {"ok": not fails, "failures": fails}
        if cited and not (NO_INFO.search(a) and len(cited) < 2) and not fails:
            out.update(answer=a, cited=cited, answer_source="apertus", citation_ok=True)
        else:  # uncited, "no information" despite routed sources, or a number its citation does not contain: show the passages
            out["llm_rejected"] = a
            out["llm_rejected_reason"] = "number_not_in_cited_source" if fails else "uncited_or_no_information"
            a = None
    if not a:
        out.update(answer="\n".join(f"[{i + 1}] {h['text'][:300]}" for i, h in enumerate(hits[:3])),
                   cited=[1, 2, 3][:len(hits)], answer_source="extractive", citation_ok=True)
    return out


if __name__ == "__main__":
    idx = Index()
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or "Wie viele Kinder besuchten die Schule in Hörndli?"
    print(json.dumps(answer(idx, q, use_llm="--no-llm" not in sys.argv), ensure_ascii=False, indent=1, default=str))
