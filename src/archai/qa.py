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
                  "year": int(d["year"]) if str(d.get("year") or "").isdigit() else None,
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


_CATALOGUE = {}


def questionnaire():
    """The Stapfer questionnaire as answered in the data: question number -> (German, French) text."""
    if "q" not in _CATALOGUE:
        import pandas as pd
        a = pd.read_parquet(OUT / "stapfer_answers.parquet", columns=["question_no", "question_de", "question_fr"])
        a = a.dropna(subset=["question_no"]).drop_duplicates("question_no")
        _CATALOGUE["q"] = {r.question_no: (str(r.question_de or ""), str(r.question_fr or "")) for r in a.itertuples()
                           if not str(r.question_no).startswith("zusatz")}
    return _CATALOGUE["q"]


DYNAMIC = """You turn a user's question into a search request over Swiss archives. Choose the dataset first:
- "stapfer": schools in 1799 (pupils, teachers, pay, books, schoolhouse, lessons, villages). Example: "Wie viele Kinder in Hörnli?"
- "consultation": Swiss federal consultations (Vernehmlassungen) 1960-1991 on laws, ordinances, treaties, e.g. health
  insurance, data protection, taxes, the army. Example: "Which consultation in 1984 dealt with data protection?"
{other_line}- "none": ONLY for questions unrelated to both (sport results, weather, recipes, people of today, general knowledge).
Reply with JSON only:
{{"dataset": "stapfer" | "consultation"{other_opt} | "none",
 "sections": ["for stapfer: question numbers from the questionnaire below that answer the question, most relevant first, max 4"],
 "places": ["villages or towns the question names, as written, max 5"],
 "years": [from, to] copied from the question, or null,
 "keywords": ["ALWAYS 3-6 search words, mostly German (the sources are mostly German) plus French, as the source would phrase them"]}}
Stapfer questionnaire (number: German / French):
{catalogue}{feedback}
Question: {q}"""


def _valid_sections(raw, catalogue):
    out = []
    for x in raw if isinstance(raw, list) else []:
        x = str(x).strip().rstrip(".")
        if x in catalogue or any(k.startswith(x + ".") for k in catalogue):  # "III.12" covers III.12.a/b
            out.append(x)
    return list(dict.fromkeys(out))[:4]


def _valid_places(raw, gazetteer):
    import difflib
    ok, unknown = [], []
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, str) or not x.strip():
            continue
        a = _ascii(x.strip())
        hit = [a] if a in gazetteer else difflib.get_close_matches(a, gazetteer, n=1, cutoff=0.85)
        (ok if hit else unknown).append(hit[0] if hit else x.strip())
    return list(dict.fromkeys(ok))[:5], unknown


def _derive_intents(dataset, sections):
    """The fixed intents follow from the chosen sections, so the pupil total and the confidence score keep working."""
    if dataset == "consultation":
        return ["consultation"]
    if dataset == "other":
        return ["other_dataset"]
    out = []
    for k, (_, secs) in INTENTS.items():
        if secs and any(s.startswith(tuple(secs)) or any(x.startswith(s) for x in secs) for s in sections):
            out.append(k)
    return out


def dynamic_route(q, idx, rules):
    """Apertus picks dataset, questionnaire sections, places and years from the real questionnaire; code validates every
    pick. One feedback round when a place is unknown or nothing matches: Apertus is told why and revises.
    Returns (route, expanded query) or None when Apertus gives nothing usable."""
    import difflib
    pub = published()
    cat = idx.catalogue
    catalogue = "\n".join(f"  {k}: {de[:70]} / {fr[:50]}" for k, (de, fr) in cat.items())
    base = dict(other_opt=' | "other"' if pub else "", catalogue=catalogue,
                other_line=('- "other": ' + "; ".join(d["description"] for d in pub) + "\n") if pub else "")
    feedback, rounds = "", []
    for rnd in range(2):
        obj, meta = llm.chat_json(DYNAMIC.format(**base, feedback=feedback, q=q), schema={"sections": list, "keywords": list}, max_tokens=220)
        if not isinstance(obj, dict):
            rounds.append({"round": rnd, "failed": meta.get("errors")})
            break
        ds = obj.get("dataset") if obj.get("dataset") in ("stapfer", "consultation", "other", "none") else "stapfer"
        if ds == "other" and not pub:
            ds = "stapfer"
        if ds == "stapfer" and rules["intents"] == ["consultation"] and not (obj.get("places") or rules["places"]):
            ds = "consultation"  # "vernehmlass…", "Bundesgesetz": the rules' unambiguous consultation words win over a guess
        if ds == "none" and (rules["intents"] or rules["places"]):  # safety net: never decline what the rules can route
            ds = "consultation" if rules["intents"] == ["consultation"] else "stapfer"
            obj = dict(obj, sections=obj.get("sections") or [], places=obj.get("places") or rules["places"])
            rounds_note = "none_overridden_by_rules"
        else:
            rounds_note = None
        secs = _valid_sections(obj.get("sections"), cat) if ds == "stapfer" else []
        places, unknown = _valid_places(obj.get("places"), idx.gaz) if ds == "stapfer" else ([], [])
        yrs = obj.get("years")
        years = sorted(int(y) for y in yrs[:2]) if ds == "consultation" and isinstance(yrs, list) and len(yrs) >= 1 and all(str(y).isdigit() for y in yrs[:2]) else None
        if ds == "consultation" and not years:  # a year written in the question is read by code
            ys = sorted({int(y) for y in re.findall(r"\b(19[6-9]\d)\b", q) if 1960 <= int(y) <= 1991})
            years = [ys[0], ys[-1]] if ys else None
        if years and len(years) == 1:
            years = [years[0], years[0]]
        if years and not (1960 <= years[0] <= 1991 or 1960 <= years[-1] <= 1991):
            years = None
        kw = [k.strip() for k in obj.get("keywords", []) if isinstance(k, str) and k.strip()][:6]
        r = {"intents": _derive_intents(ds, secs), "places": places, "sections": secs, "dataset": ds, "years": years,
             "rule_intents": rules["intents"], "routed_by": "apertus-dynamic"}
        rounds.append({"round": rnd, "dataset": ds, "note": rounds_note, "sections": secs, "places": places, "unknown_places": unknown,
                       "years": years, "keywords": kw, "dropped_sections": [x for x in obj.get("sections") or [] if str(x).strip().rstrip(".") not in secs]})
        if ds == "none":  # only a hint: the answer step decides whether the archives answer it
            break
        problems = []
        for u in unknown:
            near = difflib.get_close_matches(_ascii(u), idx.gaz, n=3, cutoff=0.6)
            problems.append(f'the place "{u}" is not in the gazetteer' + (f"; closest names: {', '.join(near)}" if near else "; leave it out if no listed name fits"))
        if ds == "stapfer" and not secs:
            problems.append("you chose no question number; choose at least one from the questionnaire that answers the question")
        if ds in ("stapfer", "consultation") and not kw:
            problems.append("you gave no keywords; give 3-6 search words in German and French")
        if ds == "stapfer" and not problems and places and secs and not idx.chosen_only(r).any():
            have = idx.present_sections(places)
            problems.append(f"the sheets of {', '.join(places)} answer none of {secs}; they answer: {', '.join(have[:40])}")
        if not problems or rnd == 1:
            break
        feedback = "\nYour previous request had problems: " + " | ".join(problems) + ". Reply again with a corrected request."
    if not rounds or "failed" in rounds[-1]:  # also a first call that failed (timeout, no key): no route was built
        return None
    r["rewrite"] = {"rounds": rounds}
    last = rounds[-1]
    if last.get("dataset") == "none" or (last.get("dataset") == "stapfer" and not last.get("sections")):
        return None  # no verdict / no question chosen: the validated 9-intent rewrite routes this one instead
    return r, q + " " + " ".join(rounds[-1].get("keywords", []))


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
        self.year = np.array([p.get("year") or 0 for p in self.P])
        self.catalogue = questionnaire()

    def mask(self, r):
        if r.get("sections") is not None:  # dynamic route: sections chosen from the questionnaire itself
            return self._mask_dynamic(r)
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

    def _at(self, places):
        return np.array([bool(p["places"] & set(places)) for p in self.P])

    def _mask_dynamic(self, r):
        """Sections and places chosen by the dynamic rewrite. If nothing matches, widen step by step and say so:
        first the same place with any section, never a different place."""
        ds = r.get("dataset")
        if ds == "consultation":
            m = self.kind == "consultation"
            if r.get("years"):
                lo, hi = r["years"]
                ym = m & (self.year >= lo) & (self.year <= hi)
                if ym.any():
                    return ym
                r["fallback"] = f"no consultation dated {lo}-{hi}; searched all consultations"
            return m
        if ds == "other":
            return self.kind == "onboarded"
        if ds == "none":  # a hint, not a verdict: search everything and let the answer step decide
            return np.ones(len(self.P), bool)
        m = self.kind == "stapfer"
        free = np.array([q.startswith(FREE) for q in self.qno])  # sheets that answer in prose carry the content here
        secs = tuple(r["sections"])
        if not secs:
            return m & self._at(r["places"]) if r["places"] else m
        tiers = [("chosen", secs), ("related", self._related(secs, r["intents"]))]
        at = self._at(r["places"]) if r["places"] else np.ones(len(self.P), bool)
        for name, ss in tiers:
            mm = m & at & (np.array([q.startswith(ss) for q in self.qno]) | free)
            if mm.any():
                if name != "chosen":
                    r["fallback"] = "the sheets do not answer the chosen questions; searched the related questions"
                return mm
        r["fallback"] = "the sheets answer none of the related questions; searched the whole sheet"
        return m & at

    def _related(self, secs, intents):
        """Parent questions of the chosen sub-questions, plus the sections of the intents they belong to (with the pay
        fallback to the school-fund questions, as in the intent route)."""
        out = {".".join(x.split(".")[:2]) for x in secs} | set(secs)
        for k in intents:
            out |= set(INTENTS[k][1] or [])
        if "salary" in intents:
            out |= {"IV.13", "IV.14"}
        return tuple(sorted(out))

    def chosen_only(self, r):
        """Mask of exactly the chosen sections (no widening): decides whether the feedback round is needed."""
        at = self._at(r["places"]) if r["places"] else np.ones(len(self.P), bool)
        return (self.kind == "stapfer") & at & np.array([q.startswith(tuple(r["sections"])) for q in self.qno])

    def present_sections(self, places):
        """Questionnaire sections the sheets of these places actually answer (for the rewrite's feedback round)."""
        at = self._at(places)
        return sorted({q for q, a in zip(self.qno, at, strict=False) if a and q and not q.startswith("zusatz")})

    def search(self, q, k=6, rewrite="off"):
        """rewrite: "off" = rules only; "gaps" = Apertus fills what the rules missed (rules win);
        "apertus" = Apertus intents win, rules are the fallback. Places: exact gazetteer match from rules first."""
        r = route(q, self.gaz)
        qx = q
        if rewrite == "dynamic":
            dyn = dynamic_route(q, self, r)
            if dyn:
                r, qx = dyn
                rewrite = "done"
            else:  # Apertus unavailable or unusable: the validated single-shot rewrite, then the rules
                rewrite = "apertus"
        if rewrite in ("gaps", "apertus") and (rewrite == "apertus" or needs_rewrite(r)):
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
        if r.get("sections"):  # Apertus lists sections most relevant first: a small boost in that order
            for rank, sec in enumerate(r["sections"]):
                s = s + np.array([q == sec or q.startswith(sec + ".") for q in self.qno]) * 0.05 * (len(r["sections"]) - rank)
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


from .tools import word_numbers  # FR/DE number words, shared with extract_quantities


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

_FEMALE_SEX = re.compile(r"sch(?:ue|u)lerinn|m(?:ae|a)d(?:chen|gen|lein|li)|m(?:ae|a)gd(?:chen|lein|li)|meitli|filles?|t(?:oe|o)chter|ecolieres?|\bgirls?\b")
_MALE_SEX = re.compile(r"\bknab|\bkaben\b|\bbuben?\b|garcons?|sohne\b|ecoliers?|\bboys?\b")
_GENERIC_SEX = re.compile(r"sch(?:ue|u)ler|schulkind|\bkind(?:er)?\b|pupils?|\bchildren\b|\beleves?\b|\benfants?\b")
_COUNT_WORD = re.compile(r"wie\s*viele?|wieviele?|how\s*many|combien|total|\bsum(?:me)?\b|insgesamt|zusammen|(?:gesamt)?zahl|anzahl|\bnombre\b")
_PUPIL_WORD = re.compile(r"sch(?:ue|u)ler|schulkind|\bkind(?:er)?\b|\bknab|\bbuben?\b|\bm(?:ae|a)d|\bmeitli|\btochter|"
                         r"pupils?|\bstudents?\b|\bscholars?\b|\bchildren\b|\bkids?\b|\bboys?\b|\bgirls?\b|\beleves?\b|\becoliers?\b|\becolieres?\b|"
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
    out = {"question": q, "route": r}
    # a count, total, average or list over records ("how many schools / pupils in Bern?") is stated by no passage:
    # the data engine plans it with Apertus, reads and verifies each record, and computes it in code (archai.dataq)
    from . import dataq
    if dataq.is_data_question(q, r):
        res = dataq.answer(idx, q, r, use_llm)
        if res:
            out.update(res)
            return out
    # without Apertus: the regex aggregate over the whole routed pool (all schools of the place), not the top k
    if "pupils" in r["intents"] and is_pupil_count_question(q) and 0 < int(r.get("filtered_pool") or 0) <= AGG_SOURCES_MAX:
        pool = idx.pool(r)
        if hasattr(idx, "P"):  # the pupil questions only ("80 Knaben sollen kommen" in I.3 is not an attendance figure)
            pool = [h for h in pool if h["qno"].startswith(("III.12", FREE))] or pool
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
