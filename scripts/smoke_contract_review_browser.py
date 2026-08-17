#!/usr/bin/env python3
"""Playwright browser E2E for the Contract Review user interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page, sync_playwright
except ImportError as exc:  # pragma: no cover - environment preflight
    raise SystemExit(
        "Playwright is missing. Install it in the project venv and run "
        "`playwright install chromium`."
    ) from exc

from smoke_contract_review_docker import cleanup_users


ROOT = Path(__file__).resolve().parents[1]
DOCX_SAMPLE = ROOT / "samples/contract-review/sample_labor_contract.docx"
PDF_SAMPLE = ROOT / "samples/contract-review/sample_labor_contract.pdf"
CONTRACT_ROUTE = "/contract-reviews"


def _upload_and_assert(page: Page, sample: Path) -> None:
    page.locator("#contract-file").set_input_files(str(sample))
    page.locator(".contract-upload-card").get_by_role(
        "button",
        name="Rà soát hợp đồng",
        exact=True,
    ).click()
    page.locator(".contract-report-header").wait_for(state="visible", timeout=180_000)
    page.locator(".contract-report-header h2").filter(has_text=sample.name).wait_for()
    findings = page.locator(".contract-finding")
    assert findings.count() == 4, f"Expected 4 findings for {sample.name}"
    assert page.locator(".contract-report-panel").get_by_text(
        "Kết quả chỉ mang tính hỗ trợ rà soát thông tin",
        exact=False,
    ).is_visible()


def run(frontend_url: str, output: Path, screenshot: Path, headed: bool) -> None:
    base_url = frontend_url.rstrip("/")
    contract_url = base_url + CONTRACT_ROUTE
    suffix = uuid4().hex
    email = f"contract-browser-{suffix}@example.test"
    password = "MatKhauAnToan123"
    console_errors: list[str] = []
    resource_401_console_errors: list[str] = []
    expected_auth_probe_401s: list[str] = []
    page_errors: list[str] = []
    network_errors: list[str] = []
    steps: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed)
            context = browser.new_context(locale="vi-VN")
            page = context.new_page()

            def record_console_error(message: object) -> None:
                if getattr(message, "type", "") != "error":
                    return
                message_text = str(getattr(message, "text", ""))
                if (
                    message_text.startswith("Failed to load resource:")
                    and "401 (Unauthorized)" in message_text
                ):
                    resource_401_console_errors.append(message_text)
                    return
                console_errors.append(message_text)

            page.on("console", record_console_error)
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            def record_bad_response(response: object) -> None:
                status = getattr(response, "status", 0)
                url = str(getattr(response, "url", ""))
                if status < 400:
                    return
                if status == 401 and url.endswith("/api/auth/me"):
                    expected_auth_probe_401s.append(url)
                    return
                network_errors.append(f"HTTP {status}: {url}")

            page.on("response", record_bad_response)
            page.goto(
                contract_url,
                wait_until="networkidle",
                timeout=120_000,
            )
            page.get_by_role(
                "heading",
                name="Cần đăng nhập để rà soát hợp đồng",
            ).wait_for()
            steps.append("PASS 1/8: Contract Review route rendered")

            page.get_by_role("button", name="Đăng nhập", exact=True).first.click()
            page.get_by_role("button", name="Đăng ký", exact=True).click()
            page.get_by_label("Tên hiển thị").fill("Browser E2E")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Mật khẩu").fill(password)
            page.get_by_role("button", name="Tạo tài khoản", exact=True).click()
            page.get_by_role(
                "heading",
                name="Rà soát hợp đồng lao động",
            ).wait_for(timeout=30_000)
            steps.append("PASS 2/8: account registered through UI")

            _upload_and_assert(page, DOCX_SAMPLE)
            steps.append("PASS 3/8: DOCX uploaded and four findings rendered")

            _upload_and_assert(page, PDF_SAMPLE)
            steps.append("PASS 4/8: PDF uploaded and four findings rendered")

            page.reload(wait_until="networkidle")
            page.locator(".contract-review-history").get_by_text(
                DOCX_SAMPLE.name,
                exact=True,
            ).wait_for()
            page.locator(".contract-review-history").get_by_text(
                PDF_SAMPLE.name,
                exact=True,
            ).wait_for()
            steps.append("PASS 5/8: reports survived browser reload")

            page.on("dialog", lambda dialog: dialog.accept())
            pdf_delete = page.get_by_role(
                "button",
                name=f"Xóa {PDF_SAMPLE.name}",
            )
            pdf_delete.click()
            pdf_delete.wait_for(state="detached")
            docx_delete = page.get_by_role(
                "button",
                name=f"Xóa {DOCX_SAMPLE.name}",
            )
            docx_delete.click()
            docx_delete.wait_for(state="detached")
            page.get_by_text("Chưa có báo cáo nào.", exact=True).wait_for()
            steps.append("PASS 6/8: reports deleted through UI")

            page.get_by_role("button", name="Đăng xuất", exact=True).click()
            page.get_by_role(
                "button",
                name="Đăng nhập",
                exact=True,
            ).first.wait_for(timeout=30_000)
            page.locator(".top-navigation").get_by_role(
                "button",
                name="Rà soát hợp đồng",
                exact=True,
            ).click()
            page.wait_for_url(contract_url, timeout=30_000)
            page.get_by_role(
                "heading",
                name="Cần đăng nhập để rà soát hợp đồng",
            ).wait_for(timeout=30_000)
            steps.append("PASS 7/8: logout returned UI to protected state")

            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            context.close()
            browser.close()

        unexpected_console_401_count = max(
            0,
            len(resource_401_console_errors) - len(expected_auth_probe_401s),
        )
        if unexpected_console_401_count:
            console_errors.extend(
                resource_401_console_errors[-unexpected_console_401_count:],
            )
        assert not console_errors, "Console errors: " + " | ".join(console_errors)
        assert not page_errors, "Page errors: " + " | ".join(page_errors)
        assert not network_errors, "Network errors: " + " | ".join(network_errors)
        steps.append("PASS 8/8: no unexpected console, page or HTTP errors")

        result = {
            "schema_version": "contract-review-browser-e2e-v1",
            "status": "PASS",
            "run_at": datetime.now().astimezone().isoformat(),
            "frontend_url": frontend_url,
            "samples": [str(DOCX_SAMPLE), str(PDF_SAMPLE)],
            "console_errors": console_errors,
            "expected_auth_probe_401_count": len(expected_auth_probe_401s),
            "page_errors": page_errors,
            "network_errors": network_errors,
            "screenshot": str(screenshot),
            "steps": steps,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        cleanup_users([email])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend-url", default="http://localhost:5173")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    try:
        run(args.frontend_url, args.output, args.screenshot, args.headed)
    except (AssertionError, RuntimeError, OSError, PlaywrightError) as exc:
        print(f"BROWSER E2E: FAIL: {exc}", file=sys.stderr)
        return 1
    print("BROWSER E2E: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
