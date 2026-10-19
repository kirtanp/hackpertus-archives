"""Real data for the presentation site (static/site, served at /site): every example it shows is taken from the
records, the raw transcriptions, the Apertus agent traces, the run manifest and the Q&A path itself, so the site never
shows an invented passage under a real archive signature. Built once, after the search index (GET /api/site).

The page keeps its built-in examples only as a fallback when this endpoint cannot be reached."""
import json
import random
import re
import threading

import pandas as pd

from . import qa
from .qa import OUT

SEED = 1799


# ---------------------------------------------------------------- 02 How it works: the run manifest, a real cleaning
def pipeline():
    return json.load(open(OUT / "run_manifest.json", encoding="utf-8"))


MARKUP = [(re.compile(r"#\s*([^#]+?)\s*##"), "uncertain_reading §15", lambda m: m.group(1)),
          (re.compile(r"°-°°"), "dash_mark", lambda m: "–"),
          (re.compile(r"\|\|\[Seite (\d+)\]"), "page_break", lambda m: ""),
          (re.compile(r"\$([^$]+)\$\$"), "latin_script", lambda m: m.group(1)),
          (re.compile(r"\*([^*]+)\*\*"), "note", lambda m: m.group(1)),
          (re.compile(r"\s*@\s*"), "line_break", lambda m: " ")]


def morph(answers):
    """One real answer, raw edition markup -> clean text, as tokens: {raw} or {raw, clean, ann}."""
    rows = answers[answers.text_raw.str.contains(r"#|°-°°|\|\|\[Seite|\$", regex=True, na=False)]
    rows = rows[rows.text_raw.str.len().between(80, 260) & ~rows.text_raw.str.contains(r"#\s*\$|\$\s*#", regex=True)]
    best = None
    for a in rows.itertuples():
        kinds = {ann for rx, ann, _f in MARKUP[:-1] if rx.search(a.text_raw)}
        if best is None or len(kinds) > best[0]:
            best = (len(kinds), a)
        if len(kinds) >= 3:
            break
    if not best:
        return None
    a = best[1]
    raw, toks, pos = a.text_raw, [], 0
    hits = sorted(((m.start(), m.end(), ann, f(m), m.group(0)) for rx, ann, f in MARKUP for m in rx.finditer(raw)), key=lambda x: x[0])
    for s, e, ann, clean, surface in hits:
        if s < pos:
            continue
        toks += [{"raw": w} for w in re.findall(r"\S+(?:\s+\S+){0,3}", raw[pos:s])]
        if ann != "line_break":
            toks.append({"raw": surface, "clean": clean, "ann": ann})
        pos = e
    toks += [{"raw": w} for w in re.findall(r"\S+(?:\s+\S+){0,3}", raw[pos:])]
    return {"tokens": toks[:14], "doc_id": a.doc_id, "question_no": a.question_no, "clean": a.text_clean}


# ---------------------------------------------------------------- 03 Agent loop: one real trace
def _excerpt(text, names, n=420):
    """The part of the record's text that holds the most of the names, at most n characters."""
    t = " ".join(text.split())
    best, at = -1, 0
    for i in range(0, max(1, len(t) - n), 40):
        k = sum(nm.lower() in t[i:i + n].lower() for nm in names)
        if k > best:
            best, at = k, i
    if at:  # start at a question number or at least a word, end at a word
        q = t.rfind(" [", 0, at + 1)
        at = q + 1 if q >= 0 and at - q < 240 else t.find(" ", at) + 1
    out = t[at:at + n]
    return out if at + n >= len(t) else out.rsplit(" ", 1)[0] + " …"


def _segments(text, marks):
    """[text, kind] segments with each mark (place, person, qty, date) highlighted where it occurs."""
    spans = []
    for s, kind in marks:
        for m in re.finditer(rf"(?<!\w){re.escape(s)}(?!\w)", text, flags=re.I):
            if not any(a < m.end() and m.start() < b for a, b, _k in spans):
                spans.append((m.start(), m.end(), kind))
                break
    out, pos = [], 0
    for a, b, kind in sorted(spans):
        out += [[text[pos:a], ""], [text[a:b], kind]]
        pos = b
    return out + [[text[pos:], ""]]


def _short(res):
    if isinstance(res, list):
        if res and isinstance(res[0], dict) and "lat" in res[0]:
            p = res[0]
            return f'{p.get("name_1799")} · {p["lat"]:.2f} N · {p["lon"]:.2f} E'
        return f"{len(res)} found" if res else "nothing found"
    if isinstance(res, dict):
        return ", ".join(f"{k}: {str(v)[:24]}" for k, v in list(res.items())[:2])
    return str(res)[:40]


def _grounded(t):
    """The trace's own validate step: [entity, grounded in the source]."""
    v = next((st["result"] for st in t["steps"] if st.get("step") == "validate"), [])
    return [(x["entity"], bool(x["grounded"])) for x in v if isinstance(x, dict)]


def agent(records):
    """A real Apertus agent-v4 trace, shown step by step: plan, five of its tool calls, the composed metadata, and the
    grounding check (which named entities are literally in the source: the unsupported ones are rejected)."""
    rows = [json.loads(line) for line in open(OUT / "agent_traces_v4_json.jsonl", encoding="utf-8")]
    rows = [r for r in rows if r["kind"] == "stapfer" and r.get("unsupported") and r.get("record") in records
            and len((r.get("final") or {}).get("places") or []) + len((r.get("final") or {}).get("persons") or []) >= 4]
    if not rows:
        return None
    t = rows[0]
    rec, final = records[t["record"]], t["final"] or {}
    calls = [c for st in t["steps"] for c in st.get("results") or [] if isinstance(st.get("results"), list) and st["step"].startswith("call")]
    shown, seen = [], set()
    for c in calls:  # five calls, distinct tools first
        key = c["tool"] if c["tool"] not in seen else None
        if key:
            seen.add(key); shown.append(c)
    shown += [c for c in calls if c not in shown]
    shown = shown[:5]
    grounded = _grounded(t)
    unsup = {n for n, ok in grounded if not ok}
    ents = [[n, True] for n, ok in grounded if ok][:3] + [[n, False] for n, ok in grounded if not ok][:1]
    text = _excerpt(rec["text"], [n for n, ok in ents if ok])
    q = rec.get("quantities") or {}
    marks = [(n, "person" if n in (final.get("persons") or []) else "place") for n, ok in ents if ok] + \
            [(x["surface"], "qty") for x in (q.get("pupils") or [])[:2] if x.get("surface")]
    sig = rec["provenance"]["archive_signature"]
    fmt_args = lambda a: ", ".join(f'{k}="{v}"' for k, v in (a or {}).items())[:40]  # noqa: E731
    return {"record": t["record"], "signature": sig, "model": t["model"], "prompt_version": t["prompt_version"],
            "rec": _segments(text, marks) + [[" · ", ""], [sig, "sig"]],
            "plan_json": json.dumps({"calls": [{"tool": c["tool"], **({"args": c["args"]} if c.get("args") else {})} for c in shown]},
                                    ensure_ascii=False, indent=1),
            "n_calls": t["planner"]["n_calls"],
            "tools": [{"call": f'{c["tool"]}({fmt_args(c.get("args"))})', "res": _short(c.get("result"))} for c in shown],
            "tooln": [c["tool"] if c["tool"] != "lookup_place" else "place: " + str((c.get("args") or {}).get("name", ""))[:14] for c in shown],
            "toolr": [_short(c.get("result")) for c in shown],
            "compose_json": json.dumps({k: final.get(k) for k in ("title", "summary_en", "persons", "places", "topics", "legibility")},
                                       ensure_ascii=False, indent=1),
            "ents": ents, "unsupported": sorted(unsup), **_loop_notes(t)}


def _loop_notes(t):
    """What the trace says about the loop steps the page narrates: repairs, the follow-up round, the required tools."""
    calls = [st for st in t["steps"] if st["step"].startswith("call")]
    res = [c for st in calls for c in st.get("results") or [] if isinstance(st.get("results"), list)]
    fixed = [c for c in res if c.get("repairs")]
    pl = t.get("planner") or {}
    req = pl.get("baseline_tools") or []
    used = set(pl.get("baseline_tools_chosen") or [])
    last = calls[-1] if calls else {}
    return {"repair_text": "\n".join(f'{c["tool"]} repair: {", ".join(c["repairs"])}' for c in fixed[:2])
                           or f'{len(res)} calls, every argument already matched the tool schema\n→ no repair needed',
            "observe_text": f'{len(calls)} call rounds: ' + " + ".join(str(st.get("calls")) for st in calls)
                            + (f'\nstopped: {last["stopped"]}' if last.get("stopped") else ""),
            "critic_text": "required: " + "  ".join(f'{x} {"✓" if x in used else "✗"}' for x in req)
                           + ("\n→ no feedback round needed" if used >= set(req) else "\n→ missing tools named once, then run as baseline"),
            "seconds": t.get("seconds")}


# ---------------------------------------------------------------- 06 Play
DEC_WORDS = [  # (word, correct meaning, three wrong ones, note); the context comes from a real answer
    ("Hornung", "February", ["harvest time", "a horn-blower", "March"], "An old German month name. normalize_dates() reads it as February."),
    ("Mägdchen", "girls", ["maids", "magpies", "small markets"], "An old spelling of Mädchen. extract_quantities() counts girls and boys separately."),
    ("Mütt", "a grain measure", ["a coin", "a mitten", "a field"], "A dry measure for grain. Teachers were often paid partly in kind."),
    ("fl.", "florins (Gulden)", ["flour", "floors", "fluent"], "The abbreviation for florin, the Gulden. It turns up in pay answers."),
    ("Batzen", "a small coin", ["a bakery", "a batch of grain", "a thunderstorm"], "A small coin. School fees were often paid in Batzen."),
    ("régent", "the schoolmaster", ["the ruler of the canton", "the parish priest", "a register book"], "In Vaud and Fribourg, the village teacher was the régent."),
    ("Knaben", "boys", ["bones", "knives", "farmhands"], "Boys. Pupil counts are the most-asked number in the survey."),
    ("livres", "a currency (pounds)", ["books", "deliveries", "litres"], "Here livres means money, not books. The bilingual extractor has to know the difference."),
    ("Schulstube", "the schoolroom", ["school fees", "the school stove", "the school board"], "The room where class was held, often in the teacher's own house."),
    ("Kernen", "spelt grain", ["apple cores", "candles", "nuclear"], "Dehusked spelt, a staple grain. Not \"nuclear\"."),
]


def dec(answers, docs):
    out = []
    for w, c, o, n in DEC_WORDS:
        rx = re.compile(rf"(?<!\w){re.escape(w)}(?!\w)")
        hit = answers[answers.text_clean.str.contains(rx, na=False)]
        if hit.empty:
            continue
        a = hit.iloc[0]
        t = " ".join(a.text_clean.split())
        m = rx.search(t)
        ctx = t[max(0, m.start() - 40):m.end() + 40].strip(" ,.;")
        d = docs[a.doc_id]
        out.append({"w": w, "lang": "FR" if d.get("language") == "fr" else "DE", "ctx": ("…" if m.start() > 40 else "") + ctx + "…",
                    "c": c, "o": o, "n": n, "cite": f'{", ".join(p["name_1799"] for p in d["places"][:1])} · {a.question_no}'})
    return out


ROUTE_QS = [  # (question, language, gold: dataset, section, task)
    ("Was verdiente der Schulmeister in Fischenthal?", "DE", {"ds": "stapfer", "sec": "IV · income", "task": "lookup"}),
    ("Quels livres lisait-on à l'école de Moudon ?", "FR", {"ds": "stapfer", "sec": "III · books", "task": "lookup"}),
    ("How many children went to school in Bern in winter?", "EN", {"ds": "stapfer", "sec": "III · pupils", "task": "aggregate"}),
    ("Welche Vernehmlassungen betrafen die Kernenergie in den 1970ern?", "DE", {"ds": "consultations", "sec": "— none", "task": "lookup"}),
    ("Who won the 1998 football World Cup?", "EN", {"ds": "decline", "sec": "— none", "task": "decline"}),
]


def route_game(idx, metrics):
    """The rules router's real output for each question (no model call), shown after the player routes it."""
    from . import dataq
    un = (metrics.get("qa_answer_check") or {}).get("unanswerable") or {}
    out = []
    for q, lang, g in ROUTE_QS:
        r = qa.route(q, idx.gaz)
        data = dataq.is_data_question(q, r)
        shown = {"intents": r["intents"], "places": r["places"], **({"years": r["years"]} if r.get("years") else {}),
                 "data_question": data}
        n = {"aggregate": "\"How many\" over several schools goes to the data engine: code reads, checks and sums; Apertus only phrases the result.",
             "decline": f'Out of scope: no intent, no place. ArchAI declines instead of guessing ({un.get("declined", "?")} of {un.get("questions", "?")} unanswerable test questions declined).',
             "lookup": "One fact of one record: the route picks the questionnaire section and the place, checked against the gazetteer."}[g["task"]]
        if g["ds"] == "consultations":
            n = "The consultations dataset, 1960 to 1991; a year or decade in the question is read by code."
        out.append({"q": q, "lang": lang, "g": g, "json": json.dumps(shown, ensure_ascii=False, indent=1), "n": n})
    return out


def cal_game(idx, records):
    """Real passages with an answer that cites them: as written (the number check passes) or with one number changed
    (the number check, run here for real, blocks it). The player guesses which before the check runs."""
    rng = random.Random(SEED)
    picks, out = [], []
    by_sec = {"III.12": "How many pupils went to school in {p}?", "III.11.d": "How old was the teacher in {p}?",
              "IV.16": "What was the teacher in {p} paid?"}
    for i, p in enumerate(idx.P):
        if p["kind"] != "stapfer":
            continue
        sec = next((s for s in by_sec if p["qno"].startswith(s)), None)
        body = " ".join(qa._answer_body(p).split())
        nums = [n for n in re.findall(r"(?<![\d.])\d{1,3}(?![\d])", body) if n != "0"]
        if sec and nums and 15 <= len(body) <= 90:
            picks.append((sec, i, body, nums))
    rng.shuffle(picks)
    used = set()
    for sec, i, body, nums in picks:
        if sec in used and len(out) < 3 or len(out) >= 6:
            continue
        used.add(sec)
        p = idx.P[i]
        rec = records.get(p["doc_id"]) or {}
        place = next((x["name"] for x in rec.get("places") or [] if x.get("name")), None)
        if not place:
            continue
        n = nums[0]
        wrong = len(out) % 2 == 1
        shown = str(int(n) + (20 if int(n) < 60 else 12)) if wrong else n
        ans = re.sub(rf"(?<![\d.]){n}(?!\d)", shown, f'"{body}"', count=1) + " [1]"
        src = [idx._hit(i)]
        fails = qa.check_numbers(ans, src, "")
        out.append({"q": by_sec[sec].format(p=place), "a": ans, "p": body, "cite": p.get("cite"),
                    "ok": 0 if fails else 1,
                    "why": (f"{', '.join(str(x['number']) for x in fails)} is not in [1]: the number check blocks this answer." if fails
                            else "Every number is in [1], so the number check passes.")})
    return out


def entity_rounds(records):
    """Real Apertus outputs (agent traces): the names it gave for a record, and which ones are literally in the source.
    The unsupported ones are what the grounding check rejects; decoys from other sheets fill the round up."""
    rows = [json.loads(line) for line in open(OUT / "agent_traces_v4_json.jsonl", encoding="utf-8")]
    rng = random.Random(SEED)
    pool = [n for r in rows for n in ((r.get("final") or {}).get("places") or [])]
    out = []
    for t in rows:
        if t.get("record") not in records or not t.get("unsupported"):
            continue
        final = t.get("final") or {}
        rec = records[t["record"]]
        good = [n for n, ok in _grounded(t) if ok and n.lower() in rec["text"].lower()]
        if len(good) < 2:
            continue
        src = _excerpt(rec["text"], good, 330)
        good = [n for n in good if n.lower() in src.lower()][:4]
        fake = [n for n, ok in _grounded(t) if not ok and n.lower() not in src.lower()][:2]
        decoys = [n for n in rng.sample(pool, min(60, len(pool))) if n.lower() not in rec["text"].lower() and n not in fake][:6 - len(fake)]
        if len(good) < 2:
            continue
        place = (rec.get("places") or [{}])[0].get("name") or t["record"]
        out.append({"record": t["record"], "kind": f'{rec["dataset"].split("-")[0]} · {rec.get("language") or ""}', "name": f'{place} · {rec.get("language") or ""}',
                    "src": src, "signature": rec["provenance"]["archive_signature"],
                    "good": [[n, "place" if n in (final.get("places") or []) else "person"] for n in good],
                    "fake": [[n, "place"] for n in fake + decoys], "apertus_unsupported": fake})
        if len(out) == 4:
            break
    return out


def geo_rounds(game_round, records, docs, n=5):
    rng = random.Random(SEED)
    ids = sorted(rid for rid, r in records.items() if r.get("dataset", "").startswith("stapfer"))
    out = []
    for rid in rng.sample(ids, len(ids)):
        g = game_round(rid)
        if not g:
            continue
        rv = g["reveal"]
        pl = ((docs.get(rid) or {}).get("places") or [{}])[0]
        hint = ", ".join(x for x in (f'district of {pl["district_today"]} (today)' if pl.get("district_today") else "",
                                     (pl.get("confession") or "").lower()) if x)
        out.append({"c": rv["name_1799"], "lat": rv["lat"], "lon": rv["lon"], "lang": (g["language"] or "de").upper()[:2],
                    "text": " · ".join(c["text"] for c in g["clues"][:3])[:300],
                    "hint": hint or "no district on record", "today": rv["municipality_today"],
                    "signature": rv["signature"], "url": rv["url"]})
        if len(out) == n:
            break
    return out


# ---------------------------------------------------------------- 04 Simulator: the example questions, answered live
SIM_QS = ["Wie alt war der Lehrer in Hörndli?", "how many students where there in the schoool in bern in 1799 ?",
          "Combien d'écoles y avait-il à Lausanne en 1799 ?", "Which consultation in 1984 dealt with data protection?",
          "Who won the 1998 football World Cup?"]
_SIM = {"answers": None, "running": False}
_LOCK = threading.Lock()


def sim_answers(idx, use_llm=True):
    """The example questions through the real /api/ask path, once per server start (in the background)."""
    with _LOCK:
        if _SIM["answers"] is not None or _SIM["running"]:
            return _SIM["answers"]
        _SIM["running"] = True

    def run():
        out = []
        for q in SIM_QS:
            try:
                out.append({"question": q, **qa.answer(idx, q, 6, use_llm, "dynamic")})
            except Exception as e:  # one failed example never blocks the others
                out.append({"question": q, "error": str(e)})
        _SIM["answers"] = out
        _SIM["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return None


_CACHE = {}


def build(idx, records, game_round, metrics):
    if "site" in _CACHE:
        return _CACHE["site"]
    answers = pd.read_parquet(OUT / "stapfer_answers.parquet", columns=["doc_id", "question_no", "text_raw", "text_clean"])
    docs = {json.loads(line)["doc_id"]: json.loads(line) for line in open(OUT / "stapfer_documents.jsonl", encoding="utf-8")}
    ents = entity_rounds(records)
    _CACHE["site"] = {"pipeline": pipeline(), "morph": morph(answers), "agent": agent(records), "dec": dec(answers, docs),
                      "route": route_game(idx, metrics), "cal": cal_game(idx, records), "hal": ents[:3], "rush": ents,
                      "geo": geo_rounds(game_round, records, docs)}
    return _CACHE["site"]
