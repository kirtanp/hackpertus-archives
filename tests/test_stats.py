"""Statistics module: every DuckDB figure is checked against the same figure computed independently with pandas."""
import tempfile
from pathlib import Path
import duckdb
import pandas as pd
import pytest

from archai import stats

SHEETS = 2250
ANSWERS = 92524


@pytest.fixture(scope="module")
def answers():
    return pd.read_parquet(stats.CORPUS)


def section_of(a):
    """The module's section rule (first two dot-parts of question_no), recomputed in pandas."""
    return a.question_no.fillna("").str.split(".").str[:2].str.join(".")


def hist(series):
    """A histogram as {key: count}, the shape the module returns."""
    return {int(k): int(v) for k, v in series.value_counts().sort_index().items()}


def counts(series):
    """The same histogram with the keys left as they are (question numbers are strings)."""
    return {k: int(v) for k, v in series.value_counts().items()}


def test_corpus_is_the_expected_parquet(answers):
    assert stats.CORPUS == Path("data/out/stapfer_answers.parquet")
    assert (len(answers), answers.doc_id.nunique()) == (ANSWERS, SHEETS)


def test_corpus_metadata_states_what_was_read(answers):
    out = stats.summary(query="all")
    assert out["corpus"]["rows"] == ANSWERS
    assert out["corpus"]["columns"] == list(answers.columns)
    assert out["corpus"]["engine"].startswith("duckdb ")
    assert "read_parquet" in out["corpus"]["read"]


def test_scope_covers_the_whole_corpus(answers):
    scope = stats.summary(query="all")["scope"]
    assert scope["rows_counted"] == scope["rows_in_corpus"] == ANSWERS
    assert scope["filters"] == {"section": None, "page": None}
    assert "whole corpus" in scope["description"]
    assert f"{ANSWERS:,}" in scope["description"]


def test_sections_match_pandas(answers):
    got = {r["section"]: r["answers"] for r in stats.summary(query="sections")["blocks"]["sections"]}
    assert got == counts(section_of(answers))
    assert sum(got.values()) == ANSWERS
    assert got["III.12"] == int((section_of(answers) == "III.12").sum())


def test_documents_match_pandas(answers):
    docs = stats.summary(query="documents")["blocks"]["documents"]
    assert docs == {"answers": ANSWERS, "documents": int(answers.doc_id.nunique())}
    assert docs["documents"] == SHEETS


def test_answers_per_page_matches_pandas(answers):
    pages = stats.summary(query="pages")["blocks"]["pages"]["answers_per_page"]
    assert {r["page"]: r["answers"] for r in pages} == hist(answers.page_start)
    assert sum(r["answers"] for r in pages) == ANSWERS


def test_sheet_distributions_match_pandas(answers):
    sheets = stats.summary(query="sheets")["blocks"]["sheets"]
    per_sheet = answers.groupby("doc_id").size()
    assert {r["answers"]: r["sheets"] for r in sheets["answers_per_sheet"]} == hist(per_sheet)
    assert {r["pages"]: r["sheets"] for r in sheets["pages_per_sheet"]} == hist(answers.groupby("doc_id").page_end.max())
    assert sheets["summary"] == {"sheets": int(per_sheet.size), "min_answers": int(per_sheet.min()),
                                 "median_answers": float(per_sheet.median()), "max_answers": int(per_sheet.max())}


def test_markup_counts_match_pandas(answers):
    got = stats.summary(query="markup")["blocks"]["markup"]
    assert got == {
        "answers": ANSWERS,
        "with_transcription_markup": int(answers.annotations.map(len).gt(0).sum()),
        "with_markup_errors": int((answers.markup_errors > 0).sum()),
        "with_uncertain_readings": int((answers.uncertain_readings > 0).sum()),
        "with_margin_notes": int(answers.margin_note.fillna("").str.strip().ne("").sum()),
        "with_editor_comments": int(answers.editor_comment.fillna("").str.strip().ne("").sum()),
    }
    assert got["with_markup_errors"] > 0 and got["with_margin_notes"] > 0


def test_content_per_section_matches_pandas(answers):
    text = answers.text_clean.fillna("").str.strip()
    empty = text.eq("")
    trivial = (~empty) & (text.str.len() < 3)  # the threshold is written out, not read from the module under test
    per_section = pd.DataFrame({"section": section_of(answers), "blank": empty, "trivial": trivial}).groupby("section")
    want = {sec: {"answers": len(g), "empty": int(g.blank.sum()), "trivial": int(g.trivial.sum()),
                  "non_empty": int(len(g) - g.blank.sum() - g.trivial.sum())} for sec, g in per_section}
    got = {r["section"]: {k: v for k, v in r.items() if k != "section"} for r in stats.summary(query="content")["blocks"]["content"]}
    assert got == want
    assert (sum(r["empty"] for r in got.values()), sum(r["trivial"] for r in got.values())) == (int(empty.sum()), int(trivial.sum())) == (191, 545)
    assert sum(r["non_empty"] for r in got.values()) == ANSWERS - int(empty.sum()) - int(trivial.sum()) == 91788


def test_trivial_threshold_is_pinned(answers):
    """The empty/trivial rule must stay the documented one, so the test states it instead of importing it."""
    assert stats.TRIVIAL_CHARS == 3
    assert "fewer than 3 characters after trimming" in stats.summary(query="all")["scope"]["description"]
    text = answers.text_clean.fillna("").str.strip()
    just_over = (~text.eq("")) & (text.str.len() < 4)
    assert int(just_over.sum()) == 1036  # threshold 4 would count this many, the SQL must not


def test_section_filter_matches_pandas(answers):
    qno = answers.question_no.fillna("")
    mask = (qno == "III.12") | qno.str.startswith("III.12.")
    out = stats.summary(query="content", section="III.12")
    assert out["scope"]["rows_counted"] == int(mask.sum()) < ANSWERS
    assert out["scope"]["filters"] == {"section": "III.12", "page": None}
    assert [r["section"] for r in out["blocks"]["content"]] == ["III.12"]
    assert out["blocks"]["content"][0]["answers"] == int(mask.sum())
    assert {r["section"]: r["answers"] for r in stats.summary(query="sections", section="III.12")["blocks"]["sections"]} == {"III.12": int(mask.sum())}


def test_page_filter_matches_pandas(answers):
    """Page 2 tells the two rules apart: 1,707 answers span pages, so a range match beats a page_start match."""
    mask = (answers.page_start <= 2) & (answers.page_end >= 2)
    out = stats.summary(query="pages", page=2)
    assert out["scope"]["rows_counted"] == int(mask.sum()) == 29644
    assert int((answers.page_start == 2).sum()) == 28966  # a page_start-only rule would report this
    assert out["scope"]["filters"] == {"section": None, "page": 2}
    # the histogram groups the counted rows by where they start, so answers continued from page 1 appear under page 1
    per_page = {int(k): int(v) for k, v in answers[mask].page_start.value_counts().sort_index().items()}
    got = {r["page"]: r["answers"] for r in out["blocks"]["pages"]["answers_per_page"]}
    assert got == per_page == {1: 678, 2: 28966}
    assert sum(got.values()) == out["scope"]["rows_counted"]


def test_unsupported_query_is_a_clear_error():
    with pytest.raises(stats.UnsupportedQuery) as e:
        stats.summary(query="answers_per_teacher")
    assert "unsupported query" in str(e.value) and "sections" in str(e.value)


@pytest.mark.parametrize("section", ["III 12", "III.12.a!", "", "III..12", "12.3", "*"])
def test_invalid_section_is_a_clear_error(section):
    with pytest.raises(stats.InvalidFilter):
        stats.summary(query="sections", section=section)


def test_valid_but_unknown_section_counts_zero_rows():
    out = stats.summary(query="content", section="IX.99")
    assert out["scope"]["rows_counted"] == 0
    assert out["blocks"]["content"] == []


def test_invalid_page_is_a_clear_error():
    with pytest.raises(stats.InvalidFilter):
        stats.summary(query="pages", page=0)


def test_missing_corpus_fails_clearly(tmp_path):
    absent = tmp_path / "absent.parquet"
    assert not absent.exists()
    with pytest.raises(stats.CorpusMissing) as e:
        stats.summary(query="all", corpus=absent)
    assert "corpus not found" in str(e.value) and str(absent) in str(e.value)
    with pytest.raises(stats.CorpusMissing):
        stats.connect(absent)


def test_connection_is_really_read_only():
    """DuckDB itself, not only the module guard, must refuse to change the connection's catalog."""
    con = stats.connect()
    try:
        assert stats.execute(con, "select 1 as one").fetchone() == (1,)
        assert con.execute("select count(*) from read_parquet(?)", [str(stats.CORPUS)]).fetchone() == (ANSWERS,)
        with pytest.raises(duckdb.Error) as e:
            con.execute("create table t(a int)")  # straight to DuckDB, bypassing stats.execute
        assert "read-only" in str(e.value)
    finally:
        con.close()


def test_execute_refuses_anything_but_select():
    """Read-only mode still allows COPY ... TO a file, so the module guard is what protects data/out."""
    con = stats.connect()
    try:
        for sql in ("create table t(a int)", "insert into t values (1)", "drop table t", "update t set a = 1",
                    "copy (select 1 as a) to 'x.parquet'", "export database 'x'", "attach 'x.db'"):
            with pytest.raises(stats.NotReadOnly):
                stats.execute(con, sql)
        assert not Path("x.parquet").exists() and not Path("x.db").exists()
    finally:
        con.close()


def test_nothing_is_written_to_the_data_directory():
    """A full aggregate run leaves data/out byte-identical and keeps its scratch catalog out of the repository."""
    def snapshot():
        return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in stats.OUT.iterdir()}

    before = snapshot()
    out = stats.summary(query="all")
    assert snapshot() == before
    assert out["scope"]["rows_counted"] == ANSWERS
    catalog = stats._catalog()
    assert catalog.parent.name.startswith("archai-stats-")
    assert Path(tempfile.gettempdir()) in catalog.parents
    assert [p.name for p in catalog.parent.iterdir()] == ["catalog.duckdb"]  # empty catalog, no data, no spill file
    assert stats.OUT.resolve() not in (catalog, *catalog.parents)


def test_endpoint_returns_the_same_summary():
    from app import corpus_stats
    assert corpus_stats(query="documents")["blocks"]["documents"]["documents"] == SHEETS


def test_endpoint_maps_errors_to_http_statuses(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app import corpus_stats
    with pytest.raises(HTTPException) as bad:
        corpus_stats(query="nope")
    assert bad.value.status_code == 400 and "unsupported query" in bad.value.detail
    with pytest.raises(HTTPException) as bad_section:
        corpus_stats(query="sections", section="III 12")
    assert bad_section.value.status_code == 400
    monkeypatch.setattr(stats, "CORPUS", tmp_path / "absent.parquet")
    with pytest.raises(HTTPException) as missing:
        corpus_stats(query="all")
    assert missing.value.status_code == 503 and "corpus not found" in missing.value.detail
