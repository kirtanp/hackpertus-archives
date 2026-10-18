"""Stage 3: normalise Federal Consultations and link their archive references to OLR units of description."""
import glob, json, re
from pathlib import Path
import pandas as pd

I = Path("data/interim"); OUT = Path("data/out")
MARK = {"[$]": "possible_location", "[§]": "no_consultation_documents", "[+]": "main_dossier"}
SIG = re.compile(r"^([A-Z]\d[\w.\-]*#[^,\s]+)")
AZ = re.compile(r"Az\.\s*([^,]*)")
YEARS = re.compile(r"(\d{4})\s*-\s*(\d{4})\s*(?:\[|$)|,\s*(\d{4})\s*(?:\[|$)")
OLR_URL = "https://www.recherche.bar.admin.ch/recherche/#/de/archiv/einheit/{}"


def parse_date(s):
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s.strip())
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else None


def parse_refs(block):
    refs = []
    for line in (block or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = SIG.match(line)
        flags = [v for k, v in MARK.items() if k in line]
        y = YEARS.search(line)
        refs.append({"raw": line, "signature": m.group(1) if m else None,
                     "aktenzeichen": (AZ.search(line).group(1).strip() or None) if AZ.search(line) else None,
                     "flags": flags,
                     "years": [int(g) for g in (y.groups() if y else []) if g]})
    return refs


def olr_index(sigs):
    keep = []
    for f in sorted(glob.glob(str(I / "olr/tbs_vrzng_enht.*"))):
        v = pd.read_parquet(f)
        keep.append(v[v.SGNTR_CD.isin(sigs)])
    v = pd.concat(keep)
    et = pd.read_parquet(glob.glob(str(I / "olr/tbs_entrg_typ.*"))[0])
    v = v.merge(et[["ENTRG_TYP_ID", "ENTRG_TYP_NM"]], left_on="VRZNG_ENHT_ENTRG_TYP_ID", right_on="ENTRG_TYP_ID", how="left")
    return v.drop_duplicates("SGNTR_CD").set_index("SGNTR_CD")


def hierarchy_titles(ids):
    """Resolve the archive-plan path (fonds -> series -> dossier) for each unit via HRCH_PFAD."""
    hr = pd.concat(pd.read_parquet(f, columns=["GSFT_OBJ_2_ID", "HRCH_PFAD"]) for f in sorted(glob.glob(str(I / "olr/tbs_gsft_obj_bzhng_hrch.*"))))
    hr = hr[hr.GSFT_OBJ_2_ID.isin(ids)].drop_duplicates("GSFT_OBJ_2_ID")
    paths = {r.GSFT_OBJ_2_ID: [str(int(r.HRCH_PFAD[i:i + 10])) for i in range(0, len(r.HRCH_PFAD), 10)] for r in hr.itertuples()}
    need = {p for v in paths.values() for p in v}
    titles = {}
    for f in sorted(glob.glob(str(I / "olr/tbs_vrzng_enht.*"))):
        v = pd.read_parquet(f, columns=["VRZNG_ENHT_ID", "SGNTR_CD", "VRZNG_ENHT_TITEL"])
        v = v[v.VRZNG_ENHT_ID.isin(need)]
        titles.update({r.VRZNG_ENHT_ID: (r.SGNTR_CD, r.VRZNG_ENHT_TITEL) for r in v.itertuples()})
    return {k: [{"id": p, "signature": titles[p][0], "title": titles[p][1]} for p in v if p in titles] for k, v in paths.items()}


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    c = pd.read_parquet(I / "consultations.parquet")
    c["refs"] = c.archivunterlagen.map(parse_refs)
    sigs = {r["signature"] for rs in c.refs for r in rs if r["signature"]}
    sigs |= {s.rstrip("*") for s in sigs}
    olr = olr_index(sigs)
    paths = hierarchy_titles(set(olr.VRZNG_ENHT_ID))
    recs, n_ref, n_link = [], 0, 0
    for r in c.itertuples(index=False):
        for ref in r.refs:
            if not ref["signature"]:
                continue
            n_ref += 1
            s = ref["signature"] if ref["signature"] in olr.index else ref["signature"].rstrip("*")
            if s in olr.index:
                o = olr.loc[s]; n_link += 1
                ref["olr"] = {"unit_id": o.VRZNG_ENHT_ID, "signature": s, "title": o.VRZNG_ENHT_TITEL,
                              "begin": o.BGN_DT, "end": o.END_DT, "level": o.ENTRG_TYP_NM,
                              "access": o["ZUGÄNGLICHKEIT_GEMÄSS_BGA"], "url": OLR_URL.format(o.VRZNG_ENHT_ID),
                              "hierarchy": paths.get(o.VRZNG_ENHT_ID, [])}
        recs.append({
            "doc_id": f"consultation:{r.nr}", "dataset": "federal-consultations-1960-1991",
            "title": r.titel, "type": r.typ, "department": r.departement, "department_abbr": r.departement_abk,
            "office": r.amt, "year": int(r.jahr) if str(r.jahr).isdigit() else None,
            "consultation_start": parse_date(r.frist_beginn), "consultation_end": parse_date(r.frist_ende),
            "consultation_start_raw": r.frist_beginn, "consultation_end_raw": r.frist_ende,
            "federal_gazette_ref": r.fundort_bbl, "federal_council_protocol_ref": r.fundort_br_protokoll,
            "addressees": [a.strip() for a in (r.adressaten or "").split("\n") if a.strip()],
            "document_types": [a.strip() for a in (r.dokumententypen or "").split("\n") if a.strip()],
            "archive_refs": r.refs,
            "provenance": {"source_dataset": "https://opendata.swiss/de/dataset/federal-consultations-1960-1991",
                           "source_file": "Metadatenbank-Vernehmlassungen-OGD-V1-3.csv", "row_nr": r.nr,
                           "linked_dataset": "https://opendata.swiss/de/dataset/swiss-archives-archive-database-of-the-swiss-federal-archives"},
        })
    with open(OUT / "consultations.jsonl", "w", encoding="utf-8") as f:
        for x in recs:
            f.write(json.dumps(x, ensure_ascii=False, default=str) + "\n")
    flat = pd.json_normalize(recs).drop(columns=["archive_refs"])
    flat["n_archive_refs"] = [len(x["archive_refs"]) for x in recs]
    flat["n_linked"] = [sum("olr" in a for a in x["archive_refs"]) for x in recs]
    flat.to_csv(OUT / "consultations.csv", index=False)
    stats = {"consultations": len(recs), "archive_refs_with_signature": n_ref, "linked_to_olr": n_link}
    json.dump(stats, open(OUT / "link_stats.json", "w"), indent=1)
    return stats


if __name__ == "__main__":
    print(run())
