#!/usr/bin/env python3
"""Check every canonical legal-document URL without touching the RAG index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MAPPING = Path("data/reference/official_legal_sources.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kiểm tra 16 liên kết văn bản pháp luật chính thức."
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=DEFAULT_MAPPING,
        help=f"File ánh xạ (mặc định: {DEFAULT_MAPPING})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Timeout mỗi liên kết, tính bằng giây.",
    )
    return parser.parse_args()


def load_documents(path: Path) -> list[tuple[str, dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents = payload.get("documents")
    if not isinstance(documents, dict):
        raise ValueError("File ánh xạ phải có object 'documents'.")
    return sorted(documents.items())


def check_url(url: str, *, timeout: float) -> tuple[int, str]:
    request = Request(
        url,
        headers={
            "User-Agent": "vn-labor-law-rag-link-check/1.0",
            "Range": "bytes=0-1023",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        response.read(1)
        return int(response.status), response.geturl()


def main() -> int:
    args = parse_args()
    try:
        documents = load_documents(args.mapping)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: Không đọc được bảng ánh xạ: {exc}", file=sys.stderr)
        return 2

    failures = 0
    for source_id, record in documents:
        number = str(record.get("document_number", source_id))
        url = str(record.get("canonical_url", "")).strip()
        try:
            status, final_url = check_url(url, timeout=args.timeout)
            print(f"OK   {number:<24} HTTP {status}  {final_url}")
        except HTTPError as exc:
            failures += 1
            print(f"FAIL {number:<24} HTTP {exc.code}  {url}")
        except (URLError, TimeoutError, ValueError) as exc:
            failures += 1
            print(f"FAIL {number:<24} {exc}  {url}")

    print(f"\nĐã kiểm tra {len(documents)} liên kết; lỗi: {failures}.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
