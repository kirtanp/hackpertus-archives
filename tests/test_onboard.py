import json
from archai import onboard as O

MESSY = (
    "Nr;Bezeichnung;Beschreibung;Datum;Ort;Person;Signatur;E;N;Geburtsdatum\n"
    "17;Schulhaus Hörnli;Gesuch um Beitrag an das Schulhaus, Kontakt hans.muster@example.ch;12.03.1962;Hörnli;Hans Muster;E6100C#1998/106#1039*;2707000;1240000;01.02.1990\n"
    "18;Vernehmlassung Lebensmittel;Stellungnahme, AHV 756.1234.5678.97, Tel. 031 123 45 67;1978;Lausanne;Anne Dupont;E3120B#1996/434#391*;2538000;1152000;\n"
)


def _run(tmp_path, monkeypatch, mask):
    f = tmp_path / "messy.csv"
    f.write_bytes(MESSY.encode("cp1252"))  # Windows encoding + semicolons, as Swiss exports often are
    monkeypatch.setattr(O, "BASE", tmp_path / "onboard")
    rep = O.run(str(f), "messy", "https://opendata.swiss/de/dataset/example", "CC BY 4.0", None, mask, 0, False, "acquire", False)
    recs = [json.loads(l) for l in open(tmp_path / "onboard" / "messy" / "records.jsonl", encoding="utf-8")]
    return rep, recs


def test_onboard_messy_file(tmp_path, monkeypatch):
    rep, recs = _run(tmp_path, monkeypatch, mask=False)
    assert rep["records"] == 2 and rep["schema_invalid"] == 0
    src = json.loads((tmp_path / "onboard" / "messy" / "source.json").read_text())
    assert src["encoding"] == "cp1252" and src["delimiter"] == ";"
    a, b = recs
    assert a["record_id"] == "messy:17" and a["title"] == "Schulhaus Hörnli"
    assert {d["iso"] for d in a["dates"]} >= {"1962-03-12"} and b["dates"][0]["iso"] == "1978"
    assert any(p.get("lat") and abs(p["lat"] - 47.37) < 0.2 for p in a["places"])  # gazetteer and LV95 coordinates
    assert a["archive_units"][0]["kind"] == "federal_archive" and a["provenance"]["archive_signature"].startswith("E6100C")
    assert a["source_row"]["Bezeichnung"] == "Schulhaus Hörnli"  # original row kept
    assert a["quality"]["privacy_flags"]["email"] == 2 and a["quality"]["privacy_flags"]["birth_date_possibly_living"] == 1
    assert {"ahv_number", "phone"} <= set(b["quality"]["privacy_flags"])


def test_onboard_masks_pii(tmp_path, monkeypatch):
    _, recs = _run(tmp_path, monkeypatch, mask=True)
    blob = json.dumps(recs, ensure_ascii=False)
    assert "hans.muster@example.ch" not in blob and "756.1234.5678.97" not in blob and "031 123 45 67" not in blob
    assert "[REDACTED:email]" in blob and recs[0]["quality"]["pii_masked"]


def test_mapping_validation_rejects_bad_proposal(tmp_path):
    import pandas as pd
    df = pd.DataFrame({"id": ["a1", "a2"], "titel": ["Erste Vernehmlassung", "Zweite Vernehmlassung"], "jahr": ["1970", "1971"]})
    prof = O.profile(df)
    m = O.validate_mapping({"id": "id", "title": ["id"], "dates": ["titel", "jahr"], "places": ["nope"]}, df, prof, "apertus")
    assert m["title"] == [] and m["dates"] == ["jahr"] and m["places"] == []
    assert any("not a column" in n for n in m["validation_notes"])
