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
    places = {t for t in toks if t in gz} | {a + " " + b for a, b in zip(toks, toks[1:], strict=False) if a + " " + b in gz}
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
                rule_intents = list(r["intents"]); r["rule_intents"] = rule_intents
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


NO_INFO = re.compile(r"keine(rlei)? (angaben|informationen?|antwort)|enthalten nicht|no information|do not contain|does not contain|"
                     r"none of the (provided |given )?(archival )?(sources|documents|passages)|no (relevant )?(source|passage)s? mention|"
                     r"aucune (information|des sources|source)|ne contiennent pas|ne mentionnent? pas|nessuna informazione", re.I)
CITE_RANGE = re.compile(r"\[\d+\]\s*(?:-|–|bis|to|à|au|a)\s*\[\d+\]", re.I)  # "[1] bis [6]": all sources as a block, not a citation
REFUSAL = re.compile(r"cannot (fulfil+|answer|help)|can't (answer|help)|not a meaningful|unable to answer|kann (die frage )?nicht|"
                     r"keine sinnvolle|je ne peux pas|ne peux pas répondre|pas une question", re.I)
WORD = re.compile(r"[^\W\d_]{3,}")  # a run of 3+ letters in any script
NO_DIRECT_MSG = {
    "de": "In den gefundenen Archivstellen steht dazu keine direkte Angabe. Die relevantesten Stellen stehen unten.",
    "fr": "Les passages d'archives trouvés ne donnent pas de réponse directe. Les passages les plus pertinents sont ci-dessous.",
    "it": "I passaggi d'archivio trovati non danno una risposta diretta. I passaggi più pertinenti sono qui sotto.",
    "en": "The archive passages found do not state this directly. The most relevant passages are below."}
NOT_FOUND_MSG = {
    "de": "Die hier verwendeten Archive (Stapfer-Enquête 1799, Vernehmlassungen 1960–1991) enthalten keine Antwort auf diese Frage.",
    "fr": "Les archives utilisées ici (enquête Stapfer 1799, consultations fédérales 1960–1991) ne contiennent pas de réponse à cette question.",
    "it": "Gli archivi usati qui (inchiesta Stapfer 1799, consultazioni federali 1960–1991) non contengono una risposta a questa domanda.",
    "en": "The archives used here (Stapfer-Enquête 1799, Federal Consultations 1960–1991) do not contain an answer to this question."}
INVALID_MSG = {
    "de": "Bitte stellen Sie eine Frage zu den Schulen von 1799 oder zu den Vernehmlassungen 1960–1991.",
    "fr": "Posez une question sur les écoles de 1799 ou sur les consultations fédérales 1960–1991.",
    "it": "Fai una domanda sulle scuole del 1799 o sulle consultazioni federali 1960–1991.",
    "en": "Please ask a question about the 1799 schools or the federal consultations 1960–1991."}


def _qlang(q):
    from .stapfer_clean import guess_lang
    l = guess_lang(q)
    if l in ("de", "fr", "it"):
        return l
    a = _ascii(q)
    return "de" if re.search(r"\b(wie|wer|was|wo|welche|gab|hatte)\b", a) else "fr" if re.search(r"\b(qui|quel|quelle|combien|ou)\b", a) else "en"

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


CONF_FEATURES_SIGNALS = ["top_score", "margin", "has_place", "intent_agree", "no_intent", "apertus_answer", "n_cited", "cites_top",
                         "number_ok", "consultation", "log_pool"]
CONF_FEATURES_LOGPROBS = ["tok_mean_prob", "tok_min_prob", "tok_min_number_prob", "tok_min_citation_prob"]
CONF_FEATURES = CONF_FEATURES_SIGNALS + CONF_FEATURES_LOGPROBS
_CONF = {}


def confidence_features(out):
    h, r = out["sources"], out["route"]
    sc = [x.get("score", 0.0) for x in h] + [0, 0]
    cited = out.get("cited") or []
    rule = r.get("rule_intents", r.get("intents", []))
    return {"top_score": sc[0], "margin": sc[0] - sc[1], "has_place": float(bool(r.get("places"))),
            "intent_agree": float(set(rule) == set(r.get("intents", []))), "no_intent": float(not r.get("intents")),
            "apertus_answer": float(out["answer_source"] == "apertus"), "n_cited": min(len(cited), 6) / 6, "cites_top": float(1 in cited),
            "number_ok": float((out.get("number_check") or {"ok": True})["ok"]), "consultation": float(bool(h) and h[0]["kind"] == "consultation"),
            "log_pool": float(np.log1p(r.get("filtered_pool", 0)) / 12), **_tok_features(out)}


def _tok_features(out):
    """Final-layer token probabilities of Apertus' answer; neutral values (1.0) when there is no generated answer."""
    t = (out.get("llm_meta") or {}).get("token_confidence") or {}
    used = out["answer_source"] == "apertus" and t
    g = lambda k: float(t[k]) if used and t.get(k) is not None else 1.0
    return {"tok_mean_prob": g("mean_prob"), "tok_min_prob": g("min_prob"), "tok_min_number_prob": g("min_number_prob"),
            "tok_min_citation_prob": g("min_citation_prob")}


def confidence(out, path=OUT / "confidence_model.json"):
    """Calibrated estimate that the cited sources answer the question (logistic model fitted by archai.qa_eval --confidence)."""
    if "m" not in _CONF:
        _CONF["m"] = json.loads(path.read_text()) if path.exists() else None
    m = _CONF["m"]
    if not m or out["answer_source"] not in ("apertus", "extractive") or not out["sources"]:
        return None
    f = confidence_features(out)
    z = m["intercept"] + sum(c * (f[k] - mu) / sd for k, c, mu, sd in zip(m["features"], m["coef"], m["mean"], m["std"], strict=False))
    # shown between 1 and 99: the best calibration bucket was right ~98% of the time, never "certain"
    return {"percent": int(min(99, max(1, round(100 / (1 + np.exp(-z)))))), "meaning": m["meaning"], "calibration": m["calibration_summary"]}


def answer(idx, q, k=6, use_llm=True, rewrite="off"):
    out = _answer(idx, q, k, use_llm, rewrite)
    c = confidence(out)
    if c:
        out["confidence"] = c
    return out


def _answer(idx, q, k=6, use_llm=True, rewrite="off"):
    lang = _qlang(q)
    if not WORD.search(q):  # no word at all (emoji, punctuation, digits): nothing to search for, no model call
        return {"question": q, "route": {"intents": [], "places": []}, "sources": [], "answer": INVALID_MSG[lang],
                "cited": [], "answer_source": "invalid_input", "citation_ok": True}
    hits, r = idx.search(q, k, rewrite if use_llm else "off")
    out = {"question": q, "route": r, "sources": hits}
    src = "\n".join(f"[{i + 1}] ({h['cite']}) {h['text'][:SRC_CHARS]}" for i, h in enumerate(hits))
    a = None
    if use_llm:
        a, meta = llm.chat_text(PROMPT.format(src=src, q=q), max_tokens=300, logprobs=True)
        out["llm_meta"] = meta
    if a:
        cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", a) if 0 < int(n) <= len(hits)})
        specific = sorted({int(n) for n in re.findall(r"\[(\d+)\]", CITE_RANGE.sub(" ", a)) if 0 < int(n) <= len(hits)})
        # a per-school list may say "no information" for one school; only reject answers that are a refusal overall
        fails = check_numbers(a, hits, q)
        out["number_check"] = {"ok": not fails, "failures": fails}
        if specific and not ((NO_INFO.search(a) or REFUSAL.search(a)) and len(specific) < 2) and not fails:
            out.update(answer=a, cited=cited, answer_source="apertus", citation_ok=True)
        elif not fails and (NO_INFO.search(a) or REFUSAL.search(a)) and len(specific) < 2:
            # Apertus says the passages do not answer this (Charter: "say that the answer is unknown rather than guess").
            # Fixed text, not the model's, so no outside knowledge slips in. With a recognised place the sheet exists but
            # states nothing direct ("no_direct_answer"); without one, the question is outside these archives ("not_found").
            return _decline(out, lang, a, "model_says_not_in_sources")
        else:  # uncited, or a number its citation does not contain: show the passages
            out["llm_rejected"] = a
            out["llm_rejected_reason"] = "number_not_in_cited_source" if fails else "uncited_or_no_information"
            a = None
    if not a:
        if not (r.get("intents") or r.get("places")) and (use_llm or not hits):  # nothing in the question connects to the archives
            return _decline(out, lang, out.get("llm_rejected"), out.get("llm_rejected_reason") or "no_route")
        out.update(answer="\n".join(f"[{i + 1}] {h['text'][:300]}" for i, h in enumerate(hits[:3])),
                   cited=[1, 2, 3][:len(hits)], answer_source="extractive", citation_ok=True)
    return out


def _decline(out, lang, model_text, reason):
    direct = bool(out["route"].get("places")) and bool(out["sources"])
    out.update(answer=(NO_DIRECT_MSG if direct else NOT_FOUND_MSG)[lang], cited=[], citation_ok=True,
               answer_source="no_direct_answer" if direct else "not_found", llm_rejected=model_text, llm_rejected_reason=reason)
    return out


if __name__ == "__main__":
    idx = Index()
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or "Wie viele Kinder besuchten die Schule in Hörndli?"
    print(json.dumps(answer(idx, q, use_llm="--no-llm" not in sys.argv), ensure_ascii=False, indent=1, default=str))
