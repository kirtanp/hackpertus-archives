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


def normalize_dates(text: str, default_year: int = 1799):
    """Find day-month(-year) expressions incl. 1799 month names (Hornung, Merz, ...) and return ISO dates."""
    out = []
    for m in DATE.finditer(text or ""):
        w = _ascii(m.group(2))
        mon = MONTHS.get(w) or MONTHS.get(re.sub(r"(ius|i|s)$", "", w))
        if not mon:
            continue
        day = int(m.group(1)); y = m.group(3)
        year = (1700 + int(y) if y and len(y) == 2 else int(y)) if y else default_year
        if 1 <= day <= 31:
            out.append({"surface": m.group(0).strip(), "iso": f"{year}-{mon:02d}-{day:02d}", "year_inferred": not y})
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


@lru_cache(1)
def _units():
    src = S.parent.parent / "interim/stapfer/l_waehrung.parquet"
    if src.exists():
        w = pd.read_parquet(src).waehrungstyp_name.dropna().tolist()
    else:  # interim tables not shipped: units the full run matched, snapshotted next to this module
        w = [l.strip() for l in open(Path(__file__).with_name("currencies_1799.txt"), encoding="utf-8") if l.strip() and not l.startswith("#")]
    w += ["fl", "kr", "xr", "bz", "lb", "ß", "livres", "sous", "deniers", "batz", "Gulden", "Kreuzer", "Pfund", "Schilling", "Franken", "Taler"]
    return sorted({x.strip(". ") for x in w if x}, key=len, reverse=True)


def extract_quantities(text: str):
    """Pupil counts (Knaben/Mädchen/garçons/filles) and money amounts with 1799 currency units."""
    t = text or ""; out = {"pupils": [], "money": []}
    for sex, pat in (("male", r"(?:Knaben|garçons|garcons)"), ("female", r"(?:Mädchen|Meitli|filles)")):
        for m in re.finditer(pat + r"\D{0,12}?(\d{1,3})|(\d{1,3})\s*" + pat, t, re.I):
            out["pupils"].append({"sex": sex, "count": int(m.group(1) or m.group(2)), "surface": m.group(0)})
    units = "|".join(re.escape(u) for u in _units())
    for m in re.finditer(rf"(?:\b({units})\.?\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*({units})\b\.?)", t, re.I):
        out["money"].append({"amount": float((m.group(2) or m.group(3)).replace(",", ".")), "unit": m.group(1) or m.group(4), "surface": m.group(0)})
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
