import json
from collections import Counter
from pathlib import Path

import pytest

from backend.app.ingestion.legal_parser import (
    PARSER_VERSION,
    build_inspection_report,
    parse_legal_document,
    validate_corpus,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
HTML_PATH = ROOT_DIR / "data/raw/DeMuc_20.2_Lao_Dong.html"


@pytest.fixture(scope="session")
def canonical_corpus():
    assert HTML_PATH.is_file(), f"HTML file not found at {HTML_PATH}"
    return parse_legal_document(HTML_PATH)


def test_import_no_side_effects():
    assert PARSER_VERSION == "1.2.0"


def test_topic_metadata(canonical_corpus):
    metadata = canonical_corpus["metadata"]
    assert metadata["topic_code"] == "20.2"
    assert metadata["topic_name"] == "Lao động"
    assert metadata["document_id"] == "phap-dien:20.2"


def test_article_count(canonical_corpus):
    assert len(canonical_corpus["articles"]) == 477


def test_source_distribution(canonical_corpus):
    articles = canonical_corpus["articles"]
    source_types = [a["source_type"] for a in articles]
    counts = Counter(source_types)
    assert counts["LQ"] == 220
    assert counts["NĐ"] == 174
    assert counts["TT"] == 83


def test_article_ids_validity(canonical_corpus):
    articles = canonical_corpus["articles"]
    for a in articles:
        assert isinstance(a["article_id"], str)
        assert a["article_id"] != ""
    
    article_ids = [a["article_id"] for a in articles]
    assert len(article_ids) == len(set(article_ids))


def test_duplicate_unit_ids(canonical_corpus):
    articles = canonical_corpus["articles"]
    all_unit_ids = [
        str(unit["unit_id"])
        for article in articles
        for unit in article["content_units"]
    ]
    assert len(all_unit_ids) == len(set(all_unit_ids))


def test_non_empty_articles(canonical_corpus):
    for a in canonical_corpus["articles"]:
        assert a["content_text"].strip() != ""


def test_articles_have_chapter(canonical_corpus):
    for a in canonical_corpus["articles"]:
        assert a["chapter"] is not None


def test_validation_report_metrics(canonical_corpus):
    validation = validate_corpus(canonical_corpus)
    assert validation["table_count"] == 63
    assert validation["attachment_count"] == 39
    assert validation["article_relation_count"] == 928
    assert validation["structure_relation_count"] == 6
    assert validation["same_topic_unresolved_relation_count"] == 0
    assert validation["is_valid"] is True


def test_relation_schema_keys(canonical_corpus):
    articles = canonical_corpus["articles"]
    for a in articles:
        for r in a.get("relations", []):
            assert "same_topic_20_2" not in r
            assert "same_topic" in r
            assert isinstance(r.get("source_id"), (str, type(None)))
            assert isinstance(r.get("target_id"), (str, type(None)))

    for r in canonical_corpus["structure_relations"]:
        assert "same_topic_20_2" not in r
        assert "same_topic" in r
        assert isinstance(r.get("source_id"), (str, type(None)))
        assert isinstance(r.get("target_id"), (str, type(None)))


def test_article_20_2_nd_4_2_clauses_points(canonical_corpus):
    target_article = next(
        (a for a in canonical_corpus["articles"] if a["article_code"] == "20.2.NĐ.4.2"),
        None
    )
    assert target_article is not None

    clause_1_units = [u for u in target_article["content_units"] if u["clause_number"] == "1"]
    clause_2_units = [u for u in target_article["content_units"] if u["clause_number"] == "2"]

    point_a_clause_1 = next(
        u for u in clause_1_units if u["unit_type"] == "point" and u["point_label"] == "a"
    )
    assert "Thực hiện hợp đồng lao động" in point_a_clause_1["text"]

    point_a_clause_2 = next(
        u for u in clause_2_units if u["unit_type"] == "point" and u["point_label"] == "a"
    )
    assert "Doanh nghiệp hoạt động theo Luật Doanh nghiệp" in point_a_clause_2["text"]


def test_preamble_unit_presence(canonical_corpus):
    target_article = next(
        (a for a in canonical_corpus["articles"] if a["article_code"] == "20.2.LQ.1"),
        None
    )
    assert target_article is not None
    
    has_preamble = any(u["unit_type"] == "preamble" for u in target_article["content_units"])
    assert has_preamble, "Article 20.2.LQ.1 must have a preamble unit since it has no clauses"


def test_all_ids_are_strings(canonical_corpus):
    for a in canonical_corpus["articles"]:
        assert isinstance(a["article_id"], str)
        for u in a["content_units"]:
            assert isinstance(u["unit_id"], str)
