#!/usr/bin/env python3
"""Build reproducible manifests for Pháp điển and VBPL corpora."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def count_jsonl(path: Path) -> int:
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/manifests"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    output_dir = root / args.output_dir
    generated_at = datetime.now(timezone.utc).isoformat()
    commit = git_commit(root)

    inventory: dict[str, Any] = {
        "schema_version": "legal-data-inventory-v1",
        "generated_at": generated_at,
        "git_commit": commit,
        "corpora": {},
    }

    # Pháp điển
    pa = root / "data/processed/articles_raw.json"
    pc = root / "data/processed/legal_chunks.jsonl"
    pr = root / "data/processed/parser_report.json"
    pi = root / "data/processed/html_inspection.json"
    source_html = root / "data/raw/DeMuc_20.2_Lao_Dong.html"

    if pa.exists() and pc.exists():
        articles = load_json(pa)
        parser_report = load_json(pr) if pr.exists() else {}
        inspection = load_json(pi) if pi.exists() else {}
        phapdien = {
            "schema_version": "corpus-manifest-v1",
            "corpus_id": "phapdien-20.2",
            "source_type": "phapdien",
            "generated_at": generated_at,
            "git_commit": commit,
            "source": {
                "path": str(source_html.relative_to(root))
                if source_html.exists() else None,
                "sha256": sha256_file(source_html)
                if source_html.exists() else None,
            },
            "articles": {
                "path": str(pa.relative_to(root)),
                "sha256": sha256_file(pa),
                "count": len(articles.get("articles", [])),
            },
            "chunks": {
                "path": str(pc.relative_to(root)),
                "sha256": sha256_file(pc),
                "count": count_jsonl(pc),
            },
            "quality": {
                "parser_valid": parser_report.get(
                    "is_valid", parser_report.get("status")
                ),
                "table_count": parser_report.get(
                    "table_count",
                    inspection.get("article_statistics", {}).get("table_count"),
                ),
                "attachment_count": parser_report.get(
                    "attachment_count",
                    inspection.get("article_statistics", {}).get(
                        "attachment_count"
                    ),
                ),
            },
        }
        write_json(output_dir / "phapdien_manifest.json", phapdien)
        inventory["corpora"]["phapdien"] = phapdien

    # VBPL
    va = root / "data/processed/vbpl_articles_raw.json"
    vc = root / "data/processed/vbpl_legal_chunks.jsonl"
    vs = root / "data/processed/vbpl_chunking_summary.json"
    vr = root / "data/quality/vbpl_article_build_report.json"
    vq = root / "data/quality/vbpl_chunk_quality_report.json"

    if va.exists() and vc.exists():
        articles = load_json(va)
        article_report = load_json(vr) if vr.exists() else {}
        quality_report = load_json(vq) if vq.exists() else {}
        summary = load_json(vs) if vs.exists() else {}
        vbpl = {
            "schema_version": "corpus-manifest-v1",
            "corpus_id": "vbpl-labor",
            "source_type": "vbpl",
            "generated_at": generated_at,
            "git_commit": commit,
            "articles": {
                "path": str(va.relative_to(root)),
                "sha256": sha256_file(va),
                "count": len(articles.get("articles", [])),
            },
            "chunks": {
                "path": str(vc.relative_to(root)),
                "sha256": sha256_file(vc),
                "count": count_jsonl(vc),
            },
            "quality": {
                "article_build_status": article_report.get("status"),
                "chunk_quality_status": quality_report.get("status"),
                "documents_processed": article_report.get(
                    "documents_processed"
                ),
                "articles_validated_total": article_report.get(
                    "articles_validated_total"
                ),
                "articles_selected": article_report.get("articles_selected"),
                "article_coverage": quality_report.get("article_coverage"),
                "appendix_leakage_articles": quality_report.get(
                    "appendix_leakage_articles", []
                ),
                "administrative_tail_leakage_articles": quality_report.get(
                    "administrative_tail_leakage_articles", []
                ),
                "hard_token_overflow": quality_report.get(
                    "hard_token_overflow"
                ),
                "validation": summary.get("validation"),
            },
        }
        write_json(output_dir / "vbpl_manifest.json", vbpl)
        inventory["corpora"]["vbpl"] = vbpl

    write_json(output_dir / "data_inventory.json", inventory)
    print(json.dumps(inventory, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
