from __future__ import annotations

import io
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

from scripts import congbao_docx as cb
from scripts import vbpl_portal as common


GAZETTE_URL = (
    "https://congbao.chinhphu.vn/van-ban/"
    "nghi-quyet-so-6618-2026-nq-cp-469585.htm"
)
DOCX_URL = (
    "https://g7.cdnchinhphu.vn/api/download/stream?"
    "file_name=2026_301_66.18%2f2026%2fNQ-CP.docx"
)
PDF_URL = (
    "https://g7.cdnchinhphu.vn/api/download/stream?"
    "file_name=2026_301_66.18%2f2026%2fNQ-CP.pdf"
)
GOVERNMENT_URL = "https://vanban.chinhphu.vn/?docid=218181&pageid=27160"

GAZETTE_HTML = f"""<!doctype html><html><body>
<div>Ban hành: 18/05/2026 - Hiệu lực: 01/07/2026</div>
<h1>Nghị quyết số 66.18/2026/NQ-CP về thủ tục hành chính</h1>
<div>Nằm trong các Công báo: 301</div>
<a href="{DOCX_URL}">2026_301_66.18/2026/NQ-CP.docx</a>
<a href="{PDF_URL}">2026_301_66.18/2026/NQ-CP.pdf</a>
</body></html>""".encode()

GOVERNMENT_HTML = """<!doctype html><html><body>
<h1>Nghị quyết số 66.18/2026/NQ-CP của Chính phủ</h1>
<div>Số ký hiệu</div><div>66.18/2026/NQ-CP</div>
<div>Ngày ban hành</div><div>18-05-2026</div>
<div>Ngày có hiệu lực</div><div>01-07-2026</div>
</body></html>""".encode()


def make_docx(*, backslashes: bool = True) -> bytes:
    separator = "\\" if backslashes else "/"
    document_name = f"word{separator}document.xml"
    value = io.BytesIO()
    with zipfile.ZipFile(value, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>",
        )
        archive.writestr(
            document_name,
            """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
            <w:body><w:p><w:r><w:t>Nghi quyet 66.18/2026/NQ-CP</w:t></w:r></w:p></w:body>
            </w:document>""",
        )
    return value.getvalue()


def response(url: str, body: bytes, content_type: str) -> common.HttpResponse:
    return common.HttpResponse(
        requested_url=url,
        final_url=url,
        status=200,
        headers={"Content-Type": content_type},
        body=body,
        attempts=1,
        elapsed_ms=3.5,
    )


class FakeClient:
    def __init__(self, docx: bytes) -> None:
        self.responses = {
            GAZETTE_URL: response(GAZETTE_URL, GAZETTE_HTML, "text/html"),
            DOCX_URL: response(DOCX_URL, docx, cb.DOCX_CONTENT_TYPE),
            GOVERNMENT_URL: response(
                GOVERNMENT_URL, GOVERNMENT_HTML, "text/html"
            ),
        }

    def fetch(self, url: str, **_: object) -> common.HttpResponse:
        return self.responses[url]


def spec() -> cb.DocumentSpec:
    return cb.DocumentSpec(
        document_number="66.18/2026/NQ-CP",
        canonical_document_id="vn:66.18-2026-nq-cp",
        title="Phan quyen, cat giam thu tuc hanh chinh",
        source_file="2026_301_66.18_2026_NQ-CP.docx",
        gazette_page_url=GAZETTE_URL,
        metadata_page_url=GOVERNMENT_URL,
        expected_articles=7,
    )


def test_discovers_docx_but_records_pdf_separately() -> None:
    _, links = cb.parse_page(GAZETTE_URL, GAZETTE_HTML)
    assert [link.kind for link in links] == ["docx", "pdf"]
    assert cb.select_exact_docx(links, "66.18/2026/NQ-CP").url == DOCX_URL


def test_selects_all_multipart_legacy_doc_links_in_page_order() -> None:
    page = b"""<html><body>
    <a href="https://g7.cdnchinhphu.vn/a/145-2020-ND-CP.doc">part 1</a>
    <a href="https://g7.cdnchinhphu.vn/b/145-2020-ND-CP.doc">part 2</a>
    <a href="https://g7.cdnchinhphu.vn/a/145-2020-ND-CP.pdf">pdf</a>
    </body></html>"""
    _, links = cb.parse_page(GAZETTE_URL, page)
    selected = cb.select_exact_word_parts(links, "145/2020/NĐ-CP")
    assert [link.kind for link in selected] == ["doc", "doc"]
    assert [link.url for link in selected] == [
        "https://g7.cdnchinhphu.vn/a/145-2020-ND-CP.doc",
        "https://g7.cdnchinhphu.vn/b/145-2020-ND-CP.doc",
    ]


def test_legacy_doc_normalization_keeps_original_validation_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    converted = make_docx(backslashes=False)

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        output_dir = Path(command[command.index("--outdir") + 1])
        (output_dir / "source.docx").write_bytes(converted)
        return subprocess.CompletedProcess(command, 0, b"ok", b"")

    monkeypatch.setattr(cb.subprocess, "run", fake_run)
    normalized = cb.convert_legacy_doc_to_docx(
        cb.LEGACY_DOC_MAGIC + b"test",
        libreoffice_bin="/usr/bin/soffice",
    )
    assert normalized == converted
    with pytest.raises(cb.CongbaoError, match="OLE Compound File"):
        cb.convert_legacy_doc_to_docx(
            b"<html>error</html>", libreoffice_bin="/usr/bin/soffice"
        )


def test_rejects_pdf_bytes_even_if_named_docx() -> None:
    with pytest.raises(cb.CongbaoError, match="PDF bytes rejected"):
        cb.normalized_docx_members(b"%PDF-1.7 fake")


def test_accepts_real_gazette_backslash_member_names() -> None:
    members, backslashes = cb.normalized_docx_members(make_docx())
    assert "word/document.xml" in members
    assert backslashes == 1
    assert "66.18/2026/NQ-CP" in cb.docx_visible_text(make_docx(), members)


def test_extracts_minimal_gazette_metadata() -> None:
    parser, _ = cb.parse_page(GAZETTE_URL, GAZETTE_HTML)
    metadata = cb.extract_page_metadata(parser, source="congbao")
    assert metadata["issued_at"] == "2026-05-18"
    assert metadata["effective_from"] == "2026-07-01"
    assert metadata["gazette_issue"] == "301"


def test_full_ingestion_publishes_immutable_checksum_snapshot() -> None:
    docx = make_docx()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = cb.ingest_one(
            spec(),
            client=FakeClient(docx),  # type: ignore[arg-type]
            output_root=root / "raw",
            source_dir=root / "sources",
        )
        snapshot = Path(first["snapshot_dir"])
        verified = cb.verify_snapshot(
            snapshot, expected_document_number="66.18/2026/NQ-CP"
        )
        manifest = json.loads((snapshot / "manifest.json").read_text())

        assert first["status"] == "created"
        assert verified["valid"] is True
        assert manifest["source"]["docx_only"] is True
        assert manifest["source"]["pdf_downloaded"] is False
        assert manifest["document"]["effective_to"] is None
        assert (
            manifest["field_provenance"]["legal_status"]["status"]
            == "requires_vbpl_or_legal_effect_review"
        )
        assert (root / "sources" / spec().source_file).read_bytes() == docx

        second = cb.ingest_one(
            spec(),
            client=FakeClient(docx),  # type: ignore[arg-type]
            output_root=root / "raw",
            source_dir=root / "sources",
        )
        assert second["status"] == "unchanged"
        assert second["snapshot_dir"] == first["snapshot_dir"]


def test_project_config_assigns_explicit_source_roles() -> None:
    root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (root / "config/vbpl_corpus.json").read_text(encoding="utf-8")
    )
    docx_sources = [
        item
        for item in payload["documents"]
        if item.get("source_adapter") == "official_government_docx"
    ]
    assert len(docx_sources) == 2
    for item in docx_sources:
        assert item["canonical_content_source"] == "official_gazette_docx"
        assert item["metadata_source"] == "official_government_portal_plus_gazette"
        assert item["legal_effect_source"] == "explicit_legal_effect_review"
        assert item["gazette_page_url"].startswith(
            "https://congbao.chinhphu.vn/"
        )

    policies = [item["canonical_source_policy"] for item in payload["documents"]]
    assert len(policies) == 18
    assert sum(bool(item.get("gazette_page_url")) for item in payload["documents"]) == 17
    exception = next(
        item
        for item in payload["documents"]
        if item["document_number"] == "10/2020/TT-BLĐTBXH"
    )
    assert exception["canonical_source_policy"] == {
        "preferred_adapter": "official_gazette_word",
        "preferred_status": "not_found",
        "fallback_adapter": "vbpl",
        "fallback_reason": "gazette_record_not_found",
    }
