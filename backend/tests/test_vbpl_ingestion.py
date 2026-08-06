from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import vbpl_portal as vp  # noqa: E402


HTML = """<div>
<p><b>Điều 1. Phạm vi điều chỉnh</b></p><p>1. Nội dung thứ nhất đủ dài để kiểm thử dữ liệu chính thức.</p>
<p><b>Điều 2. Hiệu lực thi hành</b></p><p>1. Nội dung thứ hai đủ dài để kiểm thử dữ liệu chính thức.</p>
</div>"""

DETAIL_URL = (
    "https://vbpl.vn/van-ban/chi-tiet/"
    "nghi-dinh-so-219-2025-nd-cp-quy-dinh-ve-nguoi-lao-dong-"
    "nuoc-ngoai-lam-viec-tai-viet-nam--180273"
)

ACTUAL_JSON_SHAPE = {
    "success": True,
    "statusCode": 200,
    "message": "OK",
    "data": {
        "id": "180273",
        "docType": {"id": "type-id", "name": "Nghị định", "code": "NĐ"},
        "docNum": "219/2025/NĐ-CP",
        "title": "Nghị định số 219/2025/NĐ-CP",
        "issueDate": "2025-08-07T00:00:00",
        "effFrom": "2025-08-07T00:00:00",
        "effTo": None,
        "effStatus": {"name": "Còn hiệu lực"},
        "issueOrg": {"name": "Chính phủ"},
        "documentContent": {
            "content": HTML,
            "documentContentFileName": "219-cp.signed.pdf",
        },
    },
}

XML_PAYLOAD = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<response><success>true</success><data>'
    '<id>180273</id><docNum>219/2025/NĐ-CP</docNum>'
    '<title>Nghị định XML</title><issueDate>2025-08-07T00:00:00</issueDate>'
    '<effFrom>2025-08-07T00:00:00</effFrom>'
    '<effStatus><name>Còn hiệu lực</name></effStatus>'
    '<documentContent><content><![CDATA[' + HTML + ']]></content>'
    '<documentContentFileName>219.pdf</documentContentFileName></documentContent>'
    '</data></response>'
).encode("utf-8")


def capture(payload: object, body: bytes | None = None) -> vp.CaptureRecord:
    raw = body or json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return vp.CaptureRecord(
        url="https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/180273",
        status=200,
        content_type="application/json",
        body=raw,
        payload=payload,
        acquisition="direct_gateway",
        attempts=1,
        elapsed_ms=12.3,
        headers={"Content-Type": "application/json"},
    )


class VbplProductionIngestionTests(unittest.TestCase):
    def test_exact_resolver_rejects_partial_matches(self) -> None:
        urls = [
            "https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-121-2025-nd-cp--178222",
            "https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-112-2025-nd-cp--178105",
        ]
        with self.assertRaises(vp.VbplPortalNotFound):
            vp.resolve_document_url(urls, "219/2025/NĐ-CP")

    def test_exact_resolver_accepts_full_number(self) -> None:
        self.assertEqual(
            vp.resolve_document_url([DETAIL_URL], "219/2025/NĐ-CP"), DETAIL_URL
        )

    def test_extracts_item_id_from_legacy_query_url(self) -> None:
        url = "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=152668"
        self.assertEqual(vp.extract_item_id_from_detail_url(url), "152668")
        self.assertEqual(
            vp.gateway_url_from_detail_url(url),
            "https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/152668",
        )

    def test_config_has_locator_for_every_vbpl_document(self) -> None:
        config = json.loads((ROOT / "config/vbpl_corpus.json").read_text(encoding="utf-8"))
        vbpl_docs = [
            item for item in config["documents"]
            if item.get("source_adapter", "vbpl") == "vbpl"
        ]
        missing = [
            item["document_number"] for item in vbpl_docs
            if not item.get("item_id") and not item.get("portal_url")
        ]
        self.assertEqual(missing, [])

    def test_parses_actual_gateway_json_shape(self) -> None:
        raw = json.dumps(ACTUAL_JSON_SHAPE, ensure_ascii=False).encode("utf-8")
        payload = vp.parse_portal_response_bytes(raw, "application/json")
        document = vp.extract_document_from_payloads(
            [payload], "219/2025/NĐ-CP", detail_url=DETAIL_URL
        )
        self.assertEqual(document.item_id, "180273")
        self.assertEqual(document.document_number, "219/2025/NĐ-CP")
        self.assertEqual(document.effective_from, "2025-08-07")
        self.assertEqual(document.legal_status, "Còn hiệu lực")
        self.assertEqual(document.issuing_authority, "Chính phủ")
        self.assertEqual(vp.detect_article_numbers(document.full_text_html), [1, 2])
        self.assertEqual(len(document.attachment_inventory), 1)
        self.assertEqual(
            document.attachment_inventory[0]["resolution_status"], "unresolved"
        )

    def test_parses_xml_gateway_shape(self) -> None:
        payload = vp.parse_portal_response_bytes(XML_PAYLOAD, "application/xml")
        document = vp.extract_document_from_payloads(
            [payload], "219/2025/NĐ-CP", detail_url=DETAIL_URL
        )
        self.assertEqual(document.item_id, "180273")
        self.assertEqual(vp.detect_article_numbers(document.full_text_html), [1, 2])

    def test_best_effort_does_not_claim_missing_attachment_bytes_complete(self) -> None:
        document = vp.extract_document_from_payloads(
            [ACTUAL_JSON_SHAPE], "219/2025/NĐ-CP", detail_url=DETAIL_URL
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = vp.write_snapshot(
                output_root=Path(tmp),
                document=document,
                detail_url=DETAIL_URL,
                sitemap_url=vp.DEFAULT_SITEMAP,
                captures=[capture(ACTUAL_JSON_SHAPE)],
                rendered_html="<html><body>detail</body></html>",
                expected_articles=2,
                attachment_policy="best_effort",
                operations=["ResolveExplicitUrl", "FetchDirectGateway"],
                retrieved_at="2026-07-24T08:00:00+00:00",
            )
            snapshot = Path(result["snapshot_dir"])
            manifest = json.loads((snapshot / "manifest.json").read_text())
            self.assertTrue(manifest["gates"]["attachment_policy_satisfied"])
            self.assertEqual(manifest["attachment_inventory"]["unresolved_count"], 1)
            self.assertEqual(manifest["attachment_inventory"]["downloaded_count"], 0)
            self.assertTrue(manifest["warnings"])
            self.assertNotIn("attachment_bytes_complete", manifest["gates"])
            self.assertEqual(
                manifest["source"]["operations"],
                ["ResolveExplicitUrl", "FetchDirectGateway"],
            )
            self.assertEqual(
                manifest["source"]["acquisition_modes"], ["direct_gateway"]
            )
            raw_path = snapshot / manifest["source"]["primary_response_path"]
            self.assertTrue(raw_path.is_file())
            vp.verify_snapshot(snapshot, expected_document_number="219/2025/NĐ-CP")

    def test_required_attachment_policy_fails_when_url_unresolved(self) -> None:
        document = vp.extract_document_from_payloads(
            [ACTUAL_JSON_SHAPE], "219/2025/NĐ-CP", detail_url=DETAIL_URL
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(vp.VbplPortalError):
                vp.write_snapshot(
                    output_root=Path(tmp),
                    document=document,
                    detail_url=DETAIL_URL,
                    sitemap_url=vp.DEFAULT_SITEMAP,
                    captures=[capture(ACTUAL_JSON_SHAPE)],
                    rendered_html="<html></html>",
                    expected_articles=2,
                    attachment_policy="required",
                    operations=["ResolveExplicitUrl", "FetchDirectGateway"],
                    retrieved_at="2026-07-24T08:00:00+00:00",
                )
            published = [
                p
                for p in (Path(tmp) / "219_2025_nd_cp").iterdir()
                if p.is_dir() and not p.name.startswith(".")
            ]
            self.assertEqual(published, [])

    def test_verify_detects_corruption_and_resume_rejects_it(self) -> None:
        payload = json.loads(json.dumps(ACTUAL_JSON_SHAPE, ensure_ascii=False))
        payload["data"]["documentContent"].pop("documentContentFileName")
        document = vp.extract_document_from_payloads(
            [payload], "219/2025/NĐ-CP", detail_url=DETAIL_URL
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = vp.write_snapshot(
                output_root=root,
                document=document,
                detail_url=DETAIL_URL,
                sitemap_url=vp.DEFAULT_SITEMAP,
                captures=[capture(payload)],
                rendered_html="<html></html>",
                expected_articles=2,
                attachment_policy="best_effort",
                operations=["ResolveExplicitUrl", "FetchDirectGateway"],
                retrieved_at="2026-07-24T08:00:00+00:00",
            )
            snapshot = Path(result["snapshot_dir"])
            (snapshot / "full_text.html").write_text("corrupted", encoding="utf-8")
            with self.assertRaises(vp.VbplPortalError):
                vp.verify_snapshot(snapshot, expected_document_number="219/2025/NĐ-CP")
            self.assertIsNone(vp.latest_valid_snapshot(root, "219/2025/NĐ-CP"))

    def test_unchanged_content_does_not_create_duplicate_snapshot(self) -> None:
        payload = json.loads(json.dumps(ACTUAL_JSON_SHAPE, ensure_ascii=False))
        payload["data"]["documentContent"].pop("documentContentFileName")
        document = vp.extract_document_from_payloads(
            [payload], "219/2025/NĐ-CP", detail_url=DETAIL_URL
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = vp.write_snapshot(
                output_root=root,
                document=document,
                detail_url=DETAIL_URL,
                sitemap_url=vp.DEFAULT_SITEMAP,
                captures=[capture(payload)],
                rendered_html="",
                expected_articles=2,
                operations=["ResolveExplicitUrl", "FetchDirectGateway"],
                retrieved_at="2026-07-24T08:00:00+00:00",
            )
            second = vp.write_snapshot(
                output_root=root,
                document=document,
                detail_url=DETAIL_URL,
                sitemap_url=vp.DEFAULT_SITEMAP,
                captures=[capture(payload)],
                rendered_html="",
                expected_articles=2,
                operations=["ResolveExplicitUrl", "FetchDirectGateway"],
                retrieved_at="2026-07-24T09:00:00+00:00",
            )
            self.assertEqual(first["status"], "created")
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual(first["snapshot_dir"], second["snapshot_dir"])



    def test_http_client_retries_transient_status(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            calls = 0

            def do_GET(self) -> None:  # noqa: N802
                type(self).calls += 1
                if type(self).calls < 3:
                    self.send_response(503)
                    self.send_header("Retry-After", "0")
                    self.end_headers()
                    return
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = vp.RetryingHttpClient(
                timeout=2, retries=3, backoff_seconds=0
            ).fetch(f"http://127.0.0.1:{server.server_port}/test")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(response.body, b"ok")
        self.assertEqual(response.attempts, 3)
        self.assertEqual(Handler.calls, 3)

    def test_http_client_rejects_oversized_response(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = b"0123456789"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(vp.VbplPortalError):
                vp.RetryingHttpClient(
                    timeout=2, retries=1, max_bytes=5
                ).fetch(f"http://127.0.0.1:{server.server_port}/large")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


    def test_article_sequence_allows_repeated_headings_after_main_sequence(self) -> None:
        html = """
        <div>
          <p>Điều 1. Một</p>
          <p>Điều 2. Hai</p>
          <p>Điều 3. Ba</p>
          <p>Điều 1. Điều được trích lại trong phụ lục</p>
          <p>Điều 2. Điều được trích lại trong phụ lục</p>
        </div>
        """
        report = vp.article_sequence_report(html, 3)
        self.assertTrue(report["valid"])
        self.assertEqual(report["duplicates"], [1, 2])
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["unexpected"], [])

    def test_article_sequence_still_rejects_missing_article(self) -> None:
        html = """
        <div>
          <p>Điều 1. Một</p>
          <p>Điều 2. Hai</p>
          <p>Điều 4. Bốn</p>
        </div>
        """
        report = vp.article_sequence_report(html, 4)
        self.assertFalse(report["valid"])
        self.assertEqual(report["missing"], [3])
        self.assertEqual(report["unexpected"], [])

    def test_attachment_inventory_supports_file_path_field(self) -> None:
        payload = json.loads(json.dumps(ACTUAL_JSON_SHAPE, ensure_ascii=False))
        payload["data"]["attachments"] = [
            {"fileName": "official.pdf", "filePath": "/files/official.pdf"}
        ]
        document = vp.extract_document_from_payloads(
            [payload], "219/2025/NĐ-CP", detail_url=DETAIL_URL
        )
        resolved = [
            item for item in document.attachment_inventory
            if item.get("name") == "official.pdf"
        ]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(
            resolved[0]["url"], "https://vbpl.vn/files/official.pdf"
        )

    def test_attachment_download_falls_back_to_browser_context(self) -> None:
        document = vp.PortalDocument(
            document_number="219/2025/NĐ-CP",
            title="Nghị định 219",
            full_text_html=HTML,
            attachment_inventory=(
                {
                    "name": "official.pdf",
                    "url": "https://vbpl.vn/files/official.pdf",
                    "resolution_status": "resolved",
                    "source_field": "attachment_collection",
                },
            ),
        )

        class FailingClient:
            def fetch(self, *args: object, **kwargs: object) -> vp.HttpResponse:
                raise vp.VbplPortalError("direct denied")

        class Browser:
            def fetch_binary(self, url: str, *, referer: str) -> vp.HttpResponse:
                return vp.HttpResponse(
                    requested_url=url,
                    final_url=url,
                    status=200,
                    headers={"Content-Type": "application/pdf"},
                    body=b"%PDF-1.7 fake",
                    attempts=1,
                    elapsed_ms=3.0,
                )

        responses, errors = vp._download_attachments(
            document,
            client=FailingClient(),  # type: ignore[arg-type]
            detail_url=DETAIL_URL,
            policy="required_if_listed",
            browser_session=Browser(),  # type: ignore[arg-type]
        )
        self.assertFalse(errors)
        self.assertIn("https://vbpl.vn/files/official.pdf", responses)

    def test_configured_official_attachment_satisfies_required_policy(self) -> None:
        payload = json.loads(json.dumps(ACTUAL_JSON_SHAPE, ensure_ascii=False))
        payload["data"]["documentContent"].pop("documentContentFileName")
        document = vp.extract_document_from_payloads(
            [payload], "219/2025/NĐ-CP", detail_url=DETAIL_URL
        )
        configured = (
            {
                "name": "97.signed.pdf",
                "url": "https://datafiles.chinhphu.vn/example/97.signed.pdf",
                "provider": "Cổng Thông tin điện tử Chính phủ",
                "source_page_url": "https://vanban.chinhphu.vn/example",
                "role": "signed_original_with_appendices",
            },
        )
        document = vp.merge_configured_attachments(
            document, configured, detail_url=DETAIL_URL
        )
        url = configured[0]["url"]
        response = vp.HttpResponse(
            requested_url=url,
            final_url=url,
            status=200,
            headers={"Content-Type": "application/pdf"},
            body=b"%PDF-1.7 configured official attachment",
            attempts=1,
            elapsed_ms=4.0,
        )
        contract = vp.build_ingestion_contract(
            document_number="219/2025/NĐ-CP",
            item_id="180273",
            expected_articles=2,
            attachment_policy="required",
            configured_attachments=configured,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = vp.write_snapshot(
                output_root=Path(tmp),
                document=document,
                detail_url=DETAIL_URL,
                sitemap_url=vp.DEFAULT_SITEMAP,
                captures=[capture(payload)],
                rendered_html="",
                expected_articles=2,
                attachment_responses={url: response},
                attachment_policy="required",
                ingestion_contract=contract,
                operations=["MergeConfiguredOfficialAttachments"],
                retrieved_at="2026-07-24T08:00:00+00:00",
            )
            snapshot = Path(result["snapshot_dir"])
            manifest = json.loads((snapshot / "manifest.json").read_text())
            self.assertTrue(manifest["gates"]["attachment_policy_satisfied"])
            self.assertEqual(manifest["attachments"][0]["provider"], configured[0]["provider"])
            self.assertTrue(manifest["attachments"][0]["has_bytes"])
            vp.verify_snapshot(snapshot, expected_document_number="219/2025/NĐ-CP")

    def test_resume_rejects_snapshot_with_different_ingestion_contract(self) -> None:
        payload = json.loads(json.dumps(ACTUAL_JSON_SHAPE, ensure_ascii=False))
        payload["data"]["documentContent"].pop("documentContentFileName")
        document = vp.extract_document_from_payloads(
            [payload], "219/2025/NĐ-CP", detail_url=DETAIL_URL
        )
        best_effort = vp.build_ingestion_contract(
            document_number="219/2025/NĐ-CP",
            item_id="180273",
            expected_articles=2,
            attachment_policy="best_effort",
            configured_attachments=(),
        )
        strict = vp.build_ingestion_contract(
            document_number="219/2025/NĐ-CP",
            item_id="180273",
            expected_articles=2,
            attachment_policy="required_if_listed",
            configured_attachments=(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = vp.write_snapshot(
                output_root=root,
                document=document,
                detail_url=DETAIL_URL,
                sitemap_url=vp.DEFAULT_SITEMAP,
                captures=[capture(payload)],
                rendered_html="",
                expected_articles=2,
                attachment_policy="best_effort",
                ingestion_contract=best_effort,
                retrieved_at="2026-07-24T08:00:00+00:00",
            )
            snapshot = Path(result["snapshot_dir"])
            self.assertEqual(
                vp.latest_valid_snapshot(
                    root, "219/2025/NĐ-CP", expected_contract=best_effort
                ),
                snapshot,
            )
            self.assertIsNone(
                vp.latest_valid_snapshot(
                    root, "219/2025/NĐ-CP", expected_contract=strict
                )
            )

    def test_config_has_explicit_official_attachments_for_strict_documents(self) -> None:
        config = json.loads((ROOT / "config/vbpl_corpus.json").read_text(encoding="utf-8"))
        by_number = {item["document_number"]: item for item in config["documents"]}
        for number, policy in (
            ("97/2022/NĐ-CP", "required"),
            ("219/2025/NĐ-CP", "required_if_listed"),
        ):
            document = by_number[number]
            attachments = document.get("official_attachments") or []
            self.assertEqual(len(attachments), 1)
            self.assertTrue(
                attachments[0]["url"].startswith("https://datafiles.chinhphu.vn/")
            )
            self.assertEqual(document["attachment_policy"], policy)

    def test_malformed_stale_lock_is_reclaimed_by_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "219_2025_nd_cp" / ".ingest.lock"
            lock.mkdir(parents=True)
            (lock / "owner.json").write_text("not-json", encoding="utf-8")
            old = 1_600_000_000
            import os
            os.utime(lock, (old, old))
            with vp.document_lock(root, "219/2025/NĐ-CP", stale_seconds=1):
                self.assertTrue(lock.exists())

    def test_document_lock_blocks_concurrent_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with vp.document_lock(root, "219/2025/NĐ-CP"):
                with self.assertRaises(vp.VbplPortalLockError):
                    with vp.document_lock(root, "219/2025/NĐ-CP"):
                        pass


if __name__ == "__main__":
    unittest.main()
