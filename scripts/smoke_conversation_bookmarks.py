#!/usr/bin/env python3
"""Runtime smoke test for session auth, PostgreSQL history, and bookmarks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from uuid import uuid4

SESSION_COOKIE = "legal_rag_session"
CSRF_COOKIE = "legal_rag_csrf"


class ApiSession:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.last_set_cookie_headers: list[str] = []

    def cookie(self, name: str) -> str | None:
        return next((item.value for item in self.cookies if item.name == name), None)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        expected_status: int = 200,
        csrf: bool = False,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any] | None:
        body = None
        headers = dict(extra_headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if csrf:
            token = self.cookie(CSRF_COOKIE)
            if not token:
                raise RuntimeError("Không tìm thấy CSRF cookie của phiên đăng nhập.")
            headers["X-CSRF-Token"] = token

        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                status = response.status
                raw = response.read()
                self.last_set_cookie_headers = response.headers.get_all(
                    "Set-Cookie",
                    [],
                )
        except HTTPError as exc:
            raw = exc.read()
            if exc.code != expected_status:
                detail = raw.decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"{method} {path}: HTTP {exc.code}: {detail}"
                ) from exc
            status = exc.code
        except (URLError, OSError, TimeoutError) as exc:
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


def _compose_command(*arguments: str) -> list[str]:
    root = Path(__file__).resolve().parents[1]
    return [
        "docker",
        "compose",
        "-f",
        str(root / "docker-compose.yml"),
        "-f",
        str(root / "docker-compose.dev.yml"),
        *arguments,
    ]


def restart_backend(base_url: str) -> None:
    root = Path(__file__).resolve().parents[1]
    try:
        subprocess.run(
            _compose_command("restart", "backend"),
            cwd=root,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Không restart được backend: {exc}") from exc
    wait_for_live(base_url)


def wait_for_postgres(timeout: float = 90.0) -> None:
    root = Path(__file__).resolve().parents[1]
    deadline = time.monotonic() + timeout
    last_status = 1
    while time.monotonic() < deadline:
        result = subprocess.run(
            _compose_command(
                "exec",
                "-T",
                "postgres",
                "sh",
                "-lc",
                'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            ),
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        last_status = result.returncode
        if last_status == 0:
            return
        time.sleep(2)
    raise RuntimeError(
        f"PostgreSQL không healthy sau restart, mã cuối: {last_status}"
    )


def restart_postgres(base_url: str) -> None:
    root = Path(__file__).resolve().parents[1]
    try:
        subprocess.run(
            _compose_command("restart", "postgres"),
            cwd=root,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Không restart được PostgreSQL: {exc}") from exc
    wait_for_postgres()
    wait_for_live(base_url)


def wait_for_authenticated_session(
    api_session: ApiSession,
    *,
    expected_email: str,
    timeout: float = 90.0,
) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            payload = api_session.request_json("GET", "/auth/me", timeout=10)
            if payload and payload.get("user", {}).get("email") == expected_email:
                return
        except RuntimeError as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Session không hoạt động lại: {last_error}")


def cleanup_accounts(emails: list[str]) -> None:
    root = Path(__file__).resolve().parents[1]
    values = ", ".join(f"'{email}'" for email in emails)
    sql = f"DELETE FROM app_users WHERE email IN ({values});"
    command = [
        "docker",
        "compose",
        "-f",
        str(root / "docker-compose.yml"),
        "-f",
        str(root / "docker-compose.dev.yml"),
        "exec",
        "-T",
        "postgres",
        "sh",
        "-lc",
        'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
    ]
    subprocess.run(command, cwd=root, input=sql, text=True, check=True)


def register(session: ApiSession, email: str, display_name: str) -> None:
    result = session.request_json(
        "POST",
        "/auth/register",
        payload={
            "email": email,
            "password": "RuntimeSmokePassword123",
            "display_name": display_name,
        },
        expected_status=201,
        extra_headers={"X-Client-Id": str(uuid4())},
    )
    assert result is not None
    assert result.get("user", {}).get("email") == email
    assert session.cookie(SESSION_COOKIE)
    assert session.cookie(CSRF_COOKIE)
    cookie_headers = "\n".join(session.last_set_cookie_headers).lower()
    session_line = next(
        (line for line in cookie_headers.splitlines() if SESSION_COOKIE in line),
        "",
    )
    assert "httponly" in session_line
    assert "samesite=lax" in session_line


def run(
    base_url: str,
    question: str,
    method: str,
    *,
    restart_backend_after_bookmark: bool = False,
    restart_postgres_after_bookmark: bool = False,
) -> None:
    suffix = uuid4().hex
    email_a = f"runtime-a-{suffix}@example.test"
    email_b = f"runtime-b-{suffix}@example.test"
    user_a = ApiSession(base_url)
    user_b = ApiSession(base_url)
    conversation_id: str | None = None

    print(f"Tài khoản thử nghiệm A: {email_a}")
    print(f"Tài khoản thử nghiệm B: {email_b}")
    try:
        register(user_a, email_a, "Runtime A")
        register(user_b, email_b, "Runtime B")
        print("PASS 1/14: đăng ký hai tài khoản và kiểm tra cookie session")

        initial = user_a.request_json("GET", "/conversations")
        assert initial == {"conversations": []}, initial
        print("PASS 2/14: tài khoản mới chưa có lịch sử")

        answer = user_a.request_json(
            "POST",
            "/ask",
            payload={"question": question, "method": method},
            csrf=True,
        )
        assert answer is not None and answer.get("history_saved") is True
        conversation_id = answer.get("conversation_id")
        assistant_id = answer.get("assistant_message_id")
        assert isinstance(conversation_id, str) and conversation_id
        assert isinstance(assistant_id, str) and assistant_id
        print("PASS 3/14: đáp án được lưu theo user A trong PostgreSQL")

        encoded_conversation = quote(conversation_id, safe="")
        encoded_message = quote(assistant_id, safe="")
        denied = user_b.request_json(
            "GET",
            f"/conversations/{encoded_conversation}",
            expected_status=404,
        )
        assert denied is not None
        user_b.request_json(
            "PUT",
            f"/bookmarks/{encoded_message}",
            payload={},
            expected_status=404,
            csrf=True,
        )
        assert user_b.request_json("GET", "/conversations") == {"conversations": []}
        assert user_b.request_json("GET", "/bookmarks") == {"items": []}
        print("PASS 4/14: user B không đọc hoặc bookmark dữ liệu user A")

        bookmark = user_a.request_json(
            "PUT",
            f"/bookmarks/{encoded_message}",
            payload={"note": "runtime smoke"},
            csrf=True,
        )
        bookmark_again = user_a.request_json(
            "PUT",
            f"/bookmarks/{encoded_message}",
            payload={"note": "runtime smoke updated"},
            csrf=True,
        )
        assert bookmark and bookmark_again
        assert bookmark_again.get("id") == bookmark.get("id")
        print("PASS 5/14: bookmark PUT idempotent")

        if restart_backend_after_bookmark:
            restart_backend(base_url)
            print("PASS 6/14: backend restart và kết nối lại thành công")
        else:
            print("PASS 6/14: bỏ qua backend restart theo cấu hình")

        wait_for_authenticated_session(user_a, expected_email=email_a)
        print("PASS 7/14: session vẫn hợp lệ sau restart backend")

        if restart_postgres_after_bookmark:
            restart_postgres(base_url)
            wait_for_authenticated_session(user_a, expected_email=email_a)
            print("PASS 8/14: PostgreSQL restart, session và volume vẫn hoạt động")
        else:
            print("PASS 8/14: bỏ qua PostgreSQL restart theo cấu hình")

        detail = user_a.request_json(
            "GET",
            f"/conversations/{encoded_conversation}",
        )
        assert detail is not None, "API không trả chi tiết hội thoại."
        messages = detail.get("messages", [])
        roles = [item.get("role") for item in messages]
        assert roles == ["user", "assistant"], {
            "expected_roles": ["user", "assistant"],
            "stored_roles": roles,
            "detail": detail,
        }
        assert messages[1].get("id") == assistant_id, {
            "expected_assistant_id": assistant_id,
            "stored_assistant_id": messages[1].get("id"),
        }
        assert messages[1].get("bookmarked") is True, messages[1]

        # Bản ghi nguồn có thêm khóa nội bộ `id`, vì vậy chỉ so sánh
        # các trường nghiệp vụ được round-trip từ AskResponse.
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
        assert stored_sources == expected_sources, {
            "expected_sources": expected_sources,
            "stored_sources": stored_sources,
        }
        print("PASS 9/14: reload giữ transcript, nguồn và bookmark")

        saved = user_a.request_json("GET", "/bookmarks")
        assert saved and len(saved.get("items", [])) == 1
        print("PASS 10/14: trang Đã lưu đọc đúng dữ liệu user A")

        renamed = user_a.request_json(
            "PATCH",
            f"/conversations/{encoded_conversation}",
            payload={"title": "Runtime session smoke"},
            csrf=True,
        )
        assert renamed and renamed.get("title") == "Runtime session smoke"
        print("PASS 11/14: đổi tên hội thoại")

        user_a.request_json(
            "DELETE",
            f"/bookmarks/{encoded_message}",
            expected_status=204,
            csrf=True,
        )
        print("PASS 12/14: bỏ bookmark")

        sessions = user_a.request_json("GET", "/auth/sessions")
        assert sessions and len(sessions.get("sessions", [])) == 1
        print("PASS 13/14: API liệt kê đúng session hiện tại")

        user_a.request_json("POST", "/auth/logout", expected_status=204, csrf=True)
        user_a.request_json("GET", "/auth/me", expected_status=401)
        assert user_b.request_json("GET", "/auth/me") is not None
        print("PASS 14/14: logout thu hồi session A, session B không bị ảnh hưởng")
    finally:
        if conversation_id and user_a.cookie(SESSION_COOKIE):
            try:
                user_a.request_json(
                    "DELETE",
                    f"/conversations/{quote(conversation_id, safe='')}",
                    expected_status=204,
                    csrf=True,
                )
            except Exception as exc:  # pragma: no cover - cleanup diagnostics
                print(f"Cảnh báo dọn hội thoại qua API: {exc}")
        try:
            cleanup_accounts([email_a, email_b])
            print("Dọn tài khoản thử nghiệm: PASS")
        except Exception as exc:  # pragma: no cover - cleanup diagnostics
            print(f"Cảnh báo: chưa dọn được tài khoản thử nghiệm: {exc}")


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
    parser.add_argument("--restart-backend", action="store_true")
    parser.add_argument("--restart-postgres", action="store_true")
    args = parser.parse_args()

    try:
        run(
            args.base_url,
            args.question,
            args.method,
            restart_backend_after_bookmark=args.restart_backend,
            restart_postgres_after_bookmark=args.restart_postgres,
        )
    except (AssertionError, RuntimeError, ValueError) as exc:
        print(f"RUNTIME SMOKE: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("RUNTIME SMOKE: PASS")


if __name__ == "__main__":
    main()
