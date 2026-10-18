"""Compare cleaned Stapfer answers with the University of Bern reference export (challenge fileshare,
`Stapfer Enquete/data/export_antworten(1).xlsx`). Join on TransID + question number; when a sheet has several
answers to one question, each Bern row is paired with its most similar answer. The Bern export renders emphasis
in UPPERCASE and keeps page markers / HTML entities, so both sides are normalised before comparison."""
import difflib, html, json, re, unicodedata
from pathlib import Path
import pandas as pd

REF = Path("data/raw/bern/export_antworten.xlsx")
OUT = Path("data/out")
DOC_LEVEL = ("Unterschrift", "Schlussbemerkungen des Schreibers", "Fliesstextantwort")


def norm(s):
    s = html.unescape(unicodedata.normalize("NFC", "" if s is None or s != s else str(s))).lower()
    s = re.sub(r"\|\|?\[seite \d+\]|\[…\]|\[\.\.\.\]|[—–-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def run():
    if not REF.exists():
        return None
    b = pd.read_excel(REF)
    a = pd.read_parquet(OUT / "stapfer_answers.parquet")
    a["TransID"] = a.doc_id.str.split(":").str[-1].astype(int)
    docs = {d["doc_id"]: d for d in map(json.loads, open(OUT / "stapfer_documents.jsonl", encoding="utf-8"))}
    doc_level = b.Frage.astype(str).str.startswith(DOC_LEVEL)
    q = b[~doc_level]
    groups = {k: [norm(t) for t in g.text_clean] for k, g in a.groupby(["TransID", "question_no"])}
    sims, worst = [], []
    for r in q.itertuples():
        cands = groups.get((r.TransID, r.Frage))
        if not cands:
            sims.append(None); continue
        ref = norm(r.Antwort)
        s = max(difflib.SequenceMatcher(None, ref, c, autojunk=False).ratio() for c in cands)
        sims.append(s)
        if s < 0.8:
            worst.append({"TransID": r.TransID, "Frage": r.Frage, "sim": round(s, 3), "bern": ref[:160], "ours": max(cands, key=lambda c: difflib.SequenceMatcher(None, ref, c).ratio())[:160]})
    s = pd.Series(sims, dtype=float)
    m = s.notna()
    sig = [docs.get(f"stapfer:transkription:{t}", {}).get("provenance", {}).get("archive_signature") == g for t, g in zip(b.TransID, b.Signatur, strict=True)]
    rep = {
        "scope": f"University of Bern reference export: {len(b)} rows over {b.TransID.nunique()} sheets "
                 f"({int((~doc_level).sum())} question answers, {int(doc_level.sum())} sheet-level fields not compared)",
        "sheets_found": round(b.TransID.isin(a.TransID).mean(), 4),
        "answers_matched": round(float(m.mean()), 4),
        "signature_exact_match": round(sum(sig) / len(sig), 4),
        "text_similarity_median": round(float(s[m].median()), 4),
        "text_exact_after_normalisation": round(float((s[m] == 1).mean()), 4),
        "text_similarity_ge_0.95": round(float((s[m] >= 0.95).mean()), 4),
        "text_similarity_lt_0.80": round(float((s[m] < 0.8).mean()), 4),
        "normalisation": "lowercase, HTML entities decoded, page markers / gap marks / dashes removed, whitespace collapsed",
    }
    json.dump(rep, open(OUT / "bern_eval.json", "w"), indent=1, ensure_ascii=False)
    pd.DataFrame(worst).to_csv(OUT / "bern_eval_low_similarity.csv", index=False)
    return rep


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, ensure_ascii=False))
