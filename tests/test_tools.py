from archai import tools as T
from archai.llm import extract_json, check_schema
from archai.stapfer_clean import clean, balance_errors, lv03_to_wgs84


def test_dates_1799_months():
    got = {d["iso"] for d in T.normalize_dates("den 16.ten Hornung 1799; le 8 mars 99; 3 Merz")}
    assert got == {"1799-02-16", "1799-03-08", "1799-03-03"}


def test_dates_ignore_non_months():
    assert T.normalize_dates("18 Knaben und 22 Mädchen") == []


def test_quantities():
    q = T.extract_quantities("Knaben 18. Mädchen 22. fl. 70 und 13 Sous")
    assert [(p["sex"], p["count"]) for p in q["pupils"]] == [("male", 18), ("female", 22)]
    assert {(m["unit"].lower(), m["amount"]) for m in q["money"]} >= {("fl", 70.0), ("sous", 13.0)}


def test_signatures():
    assert T.parse_signature("BAR B0 1000/1483, Nr. 1421, fol. 228-229v")["folio"] == "228-229v"
    s = T.parse_signature("E6100C#1998/106#1039*")
    assert (s["fonds"], s["accession"], s["dossier"]) == ("E6100C", "1998/106", "1039")


def test_verify_in_source():
    r = T.verify_in_source(["Hans Jacob Weber", "Zürich"], "Hans Jacob Weber von Fischenthal")
    assert [x["grounded"] for x in r] == [True, False]


def test_clean_markup_keeps_annotations():
    t, ann, q = clean("¥Schule¥¥@1. Knaben $18$$ °-°° ||[Seite 2] Lehrer Wil=@helm #M## Buch")
    assert "Wilhelm" in t and "Knaben 18 –" in t and "[…]" not in t and "$" not in t and "@" not in t
    assert q == {"uncertain": 1, "dashes": 1, "errors": 0}
    assert {"type": "uncertain_reading", "text": "M"} in ann


def test_assign_pages():
    import pandas as pd
    from archai.stapfer_clean import assign_pages
    a = pd.DataFrame({"doc_id": ["d"] * 4, "question_no": ["I.1", "I.2", "II.5", "II.6"],
                      "text_raw": ["a", "b ||[Seite 2] c", "||[Seite 3] d", "e"]})
    p = assign_pages(a)
    assert list(zip(p.page_start, p.page_end, strict=True)) == [(1, 1), (1, 2), (3, 3), (3, 3)]


def test_balance_errors():
    assert balance_errors("$open without close") == 1


def test_lv03():
    lat, lon = lv03_to_wgs84(600000, 200000)  # Bern old observatory
    assert abs(lat - 46.951) < 0.01 and abs(lon - 7.439) < 0.01


def test_extract_json_repair():
    assert extract_json('Sure! ```json\n{"a": "x}y", "b": [1]}\n``` thanks') == {"a": "x}y", "b": [1]}
    assert extract_json("no json") is None


def test_schema_coerce():
    o = {"persons": "Weber", "title": 3}
    assert check_schema(o, {"persons": list, "title": str, "summary_en": str}) == ["type:title", "missing:summary_en"]
    assert o == {"persons": ["Weber"]}


def test_number_check_catches_wrong_citation():
    from archai.qa import check_numbers
    hits = [{"cite": "BAR B0 1000/1483, Nr. 1442, fol. 222-223v, S. 4", "text": "l'Hôpital: 76 L. deux sacs de Fromant, 16 Quarteron"},
            {"cite": "BAR B0 1000/1483, Nr. 1442, fol. 220-221v, S. 4", "text": "Saint Laurent: deux cent Livres, bois environ 24 L."}]
    q = "Quel est le salaire du régent à Lausanne?"
    assert check_numbers("[2] Saint Laurent: deux cent Livres, bois environ 24 L.\n[1] l'Hôpital: 76 L.", hits, q) == []
    bad = check_numbers("[1] Saint Laurent: deux cent Livres, bois environ 24 L.", hits, q)
    assert [f["number"] for f in bad] == ["24"] and bad[0]["cited"] == [1]
    assert check_numbers("Total: 1'000 fl. [1]", hits, q)[0]["number"] == "1000"  # not in source 1
    assert check_numbers("Nr. 1442, S. 4 [1]", hits, q) == []  # signature and page numbers are part of the source


def test_word_numbers():
    from archai.qa import word_numbers
    assert {"41", "10"} <= word_numbers("Quarante-un à Aran. à Villette dix.")
    assert {"46", "50"} <= word_numbers("Quarante six a cinquante tous compris")
    assert "260" in word_numbers("Deux Cent soixante francs de Suisse")
    assert {"80", "21"} <= word_numbers("quatre-vingts écoliers, vingt et un garçons")
    assert {"41", "8"} <= word_numbers("einundvierzig Kinder, acht Uhr")


def test_quantities_reads_the_corpus_phrasings():
    """Every string is a real corpus row (data/out/stapfer_answers.parquet); the counts are the teacher's own."""
    def pupils(t):
        return [(p["sex"], p["count"]) for p in T.extract_quantities(t)["pupils"]]
    assert pupils("Knaben 17. Mägdlein 60. denn Knaben werden so bald sie gut Lesen können") == [("male", 17), ("female", 60)]
    # "Kaben" is a misspelling of "Knaben" (real row), so 90 is also read as a girls' figure; the sheet's real
    # girls' figure 85 is found, and the aggregate treats the sheet as two competing figures instead of summing it
    assert ("female", 85) in pupils("im Winter … heür Kaben 90 Mägdchen 85 175")
    assert pupils("Mädchen circa 65.") == [("female", 65)]
    assert pupils("Knaben deren 12") == [("male", 12)]
    # "34. Knaben und 21 Mädchen" states 21 girls; the number after "Knaben" is not a boys' count, and neither
    # is a count that already follows a sex word ("Knaben 36-40. Mädchen 60-70" is not 40 girls)
    assert pupils("55. – darunder 34. Knaben und 21 Mädchen") == [("female", 21)]
    assert pupils("Knaben 18 Mädchen 22") == [("male", 18), ("female", 22)]
    assert pupils("Knaben 36-40. Mädchen 60-70.") == [("male", 36), ("female", 60)]
    assert pupils("Knaben bis 20") == []
    # the questionnaire header in every III.12.a/b passage is not an answer
    assert pupils("Bern 1799. Frage III.12.a (Im Winter. (Knaben/Mädchen)): 40 à 50. Knaben.") == []


def _sheet(n, answer, qno="III.12", question="Schulkinder. Wie viele Kinder besuchen überhaupt die Schule?"):
    """A retrieved passage, shaped as qa.passages() builds them; same doc_id = same archival sheet."""
    return {"id": f"stapfer:antwort:{n}", "doc_id": f"stapfer:transkription:{n}", "kind": "stapfer", "qno": qno,
            "text": f"Bern, Testquartier (Lehrer: Test) 1799. Frage {qno} ({question}): {answer}",
            "cite": f"BAR B0 1000/1483, Nr. {n}, S. 1", "page": 1, "url": "u", "facsimile": None, "score": 0.5}


def test_pupil_total_keeps_the_sexes_apart():
    from archai.qa import aggregate_pupils, render_pupil_totals
    q = "Wie viele Schülerinnen gab es total in Bern?"
    hits = [_sheet(1, "Knaben 47."), _sheet(2, "Mädchen 60.")]
    agg = aggregate_pupils(hits, q)
    assert agg["sex"] == "female" and [s["sex"] for s in agg["scopes"]] == ["female"]
    assert agg["scopes"][0]["total"] == 60 and [c["value"] for c in agg["scopes"][0]["contributors"]] == [60]
    assert "47" not in render_pupil_totals(agg, q)  # the boys' count is never a girls' figure
    assert {s["sex"]: s["total"] for s in aggregate_pupils(hits, "Wie viele Knaben gab es total in Bern?")["scopes"]} == {"male": 47}
    both = aggregate_pupils(hits, "Wie viele Kinder gab es total in Bern?")
    assert both["sex"] is None and {s["sex"]: s["total"] for s in both["scopes"]} == {"female": 60, "male": 47}


def test_pupil_range_is_reported_as_a_range_and_not_summed():
    from archai.qa import aggregate_pupils, render_pupil_totals
    q = "Wie viele Schülerinnen gab es total in Bern?"
    hits = [_sheet(1, "Mädchen 70 bis 80."), _sheet(2, "Mädchen 60.")]
    sc = aggregate_pupils(hits, q)["scopes"][0]
    assert sc["total"] == 60 and [u["reason"] for u in sc["unclear"]] == ["range"]
    text = render_pupil_totals(aggregate_pupils(hits, q), q)
    assert "70 bis 80" in text and "75" not in text  # the range is kept, not collapsed to its midpoint
    assert "Summe der oben aufgeführten eindeutigen Angaben: 60." in text


def test_pupil_two_figures_of_one_sheet_are_not_summed():
    from archai.qa import aggregate_pupils, render_pupil_totals
    q = "Wie viele Knaben gab es total in Bern?"
    hits = [_sheet(1, "Knaben? 50 bis 60."), _sheet(1, "Knaben? 25 bis 35 und 40.")]  # winter and summer of one sheet
    agg = aggregate_pupils(hits, q)
    sc = agg["scopes"][0]
    assert sc["total"] is None and sc["contributors"] == [] and len(sc["unclear"]) == 1
    assert sc["unclear"][0]["hits"] == [0, 1]  # one school with two answers, not two schools
    text = render_pupil_totals(agg, q)
    assert "50 bis 60" in text and "25 bis 35 und 40" in text and "eine eindeutige Zahl" in text


def test_pupil_total_lists_the_sources_it_did_not_use():
    from archai.qa import aggregate_pupils, render_pupil_totals
    q = "Wie viele Schülerinnen gab es total in Bern?"
    hits = [_sheet(1, "Mädchen 12."), _sheet(2, "Diese Schul wurde sonst von 100 bis 130 Kindern besucht, darunter waren")]
    agg = aggregate_pupils(hits, q)
    assert agg["scopes"][0]["total"] == 12 and [s["label"] for s in agg["scopes"][0]["silent"]] == [hits[1]["text"].split(" 1799.")[0]]
    text = render_pupil_totals(agg, q)
    assert "Ohne Zahl für Mädchen (kein Beitrag): Bern, Testquartier (Lehrer: Test) [2]" in text
    assert "100" not in text  # a school total without a sex is not counted as girls


def test_pupil_list_and_total_agree_and_the_total_is_audited():
    from archai.qa import aggregate_pupils, check_numbers, render_pupil_totals
    q = "Wie viele Schülerinnen gab es total in Bern?"
    hits = [_sheet(1, "Mädchen 12."), _sheet(2, "Mädchen 30."), _sheet(3, "Knaben 99.")]
    agg = aggregate_pupils(hits, q)
    sc = agg["scopes"][0]
    assert sc["total"] == sum(c["value"] for c in sc["contributors"]) == 42
    text = render_pupil_totals(agg, q)
    assert "12 + 30 = 42" in text
    assert sorted({i for c in sc["contributors"] for i in c["hits"]}) == [0, 1]
    assert check_numbers(text, hits, q, extra=agg["extra"]) == []
    # the sum itself is in no source: it passes only against the audited addends, and the model never gets them
    assert [f["number"] for f in check_numbers(text, hits, q)] == ["42"]


def test_pupil_aggregate_reads_answers_not_the_questionnaire_header():
    from archai.qa import aggregate_pupils
    hits = [_sheet(1, "Knaben 30.", qno="III.12.a", question="Im Winter. (Knaben/Mädchen)")]
    agg = aggregate_pupils(hits, "Wie viele Kinder gab es total in Bern?")
    assert {s["sex"]: s["total"] for s in agg["scopes"]} == {"female": None, "male": 30}


class _PupilIndex:
    """Stand-in for qa.Index: search() routes, pool() is the whole routed pool (adds no retrieval of its own)."""

    def __init__(self, hits):
        self.hits = hits

    def search(self, q, k, mode):
        return self.hits[:k], {"intents": ["pupils"], "places": ["bern"], "filtered_pool": len(self.hits)}

    def pool(self, r):
        return self.hits


def test_answer_uses_the_deterministic_aggregate_for_a_pupil_count_question():
    from archai.qa import answer
    hits = [_sheet(1, "Mädchen 12."), _sheet(2, "Mädchen 30.")]
    out = answer(_PupilIndex(hits), "Wie viele Schülerinnen gab es total in Bern?", use_llm=False)
    assert out["answer_source"] == "aggregate" and out["number_check"] == {"ok": True, "failures": []}
    assert out["sources"] is hits and "12 + 30 = 42" in out["answer"] and out["cited"] == [1, 2]
    # a question that only mentions children keeps the normal answer path
    assert answer(_PupilIndex(hits), "Wie viele Stunden waren die Kinder in der Schule?", use_llm=False)["answer_source"] == "extractive"
