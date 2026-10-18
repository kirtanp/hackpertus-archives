"""Datasource module: every figure in the generated DuckDB file is checked against pandas, and against /api/stats."""
import json
import shutil
import subprocess
from pathlib import Path
import duckdb
import pandas as pd
import pytest

from archai import statssql, stats

ROOT = Path(__file__).resolve().parents[1]
SHEETS = 2250
ANSWERS = 92524


@pytest.fixture(scope="module")
def answers():
    return pd.read_parquet(stats.CORPUS)


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """The datasource, built once for the module from the committed corpus parquet."""
    return statssql.build(tmp_path_factory.mktemp("statssql") / "statssql.duckdb")


@pytest.fixture(scope="module")
def con(db):
    connection = statssql.connect(db["database"])
    yield connection
    connection.close()


def rows(con, sql):
    """A SELECT as a list of dicts, the shape both the tables and the API blocks return."""
    cur = con.execute(sql)
    return [dict(zip([d[0] for d in cur.description], r, strict=True)) for r in cur.fetchall()]


def section_of(a):
    """The module's section rule (first two dot-parts of question_no), recomputed in pandas."""
    return a.question_no.fillna("").str.split(".").str[:2].str.join(".")


def hist(series):
    return {int(k): int(v) for k, v in series.value_counts().sort_index().items()}


def counts(series):
    return {k: int(v) for k, v in series.value_counts().items()}


def test_build_writes_every_relation(db, tmp_path):
    want = set(statssql.AGGREGATES) | set(statssql.DERIVED) | {"answers", "corpus", "markup_rates"}
    assert set(db["relations"]) == want
    assert Path(db["database"]).is_file() and db["bytes"] > 0
    assert Path(db["database"]).suffix == ".duckdb"


def test_answers_table_is_the_whole_corpus(con, answers):
    assert rows(con, "select count(*) as n from answers")[0]["n"] == ANSWERS
    assert rows(con, "select count(distinct doc_id) as n from answers")[0]["n"] == SHEETS
    assert [r["column_name"] for r in rows(con, "select column_name from information_schema.columns"
                                                " where table_name = 'answers' order by ordinal_position")] == list(answers.columns)


def test_sections_table_matches_pandas(con, answers):
    got = {r["section"]: r["answers"] for r in rows(con, "select section, answers from sections")}
    assert got == counts(section_of(answers))
    assert got["III.12"] == int((section_of(answers) == "III.12").sum()) > 0


def test_documents_table_matches_pandas(con, answers):
    assert rows(con, "select answers, documents from documents") == [
        {"answers": ANSWERS, "documents": int(answers.doc_id.nunique())}]


def test_page_table_matches_pandas(con, answers):
    assert {r["page"]: r["answers"] for r in rows(con, "select page, answers from answers_per_page")} == \
        hist(answers.page_start)


def test_sheet_tables_match_pandas(con, answers):
    per_sheet = answers.groupby("doc_id").size()
    assert rows(con, "select sheets, min_answers, median_answers, max_answers from sheet_summary") == [
        {"sheets": int(per_sheet.size), "min_answers": int(per_sheet.min()),
         "median_answers": float(per_sheet.median()), "max_answers": int(per_sheet.max())}]
    assert {r["answers"]: r["sheets"] for r in rows(con, "select answers, sheets from answers_per_sheet")} == hist(per_sheet)
    assert {r["pages"]: r["sheets"] for r in rows(con, "select pages, sheets from pages_per_sheet")} == \
        hist(answers.groupby("doc_id").page_end.max())


def test_markup_table_matches_pandas(con, answers):
    assert rows(con, "select * from markup") == [{
        "answers": ANSWERS,
        "with_transcription_markup": int(answers.annotations.map(len).gt(0).sum()),
        "with_markup_errors": int((answers.markup_errors > 0).sum()),
        "with_uncertain_readings": int((answers.uncertain_readings > 0).sum()),
        "with_margin_notes": int(answers.margin_note.fillna("").str.strip().ne("").sum()),
        "with_editor_comments": int(answers.editor_comment.fillna("").str.strip().ne("").sum())}]


def test_content_table_matches_pandas(con, answers):
    text = answers.text_clean.fillna("").str.strip()
    empty, trivial = text.eq(""), (~text.eq("")) & (text.str.len() < 3)  # the threshold written out, not imported
    frame = pd.DataFrame({"section": section_of(answers), "empty": empty, "trivial": trivial}).groupby("section")
    want = [{"section": sec, "answers": len(g), "empty": int(g["empty"].sum()), "trivial": int(g["trivial"].sum()),
             "non_empty": int(len(g) - g["empty"].sum() - g["trivial"].sum())} for sec, g in frame]
    assert rows(con, "select * from content order by section") == want
    assert sum(r["empty"] for r in want) == int(empty.sum()) == 191
    assert sum(r["trivial"] for r in want) == int(trivial.sum()) == 545


def test_page_dimensions_match_pandas(con, answers):
    """The two per-page tables reuse stats.py's section and empty/trivial rules, so pandas re-derives them the same way."""
    text = answers.text_clean.fillna("").str.strip()
    mask = lambda col: col.eq("")  # noqa: E731 - the compact local-helper idiom of this repository
    page = pd.DataFrame({"page": answers.page_start, "section": section_of(answers), "blank": mask(text),
                         "trivial": (~text.eq("")) & (text.str.len() < 3)})
    want_pages = [{"page": int(p), "answers": int(len(g)), "empty": int(g.blank.sum()), "trivial": int(g.trivial.sum()),
                   "non_empty": int(len(g) - g.blank.sum() - g.trivial.sum())} for p, g in page.groupby("page", sort=True)]
    assert rows(con, "select * from content_page order by page") == want_pages
    want_cross = {tuple(r): int(c) for r, c in page.groupby(["section", "page"]).size().items()}
    got_cross = {(r["section"], r["page"]): r["answers"] for r in rows(con, "select section, page, answers from section_page")}
    assert got_cross == want_cross
    assert sum(got_cross.values()) == ANSWERS


def test_markup_rates_are_the_counts_as_rates(con, answers):
    got = rows(con, "select feature, answers_with_feature, answers_in_corpus, rate_pct from markup_rates")
    assert [r["feature"] for r in got] == ["transcription markup", "markup errors", "uncertain readings",
                                           "margin notes", "editor comments"]
    want = {"transcription markup": int(answers.annotations.map(len).gt(0).sum()),
            "markup errors": int((answers.markup_errors > 0).sum()),
            "uncertain readings": int((answers.uncertain_readings > 0).sum()),
            "margin notes": int(answers.margin_note.fillna("").str.strip().ne("").sum()),
            "editor comments": int(answers.editor_comment.fillna("").str.strip().ne("").sum())}
    for r in got:
        assert r["answers_with_feature"] == want[r["feature"]] and r["answers_in_corpus"] == ANSWERS
        assert r["rate_pct"] == round(100.0 * want[r["feature"]] / ANSWERS, 2)
    assert dict((r["feature"], r["answers_with_feature"]) for r in got)["markup errors"] > 0


def test_tables_reproduce_the_api_blocks(con):
    """The datasource must answer the same figures /api/stats answers: same SQL, so the values are compared, not re-derived."""
    every = stats.summary(query="all")
    assert rows(con, "select section, answers from sections") == every["blocks"]["sections"]
    assert rows(con, "select answers, documents from documents")[0] == every["blocks"]["documents"]
    assert rows(con, "select page, answers from answers_per_page") == every["blocks"]["pages"]["answers_per_page"]
    assert rows(con, "select sheets, min_answers, median_answers, max_answers from sheet_summary")[0] == \
        every["blocks"]["sheets"]["summary"]
    assert rows(con, "select answers, sheets from answers_per_sheet") == every["blocks"]["sheets"]["answers_per_sheet"]
    assert rows(con, "select pages, sheets from pages_per_sheet") == every["blocks"]["sheets"]["pages_per_sheet"]
    assert rows(con, "select * from markup")[0] == every["blocks"]["markup"]
    assert rows(con, "select * from content order by section") == every["blocks"]["content"]
    assert every["scope"]["rows_counted"] == ANSWERS


def test_corpus_table_states_what_was_read(db):
    meta, live = db["corpus"], stats.summary(query="all")["corpus"]
    assert meta["rows"] == live["rows"] == ANSWERS
    assert meta["columns"] == live["columns"]
    assert meta["engine"] == live["engine"] == f"duckdb {duckdb.__version__}"
    assert meta["source_parquet"] == str(stats.CORPUS) == live["file"]
    assert meta["built_at"] and meta["database"] == str(statssql.report(db["database"])["database"])


def test_build_leaves_no_temporary_file(db):
    assert not Path(f"{db['database']}.tmp").exists()
    assert [p.name for p in Path(db["database"]).parent.iterdir()] == [Path(db["database"]).name]


def test_nothing_is_written_to_the_data_directory(tmp_path):
    """Building the datasource elsewhere leaves data/out byte-identical: the corpus is read, never written."""
    def snapshot():
        return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in stats.OUT.iterdir()}

    before = snapshot()
    out = statssql.build(tmp_path / "elsewhere.duckdb")
    assert snapshot() == before
    assert out["corpus"]["rows"] == ANSWERS
    assert stats.OUT.resolve() not in Path(out["database"]).resolve().parents


def test_connect_reports_a_missing_file(tmp_path):
    absent = tmp_path / "absent.duckdb"
    with pytest.raises(FileNotFoundError) as e:
        statssql.connect(absent)
    assert "not built" in str(e.value) and "statssql" in str(e.value)


def test_connect_is_read_only_by_default(db):
    con = statssql.connect(db["database"])
    try:
        assert con.execute("select count(*) from sections").fetchone()[0] > 0
        with pytest.raises(duckdb.Error) as e:
            con.execute("create table t(a int)")
        assert "read-only" in str(e.value)
    finally:
        con.close()


def test_default_path_is_gitignored_not_committed():
    """The database file is a build artifact: git must ignore it and no *.duckdb may ever be tracked."""
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("no git checkout to ask about ignore rules")
    ignored = subprocess.run([git, "check-ignore", "-v", str(statssql.DEFAULT_DB)], cwd=ROOT, capture_output=True, text=True)
    assert ignored.returncode == 0, f"{statssql.DEFAULT_DB} must be gitignored: {ignored.stderr}"
    tracked = subprocess.run([git, "ls-files", "*.duckdb"], cwd=ROOT, capture_output=True, text=True)
    assert tracked.stdout.strip() == "", "the derived database file must never be committed"


def test_cli_builds_and_reports(tmp_path, capsys, monkeypatch):
    target = tmp_path / "cli.duckdb"
    assert statssql.main(["--db", str(target)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["corpus"]["rows"] == ANSWERS and target.is_file()
    assert statssql.main(["--db", str(target), "--report"]) == 0
    assert json.loads(capsys.readouterr().out)["relations"] == built["relations"]


def test_cli_fails_clearly_without_the_corpus(tmp_path, capsys):
    assert statssql.main(["--db", str(tmp_path / "none.duckdb"), "--corpus", str(tmp_path / "absent.parquet")]) == 1
    assert "corpus not found" in capsys.readouterr().err
    assert not (tmp_path / "none.duckdb").exists()


def test_module_is_not_imported_by_the_app():
    """The datasource is repository code, not a change to the running application: app.py must not touch it."""
    assert "statssql" not in (ROOT / "app.py").read_text()
