"""Corpus-level statistics over the Stapfer-Enquête answers, computed in SQL by DuckDB.

qa.py answers questions about the handful of passages relevant to one place. This module answers the other
workload: aggregate questions over every row of the corpus at once (``data/out/stapfer_answers.parquet``,
92,524 answers over 2,250 sheets), optionally sliced by questionnaire section and page. DuckDB scans the
parquet with ``read_parquet``; the table is never loaded into pandas, and no data file is ever written.

Read-only, two mechanisms because either one alone is incomplete:

* The connection itself is opened with ``read_only=True`` over an empty catalog (:func:`_catalog`), so DuckDB
  itself refuses to create, insert, drop or attach anything. DuckDB cannot launch an *in-memory* database in
  read-only mode ("Cannot launch in-memory database in read-only mode", the same on 1.4.1 and 1.5.6) and the
  corpus is a parquet file rather than a DuckDB database, so read-only mode needs some catalog file to attach
  to; that file holds no tables and no data, lives outside the repository in the system temp directory, and is
  removed on exit.
* :func:`execute` accepts only SELECT/WITH, because DuckDB's read-only mode protects the *catalog* and not the
  filesystem: ``COPY (SELECT ...) TO '<file>'`` succeeds on a read-only connection, which is exactly the write
  that must never touch ``data/out``. All SQL in this module is also a plain SELECT over ``read_parquet``.

Every response states the corpus it read and the exact scope it counted (:func:`summary`), so a whole-corpus
figure cannot be mistaken for the per-place aggregate the /api/ask path produces.
"""
import atexit, re, shutil, tempfile, threading
from pathlib import Path
import duckdb

OUT = Path("data/out")  # the application's data convention (qa.py, evaluate.py, app.py)
CORPUS = OUT / "stapfer_answers.parquet"

QUERIES = ("all", "sections", "documents", "pages", "sheets", "markup", "content")
# questionnaire section = the first two dot-parts of question_no (III.11.b -> III.11, zusatz.fliesstext.oekonomie -> zusatz.fliesstext)
SECTION = "coalesce(split_part(question_no, '.', 1) || '.' || split_part(question_no, '.', 2), '')"
SECTION_RE = re.compile(r"[A-Za-z]+(?:\.[A-Za-z0-9]+)*")
TRIVIAL_CHARS = 3  # trimmed answers shorter than this carry nothing: blank, "O.", "-" (qa.py skips the same answers)
EMPTY = "trim(coalesce(text_clean, '')) = ''"
TRIVIAL = f"trim(coalesce(text_clean, '')) <> '' and length(trim(coalesce(text_clean, ''))) < {TRIVIAL_CHARS}"
PLAIN = f"not ({EMPTY}) and not ({TRIVIAL})"

_CATALOG = {}  # {"path": <empty DuckDB catalog in the temp dir>}, created once per process
_CATALOG_LOCK = threading.Lock()


class CorpusMissing(RuntimeError):
    """The corpus parquet is not where the application keeps it."""


class UnsupportedQuery(ValueError):
    """The requested aggregate is not one of QUERIES."""


class InvalidFilter(ValueError):
    """A filter value is not a questionnaire section the corpus could contain."""


class NotReadOnly(RuntimeError):
    """A statement other than SELECT/WITH was handed to the corpus connection (COPY ... TO writes files)."""


def corpus_path(corpus=None):
    """The corpus parquet, or CorpusMissing when the application's data file is absent (never a silent zero)."""
    path = Path(corpus or CORPUS)
    if not path.is_file():
        raise CorpusMissing(f"corpus not found: {path} (expected the parquet written by archai.stapfer_clean)")
    return path


def _catalog():
    """An empty DuckDB catalog outside the repository, created once so the corpus connection can be read-only.

    Read-only mode needs a database file to attach to, and DuckDB will not open one in memory that way. The
    file carries no tables and no data, and its directory is deleted at interpreter exit.
    """
    with _CATALOG_LOCK:
        if "path" not in _CATALOG:
            home = Path(tempfile.mkdtemp(prefix="archai-stats-"))
            atexit.register(shutil.rmtree, home, ignore_errors=True)
            path = home / "catalog.duckdb"
            duckdb.connect(str(path)).close()  # create the empty catalog, then reopen it read-only
            _CATALOG["path"] = path
        return _CATALOG["path"]


def connect(corpus=None):
    """Read-only DuckDB connection that scans the corpus parquet in place (see the module docstring)."""
    corpus_path(corpus)
    return duckdb.connect(str(_catalog()), read_only=True)


def execute(con, sql, params=()):
    """Run one statement. SELECT/WITH only: read-only mode guards the catalog, this guards the filesystem."""
    head = sql.strip().split(None, 1)[0].lower()
    if head not in ("select", "with"):
        raise NotReadOnly(f"only SELECT/WITH is allowed on the corpus connection, not {head!r}")
    return con.execute(sql, list(params))


def _rows(con, sql, params=()):
    cur = execute(con, sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _one(con, sql, params=()):
    return _rows(con, sql, params)[0]


def _filter(section=None, page=None):
    """WHERE fragment plus parameters for the slice the scope covers; an answer spanning pages 2-3 is on both."""
    conds, params = [], []
    if section is not None:
        conds.append("(question_no = ? or starts_with(question_no, ?))")
        params += [section, f"{section}."]
    if page is not None:
        conds.append("page_start <= ? and page_end >= ?")
        params += [page, page]
    return (f" where {' and '.join(conds)}" if conds else ""), params


def _blocks(con, path, where, params, query):
    """The requested aggregates, each computed by DuckDB over the parquet (no rows are fetched to count them)."""
    src, p = "read_parquet(?)", [str(path), *params]
    out = {}
    if query in ("all", "sections"):
        out["sections"] = _rows(con, f"select {SECTION} as section, count(*) as answers from {src}{where}"
                                     " group by 1 order by answers desc, section", p)
    if query in ("all", "documents"):
        out["documents"] = _one(con, f"select count(*) as answers, count(distinct doc_id) as documents from {src}{where}", p)
    if query in ("all", "pages"):
        out["pages"] = {"answers_per_page": _rows(con, f"select page_start as page, count(*) as answers from {src}{where}"
                                                       " group by 1 order by 1", p)}
    if query in ("all", "sheets"):
        out["sheets"] = {
            "summary": _one(con, f"select count(*) as sheets, min(n) as min_answers, median(n) as median_answers,"
                                 f" max(n) as max_answers from (select count(*) as n from {src}{where} group by doc_id)", p),
            "answers_per_sheet": _rows(con, f"select n as answers, count(*) as sheets from"
                                            f" (select count(*) as n from {src}{where} group by doc_id) group by 1 order by 1", p),
            "pages_per_sheet": _rows(con, f"select p as pages, count(*) as sheets from"
                                          f" (select max(page_end) as p from {src}{where} group by doc_id) group by 1 order by 1", p),
        }
    if query in ("all", "markup"):
        out["markup"] = _one(con, f"""select count(*) as answers,
            sum(case when array_length(annotations) > 0 then 1 else 0 end) as with_transcription_markup,
            sum(case when markup_errors > 0 then 1 else 0 end) as with_markup_errors,
            sum(case when uncertain_readings > 0 then 1 else 0 end) as with_uncertain_readings,
            sum(case when trim(coalesce(margin_note, '')) <> '' then 1 else 0 end) as with_margin_notes,
            sum(case when trim(coalesce(editor_comment, '')) <> '' then 1 else 0 end) as with_editor_comments
            from {src}{where}""", p)
    if query in ("all", "content"):
        out["content"] = _rows(con, f"""select {SECTION} as section, count(*) as answers,
            sum(case when {EMPTY} then 1 else 0 end) as empty,
            sum(case when {TRIVIAL} then 1 else 0 end) as trivial,
            sum(case when {PLAIN} then 1 else 0 end) as non_empty
            from {src}{where} group by 1 order by section""", p)
    return out


def _scope(counted, total, section, page):
    """The sentence and filter record that say which rows a response covers."""
    filters = {"section": section, "page": page}
    if section is None and page is None:
        text = (f"all {total:,} answers in the corpus (whole corpus, not the passages retrieved for one place)"
                f"; definitions: empty = blank text, trivial = fewer than {TRIVIAL_CHARS} characters after trimming")
    else:
        parts = [f"section {section}" if section is not None else None, f"page {page}" if page is not None else None]
        text = f"{counted:,} of {total:,} answers in the corpus matching {', '.join(x for x in parts if x)}"
    return {"rows_counted": counted, "rows_in_corpus": total, "filters": filters, "description": text}


def summary(query="all", section=None, page=None, corpus=None):
    """Answer one aggregate over the corpus (or a section/page slice) with SQL, and state the scope it counted.

    Returns ``{"corpus": ..., "scope": ..., "query": ..., "blocks": {...}}``. Raises UnsupportedQuery,
    InvalidFilter, or CorpusMissing instead of returning a misleading zero.
    """
    if query not in QUERIES:
        raise UnsupportedQuery(f"unsupported query {query!r}; supported: {', '.join(QUERIES)}")
    if section is not None and not SECTION_RE.fullmatch(section):
        raise InvalidFilter(f"invalid section {section!r}; expected a questionnaire number such as III.12, IV.16 or zusatz.unterschrift")
    if page is not None and page < 1:
        raise InvalidFilter(f"invalid page {page!r}; pages start at 1")
    path = corpus_path(corpus)
    where, params = _filter(section, page)
    con = connect(corpus)
    try:
        total = _one(con, "select count(*) as rows from read_parquet(?)", [str(path)])["rows"]
        counted = _one(con, f"select count(*) as rows from read_parquet(?){where}", [str(path), *params])["rows"]
        columns = [d[0] for d in execute(con, "select * from read_parquet(?) limit 0", [str(path)]).description]
        blocks = _blocks(con, path, where, params, query)
    finally:
        con.close()
    return {
        "corpus": {"file": str(path), "engine": f"duckdb {duckdb.__version__}", "rows": total, "columns": columns,
                   "read": "read_parquet, scanned in SQL by DuckDB (never loaded into pandas)"},
        "scope": _scope(counted, total, section, page),
        "query": query,
        "blocks": blocks,
    }
