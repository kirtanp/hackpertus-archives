"""The corpus statistics aggregates as a DuckDB database file, so Apache Superset can chart them over SQL.

``stats.py`` answers one aggregate per HTTP request as a JSON envelope, which a BI tool cannot chart. This module
writes the same aggregates into a DuckDB *database file* that Apache Superset connects to as an ordinary SQL
datasource (driver ``duckdb-engine``, URI ``duckdb:////absolute/path/to/stapfer_stats.duckdb``).

The aggregate SQL is not written a second time: every aggregate table is created from
:func:`stats.aggregate_sql`, the very statements ``/api/stats`` runs, so the endpoint and the datasource cannot
drift apart. Three further relations are derived from ``stats.py``'s own definitions (``SECTION``, ``EMPTY``,
``TRIVIAL``, ``PLAIN``) and add the cuts a dashboard wants but the API does not aggregate per combination:
``section_page`` (section x page) and ``content_page`` (the empty/trivial/non-empty rule per page), plus the
``markup_rates`` view that turns the markup count row into rates. Nothing of ``stats.py``'s own semantics is
changed, and nothing here is imported by ``app.py``: no endpoint changes.

The corpus stays the parquet file committed in ``data/out/``; this database file is a *derived build artifact*.
It is written to a path the project gitignores (``data/out/*.duckdb``) and can be rebuilt at any time with::

    make statssql             # or: PYTHONPATH=src python -m archai.statssql

SQL tables written (all cover the whole corpus, 92,524 answers over 2,250 sheets; row counts are the ones the
committed parquet produces, and the ~41 MB file is mostly the materialised ``answers`` table):

| Table | Rows | What one row is |
|---|---|---|
| ``answers`` | 92,524 | one corpus answer, all columns of the parquet, so anything else can be sliced in SQL |
| ``sections`` | 19 | answers per questionnaire section (``III.11.b`` -> ``III.11``) |
| ``documents`` | 1 | answers counted and distinct sheets represented |
| ``answers_per_page`` | 16 | answers per page of the transcription |
| ``sheet_summary`` | 1 | sheets, min/median/max answers per sheet |
| ``answers_per_sheet`` | 146 | sheet-size histogram, answers -> sheets |
| ``pages_per_sheet`` | 16 | page-count histogram, pages -> sheets |
| ``markup`` | 1 | answers carrying markup, markup errors, uncertain readings, margin notes, editor comments |
| ``content`` | 19 | per section: answers, empty, trivial (fewer than 3 characters after trimming), non-empty |
| ``section_page`` | 173 | answers per (section, page): the two dimensions together |
| ``content_page`` | 16 | the empty/trivial/non-empty rule per page |
| ``corpus`` | 1 | what was read, and when: parquet, engine, rows, columns, build time |
| ``markup_rates`` (view) | 5 | the markup counts as rates, one row per markup feature |

``answers`` is materialised rather than left as a view over the parquet so the database file is self-contained:
Superset queries it without depending on this repository's working directory. The file is built under a temporary
name in the same directory and moved into place, so a failed build never replaces a working file.
"""
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
import duckdb
from archai import stats

OUT = Path("data/out")
DEFAULT_DB = OUT / "stapfer_stats.duckdb"  # derived, gitignored (.gitignore: data/out/*.duckdb)
SRC = "read_parquet(?)"

# The API block names of stats.aggregate_sql, keyed by the table they become (the dotted ones are the nested sheets parts).
AGGREGATES = {
    "sections": "sections",
    "documents": "documents",
    "answers_per_page": "pages.answers_per_page",
    "sheet_summary": "sheets.summary",
    "answers_per_sheet": "sheets.answers_per_sheet",
    "pages_per_sheet": "sheets.pages_per_sheet",
    "markup": "markup",
    "content": "content",
}

# The cuts stats.py filters by but does not aggregate per combination, built from its own definitions.
DERIVED = {
    "section_page": f"select {stats.SECTION} as section, page_start as page, count(*) as answers"
                    f" from {SRC} group by 1, 2 order by 1, 2",
    "content_page": f"select page_start as page, count(*) as answers,"
                    f" sum(case when {stats.EMPTY} then 1 else 0 end) as empty,"
                    f" sum(case when {stats.TRIVIAL} then 1 else 0 end) as trivial,"
                    f" sum(case when {stats.PLAIN} then 1 else 0 end) as non_empty"
                    f" from {SRC} group by 1 order by 1",
}

# The markup counts (one row of six columns) in the long shape a bar chart needs: one row per markup feature.
MARKUP_RATES = """create or replace view markup_rates as
select 'transcription markup' as feature, with_transcription_markup as answers_with_feature, answers as answers_in_corpus, round(100.0 * with_transcription_markup / answers, 2) as rate_pct from markup
union all select 'markup errors', with_markup_errors, answers, round(100.0 * with_markup_errors / answers, 2) from markup
union all select 'uncertain readings', with_uncertain_readings, answers, round(100.0 * with_uncertain_readings / answers, 2) from markup
union all select 'margin notes', with_margin_notes, answers, round(100.0 * with_margin_notes / answers, 2) from markup
union all select 'editor comments', with_editor_comments, answers, round(100.0 * with_editor_comments / answers, 2) from markup"""


def connect(db=None, read_only=True):
    """Open the datasource file; read-only by default so querying it never writes a WAL next to it."""
    path = Path(db or DEFAULT_DB)
    if not path.is_file():
        raise FileNotFoundError(f"datasource not built: {path} (run `make statssql` or `python -m archai.statssql`)")
    return duckdb.connect(str(path), read_only=read_only)


def _rows(con, name):
    return con.execute(f'select count(*) from "{name}"').fetchone()[0]


def _relations(con):
    return [r[0] for r in con.execute("select table_name from information_schema.tables"
                                      " where table_schema = 'main' order by table_name").fetchall()]


def _corpus_meta(con):
    file, engine, rows, columns, source, built = con.execute(
        "select file, engine, rows, columns, source_parquet, built_at from corpus").fetchone()
    return {"database": file, "engine": engine, "rows": rows, "columns": json.loads(columns),
            "source_parquet": source, "built_at": built}


def report(db=None):
    """What an existing datasource file contains: every relation with its row count, and the corpus it read."""
    path = Path(db or DEFAULT_DB)
    con = connect(path)
    try:
        return {"database": str(path), "bytes": path.stat().st_size,
                "relations": {name: _rows(con, name) for name in _relations(con)},
                "corpus": _corpus_meta(con)}
    finally:
        con.close()


def build(db=None, corpus=None):
    """Build the datasource file from the corpus parquet and return :func:`report` of what was written.

    The aggregate tables come from ``stats.aggregate_sql`` (the /api/stats statements, bound to the parquet path);
    the extra dimensions and the rates view are defined at the top of this module. The corpus parquet is only
    read, never written, and the database file is written to a temporary name and moved into place at the end.
    """
    source = stats.corpus_path(corpus)
    path = Path(db or DEFAULT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")  # same directory, same filesystem, so os.replace is atomic
    tmp.unlink(missing_ok=True)
    con = None
    try:
        con = duckdb.connect(str(tmp))
        bound = [str(source)]
        blocks = stats.aggregate_sql("all")
        for table, block in AGGREGATES.items():
            con.execute(f'create or replace table "{table}" as {blocks[block]}', bound)
        for table, statement in DERIVED.items():
            con.execute(f'create or replace table "{table}" as {statement}', bound)
        con.execute(f"create or replace table answers as select * from {SRC}", bound)
        con.execute(MARKUP_RATES)
        rows = con.execute("select answers from documents").fetchone()[0]
        columns = [d[0] for d in con.execute(f"select * from {SRC} limit 0", bound).description]
        con.execute("create or replace table corpus(file varchar, engine varchar, rows bigint, columns varchar,"
                    " source_parquet varchar, built_at varchar)")
        con.execute("insert into corpus values (?, ?, ?, ?, ?, ?)",
                    [str(path), f"duckdb {duckdb.__version__}", rows, json.dumps(columns), str(source),
                     datetime.now(timezone.utc).isoformat(timespec="seconds")])
    finally:
        if con is not None:
            con.close()
    os.replace(tmp, path)
    return report(path)


def main(argv=None):
    """CLI: build the datasource file (default) or print what an existing one contains (``--report``)."""
    ap = argparse.ArgumentParser(description="Write the Superset DuckDB datasource (derived from the corpus parquet).")
    ap.add_argument("--db", default=str(DEFAULT_DB), help=f"DuckDB file to write; gitignored (default {DEFAULT_DB})")
    ap.add_argument("--corpus", default=None, help=f"corpus parquet to read (default {stats.CORPUS})")
    ap.add_argument("--report", action="store_true", help="print the existing file's contents instead of building")
    args = ap.parse_args(argv)
    try:
        out = report(args.db) if args.report else build(args.db, args.corpus)
    except (stats.CorpusMissing, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
