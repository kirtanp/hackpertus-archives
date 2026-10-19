"""Data questions: answers no single passage states, because they count, sum, average, compare or list over many
records ("how many schools in Bern?", "how many pupils in Bern?", "average age of the teachers in Hinwil?", "which
schools in Lausanne teach Latin?", "how many consultations per department in 1984?"). One general engine, Apertus at
every step and code checking each:

1. plan: Apertus turns the question into a data plan over the fields that really exist (metadata vocabulary and the
   questionnaire): dataset, unit, filters, measure, operation, condition and grouping.
   Code validates every value against the values that occur in the data and drops what does not exist.
2. select: code selects the records from the metadata. A 1799 place and today's municipality of that name are kept
   apart (Bümpliz is in today's Bern, but was a village of its own in 1799).
3. read: a measure that only the answer text holds (pupils, pay, subjects, books) is read by Apertus from the chosen
   questions of every selected record. Apertus lists and labels every figure (quote, sex, season, current or past,
   asked group or another); code keeps a figure only if the quote is in the record and the number in the quote, and
   code applies the counting policy (resolve): one season, never another group or the past. Metadata measures
   (teacher age) need no reading.
4. compute: code counts, sums, averages, takes min/max, applies the condition and groups. Ranges stay ranges, money is
   never added across currencies, and the model never calculates (qa.PROMPT).
5. answer: Apertus phrases the computed result; qa.check_numbers rejects any number that is neither in a cited source
   nor computed here. The computed listing stays below it as the audit trail.
"""
import difflib
import json
import re
from concurrent.futures import ThreadPoolExecutor

from . import llm
from .qa import _FEMALE_SEX, _MALE_SEX, FREE, OUT, _answer_body, _ascii, _language, _nums, _valid_places, _valid_sections, check_numbers
from .tools import NOT_PUPILS_AFTER, NUMW_RUN, number_run_value, word_numbers

# Apertus 1.5 serves a 262,144-token context (gateway /models "max_context"): a reader call gets whole answers and the
# school's metadata, packed by size rather than cut at a fixed length; output per sheet is what bounds a call
MAX_READ = 120  # records Apertus reads for one answer
CHUNK = 3  # sheets per reader call: output (~60 tokens a figure) bounds a call, and 8 sheets ran past the gateway's
# time limit (504); a failed call is retried one sheet at a time
CHUNK_CHARS = 60_000  # input per reader call (~20k tokens), far inside the window, so long prose answers stay whole
SHEET_CHARS = 6_000  # one sheet's chosen answers, whole for all but the longest prose sheets
WORKERS = 6
ROUNDS = 2  # correction rounds; the second re-reads only the sheets the first left incomplete
MAX_LISTED = 40  # units listed (and cited) in the audit text; the computation always uses all of them
OPS = ("count", "sum", "average", "min", "max", "list")
COND = (">", ">=", "<", "<=", "=", "yes", "no")
VALUE_TYPES = ("number", "money", "yes_no", "text")
GROUPS = {"stapfer": ("place", "district", "school_type", "confession", "language", "teacher_gender", "value"),
          "consultation": ("year", "department", "type", "value")}
_NONE = re.compile(r"\b(?:keine?|keins|kein|nichts|aucune?|point|nul(?:le)?|rien|none)\b")
# a figure from the past, said in its own answer: "sonst von 100 bis 130", "Nahe bey 300 Kinder bis zur Revolution"
_PAST = re.compile(r"\bsonst\b|vormals|ehemals|ehmals|ehedem|fruher|bis zur revolution|vor der revolution|"
                   r"autrefois|anciennement|jadis|ci.devant|auparavant|avant la revolution")
_DATA = {}


# ---------------------------------------------------------------- the records and their vocabulary
def data():
    """Every record as one row of metadata, plus the value sets the planner may choose from."""
    if "d" in _DATA:
        return _DATA["d"]
    rows = {}
    for line in open(OUT / "stapfer_documents.jsonl", encoding="utf-8"):
        x = json.loads(line)
        schools = x.get("schools") or []
        names = list(dict.fromkeys(s["name"] for s in schools if s.get("name")))
        types = list(dict.fromkeys(s["type"] for s in schools if s.get("type")))
        rows[x["doc_id"]] = {
            "id": x["doc_id"], "dataset": "stapfer", "language": x.get("language"),
            "p1799": {_ascii(p["name_1799"]) for p in x["places"]},
            "today": {_ascii(p["municipality_today"] or "") for p in x["places"]} - {""},
            "names": {_ascii(p["name_1799"]): p["name_1799"] for p in x["places"]}
                     | {_ascii(p["municipality_today"]): p["municipality_today"] for p in x["places"] if p.get("municipality_today")},
            "place": [p["name_1799"] for p in x["places"]], "district": [p["district_today"] for p in x["places"] if p.get("district_today")],
            "confession": list(dict.fromkeys(p["confession"] for p in x["places"] if p.get("confession"))),
            "school_type": types, "school_ids": [s.get("school_id") for s in schools], "teachers": x.get("teachers") or [],
            "label": "; ".join(names) + (f" ({', '.join(types)})" if types else "") or ", ".join(p["name_1799"] for p in x["places"])}
    for line in open(OUT / "consultations.jsonl", encoding="utf-8"):
        x = json.loads(line)
        rows[x["doc_id"]] = {"id": x["doc_id"], "dataset": "consultation", "title": x["title"],
                             "year": int(x["year"]) if str(x.get("year") or "").isdigit() else None,
                             "department": [d.strip() for d in (x.get("department_abbr") or "").split("\n") if d.strip()],
                             "type": [x["type"]] if x.get("type") else [], "label": f'{x["title"]} ({x.get("year")})',
                             "search": _ascii(" ".join([x["title"]] + [a.get("olr", {}).get("title") or "" for a in x.get("archive_refs") or []]))}
    st = [r for r in rows.values() if r["dataset"] == "stapfer"]
    co = [r for r in rows.values() if r["dataset"] == "consultation"]
    vocab = {"district": sorted({d for r in st for d in r["district"]}), "school_type": sorted({t for r in st for t in r["school_type"]}),
             "confession": sorted({c for r in st for c in r["confession"]}), "language": sorted({r["language"] for r in st if r["language"]}),
             "teacher_gender": sorted({t["gender"] for r in st for t in r["teachers"] if t.get("gender")}),
             "department": sorted({d for r in co for d in r["department"]}), "type": sorted({t for r in co for t in r["type"]})}
    _DATA["d"] = {"rows": rows, "vocab": vocab, "n": {"stapfer": len(st), "consultation": len(co)}}
    return _DATA["d"]


def _by_doc(idx):
    if getattr(idx, "_by_doc", None) is None:
        m = {}
        for i, p in enumerate(idx.P):
            m.setdefault(p["doc_id"], []).append(i)
        idx._by_doc = m
    return idx._by_doc


# ---------------------------------------------------------------- step 1: Apertus plans, code validates
PLAN = """You plan how to answer a question that needs counting or computing over many archive records. Use only these fields.
Dataset "stapfer": {n_st} answered questionnaires of the 1799 Stapfer school survey, one per school.
  unit "schools" (one per questionnaire) or "teachers" (metadata: gender {teacher_gender}; age)
  filters: places (as named in the question), districts (today's: {district}), school_types ({school_type}),
           confessions ({confession}), languages ({language})
  measure: "teacher_age" comes from the metadata; any other value (pupils, pay, subjects, books, schoolhouse, ...) is
           "read" from the answers: give the questionnaire sections that hold it and say what to read.
Dataset "consultation": {n_co} Swiss federal consultations 1960-1991. unit "consultations"; filters: years [from, to],
  departments ({department}), types ({type}), title_words (German words of the title or its dossiers, with synonyms:
  a consultation matches if ANY of them occurs, e.g. ["Datenschutz", "Personendaten"]); no reading.
Questionnaire (number: German / French):
{catalogue}
A question about one fact of one record ("Who was the teacher in Hörndli?", "Which consultation in 1984 dealt with
data protection?") is no data question: reply {{"task": "lookup"}}. Otherwise reply with JSON only:
{{"dataset": "stapfer" | "consultation", "unit": "schools" | "teachers" | "consultations",
 "filters": {{"places": [], "districts": [], "school_types": [], "confessions": [], "languages": [], "teacher_gender": null,
             "years": null, "departments": [], "types": [], "title_words": []}},
 "measure": null or {{"field": "teacher_age" | "read", "sections": ["question numbers"], "what": "the value to read, in English",
             "value": "number" | "money" | "yes_no" | "text", "sex": null | "male" | "female"}},
 "operation": "count" | "sum" | "average" | "min" | "max" | "list" ("sum" only when the question asks for a total; "what was X in
              the schools of a town" lists X per school),
 "condition": null or {{"op": ">" | ">=" | "<" | "<=" | "=" | "yes" | "no", "value": number or null}},
 "group_by": null | "place" | "district" | "school_type" | "confession" | "language" | "teacher_gender" | "year" | "department" | "type" | "value"}}
Examples:
"How many schools were there in Bern?" -> {{"dataset": "stapfer", "unit": "schools", "filters": {{"places": ["Bern"]}}, "measure": null, "operation": "count"}}
"How many pupils were in the schools of Bern?" -> {{"dataset": "stapfer", "unit": "schools", "filters": {{"places": ["Bern"]}}, "measure": {{"field": "read", "sections": ["III.12"], "what": "number of pupils", "value": "number", "sex": null}}, "operation": "sum"}}
"How many girls went to school in Bern?" -> the same with "what": "number of girls", "sex": "female"
"What was the teacher's pay in Bern?" -> {{"dataset": "stapfer", "unit": "schools", "filters": {{"places": ["Bern"]}}, "measure": {{"field": "read", "sections": ["IV.16"], "what": "the teacher's own pay, in money and in kind", "value": "money", "sex": null}}, "operation": "list"}}
"Average age of the teachers in Hinwil?" -> {{"dataset": "stapfer", "unit": "teachers", "filters": {{"districts": ["Hinwil"]}}, "measure": {{"field": "teacher_age"}}, "operation": "average"}}
"Which schools in Lausanne teach Latin?" -> {{"dataset": "stapfer", "unit": "schools", "filters": {{"places": ["Lausanne"]}}, "measure": {{"field": "read", "sections": ["II.5"], "what": "Latin is taught", "value": "yes_no"}}, "operation": "list", "condition": {{"op": "yes"}}}}
"How many consultations did each department run in 1984?" -> {{"dataset": "consultation", "unit": "consultations", "filters": {{"years": [1984, 1984]}}, "measure": null, "operation": "count", "group_by": "department"}}
Question: {q}"""


def _pick(raw, vocab, cutoff=0.8):
    """Values of a filter that exist in the data (exact, case-insensitive or a close spelling); the rest is dropped."""
    out, low = [], {_ascii(v): v for v in vocab}
    for x in raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []:
        a = _ascii(str(x).strip())
        hit = low.get(a) or next((low[k] for k in difflib.get_close_matches(a, list(low), n=1, cutoff=cutoff)), None)
        if hit:
            out.append(hit)
    return list(dict.fromkeys(out))


_TOTAL = re.compile(r"\btotal\b|insgesamt|zusammen|gesamt|en tout|au total|\bsum(?:me)?\b|altogether|in all\b")


def validate_plan(obj, idx, q=""):
    """The plan with every value checked against the data, and what was dropped; None when nothing usable is left."""
    if not isinstance(obj, dict):
        return None
    D, V = data(), data()["vocab"]
    ds = obj.get("dataset") if obj.get("dataset") in ("stapfer", "consultation") else None
    if not ds:
        return None
    f = obj.get("filters") if isinstance(obj.get("filters"), dict) else {}
    plan = {"dataset": ds, "unit": "teachers" if ds == "stapfer" and obj.get("unit") == "teachers" else "consultations" if ds == "consultation" else "schools",
            "filters": {}, "dropped": []}
    if ds == "stapfer":
        places, unknown = _valid_places(f.get("places"), idx.gaz)
        plan["filters"]["places"] = places
        plan["dropped"] += [f"place {u}" for u in unknown]
        for k, vk in (("districts", "district"), ("school_types", "school_type"), ("confessions", "confession"), ("languages", "language")):
            got = _pick(f.get(k), V[vk])
            if got:
                plan["filters"][k] = got
            elif f.get(k):
                plan["dropped"].append(f"{k} {f.get(k)}")
        g = _pick(f.get("teacher_gender"), V["teacher_gender"]) or _pick({"male": "Mann", "female": "Frau"}.get(str(f.get("teacher_gender")).lower()), V["teacher_gender"])
        if g:
            plan["filters"]["teacher_gender"] = g
    else:
        y = f.get("years")
        if isinstance(y, list) and y and all(str(v).isdigit() for v in y[:2]):
            ys = sorted(int(v) for v in y[:2])
            if 1960 <= ys[-1] and ys[0] <= 1991:
                plan["filters"]["years"] = [ys[0], ys[-1]]
        for k, vk in (("departments", "department"), ("types", "type")):
            got = _pick(f.get(k), V[vk], cutoff=0.9)
            if got:
                plan["filters"][k] = got
        tw = [w.strip() for w in f.get("title_words") or [] if isinstance(w, str) and len(w.strip()) >= 3][:6]
        if tw:
            plan["filters"]["title_words"] = tw
    m = obj.get("measure") if isinstance(obj.get("measure"), dict) else None
    if m and ds == "stapfer":
        if m.get("field") == "teacher_age":
            plan["measure"] = {"field": "teacher_age", "value": "number", "what": "teacher age"}
            plan["unit"] = "teachers"
        else:
            # the parent question with all its sub-questions: "III.12.b" alone would hide the winter answer
            secs = list(dict.fromkeys(".".join(x.split(".")[:2]) for x in _valid_sections(m.get("sections"), idx.catalogue)))
            if not secs:
                plan["dropped"].append(f"measure without a questionnaire section: {m.get('sections')}")
                return None
            plan["measure"] = {"field": "read", "sections": secs, "what": str(m.get("what") or "the value asked for")[:120],
                               "value": m.get("value") if m.get("value") in VALUE_TYPES else "number",
                               "sex": m.get("sex") if m.get("sex") in ("male", "female") else None}
    else:
        plan["measure"] = None
    op = obj.get("operation") if obj.get("operation") in OPS else "count"
    if op in ("sum", "average", "min", "max") and not (plan["measure"] and plan["measure"]["value"] in ("number", "money")):
        op = "list" if plan["measure"] else "count"
    if op == "sum" and plan["measure"]["value"] == "money" and not _TOTAL.search(_ascii(q)):
        op = "list"  # "what was the pay in Bern" asks each school's pay; a sum of pay over schools only when asked
    plan["operation"] = op
    c = obj.get("condition") if isinstance(obj.get("condition"), dict) else None
    if c and c.get("op") in COND and plan["measure"]:
        num = c.get("value")
        if c["op"] in ("yes", "no") or isinstance(num, (int, float)):
            plan["condition"] = {"op": c["op"], "value": num if c["op"] not in ("yes", "no") else None}
    gb = obj.get("group_by")
    plan["group_by"] = gb if gb in GROUPS[ds] and (gb != "value" or plan["measure"]) else None
    plan["n_records"] = D["n"][ds]
    return plan


_COUNTED = re.compile(r"(?:wie\s*viele?|wieviele?|how\s*many|combien\s+(?:de\s+|d.)?|anzahl\s+(?:der\s+)?|number\s+of)\W*(\w+)")


def plan_problems(q, plan):
    """What code can tell is wrong with a plan, for Apertus to revise: a count of people planned as a count of records."""
    from .qa import _PUPIL_WORD
    t = _ascii(q)
    m = _COUNTED.search(t)
    out = []
    if plan and m and _PUPIL_WORD.search(m.group(1)) and plan["unit"] == "schools" and not plan.get("measure"):
        out.append(f'the question counts "{m.group(1)}" (people), not schools: read them from the answers (measure "read", '
                   f'value "number", the sex if the question names one) with operation "sum"')
    ms = (plan or {}).get("measure") or {}
    sex = "female" if _FEMALE_SEX.search(t) and not _MALE_SEX.search(t) else "male" if _MALE_SEX.search(t) and not _FEMALE_SEX.search(t) else None
    if ms.get("field") == "read" and ms.get("value") == "number" and sex and ms.get("sex") != sex:
        out.append(f'the question asks about {"girls" if sex == "female" else "boys"} only: set the measure\'s "sex" to "{sex}"')
    return out


def make_plan(q, idx):
    """Apertus' plan, validated; one correction round when code finds a problem. (None, meta) for no plan, and
    meta["lookup"] when Apertus reads the question as a lookup of one record."""
    D = data()
    V = {k: ", ".join(v) for k, v in D["vocab"].items()}
    cat = "\n".join(f"  {k}: {de} / {fr}" for k, (de, fr) in idx.catalogue.items())
    prompt = PLAN.format(**V, n_st=D["n"]["stapfer"], n_co=D["n"]["consultation"], catalogue=cat, q=q)
    obj, meta = llm.chat_json(prompt, schema={}, max_tokens=450)
    lookup = isinstance(obj, dict) and obj.get("task") == "lookup" and not obj.get("dataset")
    if lookup and not _COUNTED.search(_ascii(q)):
        return None, {"raw": obj, "errors": meta.get("errors"), "lookup": True}
    plan = None if lookup else validate_plan(obj, idx, q)
    problems = ([f'the question asks "how many" ({_COUNTED.search(_ascii(q)).group(0)}): that is a count or a total over '
                 "the records, not one fact; reply with a plan"] if lookup else plan_problems(q, plan))
    if problems:
        obj2, meta2 = llm.chat_json(prompt + "\nYour previous plan " + json.dumps(obj, ensure_ascii=False) + " had problems: "
                                    + " | ".join(problems) + ". Reply again with a corrected plan.", schema={}, max_tokens=450)
        plan2 = validate_plan(obj2, idx, q)
        if plan2 and not plan_problems(q, plan2):
            plan, obj = plan2 | {"revised": problems}, obj2
        elif lookup:
            return None, {"raw": obj2, "errors": meta.get("errors"), "lookup": True}
    return plan, {"raw": obj, "errors": meta.get("errors")}


def rules_plan(q, r):
    """Without Apertus: the one data question the rules recognise reliably, "how many schools in <place>"."""
    from .qa import is_pupil_count_question
    t = _ascii(q)
    if r.get("places") and not is_pupil_count_question(q) and re.search(
            r"(wie\s*viele?|how\s*many|combien|anzahl|nombre|number of)\W+(?:\w+\W+){0,2}?(schul(?:e|en)|schools?|ecoles?)\b", t):
        return {"dataset": "stapfer", "unit": "schools", "filters": {"places": r["places"]}, "measure": None,
                "operation": "count", "group_by": None, "dropped": [], "from": "rules"}
    return None


# ---------------------------------------------------------------- step 2: code selects
def select(plan):
    """(units, units only inside today's municipality). A unit is a record, or a teacher of a record."""
    rows, f = data()["rows"], plan["filters"]
    main, today = [], []
    for r in rows.values():
        if r["dataset"] != plan["dataset"]:
            continue
        if plan["dataset"] == "stapfer":
            if f.get("places"):
                ps = set(f["places"])
                where = "main" if r["p1799"] & ps else "today" if r["today"] & ps else None
                if not where:
                    continue
            else:
                where = "main"
            if any(f.get(k) and not set(f[k]) & set(r[v] if isinstance(r[v], list) else [r[v]])
                   for k, v in (("districts", "district"), ("school_types", "school_type"), ("confessions", "confession"), ("languages", "language"))):
                continue
        else:
            where = "main"
            if f.get("years") and not (r["year"] and f["years"][0] <= r["year"] <= f["years"][1]):
                continue
            if f.get("departments") and not set(f["departments"]) & set(r["department"]):
                continue
            if f.get("types") and not set(f["types"]) & set(r["type"]):
                continue
            if f.get("title_words") and not any(_ascii(w) in r["search"] for w in f["title_words"]):
                continue
        units = [{"rec": r, "teacher": t} for t in r["teachers"] if not f.get("teacher_gender") or t.get("gender") in f["teacher_gender"]] \
            if plan["unit"] == "teachers" else [{"rec": r, "teacher": None}]
        (main if where == "main" else today).extend(units)
    key = lambda u: int(re.sub(r"\D", "", u["rec"]["id"]) or 0)
    return sorted(main, key=key), sorted(today, key=key)


# ---------------------------------------------------------------- step 3: Apertus reads, code verifies
READ = """You read answers from the 1799 Stapfer school survey. For every school sheet below, list EVERY figure it states
for: {what}. You only label the figures; the code decides which of them are counted.
For each figure:
- "quote": copied exactly from the sheet, spelling as written: the shortest piece that holds the number.
- "min"/"max": the number; a range "60-70", "60 bis 70", "60 à 70" gives min 60, max 70; "keine", "aucun" is 0.
  "[=82]" after a number word is its value, computed for you ("zwey und achtzig [=82]"). "wenige" (few) is no number.
- "more": true for "und mehr", "über", "et plus"; else false.
- "sex": "boys", "girls", or "all" when the figure is not split by sex ("Schulkinder", "Lehrkinder", "élèves"); null
  when the figure does not count people.
- "season": "winter" or "summer" when the sheet or its question says so (III.12.a asks about winter, III.12.b about
  summer), else null.
- "current": false for a figure from the past ("sonst", "vormals", "bis zur Revolution", "autrefois"), else true.
- "asked": false when the figure counts another group or thing than asked (catechism or religious instruction:
  Unterweisung, Kinderlehre, Catechumeni, Religionsunterricht, catéchisme; houses; hours; the pay of an assistant,
  Gehülfe, aide, when the teacher's pay is asked), else true.
- "unit": the currency as written for money (fl, kr, Batzen, livres, Kronen, ...), or the measure as written for a
  payment in kind (Mütt, Klafter, Saum, muids, toises, ...), else null.
- "period": for pay, how often it is paid as the sheet says: "year", "quarter" (fronfästlich, par quartier), "week",
  "day" or "once"; else null.
- "part": true when the figure is only a part or an instalment of another figure you list for the same sheet (the
  items of a total, the quarterly instalment of a yearly sum), else false.
- "label": three words on what the figure is.
Never add or convert numbers yourself. An empty list when the sheet states no figure.
Reply with JSON only: {{"sheets": [{{"sheet": "S1", "figures": [{{"quote": "...", "min": 0, "max": 0, "more": false,
  "sex": null, "season": null, "current": true, "asked": true, "unit": null, "period": null, "part": false, "label": "..."}}]}}]}}
Questions read (number: German / French):
{questions}
Sheets:
{sheets}{feedback}"""
READ_SIMPLE = """You read answers from the 1799 Stapfer school survey. For every school sheet below, report: {what}.
{value_rule}
"quote" is copied exactly from the sheet, spelling as written: the shortest piece that states it; null when the sheet
does not say. Reply with JSON only: {{"sheets": [{{"sheet": "S1", "value": ... or null}}]}}
Questions read (number: German / French):
{questions}
Sheets:
{sheets}{feedback}"""
VALUE_RULES = {"yes_no": '"value": {"yes": true or false, "quote": "..."}',
               "text": '"value": {"text": "the answer in at most 8 words", "quote": "..."}'}


def _norm(s):
    return " ".join(re.sub(r"[^\w]+", " ", _ascii(s)).split())


_WINTER, _SUMMER = re.compile(r"winter|hiver|inverno"), re.compile(r"sommer|summer|\bete\b|estate")
# groups the survey counts apart from the pupils: catechism and religious instruction (Unterweisung, Kinderlehre ...)
_OTHER_GROUP = re.compile(r"unterweis|kinderlehr|c[ae]te?chum|katechum|religions|abendmahl|catech|instruction relig|admis a la (?:ste|sainte) cene")


# how often pay is due, from the words of the sheet (nearest before the figure wins): "fronfästlich 32 kr., jährlich 12 kr."
_PERIOD = {"quarter": r"fro?h?n?fast|fraufast|quatember|quartal|vierteljahr|par quartier|par trimestre|trimestr",
           "year": r"jahrlich|jahrlig|im jahr\b|des jahrs|per jahr|jahrgang|alle jahre|par an\b|l an\b|annuel|pour l annee|par annee",
           "week": r"wochentlich|wochenlich|die woche|alle wochen|par semaine|chaque semaine|hebdomad",
           "day": r"taglich|alle tage|par jour|chaque jour"}
_PERIOD_RE = re.compile("|".join(f"(?P<{k}>{v})" for k, v in _PERIOD.items()))
_ASSISTANT = re.compile(r"gehulf|gehilf|provisor|unterlehrer|\baides?\b|adjoint|sous.?maitre|sous.?regent")


def _period(quote, before, n):
    """The period the sheet states for a payment of n: the nearest period word before the number (in the quote, then
    in the words before it), else one after it in the quote ("Jährlich 200 kr. frohnfästlich mit 50 kr.")."""
    nq = _norm(quote)
    at = next((m.start() for m in re.finditer(rf"(?<!\d){n}(?!\d)", nq)), len(nq))
    for text, last in ((nq[:at], True), (before[-60:], True), (nq[at:], False)):
        ms = list(_PERIOD_RE.finditer(text))
        if ms:
            return (ms[-1] if last else ms[0]).lastgroup
    return None


def unit_key(u):
    """One currency or measure however it is punctuated or capitalised ("L." = "L", "kr" = "Kr."); spellings that may
    be different units ("kr" Kronen or Kreuzer, "Cronen") are never merged."""
    return _norm(u).lower() if u else None


_MORE = re.compile(r"\bmehr\b|\buber\b|daruber|\bplus\b|au.dessus|\bmore\b|\bover\b")
_RANGE = re.compile(r"(\d+)\s*(?:bis|a|-|to)\s*(\d+)")


def _where(quote, ctx):
    """The sub-questions whose answer holds the quote: (question number, question text, text before, text after)."""
    nq, hits = _norm(quote), []
    if not nq:
        return hits
    pat = re.compile(rf"(?<!\w){re.escape(nq)}(?!\w)")  # whole words: "50 kr" is not the end of "150 kr"
    for qno, question, body in ctx:
        nb = _norm(body)
        m = pat.search(nb)
        if m:
            i = m.start()
            hits.append((qno, _ascii(question), nb[max(0, i - 80):i], nb[i + len(nq):i + len(nq) + 30]))
    return hits


def _unit_in(unit, quote):
    """The currency or measure as the quote writes it, allowing the sources' spelling ("Klafter" for "Klaffter"); None
    when the quote does not hold it."""
    import difflib
    nu = _norm(unit or "")
    if not nu:
        return None
    if nu in _norm(quote):
        return unit
    words = re.findall(r"[^\W\d_]+", plain(quote))
    near = difflib.get_close_matches(nu, [_norm(w) for w in words], n=1, cutoff=0.8)
    return next((w for w in words if near and _norm(w) == near[0]), None) if len(nu) >= 4 else None


def _sex_before(t, n):
    """In a quote naming both sexes ("knaben 17. magdlein 60."), the sex of the word nearest before the number n. A
    number after another number ("Mägdchen 85 175") is no longer that word's: it is the sheet's total ("all")."""
    m = re.search(rf"(?<!\d){n}(?!\d)", t)
    if not m:
        return None
    marks = [(x.end(), "boys") for x in _MALE_SEX.finditer(t[:m.start()])] + [(x.end(), "girls") for x in _FEMALE_SEX.finditer(t[:m.start()])]
    if not marks:
        return None
    end, sex = max(marks)
    return "all" if re.search(r"\d", t[end:m.start()]) else sex


def _two_figures(quote, lo, hi):
    """Whether lo and hi are two figures of the quote with a sex word between them ("Kaben 90 Mägdchen 85")."""
    t = _ascii(re.sub(rf"(?:{NUMW_RUN})\s*\[=(\d+)\]", r"\1", annotate(plain(quote)), flags=re.I))
    pos = {int(m.group(0)): m.start() for m in reversed(list(re.finditer(r"\d+", t)))}
    if lo not in pos or hi not in pos:
        return False
    a, b = sorted((pos[lo], pos[hi]))
    between = t[a:b]
    return bool(_MALE_SEX.search(between) or _FEMALE_SEX.search(between))


def plain(quote):
    """A quote as the source writes it, without the [=82] values annotate() added for the reader."""
    return re.sub(r"\s*\[=\d+\]", "", quote or "")


def verify_figure(p, kind, text, ctx=()):
    """One figure Apertus read, kept only if its quote is in the sheet, its numbers are in the quote, its currency is
    in the quote and its sex label does not contradict the quote. What the text itself settles, code sets: the season
    from the wording of the sub-question the quote answers ("Im Winter", "En hiver"), another group from the words
    around the quote (catechism classes), the sex when the quote names only one. Returns (figure or None, reason)."""
    if not isinstance(p, dict):
        return None, "not a figure"
    quote = str(p.get("quote") or "").strip()
    try:
        lo = int(p.get("min")); hi = int(p.get("max") if p.get("max") is not None else lo)
    except (TypeError, ValueError):
        return None, f"not a number in {quote!r}"
    if quote and _norm(quote) not in _norm(text) and kind == "money":  # "200 kr." for "kr. 200.": the sheet's order
        sw = re.fullmatch(r"(\d+)\s+([^\W\d_]+)", _norm(quote)) or re.fullmatch(r"([^\W\d_]+)\s+(\d+)", _norm(quote))
        if sw and re.search(rf"(?<!\w){sw.group(2)} {sw.group(1)}(?!\w)", _norm(text)):
            m_ = re.search(rf"{re.escape(sw.group(2))}\W+{sw.group(1)}\b\.?", text, re.I)
            quote = m_.group(0) if m_ else quote
    if not quote or _norm(quote) not in _norm(text):
        return None, f"the quote {quote!r} is not in the sheet"
    have = {int(n) for n in _nums(quote) if n.isdigit()} | {int(n) for n in word_numbers(quote)}
    if _NONE.search(_ascii(quote)):
        have.add(0)
    if lo not in have or hi not in have or lo > hi:
        return None, f"{lo}-{hi} is not in the quote {quote!r}, which holds {sorted(have)}"
    if lo != hi and _two_figures(quote, lo, hi):
        return None, f"{lo} and {hi} in the quote {quote!r} are two figures (another sex between them), not a range"
    rng = next((m for m in _RANGE.finditer(_norm(quote)) if int(m.group(1)) == lo), None)
    if lo == hi and rng and int(rng.group(2)) > lo:  # "70 bis 80" read as 70: the quote itself states the range
        hi = int(rng.group(2))
    unit = str(p.get("unit") or "").strip() or None
    if kind == "money":
        written = _unit_in(unit, quote)
        if not written:
            return None, f"the currency {unit!r} is not in the quote {quote!r}"
        unit = written
    sex = p.get("sex") if p.get("sex") in ("boys", "girls", "all") else None
    qa_ = _ascii(quote)
    m_, f_ = bool(_MALE_SEX.search(qa_)), bool(_FEMALE_SEX.search(qa_))
    if sex == "boys" and f_ and not m_ or sex == "girls" and m_ and not f_:
        return None, f"labelled {sex}, but the quote {quote!r} names the other sex"
    if m_ and f_ and sex in ("boys", "girls", "all"):  # both named: the sex word nearest before the number is its sex
        sex = _sex_before(qa_, lo) or sex
    if m_ != f_ and have - {0} <= {lo, hi} and sex in (None, "all", "boys", "girls"):
        # the quote names one sex only and holds only this figure: that is its sex ("Kaben 90 Mägdchen 85" holds two)
        sex = "boys" if m_ else "girls"
    season = p.get("season") if p.get("season") in ("winter", "summer") else None
    asked = p.get("asked") is not False
    current = p.get("current") is not False
    where = _where(quote, ctx)
    if where:  # past or present, from the figure's own answer, never from another sub-answer of the sheet
        current = not any(_PAST.search(w[2] + " " + _norm(quote) + " " + w[3]) for w in where)
        qs = " ".join(w[1] for w in where)
        season = "winter" if _WINTER.search(qs) else "summer" if _SUMMER.search(qs) else season
        if any(_OTHER_GROUP.search(w[2] + " " + qa_) for w in where):
            asked = False
    if any(NOT_PUPILS_AFTER.match(w[3]) for w in where) and kind == "number" and sex:  # "de 4 à 20 ans": ages, not people
        asked = False
    more = bool(p.get("more")) and bool(_MORE.search(_norm(quote)))  # "und mehr" only when the quote says so
    period = None
    if kind == "money":
        period = next((x for w in where if (x := _period(quote, w[2], lo))), None) or (p.get("period") if p.get("period") in (*_PERIOD, "once") else None)
        if any(_ASSISTANT.search(w[2][-40:] + " " + qa_) for w in where):  # "für einen Gehülfen kr. 50": not the teacher's own pay
            asked = False
    return {"label": str(p.get("label") or "")[:40], "sex": sex, "season": season, "current": current,
            "asked": asked, "lo": lo, "hi": hi, "more": more, "unit": unit, "quote": quote, "period": period,
            "part": p.get("part") is True, "question": [w[0] for w in where]}, None


def resolve(figs, sex, single, complete=True):
    """The counting policy, in code: the figures of one sheet that answer the question, and the others with the reason
    they are left out. Another group or a past figure never counts; one season only, the winter first (the same
    pupils attend both); boys and girls are added only when both are stated, or when the metadata says it is a
    girls' or boys' school; one sex alone at a mixed school is incomplete."""
    # the same figure quoted twice, once inside a longer quote: the longer reading stands
    figs = [f for f in figs if not any(g is not f and (g["lo"], g["hi"], g["sex"]) == (f["lo"], f["hi"], f["sex"]) and len(g["quote"]) > len(f["quote"])
                                       and _norm(f["quote"]) in _norm(g["quote"]) for g in figs)]
    out = [(f, "another group or thing") for f in figs if not f["asked"]] + [(f, "a past figure") for f in figs if f["asked"] and not f["current"]]
    use = [f for f in figs if f["asked"] and f["current"]]
    if any(not f.get("part") for f in use):  # the items or instalments of a total the sheet also states
        out += [(f, "a part of a total stated on the sheet") for f in use if f.get("part")]
        use = [f for f in use if not f.get("part")]
    seasons = {f["season"] for f in use}
    pick = "winter" if "winter" in seasons else None if None in seasons else "summer"
    out += [(f, "winter figure used" if pick == "winter" else "general figure used") for f in use if f["season"] != pick]
    use = [f for f in use if f["season"] == pick]
    seen, uniq = set(), []
    for f in use:  # a sheet repeats one answer for the sub-questions
        k = (f["sex"], f["lo"], f["hi"], f["more"], unit_key(f["unit"]), f.get("period"))
        if k not in seen:
            seen.add(k); uniq.append(f)
    boys, girls = [f for f in uniq if f["sex"] == "boys"], [f for f in uniq if f["sex"] == "girls"]
    rest = [f for f in uniq if f["sex"] not in ("boys", "girls")]
    zero = {"label": "", "sex": None, "season": None, "current": True, "asked": True, "lo": 0, "hi": 0, "more": False, "unit": None,
            "quote": None, "implied": "girls' school" if single == "female" else "boys' school"}
    if sex == "male":
        chosen, ok = (boys, True) if boys else ([zero], True) if single == "female" else ([], False)
    elif sex == "female":
        chosen, ok = (girls, True) if girls else ([zero], True) if single == "male" else ([], False)
    elif boys and girls:
        chosen, ok = boys + girls, True
        out += [(f, "boys and girls used") for f in rest if f["sex"] == "all"]
    elif rest:
        chosen, ok = rest, True
        out += [(f, "the total used") for f in boys + girls]
    elif boys or girls:
        chosen, ok = boys + girls, (single == "male" and boys) or (single == "female" and girls)
    else:
        chosen, ok = [], False
    return {"parts": chosen, "complete": bool(ok) and complete and bool(chosen)} if chosen else None, out


def _passages(idx, rid, sections):
    secs = tuple(sections) + (FREE,)
    return [i for i in _by_doc(idx).get(rid, []) if idx.P[i]["qno"].startswith(secs)]


def _context(r):
    """What the metadata says about the school: the reader's context for interpreting its answers."""
    t = "; ".join(f'{x.get("name") or "?"} ({", ".join(str(v) for v in (x.get("gender"), x.get("age") and f"age {x.get('age')}") if v)})'
                  for x in r["teachers"])
    return (f'  1799 place: {", ".join(r["place"])}; today: {", ".join(sorted(set(r["names"].values()) - set(r["place"])) or r["place"])}'
            + (f'; teachers: {t}' if t else "") + (f'; language: {r["language"]}' if r.get("language") else ""))


_NUMWORDS = re.compile(rf"\b(?:{NUMW_RUN})\b", re.I)


def annotate(text):
    """Old number words with their value, computed by the repository's parser: "zwey und achtzig [=82]". Context for
    the reader (German writes the units first, which models misread as 28); the words themselves stay untouched."""
    def one(m):
        v = number_run_value(m.group(0))
        article = m.group(0).lower() in ("un", "une", "ein", "eine", "eins")  # "un enfant": an article, not a count
        return f"{m.group(0)} [={v}]" if v and not article else m.group(0)
    return _NUMWORDS.sub(one, text)


def _block(idx, rid, sections):
    seen, rows, size = set(), [], 0
    for i in _passages(idx, rid, sections):
        body = " ".join(_answer_body(idx.P[i]).split())
        if body in seen:  # sheets often repeat one answer for the sub-questions
            continue
        seen.add(body)
        body = annotate(body)
        room = SHEET_CHARS - size
        if room <= 200:
            break
        rows.append(f"  {idx.P[i]['qno']}: {body[:room]}")
        size += len(rows[-1])
    return "\n".join(rows)


def verify_simple(v, kind, text):
    if not isinstance(v, dict):
        return None, None
    quote = str(v.get("quote") or "").strip()
    if not quote or _norm(quote) not in _norm(text):
        return None, f"the quote {quote!r} is not in the sheet"
    if kind == "yes_no":
        return ({"yes": v["yes"], "quote": quote}, None) if isinstance(v.get("yes"), bool) else (None, "no yes/no")
    return {"text": str(v.get("text") or quote)[:80], "quote": quote}, None


def read(idx, plan, recs):
    """{record id: {"value", "excluded", "rejected"}}; None when Apertus gave nothing usable."""
    m = plan["measure"]
    numeric = m["value"] in ("number", "money")
    blocks = [(r, b) for r in recs if (b := _block(idx, r["id"], m["sections"]))]
    if not blocks:
        return {}
    cat = idx.catalogue
    ctx = {r["id"]: [(idx.P[i]["qno"], " ".join(cat.get(idx.P[i]["qno"], ("", ""))), annotate(" ".join(_answer_body(idx.P[i]).split())))
                     for i in _passages(idx, r["id"], m["sections"])] for r, _b in blocks}
    sec = tuple(m["sections"])
    questions = "\n".join(f"  {k}: {de} / {fr}" for k, (de, fr) in idx.catalogue.items() if k.startswith(sec) or any(s.startswith(k) for s in sec))
    chunks, cur, size = [], [], 0
    for r, b in blocks:  # packed by size: whole answers, never a sheet split across calls
        if cur and (len(cur) == CHUNK or size + len(b) > CHUNK_CHARS):
            chunks.append(cur); cur, size = [], 0
        cur.append((r, b)); size += len(b)
    chunks.append(cur)

    def ask(ch, feedback=""):
        sheets = "\n\n".join(f"[S{n + 1}] {r['label']}\n{_context(r)}\n{b}" for n, (r, b) in enumerate(ch))
        if numeric:
            prompt = READ.format(what=m["what"], questions=questions, sheets=sheets, feedback=feedback)
            per_sheet = 1000 if m["value"] == "money" else 600  # pay lists money and every payment in kind
            return llm.chat_json(prompt, schema={"sheets": list}, max_tokens=per_sheet * len(ch) + 300)
        prompt = READ_SIMPLE.format(what=m["what"], value_rule=VALUE_RULES[m["value"]], questions=questions, sheets=sheets, feedback=feedback)
        return llm.chat_json(prompt, schema={"sheets": list}, max_tokens=90 * len(ch) + 120)

    def take(ch, obj, out, prev=None):
        got = {str(s.get("sheet", "")).strip("[] "): s for s in obj["sheets"] if isinstance(s, dict)}
        retry = []
        for n, (r, b) in enumerate(ch):
            s = got.get(f"S{n + 1}") or {}
            if numeric:
                figs, bad = [], []
                for fg in s.get("figures") if isinstance(s.get("figures"), list) else []:
                    f, why = verify_figure(fg, m["value"], b, ctx[r["id"]])
                    (figs.append(f) if f else bad.append(why))
                if prev and r["id"] in prev:  # the correction round adds to what already passed
                    figs = prev[r["id"]]["figures"] + [f for f in figs if (f["quote"], f["lo"], f["hi"]) not in
                                                       {(x["quote"], x["lo"], x["hi"]) for x in prev[r["id"]]["figures"]}]
                # coverage: every answer that holds a number must be covered by a figure (counted or left out)
                covered = {q for f in figs for q in f["question"]}
                skipped = [(qno, question, body) for qno, question, body in ctx[r["id"]] if qno not in covered and _has_number(body)]
                bad += [f"you listed no figure from the answer to {qno}: {plain(body)[:160]!r}" for qno, _q, body in skipped]
                v, excl = resolve(figs, m.get("sex"), _single_sex(r))
                if prev is not None and v and any(_could_change(v, question) for _qno, question, _b in skipped):
                    v = dict(v, complete=False)  # still skipped after the correction rounds: kept out, never a silent guess
                out[r["id"]] = {"value": v, "figures": figs, "excluded": [{"quote": f["quote"], "label": f["label"], "reason": why}
                                                                         for f, why in excl if f.get("quote")], "rejected": bad}
            else:
                v, why = verify_simple(s.get("value"), m["value"], b)
                bad = [why] if why else []
                out[r["id"]] = {"value": v, "figures": [], "excluded": [], "rejected": bad}
            if bad:
                retry.append((r, b, bad))
        return retry

    with ThreadPoolExecutor(max_workers=min(WORKERS, len(chunks))) as ex:
        replies = list(ex.map(ask, chunks))
    ok = lambda obj: isinstance(obj, dict) and isinstance(obj.get("sheets"), list)  # noqa: E731
    lost = [ch for ch, (obj, _m) in zip(chunks, replies, strict=False) if not ok(obj) and len(ch) > 1]
    if lost:  # a call that failed (timeout, cut-off JSON): each of its sheets alone
        singles = [[x] for ch in lost for x in ch]
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(singles))) as ex:
            more = list(ex.map(ask, singles))
        chunks, replies = chunks + singles, replies + more
    out, retry = {}, []
    for ch, (obj, _meta) in zip(chunks, replies, strict=False):
        if ok(obj):
            retry += take(ch, obj, out)
    if not out:
        return None
    for rnd in range(ROUNDS):  # feedback rounds: code says exactly what failed, Apertus re-reads those sheets only
        if rnd and not numeric:
            break
        if rnd:  # a later round re-reads only the sheets still left out, not the whole batch
            retry = [(r, b, bad) for r, b, bad in retry if (v := out[r["id"]]["value"]) is None or not v.get("complete", True)]
        if not retry:
            break
        groups = [retry[i:i + CHUNK] for i in range(0, len(retry), CHUNK)]

        def again(grp):
            notes = " | ".join(f"S{n + 1}: " + "; ".join(bad) for n, (_r, _b, bad) in enumerate(grp))
            return ask([(r, b) for r, b, _bad in grp], f"\nYour previous reading of these sheets failed the check: {notes}. "
                                                        "Read them again and list every figure, with exact quotes.")
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(groups))) as ex:
            replies = list(ex.map(again, groups))
        retry = []
        for grp, (obj, _meta) in zip(groups, replies, strict=False):
            if not (isinstance(obj, dict) and isinstance(obj.get("sheets"), list)):
                retry += grp
                continue
            fixed = {}
            retry += take([(r, b) for r, b, _bad in grp], obj, fixed, prev=out)
            for rid, rd in fixed.items():
                out[rid] = rd | {"corrected": rnd + 1}
    return out


def _season_rank(text):
    t = _ascii(text)
    return 2 if _WINTER.search(t) else 0 if _SUMMER.search(t) else 1


def _could_change(v, question):
    """Whether an answer the reader skipped could change the value: under the one-season policy a skipped summer (or
    general) answer cannot change a value taken from the winter answer; any other skipped answer can."""
    used = [p for p in v.get("parts", []) if p.get("quote")]
    if not used:
        return True
    rank = max(2 if p.get("season") == "winter" else 0 if p.get("season") == "summer" else 1 for p in used)
    return _season_rank(question) >= rank


def _has_number(text):
    t = plain(text)
    return any(len(n) < 4 for n in _nums(t)) or bool(word_numbers(t) - {"1"})  # not "1799", not the article "un"


def _single_sex(r):
    types = set(r.get("school_type") or [])
    return "female" if types == {"Mädchenschule"} else "male" if types == {"Knabenschule"} else None


# ---------------------------------------------------------------- step 4: code computes
def totals(v):
    """A verified numeric value as {(currency or measure, period): (lo, hi, more, unit as written, period)}: parts are
    added only within one currency and one period, never across them (kr and Mütt, quarterly and yearly). {} for a
    value that is incomplete."""
    if not v or "parts" not in v or not v["complete"]:
        return {}
    out = {}
    for p in v["parts"]:
        k = (unit_key(p["unit"]), p.get("period"))
        lo, hi, more, _u, _per = out.get(k, (0, 0, False, None, None))
        out[k] = (lo + p["lo"], hi + p["hi"], more or p["more"], p["unit"], p.get("period"))
    return out


def _num(v):
    """A verified numeric value as one (lo, hi, more, unit); None for a value that is incomplete or mixes currencies,
    measures or periods (it has no single number to test or rank)."""
    t = totals(v)
    if len(t) != 1:
        return None
    lo, hi, more, unit, _per = next(iter(t.values()))
    return lo, hi, more, unit


def value_of(u, plan, readings):
    m = plan["measure"]
    if not m:
        return None
    if m["field"] == "teacher_age":
        a = str((u["teacher"] or {}).get("age") or "")
        return {"parts": [{"label": "age", "lo": int(a), "hi": int(a), "more": False, "unit": None, "quote": None}], "complete": True} if a.isdigit() else None
    return (readings.get(u["rec"]["id"]) or {}).get("value")


def test(v, cond):
    """True / False / None (a range that straddles the threshold) for a unit's value under the plan's condition."""
    if not cond:
        return True
    if cond["op"] in ("yes", "no"):
        return None if not v or "yes" not in v else v["yes"] == (cond["op"] == "yes")
    n = _num(v)
    if not n:
        return None
    lo, hi, more, _u = n
    x = cond["value"]
    ok = {">": (lo > x, hi <= x and not more), ">=": (lo >= x, hi < x and not more), "<": (hi < x and not more, lo >= x),
          "<=": (hi <= x and not more, lo > x), "=": (lo == hi == x and not more, x < lo or (x > hi and not more))}[cond["op"]]
    return True if ok[0] else False if ok[1] else None


def _group_key(u, gb, v):
    r = u["rec"]
    if gb == "teacher_gender":
        return (u["teacher"] or {}).get("gender") or "?"
    if gb == "value":
        return (v or {}).get("text") or ("yes" if (v or {}).get("yes") else "no" if v and "yes" in v else "?")
    val = r.get(gb)
    return str((val[0] if val else "?") if isinstance(val, list) else val if val is not None else "?")


def compute(units, plan, readings):
    """The operation over the units that pass the condition: totals per currency, ranges kept as ranges."""
    rows, unclear = [], []
    for u in units:
        v = value_of(u, plan, readings)
        t = test(v, plan.get("condition"))
        (rows if t else unclear if t is None and plan.get("condition") else []).append((u, v))
    op = plan["operation"]
    if op in ("count", "list"):
        return {"op": op, "n": len(rows), "rows": rows, "unclear": unclear}
    per_unit, missing = {}, []
    for u, v in rows:
        t = totals(v)
        for k, (lo, hi, more, unit, _per) in t.items():  # a school paying in kr and in Mütt adds to both totals
            per_unit.setdefault(k, []).append((u, v, (lo, hi, more, unit)))
        if not t:
            missing.append((u, v))
    res = {}
    for key, xs in per_unit.items():
        lo, hi, more = [x[2][0] for x in xs], [x[2][1] for x in xs], any(x[2][2] for x in xs)
        cur = (xs[0][2][3], key[1])  # the currency as written, the period
        if op == "sum":
            res[cur] = {"lo": sum(lo), "hi": sum(hi), "more": more, "n": len(xs)}
        elif op == "average":
            res[cur] = {"lo": round(sum(lo) / len(xs), 1), "hi": round(sum(hi) / len(xs), 1), "more": more, "n": len(xs)}
        else:
            pick = (max if op == "max" else min)(xs, key=lambda x: (x[2][0] + x[2][1]) / 2)
            res[cur] = {"lo": pick[2][0], "hi": pick[2][1], "more": pick[2][2], "n": len(xs), "unit": pick[0]}
    seen = set()
    listed = [(u, v) for xs in per_unit.values() for u, v, _ in xs if not (id(u) in seen or seen.add(id(u)))]
    return {"op": op, "result": res, "rows": listed, "missing": missing, "unclear": unclear}


# ---------------------------------------------------------------- rendering (the audit trail) and step 5
T = {
    "en": {"boys": "boys", "girls": "girls", "implied": "none stated: {what}",
           "head": "Computed from the cited records, not stated in any single source.", "plan": "Plan", "result": "Result",
           "count": "{n} {unit}", "range": "{lo} to {hi}", "more": " and more", "of": "from {n} {unit} with a value",
           "missing": "Without a usable value (not in the result):", "unclear": "Cannot be decided from the stated range:",
           "today": "Inside today's municipality of the same name, but separate places in 1799 (not in the result):",
           "excluded": "Read but left out on purpose:", "more_units": "… and {n} more", "by": "by {g}",
           "unit": {"schools": "schools", "teachers": "teachers", "consultations": "consultations"},
           "one": {"schools": "school", "teachers": "teacher", "consultations": "consultation"},
           "op": {"count": "count", "sum": "total", "average": "average", "min": "smallest", "max": "largest", "list": "list"},
           "per": {"year": "a year", "quarter": "a quarter", "week": "a week", "day": "a day", "once": "once"}},
    "de": {"boys": "Knaben", "girls": "Mädchen", "implied": "keine genannt: {what}",
           "head": "Berechnet aus den zitierten Datensätzen, in keiner einzelnen Quelle so genannt.", "plan": "Vorgehen", "result": "Ergebnis",
           "count": "{n} {unit}", "range": "{lo} bis {hi}", "more": " und mehr", "of": "aus {n} {unit} mit einer Angabe",
           "missing": "Ohne verwendbare Angabe (nicht im Ergebnis):", "unclear": "Aus dem genannten Bereich nicht entscheidbar:",
           "today": "In der heutigen gleichnamigen Gemeinde, 1799 aber eigene Orte (nicht im Ergebnis):",
           "excluded": "Gelesen, aber bewusst ausgelassen:", "more_units": "… und {n} weitere", "by": "nach {g}",
           "unit": {"schools": "Schulen", "teachers": "Lehrpersonen", "consultations": "Vernehmlassungen"},
           "one": {"schools": "Schule", "teachers": "Lehrperson", "consultations": "Vernehmlassung"},
           "op": {"count": "Anzahl", "sum": "Summe", "average": "Durchschnitt", "min": "kleinster Wert", "max": "grösster Wert", "list": "Liste"},
           "per": {"year": "jährlich", "quarter": "vierteljährlich", "week": "wöchentlich", "day": "täglich", "once": "einmalig"}},
    "fr": {"boys": "garçons", "girls": "filles", "implied": "aucun indiqué : {what}",
           "head": "Calculé à partir des fiches citées, et non repris d'une seule source.", "plan": "Démarche", "result": "Résultat",
           "count": "{n} {unit}", "range": "de {lo} à {hi}", "more": " et plus", "of": "sur {n} {unit} avec une valeur",
           "missing": "Sans valeur utilisable (hors du résultat) :", "unclear": "Impossible à trancher d'après la fourchette :",
           "today": "Dans la commune actuelle du même nom, mais lieux distincts en 1799 (hors du résultat) :",
           "excluded": "Lu, mais écarté volontairement :", "more_units": "… et {n} autres", "by": "par {g}",
           "unit": {"schools": "écoles", "teachers": "enseignants", "consultations": "consultations"},
           "one": {"schools": "école", "teachers": "enseignant", "consultations": "consultation"},
           "op": {"count": "nombre", "sum": "total", "average": "moyenne", "min": "plus petit", "max": "plus grand", "list": "liste"},
           "per": {"year": "par an", "quarter": "par trimestre", "week": "par semaine", "day": "par jour", "once": "une fois"}},
}


class _Audit:
    """The listing and its citations: each unit cites the passage that holds its value (or its first passage)."""

    def __init__(self, idx, plan):
        self.idx, self.plan, self.sources, self.pos, self.extra = idx, plan, [], {}, set()

    def cite(self, u, quote=None):
        secs = tuple(self.plan["measure"]["sections"]) if (self.plan.get("measure") or {}).get("field") == "read" else None
        ids = _passages(self.idx, u["rec"]["id"], secs) if secs else []
        ids = ids or _by_doc(self.idx).get(u["rec"]["id"], [])
        pick = next((i for i in ids if quote and _norm(plain(quote)) in _norm(_answer_body(self.idx.P[i]))), ids[0] if ids else None)
        if pick is None:
            return ""
        if pick not in self.pos:
            self.sources.append(self.idx._hit(pick)); self.pos[pick] = len(self.sources)
        return f"[{self.pos[pick]}]"

    def num(self, *xs):
        self.extra |= {str(x) for x in xs if x is not None}
        return xs


def _fmt(lo, hi, more, unit, L, period=None):
    s = (str(lo) if lo == hi else L["range"].format(lo=lo, hi=hi)) + (L["more"] if more else "")
    return s + (f" {unit}" if unit else "") + (f' {L["per"][period]}' if period else "")


def _unit_line(u, v, A, L):
    r, t = u["rec"], u["teacher"]
    label = (f'{t.get("name") or "?"}, ' if t else "") + r["label"]
    if not v:
        return f"- {label} {A.cite(u)}"
    if "parts" in v:
        bits = []
        for p in v["parts"]:
            A.num(p["lo"], p["hi"])
            c = A.cite(u, p["quote"]) if p.get("quote") else A.cite(u)
            name = L.get(p.get("sex") or "", "") or (p.get("label") if len(v["parts"]) > 1 else "")
            how = f' ({L["implied"].format(what=p["implied"])})' if p.get("implied") else ""
            bits.append(f'{(name + " ") if name else ""}{_fmt(p["lo"], p["hi"], p["more"], p["unit"], L, p.get("period"))}{how} {c}'.strip())
        tots = []
        for k, (lo, hi, more, unit, per) in totals(v).items():  # a sum per currency and period, where it adds parts
            if sum((unit_key(p["unit"]), p.get("period")) == k for p in v["parts"]) > 1:
                A.num(lo, hi)
                tots.append(_fmt(lo, hi, more, unit, L, per))
        tot = f" = {'; '.join(tots)}" if tots else ""
        return f"- {label}: " + ", ".join(bits) + tot
    if "yes" in v:
        return f'- {label}: {"yes" if v["yes"] else "no"} — "{plain(v["quote"])}" {A.cite(u, v["quote"])}'
    return f'- {label}: {v["text"]} — "{plain(v["quote"])}" {A.cite(u, v["quote"])}'


def render(q, plan, units, today, readings, idx):
    L = T[_language(q)]
    A = _Audit(idx, plan)
    unit = L["unit"][plan["unit"]]
    f = plan["filters"]
    desc = [f"{k}: {', '.join(map(str, v))}" for k, v in f.items() if v]
    if plan.get("measure"):
        m = plan["measure"]
        desc.append(m["what"] + (f' ({", ".join(m["sections"])})' if m.get("sections") else ""))
    if plan.get("condition"):
        c = plan["condition"]
        desc.append(f'{c["op"]} {c["value"]}' if c["value"] is not None else c["op"])
    if plan.get("group_by"):
        desc.append(L["by"].format(g=plan["group_by"]))
    A.num(*[y for y in f.get("years") or []], *([plan["condition"]["value"]] if plan.get("condition") else []))
    lines = [L["head"], f'{L["plan"]}: {unit} · {" · ".join(desc)} · {L["op"][plan["operation"]]}', ""]

    groups = {}
    if plan.get("group_by"):
        for u in units:
            groups.setdefault(_group_key(u, plan["group_by"], value_of(u, plan, readings)), []).append(u)
    else:
        groups[None] = units
    summary = []
    for g, us in groups.items():
        res = compute(us, plan, readings)
        if res["op"] in ("count", "list"):
            n = res["n"]; A.num(n)
            summary.append((g, n, L["count"].format(n=n, unit=unit if n != 1 else L["one"][plan["unit"]])))
            if g is not None:  # a grouped count cites up to three records per group
                cites = "".join(A.cite(u) for u, _v in res["rows"][:3])
                summary[-1] = (g, n, summary[-1][2] + (f" {cites}" if cites else ""))
            else:
                for u, v in res["rows"][:MAX_LISTED]:
                    lines.append(_unit_line(u, v, A, L))
                if len(res["rows"]) > MAX_LISTED:
                    A.num(len(res["rows"]) - MAX_LISTED)
                    lines.append(L["more_units"].format(n=len(res["rows"]) - MAX_LISTED))
        else:
            for u, v in res["rows"][:MAX_LISTED]:
                lines.append(_unit_line(u, v, A, L))
            if len(res["rows"]) > MAX_LISTED:
                A.num(len(res["rows"]) - MAX_LISTED)
                lines.append(L["more_units"].format(n=len(res["rows"]) - MAX_LISTED))
            parts = []
            for (cur, per), x in res["result"].items():
                A.num(x["lo"], x["hi"], x["n"])
                who = f' — {x["unit"]["rec"]["label"]}' if x.get("unit") else ""
                parts.append(f'{_fmt(x["lo"], x["hi"], x["more"], cur, L, per)} ({L["of"].format(n=x["n"], unit=unit)}){who}')
            summary.append((g, None, "; ".join(parts) or "–"))
            if res["missing"]:
                lines += [L["missing"]] + [_unit_line(u, v, A, L) for u, v in res["missing"][:MAX_LISTED]]
        if res.get("unclear"):
            lines += [L["unclear"]] + [_unit_line(u, v, A, L) for u, v in res["unclear"][:MAX_LISTED]]
    if plan.get("group_by"):
        summary.sort(key=lambda s: -(s[1] or 0))
        lines += ["", f'{L["result"]} ({L["by"].format(g=plan["group_by"])}):'] + [f"- {g}: {s}" for g, _n, s in summary[:30]]
        A.extra |= {str(g) for g in groups if g}
    else:
        lines += ["", f'{L["result"]}: {summary[0][2]}']
    if today:
        lines += ["", L["today"]] + [_unit_line(u, value_of(u, plan, readings), A, L) for u in today[:MAX_LISTED]]
    for rd in readings.values():  # each figure was verified against the whole sheet, beyond what check_numbers sees of it
        for fg in rd.get("figures", []):
            A.num(fg["lo"], fg["hi"])
    exc = [(rid, e) for rid, rd in readings.items() for e in rd.get("excluded", [])]
    if exc:
        rows = {u["rec"]["id"]: u for u in units + today}
        lines += ["", L["excluded"]]
        for rid, e in exc[:MAX_LISTED]:
            if rid in rows:
                lines.append(f'- {rows[rid]["rec"]["label"]}: "{plain(e["quote"])}" ({e.get("label") or "?"}) — {e["reason"]} {A.cite(rows[rid], e["quote"])}')
    return "\n".join(lines), A


SUMMARY = """Answer the question in one to three sentences in {lang}, using ONLY the computed result below. Use only numbers
that appear in it, keep its citations [n] exactly as written, and add nothing from outside (no history, no estimates).
Say that the figure is counted or computed from the archive records, and mention a range or "and more" if the result has
one. Records listed as separate places in 1799 or without a usable value are not part of the result: say so if there are any.
Question: {q}
Computed result:
{facts}"""
LANG = {"en": "English", "de": "German", "fr": "French"}


def phrase(q, text, sources, extra):
    """Apertus' summary of the computed result, or None if it brings a number the result does not contain."""
    a, _meta = llm.chat_text(SUMMARY.format(lang=LANG[_language(q)], q=q, facts=text[:200_000]), max_tokens=220)
    # uncited sentences are checked against every source plus the computed values; the audit below carries the citations
    if not a or check_numbers(a, sources, q, extra=extra):
        return None
    return a.strip()


_DATA_WORDS = re.compile(r"wie\s*viele?|wieviele?|how\s*many|combien|anzahl|\bnombre\b|number of|average|durchschnitt|moyenne|"
                         r"\btotal\b|insgesamt|zusammen|\bmost\b|am meisten|le plus|\bfewest\b|\bwhich\b|welche|quel(?:le)?s\b|"
                         r"more than|mehr als|plus de|less than|weniger als|moins de|\beach\b|\bje\b|\bpro\b|\bper\b|chaque|list\b|liste")


# wording that always asks over several records, even when it names a place ("the district of Hinwil")
_AGG_WORDS = re.compile(r"average|durchschnitt|moyenne|\bmean\b|district|bezirk|kanton|canton|\beach\b|\bjede[rsn]?\b|chaque|"
                        r"\bper\b|\bpro\b|\bje\b|\btotal\b|insgesamt|zusammen")
_ONE_RECORD = re.compile(r"\b(?:which|what|welche[rs]?|quelle?)\s+(?:consultation|vernehmlassung|law|gesetz|loi)\b(?!s|en\b)")


def is_data_question(q, r):
    """Whether to give the question to the engine, whose planner (Apertus) then reads it as a data question or as a
    lookup. Tried when it could be one: a question about a place with several schools ("the teacher's pay in Bern"),
    a pupil or school count, or data wording ("how many", "which", "more than", "average", "each") over consultations.
    A place with a single sheet stays with the passage answer, and so does every other question."""
    from .qa import is_pupil_count_question
    if r.get("places") and (is_pupil_count_question(q) or rules_plan(q, r) is not None):
        return True
    if r.get("places") and not _AGG_WORDS.search(_ascii(q)):
        main, _today = select({"dataset": "stapfer", "unit": "schools", "filters": {"places": r["places"]}})
        return len(main) > 1
    if _ONE_RECORD.search(_ascii(q)):  # "which consultation dealt with ...": one record, found by the search
        return False
    return bool(_DATA_WORDS.search(_ascii(q)))  # no place: districts, confessions, consultations, the whole survey


def answer(idx, q, r, use_llm=True):
    """The whole engine; None when it does not apply or cannot answer, and qa continues with the passage answer."""
    if not hasattr(idx, "P"):
        return None
    meta = {}
    plan = None
    if use_llm:
        plan, meta = make_plan(q, idx)
    note = lambda why: r.__setitem__("data_engine", {"used": False, "reason": why, "plan": plan, "planner": meta.get("raw")})
    from .qa import is_pupil_count_question
    if meta.get("lookup") and rules_plan(q, r) is None and not (r.get("places") and is_pupil_count_question(q)):
        return note("Apertus reads it as a lookup of one record")
    plan = plan or rules_plan(q, r)
    if not plan:
        return note("no usable plan")
    units, today = select(plan)
    if not units and not today:
        return note("the plan selects no record")
    readings = {}
    needs_read = (plan.get("measure") or {}).get("field") == "read"
    if needs_read:
        recs = list({u["rec"]["id"]: u["rec"] for u in units + today}.values())
        if not use_llm:
            return note("reading the answers needs Apertus")
        if len(recs) > MAX_READ:  # too many sheets to read for one answer; the passage answer (or a narrower question)
            return note(f"{len(recs)} records to read, more than {MAX_READ}")
        readings = read(idx, plan, recs)
        if readings is None:
            return note("Apertus returned no usable reading")
        if not any(totals(value_of(u, plan, readings)) or "yes" in (value_of(u, plan, readings) or {}) or
                   "text" in (value_of(u, plan, readings) or {}) for u in units):
            return note("no record has a usable value")  # the passage answer shows what the sheets do say
    text, A = render(q, plan, units, today, readings, idx)
    fails = check_numbers(text, A.sources, q, extra=A.extra)
    summary = phrase(q, text, A.sources, A.extra) if use_llm and not fails else None
    out_text = (summary + "\n\n" + text) if summary else text
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", out_text) if 0 < int(n) <= len(A.sources)})
    return {"sources": A.sources, "answer": out_text, "cited": cited, "answer_source": "aggregate", "citation_ok": not fails,
            "number_check": {"ok": not fails, "failures": fails},
            "data_plan": {k: v for k, v in plan.items()} | {"phrased_by": "apertus" if summary else "code", "planner": meta.get("raw")},
            "readings": {k: v for k, v in readings.items()}}
