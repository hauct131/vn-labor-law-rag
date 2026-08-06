#!/usr/bin/env python3
"""Runtime smoke test for PostgreSQL conversation history and bookmarks.

The script performs one real /api/ask call, then verifies that the generated
answer survives reload and can be bookmarked. It always attempts to clean up the
temporary conversation before returning.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    client_id: str,
    payload: dict[str, Any] | None = None,
    expected_status: int = 200,
    timeout: float = 120.0,
) -> dict[str, Any] | None:
    body = None
    headers = {"X-Client-Id": client_id}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path}: HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Không kết nối được API: {exc}") from exc

    if status != expected_status:
        raise RuntimeError(
            f"{method} {path}: cần HTTP {expected_status}, nhận HTTP {status}"
        )
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def wait_for_live(base_url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    live_url = f"{base_url.rstrip('/')}/live"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(live_url, timeout=5) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Backend không hoạt động lại sau restart: {last_error}")


def restart_backend(base_url: str) -> None:
    root = Path(__file__).resolve().parents[1]
    command = [
        "docker",
        "compose",
        "-f",
        str(root / "docker-compose.yml"),
        "-f",
        str(root / "docker-compose.dev.yml"),
        "restart",
        "backend",
    ]
    try:
        subprocess.run(command, cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Không restart được backend bằng Docker Compose: {exc}") from exc
    wait_for_live(base_url)


def run(
    base_url: str,
    question: str,
    method: str,
    *,
    restart_backend_after_bookmark: bool = False,
) -> None:
    client_id = str(uuid4())
    conversation_id: str | None = None

    print(f"Client thử nghiệm: {client_id}")
    try:
        initial = request_json(
            base_url,
            "GET",
            "/conversations",
            client_id=client_id,
        )
        assert initial == {"conversations": []}, initial
        print("PASS 1/9: client mới chưa có lịch sử")

        answer = request_json(
            base_url,
            "POST",
            "/ask",
            client_id=client_id,
            payload={"question": question, "method": method},
        )
        assert answer is not None
        assert answer.get("history_saved") is True, answer
        conversation_id = answer.get("conversation_id")
        assistant_message_id = answer.get("assistant_message_id")
        assert isinstance(conversation_id, str) and conversation_id
        assert isinstance(assistant_message_id, str) and assistant_message_id
        print("PASS 2/9: đáp án được lưu vào PostgreSQL")

        encoded_conversation = quote(conversation_id, safe="")
        encoded_message = quote(assistant_message_id, safe="")
        bookmark = request_json(
            base_url,
            "PUT",
            f"/bookmarks/{encoded_message}",
            client_id=client_id,
            payload={"note": "runtime smoke"},
        )
        assert bookmark is not None
        assert bookmark.get("message_id") == assistant_message_id
        print("PASS 3/9: tạo bookmark")

        # PUT must be idempotent, not create a duplicate row.
        bookmark_again = request_json(
            base_url,
            "PUT",
            f"/bookmarks/{encoded_message}",
            client_id=client_id,
            payload={"note": "runtime smoke updated"},
        )
        assert bookmark_again is not None
        assert bookmark_again.get("id") == bookmark.get("id")
        print("PASS 4/9: bookmark PUT idempotent")

        if restart_backend_after_bookmark:
            restart_backend(base_url)
            print("PASS 5/9: backend restart và kết nối lại thành công")
        else:
            print("PASS 5/9: bỏ qua backend restart theo cấu hình")

        detail = request_json(
            base_url,
            "GET",
            f"/conversations/{encoded_conversation}",
            client_id=client_id,
        )
        assert detail is not None
        messages = detail.get("messages", [])
        assert [item.get("role") for item in messages] == ["user", "assistant"]
        assert messages[1].get("id") == assistant_message_id
        assert messages[1].get("bookmarked") is True
        expected_sources = [
            (
                item.get("source_id"),
                item.get("chunk_id"),
                item.get("article_code"),
                item.get("content"),
                item.get("rank"),
            )
            for item in answer.get("sources", [])
        ]
        stored_sources = [
            (
                item.get("source_id"),
                item.get("chunk_id"),
                item.get("article_code"),
                item.get("content"),
                item.get("rank"),
            )
            for item in messages[1].get("sources", [])
        ]
        assert stored_sources == expected_sources
        print("PASS 6/9: reload giữ câu hỏi, câu trả lời, nguồn và bookmark")

        saved = request_json(base_url, "GET", "/bookmarks", client_id=client_id)
        assert saved is not None
        items = saved.get("items", [])
        assert len(items) == 1
        assert items[0].get("answer", {}).get("id") == assistant_message_id
        assert items[0].get("question") == question
        print("PASS 7/9: trang Đã lưu đọc được snapshot câu trả lời")

        renamed = request_json(
            base_url,
            "PATCH",
            f"/conversations/{encoded_conversation}",
            client_id=client_id,
            payload={"title": "Runtime smoke conversation"},
        )
        assert renamed is not None
        assert renamed.get("title") == "Runtime smoke conversation"
        print("PASS 8/9: đổi tên hội thoại")

        request_json(
            base_url,
            "DELETE",
            f"/bookmarks/{encoded_message}",
            client_id=client_id,
            expected_status=204,
        )
        print("PASS 9/9: bỏ bookmark")
    finally:
        if conversation_id:
            try:
                request_json(
                    base_url,
                    "DELETE",
                    f"/conversations/{quote(conversation_id, safe='')}",
                    client_id=client_id,
                    expected_status=204,
                )
                print("Dọn dữ liệu thử nghiệm: PASS")
            except Exception as exc:  # pragma: no cover - cleanup diagnostics
                print(f"Cảnh báo: chưa dọn được hội thoại thử nghiệm: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000/api")
    parser.add_argument(
        "--question",
        default="Người lao động được nghỉ hằng năm bao nhiêu ngày?",
    )
    parser.add_argument(
        "--method",
        choices=("sparse", "dense", "hybrid"),
        default="sparse",
    )
    parser.add_argument(
        "--restart-backend",
        action="store_true",
        help=(
            "Restart service backend sau khi lưu bookmark để xác minh dữ liệu "
            "PostgreSQL tồn tại qua vòng đời process."
        ),
    )
    args = parser.parse_args()

    try:
        run(
            args.base_url,
            args.question,
            args.method,
            restart_backend_after_bookmark=args.restart_backend,
        )
    except (AssertionError, RuntimeError, ValueError) as exc:
        print(f"RUNTIME SMOKE: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("RUNTIME SMOKE: PASS")


if __name__ == "__main__":
    main()
