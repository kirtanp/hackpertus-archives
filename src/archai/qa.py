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
from .tools import extract_quantities

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
        hits = [self._hit(int(i), round(float(s[i]), 3)) for i in s.argsort()[::-1][:k] if s[i] >= 0]
        return hits, r

    def _hit(self, i, score=None):
        return {k: v for k, v in self.P[i].items() if k != "places"} | {"score": score}

    def pool(self, r):
        """Every passage the route selects, as hits: the pupil total reads the whole routed pool (all schools of the
        place asked about), while an answer otherwise cites the top k passages only."""
        return [self._hit(int(i)) for i in np.where(self.mask(r))[0]]


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


def check_numbers(a, hits, q, extra=()):
    """Deterministic grounding of numbers: every number written in digits in a sentence must occur in the sources that
    sentence cites (text as shown to the model + its citation, digits or FR/DE number words), or in the question.
    Uncited sentences are checked against all sources. Numbers the answer itself writes as words are not checked.
    `extra` is for digits the caller has audited itself: only the deterministic pupil total uses it, and only together
    with the per-school values it is summed from (see aggregate_pupils). The model's own text is never checked with it."""
    seen = [_nums(f"{h['cite']} {h['text'][:SRC_CHARS]}") | word_numbers(h["text"][:SRC_CHARS]) for h in hits]
    allowed_q, every = _nums(q) | set(extra), set().union(*seen) if seen else set()
    fails = []
    for seg in filter(None, (x.strip() for x in SENT.split(a))):
        refs = [int(n) for n in re.findall(r"\[(\d+)\]", seg) if 0 < int(n) <= len(hits)]
        pool = set().union(*(seen[n - 1] for n in refs)) if refs else every
        for n in _nums(re.sub(r"\[\d+\]", " ", seg)) - allowed_q - pool:
            fails.append({"number": n, "cited": refs, "sentence": seg[:200]})
    return fails


# ---------------------------------------------------------------- deterministic pupil totals
#
# "Wie viele Schuelerinnen gab es total in Bern?" asks for a sum over the schools of Bern, but the counts are not a
# column: they sit in the OCR text of the 1799 answers ("Knaben 70", "70 bis 80 Maedchen"). No SUM or GROUP BY can
# aggregate them, and no dataframe engine is needed for the handful of sheets one question retrieves. tools.
# extract_quantities is the repository's parser for exactly those phrases; the code below groups what it returns,
# keeps boys and girls apart (a total for one sex must never contain the other) and refuses to turn a range or two
# competing figures into one number. The sum is arithmetic over values the cited sheets state, so an answer that
# carries one is marked answer_source "aggregate" and never comes from the model, which must not calculate.
AGG_SOURCES_MAX = 120  # more retrieved sources than this and a single total stops being auditable in one answer

_FEMALE_SEX = re.compile(r"sch(?:ue|u)lerinn|m(?:ae|a)d(?:chen|gen|lein|li)|meitli|filles?|t(?:oe|o)chter|ecolieres?|\bgirls?\b")
_MALE_SEX = re.compile(r"\bknab|\bbuben?\b|garcons?|sohne\b|ecoliers?|\bboys?\b")
_GENERIC_SEX = re.compile(r"sch(?:ue|u)ler|schulkind|\bkind(?:er)?\b|pupils?|\bchildren\b|\beleves?\b|\benfants?\b")
_COUNT_WORD = re.compile(r"wie\s*viele?|wieviele?|how\s*many|combien|total|\bsum(?:me)?\b|insgesamt|zusammen|(?:gesamt)?zahl|anzahl|\bnombre\b")
_PUPIL_WORD = re.compile(r"sch(?:ue|u)ler|schulkind|\bkind(?:er)?\b|\bknab|\bbuben?\b|\bm(?:ae|a)d|\bmeitli|\btochter|"
                         r"pupils?|\bchildren\b|\bkids?\b|\bboys?\b|\bgirls?\b|\beleves?\b|\becoliers?\b|\becolieres?\b|"
                         r"\bgarcons?\b|\bfilles?\b|\benfants?\b")


def asked_sex(q):
    """'female', 'male' or None for pupils in general: "Schuelerinnen" must beat the "schueler" inside it, and a
    question that asks both sexes ("Knaben und Maedchen") or only the umbrella word stays None, never one sex."""
    t = _ascii(q)
    if not _FEMALE_SEX.search(t):
        return "male" if _MALE_SEX.search(t) else None
    rest = _FEMALE_SEX.sub(" ", t)  # "schuelerinnen und schueler" asks for girls and pupils, not for girls only
    return None if _MALE_SEX.search(rest) or _GENERIC_SEX.search(rest) else "female"


def is_pupil_count_question(q):
    """True for a question that asks for a number of pupils ("wie viele Schuelerinnen ... total", "combien
    d'ecoliers") and not for one that merely mentions children ("wie viele Stunden waren die Kinder in der Schule"):
    the count word has to stand within three words of a pupil noun."""
    t = _ascii(q)
    for m in _COUNT_WORD.finditer(t):
        near = (" ".join(t[m.end():].split()[:3]), " ".join(t[:m.start()].split()[-2:]))
        if any(_PUPIL_WORD.search(x) for x in near):
            return True
    return False


def _answer_body(h):
    """The teacher's own answer, without the "Bern, Mattenquartier (Lehrer: ...) 1799. Frage III.12 (...):" prefix
    that passages() prepends. The questionnaire header "(Knaben/Mädchen)" is not an answer."""
    t = h.get("text") or ""
    parts = re.split(r"\): ", t, maxsplit=1)
    return parts[1] if len(parts) == 2 else t


def _school_label(h):
    """The sheet an answer belongs to, as passages() labels it: "Bern, Postgasse (Lehrer: Maria Magdalena Wyss)"."""
    return (h.get("text") or "").split(" 1799.")[0].strip() or h.get("cite") or ""


_GAP = r"[\s.,:;!?–—/'’-]"
_CONNECTOR = r"(?:bis|à|a|to|–|—|-)"
_APPROX = re.compile(r"(?:circa|ca\b|etwa|ungef(?:ae|ä)hr|ungefehr|ohngef(?:ae|ä)hr|beil(?:ae|ä)ufig|nahe|rund|environ|approx|about|pres)"
                     + _GAP + r"{0,3}$", re.I)
_OPEN_END = re.compile(_GAP + r"{0,3}(?:und\s+mehr(?:ere)?|oder\s+mehr|et\s+plus|and\s+more|ou\s+plus)", re.I)


def _observation(body, start, end, item):
    """Classify one count extract_quantities returned, from the text around the digits: a plain value, an
    approximation ("circa 65"), a range ("70 bis 80"), a lower bound ("100 und mehr") or a range with a further
    figure ("25 bis 35 und 40"). Every digit comes from the source; nothing here invents or averages one."""
    before, after = body[max(0, start - 18):start], body[end:end + 18]
    o = {"sex": item["sex"], "count": item["count"], "surface": item["surface"], "hits": [item["hit"]],
         "kind": "value", "approx": bool(_APPROX.search(before)), "lo": None, "hi": None, "extra": None,
         "open": bool(_OPEN_END.match(after))}
    left = re.search(r"(\d{1,3})" + _GAP + r"*" + _CONNECTOR + _GAP + r"*$", before)
    right = re.match(_GAP + r"*" + _CONNECTOR + _GAP + r"*(\d{1,3})(?!\d)", after)
    if left or right:
        lo, hi = (int(left.group(1)), item["count"]) if left else (item["count"], int(right.group(1)))
        o.update(kind="range", lo=min(lo, hi), hi=max(lo, hi))
        tail = after[right.end():] if right else after
        m = re.match(_GAP + r"*(?:und|et|sowie|,)" + _GAP + r"*(\d{1,3})(?!\d)", tail)
        o.update(extra=int(m.group(1)) if m else None, open=o["open"] or bool(_OPEN_END.match(tail)))
    elif o["open"]:
        o["kind"] = "open"
    elif o["approx"]:
        o["kind"] = "approx"
    return o


def _observed(body, items, hit):
    """Each extracted count with the digits it belongs to (extract_quantities returns no offsets), classified by
    _observation. A digit is claimed once, so a repeated phrase is read where it actually occurs."""
    claims, out = set(), []
    for it in items:
        span = next((m.span() for m in re.finditer(r"\d+", it["surface"]) if int(m.group()) == it["count"]), None)
        if span is None:
            continue
        for m in re.finditer(re.escape(it["surface"]), body, re.I):
            claim = (m.start() + span[0], m.start() + span[1])
            if claim in claims:
                continue
            claims.add(claim)
            o = _observation(body, claim[0], claim[1], it | {"hit": hit})
            if o:
                out.append(o)
            break
    return out


def _sheet_verdict(obs):
    """What one sheet states for one sex: a single value, or figures that cannot be reduced to one (a range, two
    seasons, a subgroup, "und mehr"), or None when the sheet states no figure for that sex at all. A sheet repeats
    its answer across the questionnaire's sub-questions, so identical readings are merged, citations kept."""
    if not obs:
        return None
    merged = {}
    for o in obs:
        key = tuple(o[k] for k in ("kind", "count", "lo", "hi", "extra", "open", "approx"))
        if key in merged:
            merged[key]["hits"] = sorted({*merged[key]["hits"], *o["hits"]})
        else:
            merged[key] = dict(o)
    obs = list(merged.values())
    plain = [o for o in obs if o["kind"] in ("value", "approx")]
    hits = sorted({i for o in obs for i in o["hits"]})
    if plain and len(plain) == len(obs) and len({o["count"] for o in obs}) == 1:
        return {"kind": "value", "value": obs[0]["count"], "approx": any(o["approx"] for o in obs), "obs": obs, "hits": hits}
    kinds = {o["kind"] for o in obs}
    unbounded = any(o["open"] for o in obs)
    return {"kind": "unclear", "obs": obs, "hits": hits,
            "reason": "open" if unbounded and kinds <= {"range", "open"}
                      else "range" if kinds <= {"range"} else "unclear"}


def aggregate_pupils(sources, question):
    """Group the counts tools.extract_quantities finds over the retrieved sources by sex and by sheet, and total the
    unambiguous ones. Sources that state a range or competing figures are reported as they stand, never folded into
    the total. Returns None when no source states a figure for a sex the question asks about."""
    sheets = {}
    for i, h in enumerate(sources):
        if h.get("kind") != "stapfer":
            continue
        body = _answer_body(h)
        # every retrieved sheet is registered, so a sheet without a figure can be named as such in the answer
        sheet = sheets.setdefault(h.get("doc_id") or h.get("id") or str(i), {"label": _school_label(h), "obs": {}, "hit": i})
        for o in _observed(body, extract_quantities(body)["pupils"], i):
            sheet["obs"].setdefault(o["sex"], []).append(o)
    if not sheets:
        return None
    sex = asked_sex(question)
    scopes = []
    for sx in ([sex] if sex else ["female", "male"]):
        rows = [(s, _sheet_verdict(s["obs"].get(sx, []))) for s in sheets.values()]
        contributors = [dict(s, **v) for s, v in rows if v and v["kind"] == "value"]
        scopes.append({"sex": sx, "contributors": contributors,
                       "unclear": [dict(s, **v) for s, v in rows if v and v["kind"] == "unclear"],
                       "silent": [s for s, v in rows if v is None],
                       "total": sum(c["value"] for c in contributors) if contributors else None,
                       "approx_total": any(c["approx"] for c in contributors)})
    if not any(sc["contributors"] or sc["unclear"] for sc in scopes):
        return None
    return {"sex": sex, "scopes": scopes, "n_sources": len(sources),
            "extra": {str(sc["total"]) for sc in scopes if sc["total"] is not None}}


_PUPIL_TEXT = {
    "de": {"sex": {"female": "Mädchen", "male": "Knaben"}, "approx": "ca. ", "range": "{lo} bis {hi}",
           "more": " und mehr", "extra": " und {v}",
           "computed": "Berechnet aus den zitierten Quellen (nicht aus einer einzelnen Quelle zitiert):",
           "both": "Die Quellen unterscheiden Knaben und Mädchen; eine gemeinsame Gesamtzahl wird aus ihnen nicht gebildet.",
           "heading": "{sex} je Schule:",
           "sum": "Summe der oben aufgeführten eindeutigen Angaben: {expr}. {cites}",
           "sum_approx": "Summe der oben aufgeführten Angaben (teils Näherungswerte): {expr}. {cites}",
           "excluded": "Nicht in die Summe einbezogen:",
           "no_exact": "Für {sex} nennt keine zitierte Quelle eine eindeutige Zahl; daraus lässt sich keine Gesamtzahl bilden.",
           "silent": "Ohne Zahl für {sex} (kein Beitrag): {items}",
           "reasons": {"range": "Bereich statt einer Zahl", "open": "Mindestangabe statt einer Zahl",
                       "unclear": "mehrere verschiedene Angaben"}},
    "fr": {"sex": {"female": "filles", "male": "garçons"}, "approx": "env. ", "range": "de {lo} à {hi}",
           "more": " et plus", "extra": " et {v}",
           "computed": "Calculé à partir des sources citées, et non repris d'une seule source :",
           "both": "Les sources distinguent garçons et filles; aucun total commun n'en est formé.",
           "heading": "{sex} par école :",
           "sum": "Somme des indications univoques listées ci-dessus : {expr}. {cites}",
           "sum_approx": "Somme des indications listées ci-dessus (en partie approximatives) : {expr}. {cites}",
           "excluded": "Non comprises dans la somme :",
           "no_exact": "Aucune source citée ne donne un chiffre univoque de {sex}; aucun total n'en peut être formé.",
           "silent": "Sans chiffre pour {sex} (aucune contribution) : {items}",
           "reasons": {"range": "fourchette, pas un chiffre", "open": "valeur minimale, pas un chiffre",
                       "unclear": "plusieurs indications différentes"}},
    "en": {"sex": {"female": "girls", "male": "boys"}, "approx": "approx. ", "range": "{lo} to {hi}",
           "more": " and more", "extra": " and {v}",
           "computed": "Computed from the cited sources, not quoted from any single one:",
           "both": "The sources keep boys and girls apart; no combined total is formed from them.",
           "heading": "{sex} per school:",
           "sum": "Sum of the unambiguous figures listed above: {expr}. {cites}",
           "sum_approx": "Sum of the figures listed above (some approximate): {expr}. {cites}",
           "excluded": "Not included in the sum:",
           "no_exact": "No cited source gives an unambiguous {sex} figure; no total can be formed from them.",
           "silent": "Without a {sex} figure (no contribution): {items}",
           "reasons": {"range": "a range, not a single figure", "open": "a lower bound, not a single figure",
                       "unclear": "more than one figure"}},
}


def _language(q):
    t = _ascii(q)
    if re.search(r"combien|\bquel|\becol|\beleve|\bfille|\bgarcon|\bdu\b|d'|de la", t):
        return "fr"
    return "en" if re.search(r"how many|pupils?|\bchildren\b|\bthe\b|number of|school", t) else "de"


def _obs_text(o, L):
    if o["kind"] in ("value", "approx"):
        return (L["approx"] if o["kind"] == "approx" else "") + str(o["count"])
    if o["kind"] == "open":
        return str(o["count"]) + L["more"]
    text = L["range"].format(lo=o["lo"], hi=o["hi"])
    if o["extra"] is not None:
        text += L["extra"].format(v=o["extra"])
    return text + (L["more"] if o["open"] else "")


def _cites(hits):
    return "".join(f"[{i + 1}]" for i in hits)


def render_pupil_totals(agg, question):
    """The aggregate as text in the language of the question, with every contributing school, its own value and its
    citation, the schools that could not be summed and why, and the total labelled as computed from the sources."""
    L = _PUPIL_TEXT[_language(question)]
    lines = [L["computed"]]
    if agg["sex"] is None:
        lines.append(L["both"])
    for sc in agg["scopes"]:
        sex = L["sex"][sc["sex"]]
        n = len(sc["contributors"])
        lines += ["", L["heading"].format(sex=sex)]
        for c in sc["contributors"]:
            value = (L["approx"] if c["approx"] else "") + str(c["value"])
            lines.append(f'- {c["label"]}: {value} {_cites(c["hits"])}')
        if sc["total"] is None:
            lines.append(L["no_exact"].format(sex=sex))
        else:
            add = " + ".join(str(c["value"]) for c in sc["contributors"])
            expr = str(sc["total"]) if n == 1 else f"{add} = {sc['total']}"
            if n == 1 and sc["approx_total"]:
                expr = L["approx"] + expr
            cites = _cites(sorted({i for c in sc["contributors"] for i in c["hits"]}))
            lines.append(L["sum_approx" if sc["approx_total"] else "sum"].format(expr=expr, cites=cites))
        if sc["unclear"] or sc["silent"]:
            lines.append(L["excluded"])
            for u in sc["unclear"]:
                values = ", ".join(f'{_obs_text(o, L)} {_cites(o["hits"])}' for o in u["obs"])
                lines.append(f'- {u["label"]}: {values} — {L["reasons"][u["reason"]]}')
            if sc["silent"]:
                items = "; ".join(f'{s["label"]} {_cites([s["hit"]])}' for s in sc["silent"])
                lines.append(L["silent"].format(sex=sex, items=items))
    return "\n".join(lines)


def answer(idx, q, k=6, use_llm=True, rewrite="off"):
    hits, r = idx.search(q, k, rewrite if use_llm else "off")
    out = {"question": q, "route": r}
    # a count question is answered from the whole routed pool (all schools of the place), not from the top k passages
    if "pupils" in r["intents"] and is_pupil_count_question(q) and 0 < int(r.get("filtered_pool") or 0) <= AGG_SOURCES_MAX:
        pool = idx.pool(r)
        agg = aggregate_pupils(pool, q)
        if agg:
            text = render_pupil_totals(agg, q)
            fails = check_numbers(text, pool, q, extra=agg["extra"])
            cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 0 < int(n) <= len(pool)})
            out.update(sources=pool, answer=text, cited=cited, answer_source="aggregate", citation_ok=not fails,
                       number_check={"ok": not fails, "failures": fails})
            return out
    out["sources"] = hits
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
