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


def test_parser_version():
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
    assert validation["attachment_count"] == 41
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


@pytest.fixture
def minimal_appendix_html():
    return """
<p class="pChuong">Chương IV</p>
<p class="pChuong">ĐIỀU KHOẢN THI HÀNH</p>
<p class="pDieu"><a name="2000200000000001700022040282120097000140"></a>Điều 20.2.NĐ.6.14. Hiệu lực và trách nhiệm thi hành</p>
<p class="pGhiChu">(Điều 14 Nghị định số 97/2022/NĐ-CP, có hiệu lực thi hành kể từ ngày 15/01/2023)</p>
<p class="pNoiDung"></p>
<p>1. Nghị định này có hiệu lực thi hành từ ngày 15 tháng 01 năm 2023.</p>
<p>6. Các Bộ trưởng, Thủ trưởng cơ quan ngang bộ, Thủ trưởng cơ quan thuộc Chính phủ, Chủ tịch Ủy ban nhân dân tỉnh, thành phố trực thuộc trung ương, Hội đồng thành viên các tập đoàn kinh tế nhà nước, tổng công ty nhà nước, công ty mẹ trong nhóm công ty mẹ - công ty con, công ty độc lập chịu trách nhiệm thi hành Nghị định này./.</p>
<p><strong>PHỤ LỤC I</strong></p>
<p>QUY TRÌNH XÂY DỰNG PHƯƠNG ÁN SỬ DỤNG LAO ĐỘNG</p>
<p><strong>PHỤ LỤC II</strong></p>
<p>HỆ THỐNG BIỂU MẪU</p>
<p>Mẫu số 01</p>
<table>
  <tr><td>TT</td><td>Nội dung</td></tr>
  <tr><td>1</td><td>Mẫu số 01</td></tr>
</table>
"""


def test_appendix_boundary_bug_minimal_correct(tmp_path, minimal_appendix_html):
    html_file = tmp_path / "test.html"
    html_file.write_text(minimal_appendix_html, encoding="utf-8")
    corpus = parse_legal_document(
        html_file,
        topic_code_override="20.2",
        topic_name_override="Lao động"
    )
    assert len(corpus["articles"]) == 1
    article = corpus["articles"][0]

    # Correct boundary behavior: the article content_units should only contain clauses, not appendix elements
    unit_texts = [u["text"] for u in article["content_units"]]
    for text in unit_texts:
        assert "PHỤ LỤC I" not in text
        assert "PHỤ LỤC II" not in text
        assert "HỆ THỐNG BIỂU MẪU" not in text
        assert "Mẫu số 01" not in text

    # The appendix elements are parsed as attachments of the article
    assert len(article["attachments"]) == 2
    att1, att2 = article["attachments"]
    assert att1["title"] == "PHỤ LỤC I"
    assert att2["title"] == "PHỤ LỤC II"


def test_appendix_boundary_bug_real_corpus_correct(canonical_corpus):
    article = next(
        (a for a in canonical_corpus["articles"] if a["article_code"] == "20.2.NĐ.6.14"),
        None
    )
    assert article is not None

    # Correct boundary behavior: the real article should only have 6 clause units
    assert len(article["content_units"]) == 6
    unit_texts = [u["text"] for u in article["content_units"]]
    for text in unit_texts:
        assert "PHỤ LỤC I" not in text
        assert "PHỤ LỤC II" not in text
        assert "HỆ THỐNG BIỂU MẪU" not in text
        assert "Mẫu số 01" not in text

    # We should have PHỤ LỤC I and PHỤ LỤC II as attachments
    assert len(article["attachments"]) == 2
    att1, att2 = article["attachments"]
    assert att1["title"] == "PHỤ LỤC I"
    assert att2["title"] == "PHỤ LỤC II"


def test_appendix_boundary_bug_minimal_desired(tmp_path, minimal_appendix_html):
    html_file = tmp_path / "test.html"
    html_file.write_text(minimal_appendix_html, encoding="utf-8")
    corpus = parse_legal_document(
        html_file,
        topic_code_override="20.2",
        topic_name_override="Lao động"
    )
    assert len(corpus["articles"]) == 1
    article = corpus["articles"][0]

    # Desired: the article should only contain clause 1 and clause 6, no appendix content
    assert len(article["content_units"]) == 2
    unit_texts = [u["text"] for u in article["content_units"]]
    for text in unit_texts:
        assert "PHỤ LỤC I" not in text
        assert "PHỤ LỤC II" not in text
        assert "HỆ THỐNG BIỂU MẪU" not in text
        assert "Mẫu số 01" not in text


def test_appendix_boundary_bug_real_corpus_desired(canonical_corpus):
    article = next(
        (a for a in canonical_corpus["articles"] if a["article_code"] == "20.2.NĐ.6.14"),
        None
    )
    assert article is not None

    # Desired: the article 20.2.NĐ.6.14 should only contain its 6 clauses
    assert len(article["content_units"]) == 6
    for u in article["content_units"]:
        assert u["unit_type"] == "clause"
        assert "PHỤ LỤC I" not in u["text"]
        assert "PHỤ LỤC II" not in u["text"]
        assert "HỆ THỐNG BIỂU MẪU" not in u["text"]
        assert "Mẫu số 01" not in u["text"]

    # Check that PHỤ LỤC I and PHỤ LỤC II are present in the attachments of the article
    assert len(article["attachments"]) == 2
    att1, att2 = article["attachments"]
    assert att1["title"] == "PHỤ LỤC I"
    assert att2["title"] == "PHỤ LỤC II"

    # Check that form markers and tables are preserved
    assert any("Mẫu số 01" in form for form in att2["form_markers"])
    assert any("Mẫu số 11" in form for form in att2["form_markers"])
    assert len(att2["tables"]) > 0


def test_appendix_content_conservation(tmp_path, minimal_appendix_html):
    html_file = tmp_path / "test.html"
    html_file.write_text(minimal_appendix_html, encoding="utf-8")
    corpus = parse_legal_document(
        html_file,
        topic_code_override="20.2",
        topic_name_override="Lao động"
    )
    article = corpus["articles"][0]

    # 2 clauses in article content units
    assert len(article["content_units"]) == 2
    assert article["content_units"][0]["clause_number"] == "1"
    assert article["content_units"][1]["clause_number"] == "6"

    # 2 attachments representing PHỤ LỤC I and PHỤ LỤC II
    assert len(article["attachments"]) == 2
    att1, att2 = article["attachments"]

    assert att1["title"] == "PHỤ LỤC I"
    assert "QUY TRÌNH XÂY DỰNG PHƯƠNG ÁN SỬ DỤNG LAO ĐỘNG" in att1["text"]

    assert att2["title"] == "PHỤ LỤC II"
    assert "HỆ THỐNG BIỂU MẪU" in att2["text"]
    assert "Mẫu số 01" in att2["text"]

    # 1 table inside attachments
    assert len(att2["tables"]) == 1
    table = att2["tables"][0]
    table_cells = list(table.get("headers", []))

    for row in table.get("rows", []):
        table_cells.extend(row)

    assert "TT" in table_cells
    assert "Nội dung" in table_cells
    assert "1" in table_cells
    assert "Mẫu số 01" in table_cells

    article_table_ids = {
        current_table["table_id"]
        for current_table in article["tables"]
    }

    assert table["table_id"] in article_table_ids

    # The table is also preserved in the article's table list (for canonical global counting)
    assert len(article["tables"]) == 1


def test_appendix_boundary_false_positive_protection(tmp_path):
    html_content = """
<p class="pChuong">Chương IV</p>
<p class="pChuong">ĐIỀU KHOẢN THI HÀNH</p>
<p class="pDieu"><a name="test_art"></a>Điều 20.2.NĐ.6.14. Hiệu lực và trách nhiệm thi hành</p>
<p class="pGhiChu">(Ghi chú...)</p>
<p class="pNoiDung"></p>
<p>1. Thực hiện theo Mẫu số 12/PLI Phụ lục I ban hành kèm theo Nghị định này.</p>
<p>2. Điều này quy định tại Phụ lục II.</p>
"""
    html_file = tmp_path / "test.html"
    html_file.write_text(html_content, encoding="utf-8")
    corpus = parse_legal_document(
        html_file,
        topic_code_override="20.2",
        topic_name_override="Lao động"
    )
    assert len(corpus["articles"]) == 1
    article = corpus["articles"][0]
    # The article should have 2 clauses because the text contains Phụ lục/Mẫu số inside sentences,
    # which should not trigger the appendix boundary.
    assert len(article["content_units"]) == 2
    assert len(article["attachments"]) == 0


from copy import deepcopy


def without_volatile_metadata(corpus):
    normalized = deepcopy(corpus)
    normalized["metadata"].pop("parsed_at", None)
    return normalized


def test_parser_determinism():
    corpus1 = parse_legal_document(HTML_PATH)
    corpus2 = parse_legal_document(HTML_PATH)

    assert without_volatile_metadata(corpus1) == without_volatile_metadata(corpus2)


@pytest.fixture
def amended_clause_html():
    return """
<p class="pDieu"><a name="2000200000000001700021900000000000000000"></a>Điều 20.2.LQ.219. Sửa đổi, bổ sung một số điều của các luật có liên quan đến lao động</p>
<p class="pGhiChu">(Điều 219 Bộ luật số 45/2019/QH14, có hiệu lực thi hành kể từ ngày 01/01/2021)</p>
<p class="pNoiDung"></p>
<p>2. Sửa đổi, bổ sung Điều 32 của Bộ luật Tố tụng dân sự số 92/2015/QH13 như sau:</p>
<p>a) Sửa đổi, bổ sung tên điều, khoản 1; bổ sung các khoản 1a, 1b và 1c vào sau khoản 1 như sau:</p>
<p>“Điều 32. Những tranh chấp về lao động và tranh chấp liên quan đến lao động thuộc thẩm quyền giải quyết của Tòa án</p>
<p>1. Tranh chấp lao động cá nhân giữa người lao động với người sử dụng lao động...</p>
<p>a) Về xử lý kỷ luật lao động...</p>
<p>e) Giữa người lao động thuê lại...</p>
<p>1a. Tranh chấp lao động cá nhân...</p>
<p>1b. Tranh chấp lao động tập thể...</p>
<p>1c. Tranh chấp lao động tập thể về quyền...”;</p>
<p>b) Bãi bỏ khoản 2 Điều 32.</p>
"""


def test_amended_clause_labels_correct_structure(tmp_path, amended_clause_html):
    html_file = tmp_path / "test.html"
    html_file.write_text(amended_clause_html, encoding="utf-8")
    corpus = parse_legal_document(
        html_file,
        topic_code_override="20.2",
        topic_name_override="Lao động"
    )
    article = corpus["articles"][0]
    units = article["content_units"]

    unit_1a = next((u for u in units if "1a. Tranh chấp lao động cá nhân" in u["text"]), None)
    unit_1b = next((u for u in units if "1b. Tranh chấp lao động tập thể" in u["text"]), None)
    unit_1c = next((u for u in units if "1c. Tranh chấp lao động tập thể" in u["text"]), None)

    assert unit_1a is not None
    assert unit_1b is not None
    assert unit_1c is not None

    assert unit_1a["unit_type"] == "clause"
    assert unit_1b["unit_type"] == "clause"
    assert unit_1c["unit_type"] == "clause"

    assert unit_1a["clause_number"] == "1a"
    assert unit_1b["clause_number"] == "1b"
    assert unit_1c["clause_number"] == "1c"


def test_amended_clause_labels_real_corpus_correct_structure(canonical_corpus):
    article = next(
        (a for a in canonical_corpus["articles"] if a["article_code"] == "20.2.LQ.219"),
        None
    )
    assert article is not None
    units = article["content_units"]

    unit_1a = next((u for u in units if "1a. Tranh chấp lao động cá nhân" in u["text"]), None)
    unit_1b = next((u for u in units if "1b. Tranh chấp lao động tập thể" in u["text"]), None)
    unit_1c = next((u for u in units if "1c. Tranh chấp lao động tập thể" in u["text"]), None)

    assert unit_1a is not None
    assert unit_1b is not None
    assert unit_1c is not None

    assert unit_1a["unit_type"] == "clause"
    assert unit_1b["unit_type"] == "clause"
    assert unit_1c["unit_type"] == "clause"

    assert unit_1a["clause_number"] == "1a"
    assert unit_1b["clause_number"] == "1b"
    assert unit_1c["clause_number"] == "1c"


def test_amended_clause_labels_minimal_desired(tmp_path, amended_clause_html):
    html_file = tmp_path / "test.html"
    html_file.write_text(amended_clause_html, encoding="utf-8")
    corpus = parse_legal_document(
        html_file,
        topic_code_override="20.2",
        topic_name_override="Lao động"
    )
    article = corpus["articles"][0]
    units = article["content_units"]

    unit_1a = next((u for u in units if "1a. Tranh chấp lao động cá nhân" in u["text"]), None)
    unit_1b = next((u for u in units if "1b. Tranh chấp lao động tập thể" in u["text"]), None)
    unit_1c = next((u for u in units if "1c. Tranh chấp lao động tập thể" in u["text"]), None)

    assert unit_1a is not None
    assert unit_1b is not None
    assert unit_1c is not None

    assert unit_1a["unit_type"] == "clause"
    assert unit_1b["unit_type"] == "clause"
    assert unit_1c["unit_type"] == "clause"

    assert unit_1a["clause_number"] == "1a"
    assert unit_1b["clause_number"] == "1b"
    assert unit_1c["clause_number"] == "1c"

    assert unit_1a.get("point_label") is None
    assert unit_1b.get("point_label") is None
    assert unit_1c.get("point_label") is None

    assert unit_1a["unit_id"] != unit_1b["unit_id"]
    assert unit_1b["unit_id"] != unit_1c["unit_id"]

    assert "clause=1a" in unit_1a["unit_id"]
    assert "clause=1b" in unit_1b["unit_id"]
    assert "clause=1c" in unit_1c["unit_id"]


def test_amended_clause_labels_real_corpus_desired(canonical_corpus):
    article = next(
        (a for a in canonical_corpus["articles"] if a["article_code"] == "20.2.LQ.219"),
        None
    )
    assert article is not None
    units = article["content_units"]

    unit_1a = next((u for u in units if "1a. Tranh chấp lao động cá nhân" in u["text"]), None)
    unit_1b = next((u for u in units if "1b. Tranh chấp lao động tập thể" in u["text"]), None)
    unit_1c = next((u for u in units if "1c. Tranh chấp lao động tập thể" in u["text"]), None)

    assert unit_1a is not None
    assert unit_1b is not None
    assert unit_1c is not None

    assert unit_1a["unit_type"] == "clause"
    assert unit_1b["unit_type"] == "clause"
    assert unit_1c["unit_type"] == "clause"

    assert unit_1a["clause_number"] == "1a"
    assert unit_1b["clause_number"] == "1b"
    assert unit_1c["clause_number"] == "1c"

    assert unit_1a.get("point_label") is None
    assert unit_1b.get("point_label") is None
    assert unit_1c.get("point_label") is None

    assert "clause=1a" in unit_1a["unit_id"]
    assert "clause=1b" in unit_1b["unit_id"]
    assert "clause=1c" in unit_1c["unit_id"]



def test_amended_clause_labels_false_positives(tmp_path):
    html_content = """
<p class="pDieu"><a name="test_art"></a>Điều 20.2.LQ.219. Sửa đổi, bổ sung...</p>
<p class="pGhiChu">(Ghi chú...)</p>
<p class="pNoiDung"></p>
<p>1. Thực hiện theo điểm 1a và tại khoản 1a.</p>
<p>2. Sử dụng Mẫu số 1a để báo cáo.</p>
<p>3. Trong đó 1a là chỉ số quan trọng.</p>
"""
    html_file = tmp_path / "test.html"
    html_file.write_text(html_content, encoding="utf-8")
    corpus = parse_legal_document(
        html_file,
        topic_code_override="20.2",
        topic_name_override="Lao động"
    )
    article = corpus["articles"][0]
    units = article["content_units"]

    clause_numbers = [u.get("clause_number") for u in units]
    assert "1a" not in clause_numbers


def test_quoted_outer_state_minimal(tmp_path, amended_clause_html):
    html_file = tmp_path / "test.html"
    html_file.write_text(amended_clause_html, encoding="utf-8")
    corpus = parse_legal_document(
        html_file,
        topic_code_override="20.2",
        topic_name_override="Lao động"
    )
    article = corpus["articles"][0]
    units = article["content_units"]

    unit_b = next((u for u in units if "Bãi bỏ khoản 2 Điều 32" in u["text"]), None)
    assert unit_b is not None

    assert unit_b["unit_type"] == "point"
    assert unit_b["clause_number"] == "2"
    assert unit_b["point_label"] == "b"
    assert "clause=2|point=b" in unit_b["unit_id"]


def test_quoted_outer_state_real_corpus(canonical_corpus):
    article = next(
        (a for a in canonical_corpus["articles"] if a["article_code"] == "20.2.LQ.219"),
        None
    )
    assert article is not None
    units = article["content_units"]

    unit_b = next((u for u in units if "Bãi bỏ khoản 2 Điều 32" in u["text"]), None)
    assert unit_b is not None

    assert unit_b["unit_type"] == "point"
    assert unit_b["clause_number"] == "2"
    assert unit_b["point_label"] == "b"
    assert "clause=2|point=b" in unit_b["unit_id"]
