#!/usr/bin/env python3
"""Real HTTP E2E for contract review using a persistent application database."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import httpx
from docx import Document


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples/contract-review/sample_labor_contract.docx"
PDF_SAMPLE = ROOT / "samples/contract-review/sample_labor_contract.pdf"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_live(base_url: str, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    with httpx.Client(trust_env=False) as client:
        while time.time() < deadline:
            try:
                if client.get(f"{base_url}/live", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    raise RuntimeError("Backend runtime did not become ready.")


def start_server(
    port: int,
    database_path: Path,
    log_path: Path,
    *,
    require_authority_approval: bool,
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT / "backend"),
        "DATABASE_URL": f"sqlite+pysqlite:///{database_path}",
        "DATABASE_AUTO_CREATE": "true",
        "PASSWORD_PBKDF2_ITERATIONS": "100000",
        "LEGAL_CHUNKS_PATH": str(ROOT / "data/releases/labor-law-canonical-word-20260804-164432-candidate/canonical_chunks.jsonl"),
        "CORPUS_REQUIRE_AUTHORITY_APPROVAL": (
            "true" if require_authority_approval else "false"
        ),
    })
    log = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.runtime_contract_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process


def stop_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


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
    assert csrf, "missing csrf cookie"
    return csrf


def assert_markers(review: dict[str, object]) -> None:
    findings = review["findings"]
    assert isinstance(findings, list) and len(findings) == 4
    assert {item["category"] for item in findings} == {
        "probation", "salary", "working_time", "termination"
    }
    for finding in findings:
        sources = finding["sources"]
        assert sources, finding
        source_ids = {source["source_id"] for source in sources}
        markers = set(re.findall(r"\[(S\d+)\]", finding["analysis"]))
        assert markers, finding["analysis"]
        assert markers.issubset(source_ids), (markers, source_ids)
        assert finding["contract_excerpt"]
        assert finding["recommendation"]

    by_category = {item["category"]: item for item in findings}
    assert by_category["probation"]["severity"] == "attention"
    probation_excerpt = "".join(
        character
        for character in unicodedata.normalize(
            "NFD", by_category["probation"]["contract_excerpt"].casefold()
        ).replace("đ", "d")
        if unicodedata.category(character) != "Mn"
    )
    salary_excerpt = "".join(
        character
        for character in unicodedata.normalize(
            "NFD", by_category["salary"]["contract_excerpt"].casefold()
        ).replace("đ", "d")
        if unicodedata.category(character) != "Mn"
    )
    termination_excerpt = "".join(
        character
        for character in unicodedata.normalize(
            "NFD", by_category["termination"]["contract_excerpt"].casefold()
        ).replace("đ", "d")
        if unicodedata.category(character) != "Mn"
    )
    assert "75 ngay" in probation_excerpt
    assert by_category["salary"]["severity"] == "info"
    assert "12.000.000 dong" in salary_excerpt
    assert by_category["working_time"]["severity"] == "attention"
    assert "45 giờ/tuần" in by_category["working_time"]["analysis"]
    assert by_category["termination"]["severity"] == "warning"
    assert "10 ngay" in termination_excerpt


def create_adversarial_docx(path: Path) -> None:
    document = Document()
    for paragraph in (
        "HỢP ĐỒNG LAO ĐỘNG XÁC ĐỊNH THỜI HẠN 24 tháng",
        "Thử việc 30 ngày; khoản thanh toán hoàn tất trong 90 ngày.",
        "Tiền lương 12.000.000 đồng, trả vào ngày 05 hằng tháng.",
        "Thời giờ làm việc 8 giờ/ngày, 48 giờ/tuần.",
        "Khi chấm dứt hợp đồng phải báo trước 45 ngày; nghỉ phép riêng 1 ngày.",
    ):
        document.add_paragraph(paragraph)
    document.save(path)


def upload_contract(
    client: httpx.Client,
    csrf: str,
    path: Path,
    content_type: str,
) -> httpx.Response:
    with path.open("rb") as source:
        return client.post(
            "/contract-reviews",
            headers={"X-CSRF-Token": csrf},
            data={"method": "sparse"},
            files={"file": (path.name, source, content_type)},
        )


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="contract-review-e2e-") as temp_name:
        temp = Path(temp_name)
        database_path = temp / "runtime.db"
        server_log = temp / "server.log"
        adversarial_path = temp / "adversarial_numbers_contract.docx"
        create_adversarial_docx(adversarial_path)
        port = free_port()
        base_url = f"http://127.0.0.1:{port}/api"
        process = start_server(
            port,
            database_path,
            server_log,
            require_authority_approval=True,
        )
        steps: list[str] = []
        try:
            wait_live(base_url)
            steps.append("PASS 1: backend HTTP runtime ready")
            user_a = httpx.Client(base_url=base_url, timeout=120, trust_env=False)
            user_b = httpx.Client(base_url=base_url, timeout=120, trust_env=False)
            csrf_a = register(user_a, "runtime-contract-a@example.test")
            csrf_b = register(user_b, "runtime-contract-b@example.test")
            steps.append("PASS 2: registered two users with real session cookies")

            blocked = upload_contract(
                user_a,
                csrf_a,
                SAMPLE,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            assert blocked.status_code == 503, blocked.text
            assert "authority_review_pending" in blocked.text
            assert user_a.get("/contract-reviews").json()["total"] == 0
            steps.append("PASS 3: authority gate blocked a new legal report before persistence")

            stop_server(process)
            process = start_server(
                port,
                database_path,
                server_log,
                require_authority_approval=False,
            )
            wait_live(base_url)

            response = upload_contract(
                user_a,
                csrf_a,
                SAMPLE,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            assert response.status_code == 201, response.text
            review = response.json()
            review_id = review["id"]
            assert review["extracted_character_count"] > 500
            assert_markers(review)
            steps.append("PASS 4: uploaded real DOCX and created four grounded findings")

            adversarial_response = upload_contract(
                user_a,
                csrf_a,
                adversarial_path,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            assert adversarial_response.status_code == 201, adversarial_response.text
            adversarial_review = adversarial_response.json()
            adversarial_id = adversarial_review["id"]
            adversarial_findings = {
                item["category"]: item for item in adversarial_review["findings"]
            }
            assert "30 ngày" in adversarial_findings["probation"]["analysis"]
            assert "90 ngày" not in adversarial_findings["probation"]["analysis"]
            assert "8 giờ/ngày" in adversarial_findings["working_time"]["analysis"]
            assert "48 giờ/ngày" not in adversarial_findings["working_time"]["analysis"]
            assert "45 ngày" in adversarial_findings["termination"]["analysis"]
            assert "1 ngày" not in adversarial_findings["termination"]["analysis"]
            steps.append("PASS 5: adversarial DOCX kept each number bound to its legal term")

            pdf_response = upload_contract(
                user_a,
                csrf_a,
                PDF_SAMPLE,
                "application/pdf",
            )
            assert pdf_response.status_code == 201, pdf_response.text
            pdf_review = pdf_response.json()
            pdf_id = pdf_review["id"]
            assert pdf_review["extracted_character_count"] > 300
            assert_markers(pdf_review)
            steps.append("PASS 6: uploaded a real text-layer PDF through the HTTP API")

            listing = user_a.get("/contract-reviews")
            assert listing.status_code == 200 and listing.json()["total"] == 3
            detail = user_a.get(f"/contract-reviews/{review_id}")
            assert detail.status_code == 200
            assert detail.json()["file_sha256"] == review["file_sha256"]
            steps.append("PASS 7: list and detail APIs returned all persisted reports")

            assert user_b.get(f"/contract-reviews/{review_id}").status_code == 404
            assert user_b.delete(
                f"/contract-reviews/{review_id}", headers={"X-CSRF-Token": csrf_b}
            ).status_code == 404
            steps.append("PASS 8: cross-user read and delete were denied with 404")

            stop_server(process)
            process = start_server(
                port,
                database_path,
                server_log,
                require_authority_approval=False,
            )
            wait_live(base_url)
            persisted = user_a.get(f"/contract-reviews/{review_id}")
            assert persisted.status_code == 200, persisted.text
            assert_markers(persisted.json())
            assert user_a.get(f"/contract-reviews/{adversarial_id}").status_code == 200
            assert user_a.get(f"/contract-reviews/{pdf_id}").status_code == 200
            assert user_a.get("/auth/me").status_code == 200
            steps.append("PASS 9: session and all reports survived backend process restart")

            for stored_id in (review_id, adversarial_id, pdf_id):
                deleted = user_a.delete(
                    f"/contract-reviews/{stored_id}",
                    headers={"X-CSRF-Token": csrf_a},
                )
                assert deleted.status_code == 204, deleted.text
                assert user_a.get(f"/contract-reviews/{stored_id}").status_code == 404
            assert user_a.get("/contract-reviews").json()["total"] == 0
            steps.append("PASS 10: owner deleted all reports and no data remained")

            user_a.close()
            user_b.close()
            return {
                "status": "PASS",
                "database": "persistent SQLite file over real HTTP",
                "sample": str(SAMPLE.relative_to(ROOT)),
                "steps": steps,
                "review_id": review_id,
                "finding_summary": [
                    {
                        "category": item["category"],
                        "severity": item["severity"],
                        "article_codes": [
                            source["article_code"] for source in item["sources"]
                        ],
                        "analysis": item["analysis"],
                    }
                    for item in review["findings"]
                ],
                "server_log_tail": server_log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:],
            }
        finally:
            stop_server(process)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
