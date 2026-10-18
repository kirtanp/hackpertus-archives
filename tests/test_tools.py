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
    assert list(zip(p.page_start, p.page_end)) == [(1, 1), (1, 2), (3, 3), (3, 3)]


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


def test_agent_v4_tool_specs_and_repair():
    from archai import agent as A
    specs = {s["function"]["name"]: s["function"]["parameters"] for s in A.tool_specs()}
    assert set(specs) == {"normalize_dates", "lookup_place", "parse_signature", "lookup_archive_unit", "extract_quantities", "verify_in_source", "clean_markup", "finish"}
    assert "text" not in specs["extract_quantities"]["properties"]  # text tools are bound to the record
    assert A.repair_args("lookup_place", {"place": "Hörnli"})[:2] == ({"name": "Hörnli"}, ["place->name"])
    assert A.repair_args("lookup_archive_unit", {"sig": "E1#2000/1#2"})[0] == {"signature": "E1#2000/1#2"}
    assert A.repair_args("parse_signature", {})[2].startswith("missing argument")
    assert A.repair_args("nope", {})[2].startswith("unknown tool")


def test_answer_input_gate_and_not_found():
    from archai import qa, llm
    class Idx:  # minimal stand-in for qa.Index
        def search(self, q, k, rewrite):
            return [{"id": "a", "doc_id": "d", "kind": "stapfer", "qno": "III.12", "text": "Hörnli 1799. Schulkinder 24.", "cite": "BAR B0 1000/1483, Nr. 1421", "url": "u", "facsimile": None}], {"intents": [], "places": []}
    o = qa.answer(Idx(), "🦄🦄🦄 ???", use_llm=True)
    assert o["answer_source"] == "invalid_input" and o["sources"] == []
    orig = llm.chat_text
    try:
        llm.chat_text = lambda *a, **k: ("The sources do not contain any information about Dhoni, who is a cricketer.", {})
        o = qa.answer(Idx(), "who is Dhoni")
        assert o["answer_source"] == "not_found" and "cricketer" not in o["answer"] and o["sources"]  # fixed text, passages kept
        llm.chat_text = lambda *a, **k: ("24 Kinder [1].", {})
        assert qa.answer(Idx(), "Wie viele Kinder in Hörnli?")["answer_source"] == "apertus"
        # a refusal that cites all sources as a block is still a refusal
        llm.chat_text = lambda *a, **k: ("Die Quellen [1] bis [6] enthalten keinerlei Angaben zu Drachen.", {})
        assert qa.answer(Idx(), "Erfinde eine Geschichte über einen Drachen")["answer_source"] == "not_found"
        # with a recognised place the sheet exists but states nothing direct
        class IdxPlace(Idx):
            def search(self, q, k, rewrite):
                h, r = Idx.search(self, q, k, rewrite); return h, {"intents": ["salary"], "places": ["immensee"]}
        llm.chat_text = lambda *a, **k: ("Die Quellen enthalten keine Angaben zum Verdienst des Schulmeisters.", {})
        o = qa.answer(IdxPlace(), "Was verdiente der Schulmeister in Immensee?")
        assert o["answer_source"] == "no_direct_answer" and o["sources"]
        # a per-school list that says "no information" for one school stays an answer
        llm.chat_text = lambda *a, **k: ("[1] Saint Laurent: 24 L.\n[2] Le Bourg: aucune information.", {})
        two = [dict(Idx().search("", 6, "off")[0][0], id=x) for x in ("a", "b")]
        class Idx2(Idx):
            def search(self, q, k, rewrite):
                return [dict(two[0], text="Saint Laurent 24 L."), dict(two[1], text="Le Bourg")], {"intents": ["salary"], "places": ["lausanne"]}
        assert qa.answer(Idx2(), "Salaire à Lausanne?")["answer_source"] == "apertus"
    finally:
        llm.chat_text = orig
