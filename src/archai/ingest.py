"""Stage 1: load the raw open-data files into tidy parquet tables (no content changes)."""
import re, sys, zipfile, zlib, struct
from pathlib import Path
import pandas as pd
from .mysqldump import parse_values, table_columns, iter_inserts

RAW = Path("data/raw"); INTERIM = Path("data/interim")


def stapfer():
    with zipfile.ZipFile(RAW / "stapferappdb_final.sql.zip") as z:
        name = [n for n in z.namelist() if n.endswith(".sql")][0]
        sql = z.read(name).decode("utf-8", "replace")
    cols = table_columns(sql)
    rows = {}
    for t, _, vals in iter_inserts(sql.split("\n")):
        rows.setdefault(t, []).extend(parse_values(vals))
    out = INTERIM / "stapfer"; out.mkdir(parents=True, exist_ok=True)
    for t, r in rows.items():
        df = pd.DataFrame(r, columns=cols[t]).drop(columns=["SSMA_TimeStamp"], errors="ignore")
        df.to_parquet(out / f"{t}.parquet", index=False)
    return {t: len(r) for t, r in rows.items()}


def consultations():
    f = RAW / "Metadatenbank-Vernehmlassungen-OGD-V1-3.csv"
    df = pd.read_csv(f, sep=";", encoding="cp1252", dtype=str)
    short = ["nr", "typ", "titel", "departement", "departement_abk", "amt", "jahr", "frist_beginn",
             "frist_ende", "fundort_bbl", "fundort_br_protokoll", "adressaten", "archivunterlagen", "dokumententypen"]
    df.columns = short[: len(df.columns)]
    df = df.dropna(how="all")
    df.to_parquet(INTERIM / "consultations.parquet", index=False)
    return len(df)


OLR_KEEP = {"tbs_vrzng_enht", "tbs_entrg_typ", "tbs_ablfr", "tbs_gsft_obj_bzhng_hrch"}


def olr(limit_tables=OLR_KEEP):
    """Stream-inflate the 3.6 GB dump inside 02-OLR.zip; keep only the tables we use."""
    out = INTERIM / "olr"; out.mkdir(parents=True, exist_ok=True)
    f = open(RAW / "02-OLR.zip", "rb")
    hdr = f.read(30); nl, el = struct.unpack("<HH", hdr[26:30]); f.read(nl + el)
    dc = zlib.decompressobj(-15); buf = b""; cols = {}; cur = None; rows = {}; counts = {}
    def flush(t):
        if rows.get(t):
            part = counts.get(t, 0)
            pd.DataFrame(rows[t], columns=cols[t]).to_parquet(out / f"{t}.{part:04d}.parquet", index=False)
            counts[t] = part + 1; rows[t] = []
    while True:
        c = f.read(1 << 22)
        if not c: break
        data = buf + dc.decompress(c)
        lines = data.split(b"\n"); buf = lines.pop()
        for l in lines:
            if l.startswith(b"CREATE TABLE"):
                cur = re.match(rb"CREATE TABLE `(\w+)`", l).group(1).decode(); cols[cur] = []; continue
            if cur and l.startswith(b"  `"):
                cols[cur].append(re.match(rb"  `([^`]+)`", l).group(1).decode()); continue
            if l[:6].lower() == b"insert":
                s = l.decode("utf-8", "replace")
                for t, explicit, vals in iter_inserts([s]):
                    if t not in limit_tables: continue
                    if explicit: cols[t] = explicit
                    rows.setdefault(t, []).extend(parse_values(vals))
                    if len(rows[t]) > 500_000: flush(t)
    for t in list(rows): flush(t)
    return counts


if __name__ == "__main__":
    steps = sys.argv[1:] or ["stapfer", "consultations", "olr"]
    for s in steps:
        print(s, globals()[s](), flush=True)
