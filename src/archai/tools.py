"""Deterministic tools that Apertus can call. Each returns JSON-serialisable output and never guesses."""
import glob, json, re, unicodedata
from functools import lru_cache
from pathlib import Path
import pandas as pd
from .stapfer_clean import clean as _clean, lv03_to_wgs84

S = Path("data/interim/stapfer"); I = Path("data/interim")

MONTHS = {  # 1799 German/French/Latinised month names -> month number
    "jenner": 1, "januar": 1, "janvier": 1, "hornung": 2, "februar": 2, "february": 2, "februari": 2, "fevrier": 2,
    "merz": 3, "marz": 3, "mars": 3, "april": 4, "avril": 4, "mai": 5, "mey": 5, "may": 5, "brachmonat": 6, "juni": 6,
    "juin": 6, "heumonat": 7, "juli": 7, "juillet": 7, "augst": 8, "august": 8, "aout": 8, "herbstmonat": 9,
    "september": 9, "septembre": 9, "weinmonat": 10, "oktober": 10, "october": 10, "octobre": 10,
    "wintermonat": 11, "november": 11, "novembre": 11, "christmonat": 12, "dezember": 12, "december": 12, "decembre": 12}
DATE = re.compile(r"\b(\d{1,2})\s*\.?\s*(?:ten|te|en|er|e)?\.?\s*([A-Za-zÀ-ÿ]+)\b[\s,]*((?:17|18)?\d{2})?\b")


def _ascii(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


ABBR_MONTH = {"7": 9, "8": 10, "9": 11, "10": 12, "x": 12}  # 7bre/7ber = September ... Xbre = December
ABBR = re.compile(r"\b(?:(\d{1,2})\s*\.?\s*(?:er|e|ten|te)?\.?\s*)?(7|8|9|10|X)\s*b(?:re|er)\b\.?[\s,]*((?:17|18)\d{2})?", re.I)
MONTH_YEAR = re.compile(r"\b([A-Za-zÀ-ÿ]{3,})\.?[\s,]+((?:17|18)\d{2})\b")


def normalize_dates(text: str, default_year: int = 1799):
    """Day-month(-year) expressions incl. 1799 month names (Hornung, Merz, ...) and the abbreviations 7bre/8ber/Xbre
    (Sept.-Dec.) -> ISO dates; month + year without a day ("Decembre 1798") -> "1798-12" with precision "month"."""
    out, used = [], []
    t = text or ""
    for m in DATE.finditer(t):
        w = _ascii(m.group(2))
        mon = MONTHS.get(w) or MONTHS.get(re.sub(r"(ius|i|s)$", "", w))
        if not mon:
            continue
        day = int(m.group(1)); y = m.group(3)
        year = (1700 + int(y) if y and len(y) == 2 else int(y)) if y else default_year
        if 1 <= day <= 31:
            out.append({"surface": m.group(0).strip(), "iso": f"{year}-{mon:02d}-{day:02d}", "year_inferred": not y}); used.append(m.span())
    for m in ABBR.finditer(t):
        if any(a < m.end() and m.start() < b for a, b in used) or not (m.group(1) or m.group(3)):
            continue
        mon = ABBR_MONTH[m.group(2).lower()]; y = m.group(3); day = int(m.group(1)) if m.group(1) else None
        year = int(y) if y else default_year
        if day and not 1 <= day <= 31:
            continue
        out.append({"surface": m.group(0).strip(), "iso": f"{year}-{mon:02d}" + (f"-{day:02d}" if day else ""), "year_inferred": not y,
                    **({} if day else {"precision": "month"})}); used.append(m.span())
    for m in MONTH_YEAR.finditer(t):
        mon = MONTHS.get(_ascii(m.group(1))) or MONTHS.get(re.sub(r"(ius|i|s)$", "", _ascii(m.group(1))))
        if not mon or any(a < m.end() and m.start() < b for a, b in used):
            continue
        out.append({"surface": m.group(0).strip(), "iso": f"{m.group(2)}-{mon:02d}", "year_inferred": False, "precision": "month"}); used.append(m.span())
    return out


@lru_cache(1)
def _gazetteer():
    if (S / "t_ort.parquet").exists():
        o = pd.read_parquet(S / "t_ort.parquet")
    else:  # interim tables not shipped: the same places, as exported with each sheet in data/out
        rows = {}
        for l in open(Path("data/out/stapfer_documents.jsonl"), encoding="utf-8"):
            for p in json.loads(l)["places"]:
                rows[p["place_id"]] = {"id_ort": p["place_id"].rsplit(":", 1)[-1], "ort_name": p["name_1799"], "ort_gemeinde_aktuell": p["municipality_today"],
                                       "ort_amt_aktuell": p.get("district_today"), "ort_breite": p.get("lv03_e"), "ort_laenge": p.get("lv03_n")}
        o = pd.DataFrame(list(rows.values()))
    o["key"] = o.ort_name.fillna("").map(_ascii)
    return o


def lookup_place(name: str, limit: int = 3):
    """Match a historical place name against the Stapfer gazetteer (exact, then prefix/fuzzy on ASCII form)."""
    import difflib
    g = _gazetteer(); k = _ascii(name or "").strip()
    hit = g[g.key == k]
    method = "exact"
    if hit.empty:
        cands = difflib.get_close_matches(k, g.key.unique().tolist(), n=limit, cutoff=0.8)
        hit = g[g.key.isin(cands)]; method = "fuzzy"
    res = []
    for r in hit.head(limit).itertuples():
        lat, lon = lv03_to_wgs84(r.ort_breite, r.ort_laenge)
        res.append({"place_id": f"stapfer:ort:{r.id_ort}", "name_1799": r.ort_name, "municipality_today": r.ort_gemeinde_aktuell,
                    "district_today": r.ort_amt_aktuell, "lat": lat, "lon": lon, "match": method})
    return res


SIG = re.compile(r"\b([A-Z]\d[\w.\-]*#\d{4}/\d+#[\w.\-]+\*?)")
STAPFER_SIG = re.compile(r"BAR\s+B0\s+1000/1483,\s*Nr\.\s*(\d+),\s*fol\.\s*([\dv\-]+)")


def parse_signature(sig: str):
    """Split a Swiss Federal Archives signature into fonds / accession / dossier (or Stapfer folio)."""
    m = STAPFER_SIG.search(sig or "")
    if m:
        return {"kind": "helvetic_archive", "fonds": "B0 1000/1483", "number": m.group(1), "folio": m.group(2)}
    parts = (sig or "").rstrip("*").split("#")
    if len(parts) >= 3:
        return {"kind": "federal_archive", "fonds": parts[0], "accession": parts[1], "dossier": "#".join(parts[2:])}
    return {"kind": "unknown", "raw": sig}


@lru_cache(1)
def _olr_sigs():
    return pd.concat(pd.read_parquet(f, columns=["VRZNG_ENHT_ID", "SGNTR_CD", "VRZNG_ENHT_TITEL", "BGN_DT", "END_DT"])
                     for f in sorted(glob.glob(str(I / "olr/tbs_vrzng_enht.*")))).set_index("SGNTR_CD")


def lookup_archive_unit(signature: str):
    """Exact lookup of a signature in the Archive Database (OLR)."""
    if not glob.glob(str(I / "olr/tbs_vrzng_enht.*")):  # the 3.6 GB dump is not shipped; linked units are in data/out
        return {"found": False, "signature": signature, "reason": "Archive Database not available locally (run the ingest stage)"}
    v = _olr_sigs()
    for s in (signature, (signature or "").rstrip("*"), (signature or "") + "*"):
        if s in v.index:
            r = v.loc[s]
            r = r.iloc[0] if isinstance(r, pd.DataFrame) else r
            return {"found": True, "signature": s, "unit_id": r.VRZNG_ENHT_ID, "title": r.VRZNG_ENHT_TITEL,
                    "begin": r.BGN_DT, "end": r.END_DT}
    return {"found": False, "signature": signature}


# number words in the sources (FR/DE), so "Quarante-un" in a source grounds "41" in an answer
_NW = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
       "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20, "vingts": 20, "trente": 30,
       "quarante": 40, "cinquante": 50, "soixante": 60, "septante": 70, "huitante": 80, "octante": 80, "nonante": 90,
       "cent": 100, "cents": 100, "mille": 1000,
       "ein": 1, "eins": 1, "eine": 1, "zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
       "zehn": 10, "elf": 11, "zwolf": 12, "dreizehn": 13, "vierzehn": 14, "funfzehn": 15, "sechzehn": 16, "siebzehn": 17,
       "achtzehn": 18, "neunzehn": 19, "zwanzig": 20, "dreissig": 30, "dreizig": 30, "vierzig": 40, "funfzig": 50, "sechzig": 60,
       "siebzig": 70, "achtzig": 80, "neunzig": 90, "hundert": 100, "tausend": 1000,
       "zwey": 2, "drey": 3, "zwo": 2, "quarente": 40}  # 1799 spellings
_NW_SURFACE = {**_NW, "fünf": 5, "zwölf": 12, "fünfzehn": 15, "fünfzig": 50, "dreißig": 30, "eilf": 11, "nonanté": 90, "septanté": 70, "huitanté": 80, "quarente": 40, "zwey": 2, "drey": 3, "zwo": 2}  # as written in the sources
_DE_COMPOUND = re.compile(r"(ein|zwei|drei|vier|funf|sechs|sieben|acht|neun)und(zwanzig|dreissig|vierzig|funfzig|sechzig|siebzig|achtzig|neunzig)")


def word_numbers(t):  # noqa: D401
    """Values of FR/DE number words and runs of them ("quarante six" 46, "deux cent soixante" 260, "einundvierzig" 41)."""
    t = _ascii(t)
    out = {str(_NW[m.group(1)] + _NW[m.group(2)]) for m in _DE_COMPOUND.finditer(t)}
    cur, total, run, prev = 0, 0, False, None
    for w in re.findall(r"[a-z]+", t) + [""]:
        v = _NW.get(w)
        if v is None:
            if w in ("et", "und") and run:  # "vingt et un", "vier und zwanzig"
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
        elif v == 20 and prev == "quatre":  # quatre-vingt(s) = 80, French only
            cur += 76
        else:
            cur += v
        prev = w
    return out




def number_run_value(phrase):
    """Value of one run of number words ("deux cent soixante" -> 260, "Dix huit" -> 18, "einundvierzig" -> 41)."""
    a = _ascii(phrase)
    m = _DE_COMPOUND.fullmatch(a.strip())
    if m:
        return _NW[m.group(1)] + _NW[m.group(2)]
    cur = total = 0; seen = False; prev = None
    for w in re.findall(r"[a-z]+", a):
        v = _NW.get(w)
        if v is None:
            if w in ("et", "und"):  # "vingt et un", "vier und zwanzig"
                continue
            break
        seen = True
        if v == 100:
            cur = (cur or 1) * 100
        elif v == 1000:
            total += (cur or 1) * 1000; cur = 0
        elif v == 20 and prev == "quatre":  # quatre-vingt(s) = 80 (French only: "vier und zwanzig" is 24)
            cur += 76
        else:
            cur += v
        prev = w
    return total + cur if seen else None


@lru_cache(1)
def _units():
    src = S.parent.parent / "interim/stapfer/l_waehrung.parquet"
    if src.exists():
        w = pd.read_parquet(src).waehrungstyp_name.dropna().tolist()
    else:  # interim tables not shipped: units the full run matched, snapshotted next to this module
        w = [l.strip() for l in open(Path(__file__).with_name("currencies_1799.txt"), encoding="utf-8") if l.strip() and not l.startswith("#")]
    w += ["fl", "kr", "xr", "bz", "lb", "ß", "livres", "sous", "deniers", "batz", "Gulden", "Kreuzer", "Pfund", "Schilling", "Franken", "Taler"]
    return sorted({x.strip(". ") for x in w if x}, key=len, reverse=True)


_WORDNUM = "|".join(sorted((re.escape(w) for w in _NW_SURFACE), key=len, reverse=True))
NUMW_RUN = rf"(?:(?:{_WORDNUM})(?:[\s-]+(?:(?:et|und)[\s-]+)?(?=(?:{_WORDNUM})\b))?)+|(?:ein|zwei|drei|vier|fünf|funf|sechs|sieben|acht|neun)und\w+zig"
NUM = rf"((?<![\d.,/])\d{{1,3}}(?![\d/]|\s*/)|\b(?!(?:un|une|ein|eine|eins)\b)(?:{NUMW_RUN})\b)"  # "un enfant", "ein Kind": articles, not counts
CURRENCY_WORDS = r"(?:florins?|francs?|livres|[ée]cus?|batz(?:en)?|kreuzer|creutzer|gulden|schilling|sols?|sous|kronen|bz|fl|kr|xr|rappen|piecettes?|deniers?)"  # not "Pfund" (weight), not "Livre" (book)
PUPIL = {"male": r"(?:Knaben|Buben|garçons|garcons)", "female": r"(?:Mädchen|Maedchen|Meitli|Töchter\w*|filles)",
         "unspecified": r"(?:Schulkinder|Kinder|Schüler\w*|Schueler|écoliers|ecoliers|[ée]c[ôo]liers|élèves|eleves|enfants?|Enfans)"}
BOTH = r"(?:(?:tant\s+)?garçons\s+(?:que|et)\s+filles|Knaben\s+und\s+Mädchen)"
NOT_A_COUNT = re.compile(r"^[\s.,]*(?:M\s*:|Min\b|Uhr|heures?|h\b|Stund\w*|Jahr\w*|ans?\b|Wochen|semaines?|Minut\w*|fl\b|kr\b|batz|L\.|"
                         r"Klass\w*|[Cc]lasses?|Abteilung\w*|mois|Monat\w*|jours?|Tag\w*|Häus\w*|maisons?|Höf\w*)", re.I)
COUNT_NOUN_BEFORE = re.compile(r"(?:H[äa]u?[üu]?(?:s|ß)\w*|Hof|Höf\w*|maisons?|feux|Hauß)[\s:.]{0,3}$", re.I)  # "Häüßer 23." = houses
NOT_PUPILS_AFTER = re.compile(r"^\s*(?:Kla(?:ss|ß)\w*|Cla(?:ss|ß)\w*|[Cc]lasses?|ans?\b|Jahr\w*|Abteilung\w*|Stund\w*|heures?|Uhr|Jahr\w*|ans?\b|mois|Monat\w*|jours?|Tag\w*|Wochen|semaines?)", re.I)
SECTION = re.compile(r"\[(III\.12[^\]]*)\]\s*(.*?)(?=\n\n\[|\Z)", re.S)


def _num(s):
    s = s.strip()
    return int(s) if s.isdigit() else number_run_value(s)


def extract_quantities(text: str):
    """Pupil counts and money amounts with 1799 currency units, in German and French: digits and number words
    ("Cinquante", "Dix huit Enfant", "deux cent Livres"). A number in an answer to the pupils question (III.12) counts
    as a pupil number even without a keyword ("[III.12.a] 34"); its sex is then "unspecified"."""
    raw = text or ""; out = {"pupils": [], "money": []}; taken = []
    t = re.sub(r"\[[IVX]+\.[^\]]*\]", lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + "]", raw)  # keep offsets
    family = [(m.start(), m.end()) for m in re.finditer(r"\[III\.11[^\]]*\].*?(?=\n\n\[|\Z)", raw, re.S)]
    taken += family  # spans already "used": no pupil count is read from the teacher's family answer
    def add(sex, m, g):
        v = _num(m.group(g)) if m.group(g) else None
        if v is not None and 0 < v < 1000 and not any(a < m.end() and m.start() < b for a, b in taken):
            out["pupils"].append({"sex": sex, "count": v, "surface": m.group(0).strip()}); taken.append(m.span())
    for sm in SECTION.finditer(raw):  # "65 en hiver tant garçons que filles" = all pupils, inside the pupils question only
        body = re.sub(r"\[[IVX]+\.[^\]]*\]", lambda x: " " * len(x.group(0)), sm.group(2))
        for m in re.finditer(NUM + r"\D{0,30}?" + BOTH, body, re.I):
            v = _num(m.group(1))
            span = (sm.start(2) + m.start(), sm.start(2) + m.end())
            if v and 0 < v < 1000 and not NOT_PUPILS_AFTER.match(body[m.end():]) and not any(a < span[1] and span[0] < b for a, b in taken):
                out["pupils"].append({"sex": "all", "count": v, "surface": m.group(0).strip()}); taken.append(span)
    # writers use both orders ("24. Knaben 16. Töchteren" / "Knaben 15 Mädchen 13"): each answer block is read in the
    # order of its first pupil mention, so "Knaben 16" is never taken from "24. Knaben 16. Töchteren"
    kw = "|".join(PUPIL.values())
    for bm in re.finditer(r"(?:^|\n\n)(.*?)(?=\n\n|\Z)", t, re.S):
        blk, off = bm.group(1), bm.start(1)
        lab = re.match(r"\[([IVX]+\.[^\]]*)\]", raw[off:off + 30])
        in_pupil_q = bool(lab) and lab.group(1).startswith(("III.12", "I.3"))  # the questions about pupil numbers
        if lab and not in_pupil_q:  # elsewhere only the original rule: digits next to Knaben/Mädchen/garçons/filles
            for sex in ("male", "female"):
                for m in re.finditer(r"(\d{1,3})\.?\s*" + PUPIL[sex] + "|" + PUPIL[sex] + r"\D{0,12}?(\d{1,3})", blk, re.I):
                    span = (off + m.start(), off + m.end())
                    if not any(a < span[1] and span[0] < b for a, b in taken):
                        out["pupils"].append({"sex": sex, "count": int(m.group(1) or m.group(2)), "surface": m.group(0).strip()}); taken.append(span)
            continue
        nf = next((x for x in re.finditer(NUM + r"\.?\s*(?:" + kw + ")", blk, re.I)
                   if not COUNT_NOUN_BEFORE.search(blk[max(0, x.start() - 14):x.start()])), None)  # "Haüser 7. Schulkinder 20": word-first
        wf = re.search("(?:" + kw + r")\D{0,12}?" + NUM, blk, re.I)
        num_first = bool(nf) and (not wf or nf.start() < wf.start())
        for sex, pat in PUPIL.items():
            rx = NUM + r"\.?\s*" + pat if num_first else pat + r"\D{0,12}?" + NUM
            for m in re.finditer(rx, blk, re.I):
                v = _num(m.group(1)) if m.group(1) else None
                span = (off + m.start(), off + m.end())
                if not num_first and NOT_PUPILS_AFTER.match(blk[m.end():]):  # "filles en trois Classes": not a pupil count
                    continue
                if num_first and COUNT_NOUN_BEFORE.search(blk[max(0, m.start() - 14):m.start()]):  # "Häuser 1. Schulkinder 5"
                    continue
                if v is not None and 0 < v < 1000 and not any(a < span[1] and span[0] < b for a, b in taken):
                    out["pupils"].append({"sex": sex, "count": v, "surface": m.group(0).strip()}); taken.append(span)
    for sm in SECTION.finditer(raw):  # the pupils question: bare numbers are pupil counts
        off = sm.start(2)
        body = re.sub(r"\[[IVX]+\.[^\]]*\]", lambda x: " " * len(x.group(0)), sm.group(2))
        for m in re.finditer(NUM, body, re.I):
            if NOT_A_COUNT.match(body[m.end():]) or (m.group(1).isdigit() and int(m.group(1)) >= 400):
                continue
            span = (off + m.start(), off + m.end())
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue
            v = _num(m.group(1))
            if v:
                out["pupils"].append({"sex": "unspecified", "count": v, "surface": m.group(0).strip(), "section": sm.group(1)}); taken.append(span)
    units = "|".join(re.escape(u) for u in _units())
    spans = []
    for m in re.finditer(rf"\b({units})\.?\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*({units})\b\.?", t, re.I):
        out["money"].append({"amount": float((m.group(2) or m.group(3)).replace(",", ".")), "unit": m.group(1) or m.group(4), "surface": m.group(0)})
        spans.append(m.span())
    for m in re.finditer(rf"\b({NUMW_RUN})\s+({CURRENCY_WORDS})\b\.?", t, re.I):  # number words, after the digit pass
        if any(a < m.end() and m.start() < b for a, b in spans) or re.match(r"\.?\s*\d", t[m.end():]):  # "ein fl. 700"
            continue
        val = number_run_value(m.group(1))
        if val:
            out["money"].append({"amount": float(val), "unit": m.group(2), "surface": m.group(0)})
    # "L. 8", "76 L." = livres (case-sensitive); "2 L. Hermen" is an abbreviated place name, not money
    for m in re.finditer(r"\bL\.\s*(\d+(?:[.,]\d+)?)|(?<![\d.,])(\d+(?:[.,]\d+)?)\s*L\.(?!\w)", t):
        if COUNT_NOUN_BEFORE.search(t[max(0, m.start() - 14):m.start()]):
            continue
        out["money"].append({"amount": float((m.group(1) or m.group(2)).replace(",", ".")), "unit": "L.", "surface": m.group(0)})
    return out


def verify_in_source(entities: list, text: str):
    """Deterministic grounding check: is every token (>2 chars) of each entity present in the source text?"""
    tt = set(re.sub(r"[^a-z ]", " ", _ascii(text or "")).split())
    res = []
    for e in entities or []:
        toks = [x for x in re.sub(r"[^a-z ]", " ", _ascii(str(e))).split() if len(x) > 2]
        res.append({"entity": e, "grounded": bool(toks) and all(x in tt for x in toks)})
    return res


def clean_markup(text: str):
    t, ann, q = _clean(text)
    return {"text": t, "annotations": ann[:30], "uncertain_readings": q["uncertain"], "dash_marks": q["dashes"], "markup_errors": q["errors"]}


TOOLS = {
    "normalize_dates": (normalize_dates, "Find dates (incl. 1799 month names like Hornung=Feb, Merz=Mar) -> ISO. args: {text}"),
    "lookup_place": (lookup_place, "Historical place name -> gazetteer id, modern municipality, WGS84. args: {name}"),
    "parse_signature": (parse_signature, "Split an archive signature into fonds/accession/dossier. args: {sig}"),
    "lookup_archive_unit": (lookup_archive_unit, "Look up a signature in the Swiss Federal Archives database. args: {signature}"),
    "extract_quantities": (extract_quantities, "Extract pupil counts and money amounts (fl, kr, Batzen, livres...). args: {text}"),
    "verify_in_source": (verify_in_source, "Check entities literally occur in the source text. args: {entities, text}"),
    "clean_markup": (clean_markup, "Strip transcription markup, keep annotations. args: {text}"),
}


def call(name, args):
    fn = TOOLS[name][0]
    return fn(**args)
