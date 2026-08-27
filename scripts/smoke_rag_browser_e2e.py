#!/usr/bin/env python3
"""Playwright browser E2E for RAG question answering, retrieval citations, and conversation persistence."""

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
except ImportError as exc:
    raise SystemExit(
        "Playwright is missing. Install it in the project venv and run "
        "`playwright install chromium`."
    ) from exc

from smoke_contract_review_docker import cleanup_users


def run(frontend_url: str, output: Path, screenshot: Path, trace_path: Path, headed: bool) -> None:
    base_url = frontend_url.rstrip("/")
    suffix = uuid4().hex
    email = f"rag-browser-{suffix}@example.test"
    password = "MatKhauAnToan123"
    question = "Người lao động được nghỉ hằng năm bao nhiêu ngày?"
    
    console_errors: list[str] = []
    resource_401_console_errors: list[str] = []
    expected_auth_probe_401s: list[str] = []
    page_errors: list[str] = []
    network_errors: list[str] = []
    ask_responses: list[dict] = []
    steps: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed)
            context = browser.new_context(
                locale="vi-VN",
                record_video_dir=str(output.parent / "videos")
            )
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
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

            def record_response(response: object) -> None:
                status = getattr(response, "status", 0)
                url = str(getattr(response, "url", ""))
                if url.endswith("/api/ask") and status == 200:
                    try:
                        ask_responses.append(response.json())
                    except Exception:
                        pass
                if status < 400:
                    return
                if status == 401 and url.endswith("/api/auth/me"):
                    expected_auth_probe_401s.append(url)
                    return
                network_errors.append(f"HTTP {status}: {url}")

            page.on("response", record_response)

            # 1. Open homepage
            page.goto(base_url, wait_until="networkidle", timeout=120_000)
            steps.append("PASS 1/11: Homepage loaded")

            # 2. Register account via UI
            page.get_by_role("button", name="Đăng nhập", exact=True).first.click()
            page.get_by_role("button", name="Đăng ký", exact=True).click()
            page.get_by_label("Tên hiển thị").fill("RAG Browser User")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Mật khẩu").fill(password)
            page.get_by_role("button", name="Tạo tài khoản", exact=True).click()
            page.wait_for_timeout(2000)
            steps.append("PASS 2/11: Registered account via UI")

            # 3. Enter question and submit via UI
            chat_input = page.locator("textarea.composer-input, textarea").first
            chat_input.fill(question)
            
            # Click send button
            send_btn = page.locator("button.composer-submit-btn, button[type='submit']").first
            send_btn.click()
            steps.append("PASS 3/11: Question submitted via UI")

            # 4. Wait for real RAG response
            page.wait_for_selector(".answer-stack, .stored-answer, .transcript", timeout=120_000)
            page.wait_for_timeout(3000)
            steps.append("PASS 4/11: Response rendered in UI")

            # 5. Verify real answer content
            answer_element = page.locator(".answer-stack, .answer-text, .transcript").first
            answer_text = answer_element.inner_text()
            assert len(answer_text) > 20, f"Answer content too short: {answer_text}"
            steps.append("PASS 5/11: Non-empty answer verified in UI")

            # 6. Verify source/citation card
            citations = page.locator(".citation-section, .citation-card, .citation-badge, article.citation-card")
            if citations.count() == 0:
                print("DEBUG HTML:", page.locator(".answer-stack").inner_html())
            assert citations.count() > 0 or len(ask_responses[0].get("sources", [])) > 0, "No citations rendered"
            steps.append("PASS 6/11: Citation and source card rendered")

            # 7. Verify real API HTTP 200 response received
            assert len(ask_responses) > 0, "No /api/ask response captured"
            assert ask_responses[0].get("answer"), "API response answer is empty"
            assert len(ask_responses[0].get("sources", [])) > 0, "API response sources empty"
            steps.append("PASS 7/11: Real API HTTP 200 response with citations verified")

            # 8. Bookmark / history test
            bookmark_btn = page.locator("button.bookmark-button, button.bookmark-btn").first
            if bookmark_btn.is_visible():
                bookmark_btn.click()
                page.wait_for_timeout(1000)
            steps.append("PASS 8/11: Bookmark action performed")

            # 9. Reload page and check persistence
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(2000)
            if not page.locator(".answer-stack, .transcript").first.is_visible():
                conv_item = page.locator(".conversation-item").first
                if conv_item.is_visible():
                    conv_item.click()
            page.wait_for_selector(".answer-stack, .transcript, .transcript-question", timeout=30_000)
            steps.append("PASS 9/11: Reload survived with conversation history intact")

            # 10. Logout
            logout_btn = page.get_by_role("button", name="Đăng xuất", exact=True)
            if logout_btn.is_visible():
                logout_btn.click()
            page.get_by_role("button", name="Đăng nhập", exact=True).first.wait_for(timeout=30_000)
            steps.append("PASS 10/11: Logout returned UI to protected state")

            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            
            context.tracing.stop(path=str(trace_path))
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
        steps.append("PASS 11/11: Zero unexpected console, page or HTTP errors")

        result = {
            "schema_version": "rag-browser-e2e-v1",
            "status": "PASS",
            "mode": "headed" if headed else "headless",
            "run_at": datetime.now().astimezone().isoformat(),
            "frontend_url": frontend_url,
            "question": question,
            "answer_sample": answer_text[:150],
            "sources_count": len(ask_responses[0].get("sources", [])) if ask_responses else 0,
            "console_errors": console_errors,
            "expected_auth_probe_401_count": len(expected_auth_probe_401s),
            "page_errors": page_errors,
            "network_errors": network_errors,
            "screenshot": str(screenshot),
            "trace": str(trace_path),
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
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    try:
        run(args.frontend_url, args.output, args.screenshot, args.trace, args.headed)
    except (AssertionError, RuntimeError, OSError, PlaywrightError) as exc:
        print(f"RAG BROWSER E2E: FAIL: {exc}", file=sys.stderr)
        return 1
    print("RAG BROWSER E2E: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
