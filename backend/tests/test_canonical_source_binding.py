from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from scripts import audit_canonical_source_binding as audit
from scripts.build_official_docx_release import (
    extract_docx_display_paragraphs,
    extract_docx_paragraphs,
)


def make_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(
        "<w:p><w:r><w:t>" + text + "</w:t></w:r></w:p>"
        for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)


def make_automatically_numbered_docx(
    path: Path,
    *,
    article_count: int = 3,
    broken_display_from_article: int | None = None,
) -> None:
    """Model the Gazette layout where 'Điều N.' is not in w:t text."""

    def paragraph(
        text: str,
        *,
        style: str | None = None,
        num_id: int | None = None,
        level: int = 0,
    ) -> str:
        properties: list[str] = []
        if style:
            properties.append(f'<w:pStyle w:val="{style}"/>')
        if num_id is not None:
            properties.append(
                "<w:numPr>"
                f'<w:ilvl w:val="{level}"/>'
                f'<w:numId w:val="{num_id}"/>'
                "</w:numPr>"
            )
        p_pr = f"<w:pPr>{''.join(properties)}</w:pPr>" if properties else ""
        return f"<w:p>{p_pr}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>"

    article_titles = [
        "Phạm vi điều chỉnh",
        "Đối tượng áp dụng",
        "Mục đích",
    ] + [f"Tiêu đề {number}" for number in range(4, article_count + 1)]
    body_rows = [paragraph("NHỮNG QUY ĐỊNH CHUNG", num_id=8)]
    for number, title in enumerate(article_titles, start=1):
        style = (
            "DieuBroken"
            if broken_display_from_article is not None
            and number >= broken_display_from_article
            else "Dieu"
        )
        body_rows.append(paragraph(title, style=style))
        ordinal = {1: "nhất", 2: "hai", 3: "ba"}.get(number, str(number))
        body_rows.append(paragraph(f"Nội dung thứ {ordinal}."))
    body_rows.extend(
        [
            paragraph("Phụ lục I"),
            paragraph("Điều 1. Điều trong biểu mẫu"),
        ]
    )
    body = "".join(body_rows)
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    broken_style = (
        '<w:style w:type="paragraph" w:styleId="DieuBroken">'
        '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="9"/>'
        '</w:numPr></w:pPr></w:style>'
        if broken_display_from_article is not None
        else ""
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="BaseDieu">'
        '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="7"/>'
        '</w:numPr></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Dieu">'
        '<w:basedOn w:val="BaseDieu"/></w:style>'
        f'{broken_style}'
        '</w:styles>'
    )
    broken_numbering = (
        '<w:num w:numId="9"><w:abstractNumId w:val="10"/>'
        '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="3"/>'
        '</w:lvlOverride></w:num>'
        if broken_display_from_article is not None
        else ""
    )
    numbering = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:abstractNum w:abstractNumId="10"><w:lvl w:ilvl="0">'
        '<w:start w:val="1"/><w:numFmt w:val="decimal"/>'
        '<w:lvlText w:val="Điều %1."/>'
        '</w:lvl></w:abstractNum>'
        '<w:abstractNum w:abstractNumId="11"><w:lvl w:ilvl="0">'
        '<w:start w:val="1"/><w:numFmt w:val="upperRoman"/>'
        '<w:lvlText w:val="Chương %1"/>'
        '</w:lvl></w:abstractNum>'
        '<w:num w:numId="7"><w:abstractNumId w:val="10"/></w:num>'
        '<w:num w:numId="8"><w:abstractNumId w:val="11"/></w:num>'
        f'{broken_numbering}'
        '</w:numbering>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/numbering.xml", numbering)


def make_explicit_article_docx_with_layout_residue(path: Path) -> None:
    """Model explicit Điều headings polluted by harmless Word list labels."""

    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        '<w:p><w:r><w:t>Điều 1. Phạm vi điều chỉnh</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Nội dung một.</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="9"/>'
        '</w:numPr></w:pPr><w:r><w:t>Điều 2. Đối tượng áp dụng</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="11"/>'
        '</w:numPr></w:pPr></w:p>'
        '<w:p><w:r><w:t>Nội dung hai.</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Điều 3. Tổ chức thực hiện</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Nội dung ba.</w:t></w:r></w:p>'
        '</w:body></w:document>'
    )
    numbering = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:abstractNum w:abstractNumId="9"><w:lvl w:ilvl="0">'
        '<w:numFmt w:val="none"/><w:lvlText w:val="."/>'
        '</w:lvl></w:abstractNum>'
        '<w:abstractNum w:abstractNumId="11"><w:lvl w:ilvl="0">'
        '<w:start w:val="24"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
        '</w:lvl></w:abstractNum>'
        '<w:num w:numId="9"><w:abstractNumId w:val="9"/></w:num>'
        '<w:num w:numId="11"><w:abstractNumId w:val="11"/></w:num>'
        '</w:numbering>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/numbering.xml", numbering)


def test_article_range_parser() -> None:
    assert audit.article_ranges("1-3, 7,9-10") == {1, 2, 3, 7, 9, 10}


def test_extract_and_compare_exact_articles() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        path = Path(temp_name) / "source.docx"
        make_docx(
            path,
            [
                "Điều 1. Phạm vi",
                "1. Nội dung thứ nhất.",
                "Điều 2. Đối tượng",
                "Nội dung thứ hai.",
            ],
        )
        extracted = audit.extract_word_articles([path], 2)
        base = {
            "article_id": "doc:article:1",
            "article_number": 1,
            "article_title": "Phạm vi",
            "content_units": [{"text": "1. Nội dung thứ nhất."}],
        }
        result = audit.compare_article(base, extracted[1])
        assert result["status"] == "PASS"
        assert result["similarity_ratio"] == 1.0


def test_automatic_numbering_is_restored_from_style_and_numbering_xml() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        path = Path(temp_name) / "automatic-numbering.docx"
        make_automatically_numbered_docx(path)
        paragraphs, metadata = extract_docx_display_paragraphs(path)

        assert paragraphs[:4] == [
            "Chương I NHỮNG QUY ĐỊNH CHUNG",
            "Điều 1. Phạm vi điều chỉnh",
            "Nội dung thứ nhất.",
            "Điều 2. Đối tượng áp dụng",
        ]
        assert metadata["automatic_numbering_instances"] == 2
        assert metadata["automatic_numbering_prefixes_rendered"] == 4

        legacy_paragraphs, _ = extract_docx_paragraphs(path)
        assert legacy_paragraphs[0] == "NHỮNG QUY ĐỊNH CHUNG"
        assert legacy_paragraphs[1] == "Phạm vi điều chỉnh"


def test_audit_uses_main_articles_and_excludes_appendix_and_chapter_heading() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        path = Path(temp_name) / "automatic-numbering.docx"
        make_automatically_numbered_docx(path)
        extracted = audit.extract_word_articles([path], 3)

        assert list(extracted) == [1, 2, 3]
        assert extracted[1] == {
            "title": "Phạm vi điều chỉnh",
            "body": "Nội dung thứ nhất.",
        }
        assert extracted[3] == {
            "title": "Mục đích",
            "body": "Nội dung thứ ba.",
        }


def test_145_shape_finds_all_115_articles_before_appendix_restart() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        path = Path(temp_name) / "145-two-part-shape.docx"
        make_automatically_numbered_docx(path, article_count=115)
        extracted = audit.extract_word_articles([path], 115)

        assert len(extracted) == 115
        assert extracted[1]["title"] == "Phạm vi điều chỉnh"
        assert extracted[115] == {
            "title": "Tiêu đề 115",
            "body": "Nội dung thứ 115.",
        }


def test_title_fallback_recovers_unique_chain_when_word_counter_drifts() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        path = Path(temp_name) / "counter-drift.docx"
        make_automatically_numbered_docx(
            path,
            broken_display_from_article=2,
        )
        display, _ = extract_docx_display_paragraphs(path)
        assert display[3] == "Điều 3. Đối tượng áp dụng"

        titles = {
            1: "Phạm vi điều chỉnh",
            2: "Đối tượng áp dụng",
            3: "Mục đích",
        }
        extracted = audit.extract_word_articles(
            [path], 3, expected_titles=titles
        )

        assert extracted == {
            1: {
                "title": "Phạm vi điều chỉnh",
                "body": "Nội dung thứ nhất.",
            },
            2: {
                "title": "Đối tượng áp dụng",
                "body": "Nội dung thứ hai.",
            },
            3: {
                "title": "Mục đích",
                "body": "Nội dung thứ ba.",
            },
        }


def test_title_fallback_rejects_ambiguous_complete_chains() -> None:
    paragraphs = [
        "Phạm vi",
        "Đối tượng",
        "Phạm vi",
        "Đối tượng",
    ]
    try:
        audit._unique_title_sequence(
            paragraphs,
            {1: "Phạm vi", 2: "Đối tượng"},
            2,
        )
    except audit.BindingAuditError as exc:
        assert "more than one complete" in str(exc)
    else:
        raise AssertionError("ambiguous title chains were accepted")


def test_explicit_article_headings_ignore_layout_residue_and_empty_labels() -> None:
    with tempfile.TemporaryDirectory() as temp_name:
        path = Path(temp_name) / "explicit-layout-residue.docx"
        make_explicit_article_docx_with_layout_residue(path)

        display, _ = extract_docx_display_paragraphs(path)
        assert ". Điều 2. Đối tượng áp dụng" not in display
        assert "24." not in display

        extracted = audit.extract_word_articles(
            [path],
            3,
            # A selected subset must not be required to supply titles 1..N
            # when the stored Word text already contains a complete sequence.
            expected_titles={2: "Đối tượng áp dụng"},
        )
        assert extracted[2] == {
            "title": "Đối tượng áp dụng",
            "body": "Nội dung hai.",
        }


def test_comparison_fails_closed_and_reports_context() -> None:
    base = {
        "article_id": "doc:article:1",
        "article_number": 1,
        "article_title": "Phạm vi",
        "content_units": [{"text": "Mức phạt là 10 triệu đồng."}],
    }
    word = {
        "title": "Phạm vi",
        "body": "Mức phạt là 20 triệu đồng.",
    }
    result = audit.compare_article(base, word)
    assert result["status"] == "FAIL"
    assert result["first_difference"]["offset"] > 0
    assert result["vbpl_body_sha256"] != result["gazette_body_sha256"]


def test_comparison_excludes_structural_headings_and_signature_tail() -> None:
    base = {
        "article_id": "doc:article:1",
        "article_number": 1,
        "article_title": "Phạm vi",
        "content_units": [
            {
                "text": (
                    "Nội dung pháp lý.\n"
                    "Chương IIQUẢN LÝ LAO ĐỘNG\n"
                    "KT. BỘ TRƯỞNG\nTHỨ TRƯỞNG\nNguyễn Văn A"
                )
            }
        ],
    }
    word = {"title": "Phạm vi", "body": "Nội dung pháp lý."}

    assert audit.compare_article(base, word)["status"] == "PASS"


def test_manifest_shape_requires_pass_and_expected_counts() -> None:
    manifest = {
        "status": "PASS",
        "documents_requested": 18,
        "documents_succeeded": 18,
        "documents_failed": 0,
        "official_gazette_documents": 17,
        "vbpl_fallback_documents": 1,
        "one_canonical_snapshot_per_document": True,
    }
    audit.verify_manifest_shape(manifest)
    manifest["documents_failed"] = 1
    try:
        audit.verify_manifest_shape(manifest)
    except audit.BindingAuditError:
        pass
    else:
        raise AssertionError("failed canonical manifest was accepted")


def test_error_report_keeps_machine_readable_shape(tmp_path: Path) -> None:
    report_path = tmp_path / "audit.json"
    return_code = audit.main(
        [
            "--root",
            str(tmp_path),
            "--report",
            str(report_path),
        ]
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert return_code == 1
    assert report["status"] == "ERROR"
    assert report["documents"] == []
    assert report["failed_documents"] == []
    assert report["safe_to_rebind_provenance"] is False
