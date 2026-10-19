"""The data-question engine (archai.dataq): plan validation, selection, the reader's checks, the counting policy and
the computation. Records and sheet texts are real (data/out); Apertus is replaced by fixed replies where it is needed."""
import json
from collections import Counter

import pytest

from archai import dataq, llm, qa

BERN_1799 = [f"stapfer:transkription:{i}" for i in (1077, 1078, 1079, 1080, 1081, 1082, 1083, 1084, 1087)]
BERN_TODAY = ["stapfer:transkription:1300", "stapfer:transkription:1301"]  # Bümpliz, Oberbottigen


class _Idx:
    """The parts of qa.Index the engine uses, over the real passages of the Bern sheets and all consultations."""

    def __init__(self):
        keep = set(BERN_1799 + BERN_TODAY)
        self.P = [p for p in qa.passages() if p["doc_id"] in keep or p["kind"] == "consultation"]
        self.gaz = sorted(set().union(*[p["places"] for p in self.P]) - {""})
        self.catalogue = qa.questionnaire()

    def _hit(self, i, score=None):
        return {k: v for k, v in self.P[i].items() if k != "places"} | {"score": score}


@pytest.fixture(scope="module")
def idx():
    return _Idx()


def test_plan_keeps_only_values_that_exist(idx):
    plan = dataq.validate_plan({"dataset": "stapfer", "unit": "schools", "operation": "sum",
                                "filters": {"places": ["Bern", "Atlantis"], "districts": ["Nowhere"], "school_types": ["Mädchenschulen"]},
                                "measure": {"field": "read", "sections": ["III.12.b", "IX.99"], "what": "pupils", "value": "number"}}, idx)
    assert plan["filters"]["places"] == ["bern"] and plan["filters"]["school_types"] == ["Mädchenschule"]
    assert "districts" not in plan["filters"] and "place Atlantis" in plan["dropped"]
    assert plan["measure"]["sections"] == ["III.12"]  # the parent question: the reader sees winter and summer
    # a sum needs a number to add: without a measure it is a count
    assert dataq.validate_plan({"dataset": "stapfer", "operation": "sum", "filters": {}}, idx)["operation"] == "count"
    assert dataq.validate_plan({"dataset": "weather"}, idx) is None


def test_select_keeps_the_1799_place_apart_from_todays_municipality():
    main, today = dataq.select({"dataset": "stapfer", "unit": "schools", "filters": {"places": ["bern"]}})
    assert [u["rec"]["id"] for u in main] == BERN_1799 and [u["rec"]["id"] for u in today] == BERN_TODAY


def test_school_count_needs_no_model_and_cites_every_sheet(idx):
    r = {"places": ["bern"], "intents": []}
    out = dataq.answer(idx, "how many schools are there in the city of bern in 1799 ?", r, use_llm=False)
    assert out["answer_source"] == "aggregate" and out["number_check"]["ok"]
    assert "Result: 9 schools" in out["answer"] and len(out["sources"]) == 11  # 9 counted, 2 listed apart
    assert "Bümpliz" in out["answer"].split("Result: 9 schools")[1]


def test_grouped_count_matches_the_metadata(idx):
    plan = dataq.validate_plan({"dataset": "consultation", "unit": "consultations", "operation": "count",
                                "filters": {"years": [1984, 1984]}, "group_by": "department"}, idx)
    text, A = dataq.render("How many consultations did each department run in 1984?", plan, *dataq.select(plan), {}, idx)
    want = Counter()
    for line in open("data/out/consultations.jsonl", encoding="utf-8"):
        x = json.loads(line)
        if x["year"] == 1984:
            want[x["department_abbr"].split("\n")[0].strip()] += 1
    for dep, n in want.items():
        assert f"- {dep}: {n} consultation" in text
    assert qa.check_numbers(text, A.sources, "", extra=A.extra) == []


SHEET = "Knaben? – 100 und mehr Mädchen? – 70 bis 80. und mehr."
CTX = [("III.12.a", "Im Winter. (Knaben/Mädchen) En hiver, soit garçons, soit filles?", SHEET),
       ("III.12.b", "Im Sommer. (Knaben/Mädchen) En été, soit garçons, soit filles?", "Knaben? 50 bis 60. Mädchen? 25 bis 35 und 40.")]
TEXT = SHEET + "\nKnaben? 50 bis 60. Mädchen? 25 bis 35 und 40."


def _fig(quote, lo, hi=None, **kw):
    return {"quote": quote, "min": lo, "max": lo if hi is None else hi, "more": False, "sex": None, "season": None,
            "current": True, "asked": True, "unit": None, "label": "pupils"} | kw


def test_reader_figures_are_checked_against_the_sheet():
    f, why = dataq.verify_figure(_fig("Knaben? 120", 120), "number", TEXT, CTX)
    assert f is None and "not in the sheet" in why
    f, why = dataq.verify_figure(_fig("Knaben? 50 bis 60.", 55), "number", TEXT, CTX)
    assert f is None and "holds [50, 60]" in why  # the code tells Apertus what the quote really holds
    f, _ = dataq.verify_figure(_fig("Mädchen? – 70 bis 80. und mehr.", 70, more=True, sex="all"), "number", TEXT, CTX)
    assert (f["lo"], f["hi"], f["more"], f["sex"], f["season"]) == (70, 80, True, "girls", "winter")  # range, sex, season from the text
    f, _ = dataq.verify_figure(_fig("Knaben? 50 bis 60.", 50, 60, more=True), "number", TEXT, CTX)
    assert not f["more"] and f["season"] == "summer"  # "and more" only when the quote says so
    f, why = dataq.verify_figure(_fig("Knaben? 50 bis 60.", 50, 60, sex="girls"), "number", TEXT, CTX)
    assert f is None and "other sex" in why


def test_catechism_and_ages_do_not_count_as_pupils():
    text = "im Winter besuchen die Schule heür Kaben 90 Mägdchen 85 175. Zur unterweisung Zum Heil: abendmahl knaben 29. mägdchen 35 64."
    ctx = [("III.12.a", "Im Winter. (Knaben/Mädchen)", text)]
    f, why = dataq.verify_figure(_fig("Kaben 90 Mägdchen 85", 85, 90, sex="girls"), "number", text, ctx)
    assert f is None and "two figures" in why  # boys' 90 and girls' 85 are no range "85 to 90"
    f, _ = dataq.verify_figure(_fig("Kaben 90 Mägdchen 85", 90, sex="boys"), "number", text, ctx)
    assert f["sex"] == "boys"  # a quote with both figures keeps the label ("Kaben" as written)
    f, _ = dataq.verify_figure(_fig("knaben 29.", 29, sex="boys"), "number", text, ctx)
    assert f["asked"] is False
    f, _ = dataq.verify_figure(_fig("Mägdchen 85", 85, sex="girls"), "number", text, ctx)
    assert f["asked"] is True
    ages = "Les Enfans de 4 à 12 ans fréquentent l'école; ils sont 80."
    f, _ = dataq.verify_figure(_fig("de 4 à 12", 4, 12, sex="all"), "number", ages, [("III.12", "Ecoliers.", ages)])
    assert f["asked"] is False


def _figs(*specs):
    return [dataq.verify_figure(_fig(q, lo, hi, **kw), "number", TEXT, CTX)[0] for q, lo, hi, kw in specs]


def test_counting_policy_one_season_and_both_sexes():
    figs = _figs(("Knaben? – 100 und mehr", 100, None, {"more": True}), ("Mädchen? – 70 bis 80. und mehr.", 70, 80, {"more": True}),
                 ("Knaben? 50 bis 60.", 50, 60, {}), ("Mädchen? 25 bis 35 und 40.", 25, 40, {}))
    v, out = dataq.resolve(figs, None, None)
    assert dataq._num(v) == (170, 180, True, None)  # winter boys + winter girls, never the summer again
    assert {why for _f, why in out} == {"winter figure used"}
    v, _ = dataq.resolve(figs, "female", None)
    assert dataq._num(v) == (70, 80, True, None)


def test_counting_policy_uses_the_school_type_and_flags_gaps():
    girls = dataq.verify_figure(_fig("Mädchen? – 70 bis 80. und mehr.", 70, 80), "number", TEXT, CTX)[0]
    assert dataq._num(dataq.resolve([girls], None, "female")[0])[:2] == (70, 80)  # a girls' school: complete
    assert dataq._num(dataq.resolve([girls], None, None)[0]) is None  # girls only at a mixed school: incomplete
    assert dataq._num(dataq.resolve([], "male", "female")[0])[:2] == (0, 0)  # no boys at a girls' school


def test_condition_on_a_range_can_stay_undecided():
    v = {"parts": [{"lo": 96, "hi": 110, "more": False, "unit": None}], "complete": True}
    assert dataq.test(v, {"op": ">", "value": 100}) is None
    assert dataq.test(v, {"op": ">", "value": 90}) is True and dataq.test(v, {"op": ">", "value": 120}) is False


def test_money_is_never_added_across_currencies():
    plan = {"measure": {"field": "read", "value": "money"}, "operation": "sum"}
    mk = lambda rid, amount, unit: ({"rec": {"id": rid}, "teacher": None},
                                    {"value": {"parts": [{"lo": amount, "hi": amount, "more": False, "unit": unit}], "complete": True}})
    units, readings = zip(*[mk("a", 50, "Kronen"), mk("b", 30, "Kronen"), mk("c", 150, "francs")], strict=False)
    res = dataq.compute(list(units), plan, {u["rec"]["id"]: rd for u, rd in zip(units, readings, strict=False)})
    assert {k: v["lo"] for k, v in res["result"].items()} == {("Kronen", None): 80, ("francs", None): 150}


PAY = ("a. an Geld fronfästlich 32 kr. aus den ehmaligen Sekelschreiberey, und jährlich 12 kr. als Gartengeld. b. an "
       "Getreide fronfästlich 5 1/4 Mütt Korn von der Stifft. An Geld. kr. 200. und für einen Gehülfen kr. 50.")


def test_pay_is_kept_apart_by_period_and_kind_and_assistants_are_left_out():
    ctx = [("IV.16.A", "Einkommen des Schullehrers", PAY)]
    q32, y12, mutt, kr200, gehulfe = (dataq.verify_figure(_fig(q, n, unit=u), "money", PAY, ctx)[0] for q, n, u in (
        ("fronfästlich 32 kr.", 32, "kr"), ("jährlich 12 kr.", 12, "kr"), ("5 1/4 Mütt", 5, "Mütt"), ("kr. 200.", 200, "kr"), ("kr. 50.", 50, "kr")))
    assert (q32["period"], y12["period"], mutt["period"]) == ("quarter", "year", "quarter")  # from the words, not the model
    assert gehulfe["asked"] is False and kr200["asked"] is True  # "für einen Gehülfen": not the teacher's own pay
    bw = "An Geld. Jährlich 200 kr. frohnfästlich mit 50. kr. von der Verwaltungskammer, an Holz, 4 Klafter tannenes."
    y200, q50, wood = (dataq.verify_figure(_fig(q, n, unit=u), "money", bw, [("IV.16.A", "Einkommen", bw)])[0] for q, n, u in (
        ("Jährlich 200 kr. frohnfästlich", 200, "kr"), ("frohnfästlich mit 50. kr.", 50, "kr"), ("4 Klafter tannenes", 4, "Klaffter")))
    assert (y200["period"], q50["period"]) == ("year", "quarter")  # the period word nearest before the number
    assert wood["unit"] == "Klafter"  # the measure as the quote spells it
    v, _ = dataq.resolve([q32, y12, mutt], None, None)
    assert dataq.totals(v) == {("kr", "quarter"): (32, 32, False, "kr", "quarter"), ("kr", "year"): (12, 12, False, "kr", "year"),
                               ("mutt", "quarter"): (5, 5, False, "Mütt", "quarter")}
    assert dataq._num(v) is None  # no single number: kr and Mütt, quarterly and yearly are never added


def test_the_items_of_a_stated_total_are_not_added_again():
    text = "En argent. L. 715; Savoir de l'ancien gouvernement Cent et cinquante [=150] francs."
    ctx = [("IV.16.A", "Einkommen", text)]
    tot = dataq.verify_figure(_fig("L. 715", 715, unit="L"), "money", text, ctx)[0]
    part = dataq.verify_figure(_fig("Cent et cinquante [=150] francs", 150, unit="francs", part=True), "money", text, ctx)[0]
    v, out = dataq.resolve([tot, part], None, None)
    assert dataq._num(v) == (715, 715, False, "L") and [why for _f, why in out] == ["a part of a total stated on the sheet"]


def test_pay_is_listed_per_school_unless_a_total_is_asked(idx):
    obj = {"dataset": "stapfer", "unit": "schools", "filters": {"places": ["Bern"]}, "operation": "sum",
           "measure": {"field": "read", "sections": ["IV.16"], "what": "pay", "value": "money"}}
    assert dataq.validate_plan(obj, idx, "What was the teacher's pay in the schools of Bern?")["operation"] == "list"
    assert dataq.validate_plan(obj, idx, "What did the teachers of Bern earn in total?")["operation"] == "sum"


def test_a_count_of_people_planned_as_a_count_of_schools_is_sent_back(idx, monkeypatch):
    replies = [{"dataset": "stapfer", "unit": "schools", "filters": {"places": ["Bern"]}, "measure": None, "operation": "count"},
               {"dataset": "stapfer", "unit": "schools", "filters": {"places": ["Bern"]}, "operation": "sum",
                "measure": {"field": "read", "sections": ["III.12"], "what": "number of girls", "value": "number", "sex": "female"}}]
    calls = []
    monkeypatch.setattr(llm, "chat_json", lambda prompt, **kw: (calls.append(prompt), (replies[len(calls) - 1], {"errors": []}))[1])
    plan, _meta = dataq.make_plan("How many girls went to school in Bern in 1799?", idx)
    assert len(calls) == 2 and 'counts "girls"' in calls[1]
    assert plan["operation"] == "sum" and plan["measure"]["sex"] == "female" and plan["revised"]


def test_the_planner_can_hand_a_lookup_back(idx, monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda prompt, **kw: ({"task": "lookup"}, {"errors": []}))
    r = {"places": ["bern"], "intents": ["teacher"]}
    assert dataq.answer(idx, "Wer war der Lehrer an der Postgasse in Bern?", r) is None
    assert "lookup" in r["data_engine"]["reason"]


def test_number_words_are_annotated_for_the_reader():
    assert dataq.annotate("zwey und achtzig Lehrkinder, un enfant") == "zwey und achtzig [=82] Lehrkinder, un enfant"
    assert dataq.plain("zwey und achtzig [=82] Lehrkinder") == "zwey und achtzig Lehrkinder"
    assert dataq.annotate("dreyßig Cronen, zehen Klafter") == "dreyßig [=30] Cronen, zehen [=10] Klafter"  # 1799 spellings


def test_reader_gets_one_correction_round(idx, monkeypatch):
    """Apertus misreads "zwey und achtzig" as 28; the code says what the quote holds, the second reading passes."""
    calls = []

    def fake(prompt, schema=None, max_tokens=400, **kw):
        calls.append(prompt)
        n = 28 if len(calls) == 1 else 82
        return {"sheets": [{"sheet": "S1", "figures": [_fig("zwey und achtzig [=82] Lehrkinder", n, sex="all")]}]}, {"errors": []}

    monkeypatch.setattr(llm, "chat_json", fake)
    plan = {"measure": {"field": "read", "sections": ["III.12"], "what": "number of pupils", "value": "number", "sex": None}}
    rd = dataq.read(idx, plan, [dataq.data()["rows"]["stapfer:transkription:1077"]])["stapfer:transkription:1077"]
    assert len(calls) == 2 and "holds [2, 80, 82]" in calls[1] and rd["corrected"]
    assert dataq._num(rd["value"])[:2] == (82, 82)


def test_rules_net_sends_multi_record_questions_to_the_engine():
    r = {"places": ["bern"], "intents": ["pupils"]}  # a place with several schools: the planner decides
    assert dataq.is_data_question("Which schools in Bern had more than 100 pupils?", r)
    assert dataq.is_data_question("What was the teacher's pay in the schools of Bern?", r)
    assert not dataq.is_data_question("Wie alt war der Lehrer in Hörndli?", {"places": ["hornli"], "intents": ["teacher"]})
    assert not dataq.is_data_question("Which teacher taught in Hörndli?", {"places": ["hornli"], "intents": ["teacher"]})  # one sheet
    assert dataq.is_data_question("How many consultations were there in 1984?", {"intents": ["consultation"], "places": []})
    assert not dataq.is_data_question("Which consultation in 1984 dealt with data protection?", {"intents": ["consultation"], "places": []})
    assert dataq.is_data_question("Which consultations in 1984 dealt with data protection?", {"intents": ["consultation"], "places": []})


def test_reader_is_told_which_answer_it_skipped(idx, monkeypatch):
    """Bollwerk: a first reading that lists only the summer answer is sent back, naming the winter answer it skipped."""
    calls = []

    def fake(prompt, schema=None, max_tokens=400, **kw):
        calls.append(prompt)
        figs = [_fig("Knaben? 50 bis 60.", 50, 60), _fig("Mädchen? 25 bis 35 und 40.", 25, 40)]
        if len(calls) > 1:
            figs += [_fig("Knaben? – 100 und mehr", 100, more=True), _fig("Mädchen? – 70 bis 80. und mehr.", 70, 80, more=True),
                     _fig("Nahe bey 300 Kinder bis zur Revolution.", 300, current=False)]
        return {"sheets": [{"sheet": "S1", "figures": figs}]}, {"errors": []}

    monkeypatch.setattr(llm, "chat_json", fake)
    plan = {"measure": {"field": "read", "sections": ["III.12"], "what": "number of pupils", "value": "number", "sex": None}}
    rd = dataq.read(idx, plan, [dataq.data()["rows"]["stapfer:transkription:1084"]])["stapfer:transkription:1084"]
    assert len(calls) == 2 and "you listed no figure from the answer to III.12.a" in calls[1]
    assert dataq._num(rd["value"]) == (170, 180, True, None)  # the winter figures, not the summer ones


def test_an_answer_still_skipped_after_the_correction_round_is_kept_out(idx, monkeypatch):
    def fake(prompt, schema=None, max_tokens=400, **kw):  # lists only the summer answer, twice
        return {"sheets": [{"sheet": "S1", "figures": [_fig("Knaben? 50 bis 60.", 50, 60), _fig("Mädchen? 25 bis 35 und 40.", 25, 40)]}]}, {"errors": []}

    monkeypatch.setattr(llm, "chat_json", fake)
    plan = {"measure": {"field": "read", "sections": ["III.12"], "what": "number of pupils", "value": "number", "sex": None}}
    rd = dataq.read(idx, plan, [dataq.data()["rows"]["stapfer:transkription:1084"]])["stapfer:transkription:1084"]
    assert dataq._num(rd["value"]) is None and any("III.12.a" in b for b in rd["rejected"])


def test_a_second_round_rereads_only_the_sheets_still_left_out(idx, monkeypatch):
    """Bollwerk's winter answer skipped twice, read on the third call; the other sheet is never read again."""
    calls = []
    winter = [_fig("Knaben? – 100 und mehr", 100, more=True), _fig("Mädchen? – 70 bis 80. und mehr.", 70, 80, more=True),
              _fig("Nahe bey 300 Kinder bis zur Revolution.", 300, current=False)]
    summer = [_fig("Knaben? 50 bis 60.", 50, 60), _fig("Mädchen? 25 bis 35 und 40.", 25, 40)]

    def fake(prompt, schema=None, max_tokens=400, **kw):
        calls.append(prompt)
        if len(calls) == 1:  # both sheets; the French school's answers are read fully
            return {"sheets": [{"sheet": "S1", "figures": summer},
                               {"sheet": "S2", "figures": [_fig("De vingt [=20] à vingt-cinq [=25].", 20, 25),
                                                           _fig("quatorze [=14]", 14, sex="boys"), _fig("huit [=8]", 8, sex="girls"),
                                                           _fig("Quinze [=15]", 15, asked=False), _fig("Sept [=7]", 7, asked=False)]}]}, {"errors": []}
        return {"sheets": [{"sheet": "S1", "figures": summer + (winter if len(calls) == 3 else [])}]}, {"errors": []}

    monkeypatch.setattr(llm, "chat_json", fake)
    plan = {"measure": {"field": "read", "sections": ["III.12"], "what": "number of pupils", "value": "number", "sex": None}}
    rows = dataq.data()["rows"]
    out = dataq.read(idx, plan, [rows["stapfer:transkription:1084"], rows["stapfer:transkription:1087"]])
    assert len(calls) == 3 and "S2" not in calls[2].split("Sheets:")[1]
    assert dataq._num(out["stapfer:transkription:1084"]["value"]) == (170, 180, True, None)
    assert dataq._num(out["stapfer:transkription:1087"]["value"]) == (22, 22, False, None)


def test_a_skipped_answer_that_cannot_change_the_value_does_not_block_it(idx, monkeypatch):
    """The French school: the winter answer gives 14 boys and 8 girls; a skipped general answer ("De vingt à
    vingt-cinq") cannot change it under the one-season policy, so the girls stay counted."""
    def fake(prompt, schema=None, max_tokens=400, **kw):
        return {"sheets": [{"sheet": "S1", "figures": [_fig("quatorze [=14]", 14, sex="boys"), _fig("huit [=8]", 8, sex="girls")]}]}, {"errors": []}

    monkeypatch.setattr(llm, "chat_json", fake)
    plan = {"measure": {"field": "read", "sections": ["III.12"], "what": "number of girls", "value": "number", "sex": "female"}}
    rd = dataq.read(idx, plan, [dataq.data()["rows"]["stapfer:transkription:1087"]])["stapfer:transkription:1087"]
    assert dataq._num(rd["value"]) == (8, 8, False, None)


def test_a_quote_is_found_as_whole_words_only():
    text = "a. das einkommen des Schullehrers ist jährlich – 150. kr. b. für Zwey [=2] gehülfen 50 kr."
    f, _ = dataq.verify_figure(_fig("50 kr.", 50, unit="kr"), "money", text, [("IV.16.A", "Einkommen", text)])
    assert f["asked"] is False  # the "50 kr." after "gehülfen", not the end of "150. kr."


def test_aggregate_wording_reaches_the_planner_even_for_a_one_sheet_place():
    r = {"places": ["hinwil"], "intents": ["teacher"]}
    assert dataq.is_data_question("What was the average age of the teachers in the district of Hinwil?", r)


def test_a_lookup_reply_to_a_how_many_question_is_sent_back(idx, monkeypatch):
    replies = [{"task": "lookup"}, {"dataset": "stapfer", "unit": "schools", "filters": {"places": ["Bern"]}, "operation": "sum",
                                    "measure": {"field": "read", "sections": ["III.12"], "what": "girls", "value": "number", "sex": "female"}}]
    calls = []
    monkeypatch.setattr(llm, "chat_json", lambda prompt, **kw: (calls.append(prompt), (replies[len(calls) - 1], {"errors": []}))[1])
    plan, meta = dataq.make_plan("How many girls went to school in Bern in 1799?", idx)
    assert len(calls) == 2 and "how many" in calls[1] and plan["measure"]["sex"] == "female" and not meta.get("lookup")


def test_a_failed_reader_call_is_retried_one_sheet_at_a_time(idx, monkeypatch):
    """A call over several sheets that times out (the gateway's 504) loses no sheet."""
    def fake(prompt, schema=None, max_tokens=400, **kw):
        if "[S2]" in prompt:
            return None, {"errors": ["HTTPError: HTTP Error 504: Gateway Time-out"]}
        return {"sheets": [{"sheet": "S1", "figures": [_fig("zwey und achtzig [=82] Lehrkinder", 82, sex="all")]}]}, {"errors": []}

    monkeypatch.setattr(llm, "chat_json", fake)
    plan = {"measure": {"field": "read", "sections": ["III.12"], "what": "number of pupils", "value": "number", "sex": None}}
    rows = dataq.data()["rows"]
    out = dataq.read(idx, plan, [rows["stapfer:transkription:1077"], rows["stapfer:transkription:1084"]])
    assert set(out) == {"stapfer:transkription:1077", "stapfer:transkription:1084"}


def test_past_or_present_is_read_from_the_figures_own_answer():
    """Bollwerk: "bis zur Revolution" in III.12 does not make the winter answer of III.12.a a past figure."""
    head = "Nahe bey 300 Kinder bis zur Revolution. denn seit der Revolution hat die Anzahl der Schulkinder beträchtlich abgenommen."
    ctx = [("III.12", "Schulkinder.", head)] + CTX
    text = head + "\n" + TEXT
    f, _ = dataq.verify_figure(_fig("Knaben? – 100 und mehr", 100, more=True, current=False), "number", text, ctx)
    assert f["current"] is True
    f, _ = dataq.verify_figure(_fig("Nahe bey 300 Kinder", 300, current=True), "number", text, ctx)
    assert f["current"] is False


def test_a_quote_with_unit_and_number_swapped_is_found_in_the_sheets_order():
    text = "An Geld. kr. 200. und für einen Gehülfen kr. 50."
    f, _ = dataq.verify_figure(_fig("200 kr.", 200, unit="kr"), "money", text, [("IV.16.A", "Einkommen", text)])
    assert f and f["quote"] == "kr. 200." and f["asked"] is True


def test_the_engine_steps_aside_when_no_record_has_a_value(idx, monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda prompt, **kw: (
        {"dataset": "stapfer", "unit": "schools", "filters": {"places": ["Bern"]}, "operation": "sum",
         "measure": {"field": "read", "sections": ["III.12"], "what": "pupils", "value": "number"}} if "plan how to answer" in prompt
        else {"sheets": []}, {"errors": []}))
    r = {"places": ["bern"], "intents": ["pupils"]}
    assert dataq.answer(idx, "How many pupils were in the schools of Bern?", r) is None
    assert r["data_engine"]["reason"] == "no record has a usable value"


def test_in_a_quote_naming_both_sexes_the_word_before_the_number_decides():
    text = "Knaben 17. Mägdlein 60. denn Knaben werden so bald sie gut Lesen können in die obern Schulen gethan."
    ctx = [("III.12.a", "Im Winter. (Knaben/Mädchen)", text)]
    f, _ = dataq.verify_figure(_fig("Knaben 17. Mägdlein 60.", 17, sex="girls"), "number", text, ctx)
    g, _ = dataq.verify_figure(_fig("Knaben 17. Mägdlein 60.", 60, sex="boys"), "number", text, ctx)
    assert (f["sex"], g["sex"]) == ("boys", "girls")


def test_a_number_after_the_last_sexed_figure_is_the_sheets_total():
    text = "im Winter besuchen die Schule heür Kaben 90 Mägdchen 85 175. Zur unterweisung Zum Heil: abendmahl knaben 29."
    ctx = [("III.12.a", "Im Winter. (Knaben/Mädchen)", text)]
    q = "Kaben 90 Mägdchen 85 175."
    t, _ = dataq.verify_figure(_fig(q, 175, sex="girls"), "number", text, ctx)
    f, _ = dataq.verify_figure(_fig(q, 85, sex="all"), "number", text, ctx)
    assert (t["sex"], f["sex"]) == ("all", "girls")
