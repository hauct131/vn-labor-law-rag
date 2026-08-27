#!/usr/bin/env python3
"""Docker/PostgreSQL runtime E2E for the contract-review product flow."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples/contract-review/sample_labor_contract.docx"
COMPOSE = [
    "docker", "compose",
    "-f", "docker-compose.yml",
    "-f", "docker-compose.dev.yml",
]


def run_command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*args], cwd=ROOT, check=check, text=True,
        capture_output=True,
    )


def wait_http(base_url: str, timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/live", timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("Backend Docker did not become ready.")


def wait_postgres(timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = run_command(
            *COMPOSE, "exec", "-T", "postgres", "sh", "-lc",
            'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("PostgreSQL did not become healthy.")


def register(client: httpx.Client, email: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "MatKhauAnToan123",
            "display_name": email.split("@", 1)[0],
        },
    )
    assert response.status_code == 201, response.text
    csrf = client.cookies.get("legal_rag_csrf")
    assert csrf
    return csrf


def validate_review(review: dict[str, object]) -> None:
    assert review["status"] == "completed"
    assert review["extracted_character_count"] > 500
    findings = review["findings"]
    assert isinstance(findings, list) and len(findings) == 4
    assert {item["category"] for item in findings} == {
        "probation", "salary", "working_time", "termination"
    }
    for item in findings:
        assert item["contract_excerpt"]
        assert item["analysis"]
        assert item["recommendation"]
        sources = item["sources"]
        assert sources
        ids = {source["source_id"] for source in sources}
        markers = set(re.findall(r"\[(S\d+)\]", item["analysis"]))
        assert markers and markers.issubset(ids)

    by_category = {item["category"]: item for item in findings}
    assert by_category["probation"]["severity"] == "attention"
    assert "75 ngày" in by_category["probation"]["contract_excerpt"]
    assert by_category["salary"]["severity"] == "info"
    assert "12.000.000 đồng" in by_category["salary"]["contract_excerpt"]
    assert by_category["working_time"]["severity"] == "attention"
    assert "45 giờ/tuần" in by_category["working_time"]["analysis"]
    assert by_category["termination"]["severity"] == "warning"
    assert "10 ngày" in by_category["termination"]["contract_excerpt"]


def cleanup_users(emails: list[str]) -> None:
    escaped = ",".join("'" + email.replace("'", "''") + "'" for email in emails)
    sql = f"DELETE FROM app_users WHERE email IN ({escaped});"
    result = run_command(
        *COMPOSE,
        "exec", "-T", "postgres", "sh", "-lc",
        f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "{sql}"',
        check=False,
    )
    if result.returncode != 0:
        print("WARN cleanup failed:", result.stderr.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5173")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    suffix = uuid4().hex
    email_a = f"contract-runtime-a-{suffix}@example.test"
    email_b = f"contract-runtime-b-{suffix}@example.test"
    steps: list[str] = []
    wait_postgres()
    wait_http(args.base_url)
    frontend = httpx.get(args.frontend_url, timeout=20)
    assert frontend.status_code == 200 and 'id="root"' in frontend.text
    steps.append("PASS 1/10: PostgreSQL healthy, backend live and frontend served")

    user_a = httpx.Client(base_url=args.base_url, timeout=180)
    user_b = httpx.Client(base_url=args.base_url, timeout=180)
    try:
        csrf_a = register(user_a, email_a)
        csrf_b = register(user_b, email_b)
        steps.append("PASS 2/10: registered two accounts with real cookies")

        with SAMPLE.open("rb") as source:
            response = user_a.post(
                "/contract-reviews",
                headers={"X-CSRF-Token": csrf_a},
                data={"method": "sparse"},
                files={
                    "file": (
                        SAMPLE.name,
                        source,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
        assert response.status_code == 201, response.text
        review = response.json()
        validate_review(review)
        review_id = review["id"]
        steps.append("PASS 3/10: uploaded real DOCX and received grounded report")

        listing = user_a.get("/contract-reviews")
        assert listing.status_code == 200
        assert any(item["id"] == review_id for item in listing.json()["items"])
        steps.append("PASS 4/10: report is persisted and listed")

        assert user_b.get(f"/contract-reviews/{review_id}").status_code == 404
        assert user_b.delete(
            f"/contract-reviews/{review_id}", headers={"X-CSRF-Token": csrf_b}
        ).status_code == 404
        steps.append("PASS 5/10: user isolation is enforced")

        run_command(*COMPOSE, "restart", "backend")
        wait_http(args.base_url)
        detail = user_a.get(f"/contract-reviews/{review_id}")
        assert detail.status_code == 200, detail.text
        validate_review(detail.json())
        assert user_a.get("/auth/me").status_code == 200
        steps.append("PASS 6/10: session and report survived backend restart")

        run_command(*COMPOSE, "restart", "postgres")
        wait_postgres()
        wait_http(args.base_url)
        detail = user_a.get(f"/contract-reviews/{review_id}")
        assert detail.status_code == 200, detail.text
        validate_review(detail.json())
        assert user_a.get("/auth/me").status_code == 200
        steps.append("PASS 7/10: session and report survived PostgreSQL restart")

        deleted = user_a.delete(
            f"/contract-reviews/{review_id}", headers={"X-CSRF-Token": csrf_a}
        )
        assert deleted.status_code == 204, deleted.text
        assert user_a.get(f"/contract-reviews/{review_id}").status_code == 404
        steps.append("PASS 8/10: owner delete removed review")

        assert user_a.post("/auth/logout", headers={"X-CSRF-Token": csrf_a}).status_code == 204
        assert user_b.post("/auth/logout", headers={"X-CSRF-Token": csrf_b}).status_code == 204
        steps.append("PASS 9/10: both sessions logged out")

        cleanup_users([email_a, email_b])
        steps.append("PASS 10/10: test accounts cleaned from PostgreSQL")

        result = {
            "schema_version": "contract-review-docker-e2e-v1",
            "status": "PASS",
            "run_at": datetime.now().astimezone().isoformat(),
            "base_url": args.base_url,
            "sample": str(SAMPLE),
            "steps": steps,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        user_a.close()
        user_b.close()


if __name__ == "__main__":
    raise SystemExit(main())
