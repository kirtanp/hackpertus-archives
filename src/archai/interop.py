"""Interoperability files for data/out, so other tools can read the dataset without our code:

  croissant.json            MLCommons Croissant 1.0 metadata (+ Responsible-AI fields): files, hashes, licence, fields
  records.context.jsonld    JSON-LD context: record fields -> Dublin Core / schema.org terms (records read as linked data)
  docs/record.schema.json   JSON Schema of one record (hand-written; tests/test_tools.py validates every record)

    PYTHONPATH=src python -m archai.interop          # writes the two files; validate with `mlcroissant validate --jsonld`"""
import hashlib, json
from pathlib import Path

OUT = Path("data/out")
REPO = "https://github.com/kirtanp/hackpertus-archives"

CROISSANT_CONTEXT = {
    "@language": "en", "@vocab": "https://schema.org/", "citeAs": "cr:citeAs", "column": "cr:column", "conformsTo": "dct:conformsTo",
    "cr": "http://mlcommons.org/croissant/", "rai": "http://mlcommons.org/croissant/RAI/", "data": {"@id": "cr:data", "@type": "@json"},
    "dataType": {"@id": "cr:dataType", "@type": "@vocab"}, "dct": "http://purl.org/dc/terms/", "examples": {"@id": "cr:examples", "@type": "@json"},
    "extract": "cr:extract", "field": "cr:field", "fileProperty": "cr:fileProperty", "fileObject": "cr:fileObject", "fileSet": "cr:fileSet",
    "format": "cr:format", "includes": "cr:includes", "isLiveDataset": "cr:isLiveDataset", "jsonPath": "cr:jsonPath", "key": "cr:key",
    "md5": "cr:md5", "parentField": "cr:parentField", "path": "cr:path", "recordSet": "cr:recordSet", "references": "cr:references",
    "regex": "cr:regex", "repeated": "cr:repeated", "replace": "cr:replace", "sc": "https://schema.org/", "separator": "cr:separator",
    "source": "cr:source", "subField": "cr:subField", "transform": "cr:transform",
    "samplingRate": "cr:samplingRate", "equivalentProperty": "cr:equivalentProperty"}

# (name, Croissant data type, description, JSON path inside the record or None for a top-level column)
FIELDS = [
    ("record_id", "sc:Text", "stable record id, e.g. stapfer:transkription:16", None),
    ("dataset", "sc:Text", "stapfer-enquete-1799 or federal-consultations-1960-1991", None),
    ("kind", "sc:Text", "school_survey_response or federal_consultation", None),
    ("title", "sc:Text", "record title", None),
    ("language", "sc:Text", "detected language (de, fr, it)", None),
    ("text", "sc:Text", "cleaned text; Stapfer answers as [question number] blocks", None),
    ("schema_version", "sc:Text", "record schema version (docs/record.schema.json)", None),
    ("archive_signature", "sc:Text", "archive signature of the original document", "$.provenance.archive_signature"),
    ("viewer_url", "sc:URL", "link to the original in the archive's own viewer", "$.provenance.viewer_url"),
    ("source_dataset", "sc:URL", "opendata.swiss dataset the record comes from", "$.provenance.source_dataset"),
    ("uncertain_readings", "sc:Integer", "transcriber-marked uncertain readings (#..##, Editionsregeln §15)", "$.quality.uncertain_readings"),
    ("enrichment_source", "sc:Text", "deterministic or apertus:<step>: which component produced the enrichment", "$.enrichment.source"),
]


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _extract(name, jp):
    """JSON Lines are read as a table: top-level fields are columns; nested ones take the parent column + a jsonPath transform."""
    if not jp:
        return {"extract": {"column": name}}
    parent, child = jp.removeprefix("$.").split(".", 1)
    return {"extract": {"column": parent}, "transform": {"jsonPath": child}}


def croissant():
    rep = json.loads((OUT / "metrics.json").read_text())
    files = [("records-jsonl", "records.jsonl", "application/jsonlines", "unified AI-ready records (one JSON object per line)"),
             ("records-csv", "records.csv", "text/csv", "flattened copy of records.jsonl"),
             ("stapfer-answers", "stapfer_answers.parquet", "application/x-parquet", "one row per Stapfer answer: raw and cleaned text, annotations, pages"),
             ("metrics", "metrics.json", "application/json", "every measurement with its scope")]
    bias = rep.get("bias_by_language", {})
    return {
        "@context": CROISSANT_CONTEXT, "@type": "sc:Dataset", "conformsTo": "http://mlcommons.org/croissant/1.0",
        "name": "archai-swiss-archives",
        "description": "Stapfer-Enquête 1799 (2,250 handwritten school-survey sheets, transcribed) and the Federal Consultations "
                       "1960–1991 (569), cleaned, linked to the Swiss Federal Archives' archive database and enriched with Apertus; "
                       "every record keeps its archive signature and a link to the original.",
        "license": "https://opendata.swiss/de/dataset/stapfer-enquete-schule-in-der-schweiz-1799",
        "url": REPO, "version": "1.1.0", "datePublished": "2026-10-18", "inLanguage": ["de", "fr", "it"],
        "creator": {"@type": "sc:Organization", "name": "Hack Apertus team ArchAI"},
        "citeAs": "Schmidt, H.R. / Messerli, A. / Osterwalder, F. / Tröhler, D. (Hgg.), Die Stapfer-Enquête. Edition der helvetischen "
                  "Schulumfrage von 1799, Bern 2015; Schweizerische Bundeskanzlei, Metadatenbank Vernehmlassungen 1960–1991.",
        "keywords": ["archives", "Switzerland", "Helvetic Republic", "school history", "federal consultations", "Apertus"],
        "rai:dataCollection": "Open-government releases on opendata.swiss (SQL dump of the Bern Stapfer database, consultations CSV, "
                              "archive-database dump); no crawling. Transcriptions are by the University of Bern edition team.",
        "rai:dataBiases": "Survey answered mostly by German-speaking, Reformed parishes (de 77%, fr 23%, it <1%). Extraction parity "
                          f"is measured per language in metrics.json bias_by_language (flagged gaps: {bias.get('flagged', [])}). "
                          "French sheets carry a date of writing less often (source-side).",
        "rai:personalSensitiveInformation": "Teacher names, ages and families from 1799 (persons deceased > 200 years). Consultation "
                                            "records name institutions, not private persons. See docs/ETHICS.md.",
        "rai:dataUseCases": "Retrieval and question answering with citations, training data for historical-language models, "
                            "historical research on the Helvetic school system and federal legislation.",
        "rai:dataLimitations": "Model-generated enrichment (titles, summaries, topics) is marked per record; quantities are rule-based "
                               "extractions (hand-checked precision ~90% on new extractions); see metrics.json scopes.",
        "distribution": [{"@type": "cr:FileObject", "@id": i, "name": n, "description": d, "contentUrl": n,
                          "encodingFormat": f, "sha256": _sha(OUT / n)} for i, n, f, d in files],
        "recordSet": [{"@type": "cr:RecordSet", "@id": "records", "name": "records", "key": {"@id": "records/record_id"},
                       "description": "one record per Stapfer sheet or consultation (docs/record.schema.json)",
                       "field": [{"@type": "cr:Field", "@id": f"records/{n}", "name": n, "description": d, "dataType": t,
                                  "source": {"fileObject": {"@id": "records-jsonl"},
                                             **_extract(n, jp)}} for n, t, d, jp in FIELDS]}],
    }


JSONLD_CONTEXT = {"@context": {
    "@version": 1.1, "@base": REPO + "/record/", "dct": "http://purl.org/dc/terms/", "schema": "https://schema.org/",
    "archai": REPO + "/vocab#", "xsd": "http://www.w3.org/2001/XMLSchema#",
    "stapfer": {"@id": REPO + "/record/stapfer:", "@prefix": True},  # record ids "stapfer:..." / "consultation:..." become URIs
    "consultation": {"@id": REPO + "/record/consultation:", "@prefix": True},
    "record_id": "@id", "dataset": {"@id": "schema:isPartOf", "@type": "@id"}, "kind": "archai:kind",
    "title": "dct:title", "language": "dct:language", "text": "schema:text", "text_raw_available": "archai:textRawAvailable",
    "schema_version": "schema:schemaVersion",
    "dates": {"@id": "dct:date", "@context": {"iso": "schema:value", "surface": "archai:surface", "source": "archai:extractedBy",
                                               "precision": "archai:precision", "year_inferred": "archai:yearInferred"}},
    "places": {"@id": "dct:spatial", "@context": {"place_id": "schema:identifier", "name": "schema:name", "municipality_today": "schema:containedInPlace",
                                                   "lat": {"@id": "schema:latitude", "@type": "xsd:decimal"}, "lon": {"@id": "schema:longitude", "@type": "xsd:decimal"},
                                                   "source": "archai:extractedBy"}},
    "persons": {"@id": "schema:mentions", "@context": {"person_id": "schema:identifier", "name": "schema:name", "role": "schema:roleName",
                                                        "source": "archai:extractedBy", "grounded": "archai:groundedInSource"}},
    "organisations": {"@id": "schema:sourceOrganization", "@context": {"org_id": "schema:identifier", "name": "schema:name", "type": "schema:additionalType",
                                                                        "abbr": "schema:alternateName", "role": "schema:roleName"}},
    "quantities": "archai:quantities", "archive_units": {"@id": "dct:relation", "@context": {"signature": "schema:identifier", "title": "dct:title",
                                                                                               "url": {"@id": "schema:url", "@type": "@id"}}},
    "provenance": {"@id": "dct:provenance", "@context": {"archive_signature": "schema:identifier", "repository": "schema:holdingArchive",
                                                          "viewer_url": {"@id": "schema:url", "@type": "@id"}, "source_dataset": {"@id": "dct:source", "@type": "@id"},
                                                          "facsimile_file": "archai:facsimileFile", "is_original": "archai:isOriginal"}},
    "quality": "archai:quality", "enrichment": "archai:enrichment", "places_mentioned": "archai:placesMentioned"}}


def main():
    (OUT / "croissant.json").write_text(json.dumps(croissant(), indent=1, ensure_ascii=False))
    (OUT / "records.context.jsonld").write_text(json.dumps(JSONLD_CONTEXT, indent=1, ensure_ascii=False))
    print("wrote data/out/croissant.json and data/out/records.context.jsonld")


if __name__ == "__main__":
    main()
