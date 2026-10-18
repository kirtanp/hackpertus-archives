"""Dataset onboarding: any tabular open-data file -> unified AI-ready records (schema 1.1) with provenance.

    PYTHONPATH=src python -m archai.onboard FILE --id ID --source-url URL --licence LICENCE [--mask-pii] [--enrich N] [--publish]
    PYTHONPATH=src python -m archai.onboard --id ID --from transform      # re-run after editing mapping.json

Stages, outputs in data/onboard/<id>/ (each stage re-runnable; a manifest records hashes and timings):
 1 acquire    read a local file (CSV/TSV/JSON/JSONL/XLSX/Parquet; encoding + delimiter detected); hash it. No crawling:
              download from opendata.swiss yourself and pass the dataset URL and licence.
 2 profile    deterministic column profile: type, empty rate, distinct values, samples, detected roles
 3 map        Apertus proposes which columns are id / title / text / dates / places / persons / signature, from the
              profile only. The proposal is validated against the data (columns exist, dates parse, ids are unique);
              rejected parts fall back to the heuristic mapping. mapping.json is meant to be reviewed and edited.
 4 transform  rows -> records with the deterministic tools (dates, places, signatures, quantities); the original row
              is kept in source_row, so nothing is lost
 5 privacy    screen for e-mail, phone, AHV number, IBAN, birth dates < 110 years and dates inside the archival
              protection period; flag, or mask with --mask-pii
 6 enrich     optional: Apertus title/summary/topics on N records; entities not in the source text are rejected
 7 validate   export.validate; invalid records quarantined; report.json with coverage and every decision"""
import argparse, csv, datetime as dt, hashlib, io, json, os, re, sys, time
from pathlib import Path
import pandas as pd
from . import llm, tools as T
from .export import validate, SCHEMA_VERSION
from .stapfer_clean import lv03_to_wgs84, guess_lang

BASE = Path("data/onboard")
ROLES = ["id", "title", "text", "dates", "places", "persons", "organisations", "signature", "url"]
NOW_YEAR = dt.date.today().year


# ---------- 1 acquire ----------
def read_table(path):
    p = Path(path); raw = p.read_bytes(); ext = p.suffix.lower()
    meta = {"file": p.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(io.BytesIO(raw), dtype=str)
    elif ext == ".parquet":
        df = pd.read_parquet(io.BytesIO(raw)).astype(str)
    elif ext in (".json", ".jsonl", ".ndjson"):
        txt = raw.decode("utf-8-sig")
        df = pd.read_json(io.StringIO(txt), lines=ext != ".json" or txt.lstrip().startswith("{"), dtype=False)
        df = df.map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v).astype(str)
    else:
        for enc in ("utf-8-sig", "cp1252", "latin-1"):  # Swiss open data is often Windows-1252
            try:
                txt = raw.decode(enc); meta["encoding"] = enc; break
            except UnicodeDecodeError:
                continue
        try:
            sep = csv.Sniffer().sniff(txt[:20000], delimiters=",;\t|").delimiter
        except csv.Error:
            sep = ","
        meta["delimiter"] = sep
        df = pd.read_csv(io.StringIO(txt), sep=sep, dtype=str, keep_default_na=False)
    df = df.replace({"nan": "", "None": "", "NaT": ""}).fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    meta.update(rows=len(df), columns=len(df.columns))
    return df, meta


# ---------- 2 profile ----------
SIG_RX = re.compile(r"^[A-Z]\d[\w.\-]*#\d{4}/\d+#[\w.\-]+\*?$|BAR\s+B0")
YEAR_RX = re.compile(r"^(1[0-9]|20)\d\d$")


def _date_iso(v):
    v = (v or "").strip()
    if not v:
        return None
    if YEAR_RX.match(v):
        return v
    if re.match(r"^\d{4}-\d{2}-\d{2}", v):
        return v[:10]
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", v)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    # month names ("8 mars 1799", "16. Hornung 1799"); only with an explicit 4-digit year: two-digit years mean 17xx there
    d = [x for x in T.normalize_dates(v) if not x["year_inferred"] and re.search(r"\d{4}", x["surface"])] if len(v) < 60 else []
    return d[0]["iso"] if d else None


def profile(df):
    cols = []
    for c in df.columns:
        s = df[c].astype(str).str.strip(); nn = s[s != ""]
        sample = nn.head(300)
        num = pd.to_numeric(sample.str.replace("'", ""), errors="coerce")
        rates = {
            "date": float(sample.map(lambda v: _date_iso(v) is not None).mean()) if len(sample) else 0.0,
            "numeric": float(num.notna().mean()) if len(sample) else 0.0,
            "signature": float(sample.map(lambda v: bool(SIG_RX.search(v))).mean()) if len(sample) else 0.0,
            "url": float(sample.str.match(r"https?://").mean()) if len(sample) else 0.0,
        }
        roles = []
        if rates["date"] >= .6: roles.append("date")
        if rates["signature"] >= .5: roles.append("signature")
        if rates["url"] >= .8: roles.append("url")
        if len(nn) and nn.nunique() == len(nn) and nn.str.len().max() <= 64: roles.append("unique_id")
        if len(nn) and nn.str.len().mean() > 60: roles.append("long_text")
        if rates["numeric"] >= .9 and num.notna().any():
            lo, hi = num.min(), num.max()
            if 45.5 <= lo and hi <= 48.0: roles.append("wgs84_lat")
            elif 5.8 <= lo and hi <= 10.6 and not YEAR_RX.match(str(int(hi))): roles.append("wgs84_lon")
            elif 480000 <= lo and hi <= 850000: roles.append("lv03_east")
            elif 60000 <= lo and hi <= 310000: roles.append("lv03_north")
            elif 2480000 <= lo and hi <= 2850000: roles.append("lv95_east")
            elif 1060000 <= lo and hi <= 1310000: roles.append("lv95_north")
        if len(nn) and nn.nunique() <= 30: roles.append("category")
        cols.append({"name": c, "empty_rate": round(1 - len(nn) / max(len(s), 1), 3), "distinct": int(nn.nunique()),
                     "mean_length": round(float(nn.str.len().mean()), 1) if len(nn) else 0, "roles": roles,
                     "rates": {k: round(v, 3) for k, v in rates.items()},
                     "samples": [x[:80] for x in nn.drop_duplicates().head(3).tolist()]})
    return cols


# ---------- 3 map ----------
NAME_HINTS = {"title": r"titel|title|titre|bezeichnung|name$|objekt|subject", "text": r"text|beschreib|descr|inhalt|content|abstract|bemerk|remark|note",
              "places": r"\bort\b|place|lieu|gemeinde|municip|commune|kanton|canton|location|stadt",
              "persons": r"person|autor|author|verfasser|lehrer|teacher|name_|vorname|nachname",
              "organisations": r"department|departement|amt\b|office|organi|institution|behörde|verband", "url": r"url|link|href"}


def heuristic_mapping(prof):
    by = {c["name"]: c for c in prof}
    pick = lambda rx, filt=lambda c: True: [c["name"] for c in prof if re.search(rx, c["name"], re.I) and filt(c)]
    ids = [c["name"] for c in prof if "unique_id" in c["roles"] and "long_text" not in c["roles"]]
    m = {"id": next((c for c in ids if re.search(r"id|nr|num|key|code", c, re.I)), ids[0] if ids else None),
         "title": pick(NAME_HINTS["title"], lambda c: "date" not in c["roles"])[:1],
         "text": [c["name"] for c in prof if "long_text" in c["roles"]][:4] + pick(NAME_HINTS["text"])[:2],
         "dates": [c["name"] for c in prof if "date" in c["roles"]][:4],
         "places": pick(NAME_HINTS["places"], lambda c: "date" not in c["roles"])[:2],
         "persons": pick(NAME_HINTS["persons"], lambda c: "date" not in c["roles"])[:2],
         "organisations": pick(NAME_HINTS["organisations"], lambda c: "date" not in c["roles"])[:2],
         "signature": next((c["name"] for c in prof if "signature" in c["roles"]), None),
         "url": next((c["name"] for c in prof if "url" in c["roles"]), None),
         "coordinates": coord_columns(prof), "language": None, "kind": "record", "description": None}
    if not m["title"]:  # shortest informative text-like column
        cand = sorted((c for c in prof if c["mean_length"] > 8 and "date" not in c["roles"] and c["name"] != m["id"]), key=lambda c: c["mean_length"])
        m["title"] = [cand[0]["name"]] if cand else []
    m["text"] = list(dict.fromkeys(x for x in m["text"] if x in by and "url" not in by[x]["roles"]))
    return m


def coord_columns(prof):
    r = {role: c["name"] for c in prof for role in c["roles"] if role.startswith(("wgs84", "lv03", "lv95"))}
    for sys_, (e, n) in {"wgs84": ("wgs84_lon", "wgs84_lat"), "lv95": ("lv95_east", "lv95_north"), "lv03": ("lv03_east", "lv03_north")}.items():
        if e in r and n in r:
            return {"system": sys_, "east": r[e], "north": r[n]}
    return None


MAP_PROMPT = """You map the columns of a Swiss archival open-data table onto a common record schema.
Dataset: {desc}
Columns (name | empty rate | distinct | detected roles | samples):
{cols}

Reply with JSON only:
{{"id": "column with a unique identifier or null", "title": ["1-2 columns that name each record"],
 "text": ["columns with descriptive text, most informative first"], "dates": ["columns holding dates or years"],
 "places": ["columns naming places"], "persons": ["columns naming people"], "organisations": ["columns naming bodies"],
 "signature": "archive signature column or null", "url": "column with a link to the original or null",
 "language": "de|fr|it|en|mixed", "kind": "short_snake_case name for one record, e.g. federal_consultation",
 "description": "one sentence describing a record"}}
Use only column names from the list."""


def apertus_mapping(prof, desc):
    lines = "\n".join(f"- {c['name']} | {c['empty_rate']} | {c['distinct']} | {','.join(c['roles']) or '-'} | {' ¦ '.join(c['samples'])[:160]}" for c in prof)
    obj, meta = llm.chat_json(MAP_PROMPT.format(desc=desc or "unknown", cols=lines), schema={"title": list, "text": list, "dates": list}, max_tokens=500)
    return obj, meta


def validate_mapping(m, df, prof, source):
    """Check every proposed column against the data; drop what fails and say why."""
    cols, by, notes = set(df.columns), {c["name"]: c for c in prof}, []
    out = {"source": source}
    def keep(role, names, test=None, why=""):
        good = []
        for n in names if isinstance(names, list) else [names]:
            if not isinstance(n, str) or not n:
                continue
            if n not in cols:
                notes.append(f"{role}: '{n}' is not a column — dropped"); continue
            if test and not test(by[n]):
                notes.append(f"{role}: '{n}' dropped ({why})"); continue
            good.append(n)
        return good
    ids = keep("id", m.get("id"), lambda c: c["distinct"] >= 0.99 * (len(df) * (1 - c["empty_rate"])) and c["empty_rate"] < .01, "values not unique")
    out["id"] = ids[0] if ids else None
    phrase = lambda c: c["name"] != out["id"] and sum(" " in x for x in c["samples"]) >= max(1, len(c["samples"]) // 2)
    out["title"] = keep("title", m.get("title", []), phrase, "not readable text (an id or code)")[:2]
    out["text"] = keep("text", m.get("text", []))[:6]
    out["dates"] = keep("dates", m.get("dates", []), lambda c: c["rates"]["date"] >= .5, "fewer than half the values parse as dates")[:4]
    for role in ("places", "persons", "organisations"):
        out[role] = keep(role, m.get(role, []), lambda c: c["rates"]["date"] < .5, "looks like dates")[:3]
    sig = keep("signature", m.get("signature"), lambda c: c["rates"]["signature"] >= .3, "values do not look like archive signatures")
    out["signature"] = sig[0] if sig else None
    url = keep("url", m.get("url"), lambda c: c["rates"]["url"] >= .5, "values are not links")
    out["url"] = url[0] if url else None
    out["coordinates"] = m.get("coordinates") if isinstance(m.get("coordinates"), dict) else coord_columns(prof)
    for k in ("language", "kind", "description"):
        out[k] = m.get(k) if isinstance(m.get(k), str) else None
    out["kind"] = re.sub(r"[^a-z0-9_]", "_", (out["kind"] or "record").lower())[:40]
    out["validation_notes"] = notes
    return out


def build_mapping(df, prof, desc, use_llm=True):
    heur = validate_mapping(heuristic_mapping(prof), df, prof, "heuristic")
    if not use_llm:
        return heur, None
    obj, meta = apertus_mapping(prof, desc)
    if not obj:
        heur["apertus"] = {"status": "unavailable", "errors": meta.get("errors")}
        return heur, None
    ap = validate_mapping(obj, df, prof, "apertus")
    for role in ("title", "text"):  # a mapping without them is unusable: borrow from the heuristic, say so
        if not ap[role] and heur[role]:
            ap[role] = heur[role]; ap["validation_notes"].append(f"{role}: Apertus proposal empty after validation — heuristic used")
    if not ap["id"] and heur["id"]:
        ap["id"] = heur["id"]; ap["validation_notes"].append("id: heuristic used")
    ap["apertus"] = {"status": "ok", "seconds": meta.get("seconds"), "model": meta.get("model")}
    return ap, obj


# ---------- 4 transform ----------
_GAZ = None


def gazetteer():
    """Place names -> coordinates from the Stapfer places in data/out (1799 names and today's municipalities)."""
    global _GAZ
    if _GAZ is None:
        _GAZ = {}
        p = Path("data/out/stapfer_documents.jsonl")
        for l in open(p, encoding="utf-8") if p.exists() else []:
            for pl in json.loads(l)["places"]:
                if pl.get("lat"):
                    for nm in (pl["name_1799"], pl.get("municipality_today")):
                        if nm:
                            _GAZ.setdefault(T._ascii(nm), {"place_id": pl["place_id"], "municipality_today": pl.get("municipality_today"), "lat": pl["lat"], "lon": pl["lon"]})
    return _GAZ


def _coords(row, c):
    try:
        e, n = float(str(row[c["east"]]).replace("'", "")), float(str(row[c["north"]]).replace("'", ""))
    except (ValueError, KeyError):
        return None
    if c["system"] == "wgs84":
        return n, e
    if c["system"] == "lv95":
        e, n = e - 2000000, n - 1000000
    lat, lon = lv03_to_wgs84(e, n)
    return (lat, lon) if lat else None


def _split(v):
    v = (v or "").strip()
    if v.startswith("["):
        try:
            return [str(x) for x in json.loads(v.replace("'", '"'))]
        except json.JSONDecodeError:
            pass
    return [x.strip() for x in re.split(r"\s*[;|]\s*", v) if x.strip()]


def to_record(i, row, m, ds, src):
    g = lambda c: str(row.get(c, "") or "").strip()
    rid = f"{ds}:{g(m['id']) or i}" if m["id"] else f"{ds}:row{i + 1}"
    title = " – ".join(filter(None, (g(c) for c in m["title"]))) or rid
    text = "\n".join(f"{c}: {g(c)}" for c in m["text"] if g(c))
    dates = []
    for c in m["dates"]:
        iso = _date_iso(g(c))
        if iso:
            dates.append({"iso": iso, "surface": g(c)[:40], "source": f"column:{c}"})
    places = []
    for c in m["places"]:
        for nm in _split(g(c)):
            hit = gazetteer().get(T._ascii(nm))
            places.append({"place_id": hit["place_id"] if hit else None, "name": nm, "municipality_today": hit["municipality_today"] if hit else None,
                           "lat": hit["lat"] if hit else None, "lon": hit["lon"] if hit else None, "source": f"column:{c}" + ("+gazetteer" if hit else "")})
    if m.get("coordinates"):
        ll = _coords(row, m["coordinates"])
        if ll:
            places.append({"place_id": None, "name": None, "lat": round(ll[0], 6), "lon": round(ll[1], 6), "source": f"columns:{m['coordinates']['east']},{m['coordinates']['north']}"})
    persons = [{"person_id": None, "name": nm, "role": c, "source": f"column:{c}"} for c in m["persons"] for nm in _split(g(c))]
    orgs = [{"name": nm, "role": c, "source": f"column:{c}"} for c in m["organisations"] for nm in _split(g(c))]
    sig = g(m["signature"]) if m["signature"] else None
    units = [dict(T.parse_signature(s), signature=s) for s in _split(sig)] if sig else []
    body = title + "\n" + text
    return {
        "record_id": rid, "dataset": ds, "kind": m["kind"], "title": title,
        "language": m["language"] if m["language"] in ("de", "fr", "it", "en") else guess_lang(body),
        "text": text, "text_raw_available": True, "dates": dates, "places": places, "persons": persons, "organisations": orgs,
        "quantities": T.extract_quantities(text), "archive_units": units,
        "provenance": {"source_dataset": src["source_url"], "licence": src["licence"], "source_file": src["file"], "source_sha256": src["sha256"],
                       "row_nr": i + 1, "archive_signature": sig or None, "viewer_url": g(m["url"]) if m["url"] else None,
                       "pipeline": "archai.onboard", "mapping_sha256": src["mapping_sha256"]},
        "quality": {"fields_mapped": sum(bool(x) for x in (title, text, dates, places, persons, sig)), "dates_parsed": len(dates),
                    "dates_in_mapped_columns": sum(bool(g(c)) for c in m["dates"])},
        "enrichment": {"source": "deterministic"},
        "source_row": {k: v for k, v in row.items() if v not in ("", None)},
        "schema_version": SCHEMA_VERSION,
    }


# ---------- 5 privacy ----------
PII = {"email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
       "phone": re.compile(r"(?:\+41|0041|\b0)\s?\d{2}\s?\d{3}\s?\d{2}\s?\d{2}\b"),
       "ahv_number": re.compile(r"\b756[.\s]?\d{4}[.\s]?\d{4}[.\s]?\d{2}\b"),
       "iban": re.compile(r"\bCH\d{2}(?:\s?[0-9A-Z]{4}){4}\s?[0-9A-Z]\b")}
BIRTH = re.compile(r"geburt|birth|naissance|nascita|geb\.?$|jahrgang", re.I)


def privacy(rec, m, mask):
    flags = {}
    def scan(t):
        for k, rx in PII.items():
            n = len(rx.findall(t or ""))
            if n:
                flags[k] = flags.get(k, 0) + n
        return t
    for t in [rec["title"], rec["text"]] + [str(v) for v in rec["source_row"].values()]:
        scan(t)
    for d in rec["dates"]:
        col = d["source"].split(":", 1)[-1]; y = int(d["iso"][:4])
        if BIRTH.search(col) and y > NOW_YEAR - 110:
            flags["birth_date_possibly_living"] = flags.get("birth_date_possibly_living", 0) + 1
        if y > NOW_YEAR - 30:
            flags["within_30_year_protection_period"] = 1  # BGA Art. 9: check access status before publishing
    if mask and any(k in flags for k in PII):
        def red(t):
            for k, rx in PII.items():
                t = rx.sub(f"[REDACTED:{k}]", t)
            return t
        rec["title"], rec["text"] = red(rec["title"]), red(rec["text"])
        rec["source_row"] = {k: red(str(v)) for k, v in rec["source_row"].items()}
        rec["quality"]["pii_masked"] = True
    rec["quality"]["privacy_flags"] = flags
    return rec


# ---------- 6 enrich ----------
ENRICH = """Describe this archival record. Use only facts present in the input. Reply with JSON only:
{{"title": "max 12 words, in the record's language", "summary_en": "max 40 words", "topics": ["max 5 short English tags"],
 "persons": ["names exactly as written"], "places": ["place names exactly as written"]}}
Record:
{text}"""


def enrich(rec):
    body = (rec["title"] + "\n" + rec["text"])[:1500]
    obj, meta = llm.chat_json(ENRICH.format(text=body), schema={"title": str, "summary_en": str, "topics": list}, max_tokens=300)
    if not obj:
        return rec, False
    ents = [x for x in (obj.get("persons") or []) + (obj.get("places") or []) if isinstance(x, str)]
    checked = T.verify_in_source(ents, body)
    rec["enrichment"] = {"source": "apertus:onboard_enrich", "model": meta.get("model"), "prompt_version": "onboard-v1",
                         "title_generated": obj.get("title"), "summary_en": obj.get("summary_en"), "topics": obj.get("topics", [])[:5],
                         "entities_grounded": [c["entity"] for c in checked if c["grounded"]],
                         "rejected_ungrounded": [c["entity"] for c in checked if not c["grounded"]]}
    return rec, True


# ---------- run ----------
def _sha(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def run(path=None, ds=None, source_url=None, licence=None, desc=None, mask=False, n_enrich=0, publish=False, start="acquire", use_llm=True):
    out = BASE / ds; out.mkdir(parents=True, exist_ok=True)
    mf = out / "manifest.json"
    man = json.loads(mf.read_text()) if mf.exists() and start != "acquire" else {"dataset": ds, "stages": []}
    t0 = time.time()
    def stage(name, **kw):
        man["stages"].append({"stage": name, "seconds": round(time.time() - t0, 2), **kw}); mf.write_text(json.dumps(man, indent=1, ensure_ascii=False))
    if start == "acquire":
        if not (path and source_url and licence):
            sys.exit("acquire needs FILE, --source-url and --licence (check the licence on opendata.swiss)")
        df, src = read_table(path)
        rp = Path(path).resolve()  # stored relative to the repo when inside it, so runs are portable
        src.update(path=os.path.relpath(rp) if rp.is_relative_to(Path.cwd()) else str(rp), source_url=source_url, licence=licence, description=desc)
        (out / "source.json").write_text(json.dumps(src, indent=1, ensure_ascii=False)); stage("acquire", rows=src["rows"], sha256=src["sha256"])
        prof = profile(df); (out / "profile.json").write_text(json.dumps(prof, indent=1, ensure_ascii=False)); stage("profile", columns=len(prof))
        m, proposal = build_mapping(df, prof, desc, use_llm)
        if proposal:
            (out / "mapping_proposal_apertus.json").write_text(json.dumps(proposal, indent=1, ensure_ascii=False))
        (out / "mapping.json").write_text(json.dumps(m, indent=1, ensure_ascii=False)); stage("map", source=m["source"], notes=len(m["validation_notes"]))
    else:  # re-run from the (possibly hand-edited) mapping
        src = json.loads((out / "source.json").read_text()); df, _ = read_table(src["path"])
        m = json.loads((out / "mapping.json").read_text())
        if src["sha256"] != hashlib.sha256(Path(src["path"]).read_bytes()).hexdigest():
            sys.exit("source file changed since acquire — re-run from acquire")
    src["mapping_sha256"] = _sha(m)
    recs = [to_record(i, row, m, ds, src) for i, row in enumerate(df.to_dict("records"))]
    stage("transform", records=len(recs))
    recs = [privacy(r, m, mask) for r in recs]; stage("privacy", masked=mask)
    enriched = 0
    if n_enrich and use_llm:
        for r in recs[:n_enrich]:
            _, ok = enrich(r); enriched += ok
        stage("enrich", requested=n_enrich, enriched=enriched)
    good, bad = [], []
    for r in recs:
        errs = validate(r)
        (bad if errs else good).append(dict(r, _errors=errs) if errs else r)
    for name, rows in (("records.jsonl", good), ("records_invalid.jsonl", bad)):
        with open(out / name, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    cov = lambda f: round(sum(bool(f(r)) for r in good) / max(len(good), 1), 3)
    flags = {}
    for r in good:
        for k, v in r["quality"]["privacy_flags"].items():
            flags[k] = flags.get(k, 0) + 1
    report = {"dataset": ds, "scope": f"{src['file']} ({src['rows']} rows, sha256 {src['sha256'][:12]}…)", "source_url": src["source_url"], "licence": src["licence"],
              "records": len(good), "schema_invalid": len(bad), "mapping_source": m["source"], "mapping_notes": m["validation_notes"],
              "apertus_mapping": m.get("apertus"), "coverage": {"title": cov(lambda r: r["title"] != r["record_id"]), "text": cov(lambda r: r["text"]),
              "dates": cov(lambda r: r["dates"]), "places": cov(lambda r: r["places"]), "places_geocoded": cov(lambda r: any(p["lat"] for p in r["places"])),
              "persons": cov(lambda r: r["persons"]), "archive_signature": cov(lambda r: r["provenance"]["archive_signature"])},
              "records_with_privacy_flags": flags, "pii_masked": mask, "enriched": enriched, "published_to_qa": publish,
              "seconds": round(time.time() - t0, 1)}
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False)); stage("validate", invalid=len(bad))
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return report


def compare(ds, reference, prefix):
    """Field agreement of an onboarded dataset with records built by hand-written code (same rows, same ids)."""
    ref = {r["record_id"]: r for r in map(json.loads, open(reference, encoding="utf-8")) if r["record_id"].startswith(prefix)}
    got = [json.loads(l) for l in open(BASE / ds / "records.jsonl", encoding="utf-8")]
    pairs = [(g, ref.get(g["record_id"].split(":", 1)[1])) for g in got]
    pairs = [(g, r) for g, r in pairs if r]
    n = len(pairs)
    d_ref = [(g, r) for g, r in pairs if r["dates"]]
    o_ref = [(g, r) for g, r in pairs if r.get("organisations")]
    rep = {"scope": f"{ds} vs hand-built {prefix}* records in {reference}", "matched_records": n, "reference_records": len(ref),
           "title_exact": round(sum(" ".join(g["title"].split()) == " ".join(r["title"].split()) for g, r in pairs) / max(n, 1), 3),
           "title_exact_definition": "identical after collapsing whitespace",
           "reference_dates_found": round(sum({d["iso"] for d in r["dates"]} <= {d["iso"] for d in g["dates"]} for g, r in d_ref) / max(len(d_ref), 1), 3),
           "records_with_reference_dates": len(d_ref),
           "lead_organisation_found": round(sum(any(o["name"] == r["organisations"][0]["name"] for o in g["organisations"]) for g, r in o_ref) / max(len(o_ref), 1), 3),
           "records_with_reference_organisation": len(o_ref)}
    (BASE / ds / "comparison.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?"); ap.add_argument("--id", required=True)
    ap.add_argument("--source-url"); ap.add_argument("--licence"); ap.add_argument("--description")
    ap.add_argument("--mask-pii", action="store_true"); ap.add_argument("--enrich", type=int, default=0)
    ap.add_argument("--publish", action="store_true", help="make the records searchable in the Q&A")
    ap.add_argument("--from", dest="start", default="acquire", choices=["acquire", "transform"])
    ap.add_argument("--no-llm", action="store_true", help="heuristic mapping only")
    ap.add_argument("--compare", nargs=2, metavar=("REFERENCE_JSONL", "ID_PREFIX"), help="field agreement with hand-built records")
    a = ap.parse_args(argv)
    if a.compare and not a.file:
        return compare(a.id, *a.compare)
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{1,60}", a.id):
        sys.exit("--id: lowercase letters, digits and dashes")
    run(a.file, a.id, a.source_url, a.licence, a.description, a.mask_pii, a.enrich, a.publish, a.start, not a.no_llm)
    if a.compare:
        compare(a.id, *a.compare)


if __name__ == "__main__":
    main()
