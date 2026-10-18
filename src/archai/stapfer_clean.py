"""Stage 2: turn Stapfer transcriptions into provenance-preserving AI-ready documents.

Markup semantics below are inferred from the data (no published transcription guide was
found in the open-data package); the raw text is always kept next to the cleaned text.
"""
import json, re
from pathlib import Path
import pandas as pd

S = Path("data/interim/stapfer"); OUT = Path("data/out")

PAIRED = {  # open, close, inferred meaning
    "heading": ("¥", "¥¥"), "latin_script": ("$", "$$"), "underlined": ("€", "€€"),
    "emphasis_alt": ("¦", "¦¦"), "deleted_or_corrected": ("£", "££"),
    "uncertain_reading": ("#", "##"), "note": ("*", "**"),  # #..## = probable reading (Editionsregeln §15)
}
# "°-°°" encodes a dash / filler line drawn by the writer (checked against the facsimiles), not an illegible gap
DASH = re.compile(r"°\s*-?\s*°°")
ANCHOR = re.compile(r"¢(/?)([\d\-]+)¢¢")
PAGE = re.compile(r"\|\|?\[\s*Seite\s*(\d+)\s*\]")
HYPHEN = re.compile(r"(\w)=\s*@+\s*(\w)")
VIEWER = "https://www.stapferenquete.ch/db/transkriptions/view/{}"  # /transkriptions/<id> alone is a soft 404


def balance_errors(text: str) -> int:
    """Count unbalanced paired markers (structural error proxy)."""
    errs = 0
    for o, c in PAIRED.values():
        n_close = text.count(c); n_open = text.count(o) - 2 * n_close
        errs += abs(n_open - n_close)
    errs += abs(text.count("{") - text.count("}"))
    return errs


def clean(raw: str):
    if raw is None:
        return None, [], {"uncertain": 0, "dashes": 0, "errors": 0}
    t = raw; ann = []
    errors = balance_errors(t)
    dashes = len(DASH.findall(t))
    t = DASH.sub(" – ", t)
    for m in ANCHOR.finditer(t):
        ann.append({"type": "table_anchor_" + ("end" if m.group(1) else "start"), "id": m.group(2)})
    t = ANCHOR.sub(" ", t)
    for m in PAGE.finditer(t):
        ann.append({"type": "page_break", "page": int(m.group(1))})
    t = PAGE.sub("\n\n", t)
    t = HYPHEN.sub(r"\1\2", t)
    for kind, (o, c) in sorted(PAIRED.items(), key=lambda kv: -len(kv[1][0])):
        pat = re.compile(re.escape(o) + r"(?!" + re.escape(o[0]) + r")(.*?)" + re.escape(c), re.S)
        for m in pat.finditer(t):
            if m.group(1).strip():
                ann.append({"type": kind, "text": m.group(1).strip()})
        t = pat.sub(lambda m: " " + m.group(1) + " ", t)
    for o, c in PAIRED.values():
        t = t.replace(c, " ").replace(o, " ")
    t = t.replace("{", "").replace("}", "").replace("|", "")
    t = t.replace("@@", "\n\n").replace("@", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    uncertain = sum(a["type"] == "uncertain_reading" for a in ann)
    return t, ann, {"uncertain": uncertain, "dashes": dashes, "errors": errors}


def assign_pages(answers):
    """page_start/page_end per answer from the transcription page markers ([Seite N]), walking each sheet in questionnaire order."""
    answers = answers.sort_values(["doc_id", "question_no"], kind="stable")
    start, end, cur, doc = [], [], 1, None
    for d, raw in zip(answers.doc_id, answers.text_raw, strict=True):
        if d != doc:
            cur, doc = 1, d
        raw = raw if isinstance(raw, str) else ""
        ps = [int(x) for x in PAGE.findall(raw)]
        start.append(ps[0] if ps and PAGE.match(raw.lstrip()) else cur)
        cur = ps[-1] if ps else cur
        end.append(cur)
    return answers.assign(page_start=start, page_end=end)


def doc_quality(a, head_q):
    """Per-sheet counts. The same answer text is often stored under several sub-questions: count each text once."""
    u = a.drop_duplicates("text_raw")
    return {"uncertain_readings": int(u.uncertain_readings.sum()) + head_q["uncertain"],
            "dash_marks": int(u.dash_marks.sum()) + head_q["dashes"],
            "markup_errors": int(a.markup_errors.sum()) + head_q["errors"],
            "pages": int(a.page_end.max()) if len(a) else 0}


def reclean():
    """Re-derive all cleaned fields from text_raw already in data/out (no raw dumps needed); keeps every other field."""
    a = pd.read_parquet(OUT / "stapfer_answers.parquet")
    res = [clean(t if isinstance(t, str) else None) for t in a.text_raw]
    a = a.assign(text_clean=[r[0] for r in res], annotations=[r[1] for r in res],
                 uncertain_readings=[r[2]["uncertain"] for r in res], dash_marks=[r[2]["dashes"] for r in res],
                 markup_errors=[r[2]["errors"] for r in res]).drop(columns=["illegible_gaps"], errors="ignore")
    a = assign_pages(a)
    by = dict(tuple(a.groupby("doc_id", sort=False)))
    docs = [json.loads(l) for l in open(OUT / "stapfer_documents.jsonl", encoding="utf-8")]
    for d in docs:
        g = by.get(d["doc_id"], a.iloc[:0])
        head, _, hq = clean(d["letterhead_raw"])
        d["letterhead_clean"] = head
        d["provenance"]["viewer_url"] = VIEWER.format(d["doc_id"].rsplit(":", 1)[-1])
        d["text_clean"] = "\n\n".join(f"[{x.question_no}] {x.text_clean}" for x in g.itertuples() if x.text_clean)
        d.pop("illegible_gaps", None); d.update(doc_quality(g, hq))
        d["language"] = guess_lang(d["text_clean"])
    a.to_parquet(OUT / "stapfer_answers.parquet", index=False)
    write_jsonl(OUT / "stapfer_documents.jsonl", docs)
    pd.json_normalize(docs, sep=".").drop(columns=["places", "schools", "teachers"]).to_csv(OUT / "stapfer_documents.csv", index=False)
    return len(docs), len(a)


def lv03_to_wgs84(e, n):
    """swisstopo approximate formula (accuracy ~1 m)."""
    try:
        y = (float(e) - 600000) / 1e6; x = (float(n) - 200000) / 1e6
    except (TypeError, ValueError):
        return None, None
    if not (0.4 < y + 0.6 < 0.9):
        return None, None
    lon = 2.6779094 + 4.728982 * y + 0.791484 * y * x + 0.1306 * y * x * x - 0.0436 * y ** 3
    lat = 16.9023892 + 3.238272 * x - 0.270978 * y * y - 0.002528 * x * x - 0.0447 * y * y * x - 0.0140 * x ** 3
    return round(lat * 100 / 36, 6), round(lon * 100 / 36, 6)


def load(t):
    return pd.read_parquet(S / f"{t}.parquet")


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    tr, an, fr = load("t_transkription"), load("t_antwort"), load("t_frage")
    sch, ort = load("t_schule"), load("t_ort")
    lehrer, rls = load("t_lehrer"), load("r_lehrer_schule")
    fr = fr.set_index("id_frage")
    ort = ort.set_index("id_ort")

    # answers -> cleaned, one row per answer
    recs = []
    for r in an.itertuples(index=False):
        clean_t, ann, q = clean(r.antwort)
        f = fr.loc[r.f_id_frage] if r.f_id_frage in fr.index else None
        recs.append({
            "answer_id": f"stapfer:antwort:{r.id_antwort}", "doc_id": f"stapfer:transkription:{r.f_id_transkription}",
            "question_no": None if f is None else f.frage_nummer, "question_de": None if f is None else f.frage,
            "question_fr": None if f is None else f.frage_f,
            "text_raw": r.antwort, "text_clean": clean_t, "annotations": ann,
            "uncertain_readings": q["uncertain"], "dash_marks": q["dashes"], "markup_errors": q["errors"],
            "margin_note": r.antwort_randnotiz, "editor_comment": r.antwort_kommentar_oeffentlich,
        })
    answers = assign_pages(pd.DataFrame(recs))

    # places per transcription via schools
    places = {}
    for s in sch.itertuples(index=False):
        if s.f_id_ort is None or s.f_id_ort not in ort.index:
            continue
        o = ort.loc[s.f_id_ort]
        lat, lon = lv03_to_wgs84(o.ort_breite, o.ort_laenge)
        places.setdefault(s.f_id_transkription, {})[s.f_id_ort] = {
            "place_id": f"stapfer:ort:{s.f_id_ort}", "name_1799": o.ort_name, "municipality_today": o.ort_gemeinde_aktuell,
            "district_today": o.ort_amt_aktuell, "lv03_e": o.ort_breite, "lv03_n": o.ort_laenge, "lat": lat, "lon": lon,
            "confession": o.ort_konfession}
    schools = sch.groupby("f_id_transkription").apply(
        lambda g: [{"school_id": f"stapfer:schule:{x.id_schule}", "name": x.schule_name, "type": x.schule_schultypus,
                    "confession": x.schule_konfession} for x in g.itertuples()]).to_dict()
    lt = rls.merge(lehrer, left_on=rls.columns[rls.columns.str.contains("lehrer")][0], right_on="id_lehrer") \
        .merge(sch[["id_schule", "f_id_transkription"]], left_on=rls.columns[rls.columns.str.contains("schule")][0], right_on="id_schule")
    teachers = lt.groupby("f_id_transkription").apply(
        lambda g: [{"teacher_id": f"stapfer:lehrer:{x.id_lehrer}", "name": " ".join(filter(None, [x.lehrer_vorname, x.lehrer_name])),
                    "gender": x.lehrer_geschlecht, "age": x.lehrer_alter, "origin": x.lehrer_herkunft}
                   for x in g.drop_duplicates("id_lehrer").itertuples()]).to_dict()

    docs = []
    for t in tr.itertuples(index=False):
        a = answers[answers.doc_id == f"stapfer:transkription:{t.id_transkription}"]
        head, hann, hq = clean(t.transkription_briefkopf)
        body = "\n\n".join(f"[{x.question_no}] {x.text_clean}" for x in a.itertuples() if x.text_clean)
        docs.append({
            "doc_id": f"stapfer:transkription:{t.id_transkription}", "dataset": "stapfer-enquete-1799",
            "provenance": {
                "archive_signature": t.transkription_quelle_signatur, "repository": t.transkription_quelle_standort,
                "is_original": t.transkription_quelle_original == "1", "facsimile_file": t.transkription_faksimile_quelle,
                "viewer_url": VIEWER.format(t.id_transkription),
                "source_dataset": "https://opendata.swiss/de/dataset/stapfer-enquete-schule-in-der-schweiz-1799",
                "source_tables": ["t_transkription", "t_antwort", "t_frage", "t_schule", "t_ort", "t_lehrer"],
            },
            "author": " ".join(filter(None, [t.transkription_verfasser_vorname, t.transkription_verfasser_name])) or None,
            "written_by_teacher": t.transkription_lehrer_verfasst == "1",
            "date_written": t.transkription_datum_schreiben if str(t.transkription_datum_schreiben or "")[:4] in ("1798", "1799", "1800") else None,
            "transcription_date": t.transkription_datum,
            "letterhead_raw": t.transkription_briefkopf, "letterhead_clean": head,
            "places": list(places.get(t.id_transkription, {}).values()),
            "schools": schools.get(t.id_transkription, []), "teachers": teachers.get(t.id_transkription, []),
            "n_answers": len(a), "text_clean": body,
            "language": None, "handwritten": True,
            **doc_quality(a, hq),
            "editor_comment_public": t.transkription_kommentar_oeffentlich,
        })
    for d in docs:
        d["language"] = guess_lang(d["text_clean"])
    answers.to_parquet(OUT / "stapfer_answers.parquet", index=False)
    write_jsonl(OUT / "stapfer_documents.jsonl", docs)
    pd.json_normalize(docs, sep=".").drop(columns=["places", "schools", "teachers"]).to_csv(OUT / "stapfer_documents.csv", index=False)
    return len(docs), len(answers)


FR = set("le la les des une est et du qui que pour dans école".split())
DE = set("der die das und ist ein eine den dem von zu schule nicht".split())
IT = set("il della di che per scuola sono".split())


def guess_lang(t):
    w = re.findall(r"[a-zäöüéèàç]+", (t or "").lower())
    sc = {"de": sum(x in DE for x in w), "fr": sum(x in FR for x in w), "it": sum(x in IT for x in w)}
    return max(sc, key=sc.get) if max(sc.values()) else None


def write_jsonl(p, rows):
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


if __name__ == "__main__":
    import sys
    print(reclean() if "--reclean" in sys.argv else run())
