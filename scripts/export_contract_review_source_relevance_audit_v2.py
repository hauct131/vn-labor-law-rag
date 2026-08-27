#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any


def _case_text(case: dict[str, Any]) -> str:
    for key in ("text", "case_text", "contract_text", "question"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Case has no text field: {case.get('id') or case.get('case_id')}")


def _case_id(case: dict[str, Any], index: int) -> str:
    return str(case.get("id") or case.get("case_id") or f"case_{index:03d}")


def _expected(case: dict[str, Any]) -> list[str]:
    value = case.get("expected_article_codes") or []
    if isinstance(value, str):
        return [part.strip() for part in value.split(";") if part.strip()]
    return [str(item) for item in value]


def _category_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _resolve_method(service_module: Any) -> Any:
    enum_cls = service_module.RetrievalMethod
    try:
        members = list(enum_cls)
    except TypeError:
        return "contract_canonical_lexical_v1"
    for member in members:
        if str(getattr(member, "value", "")) == "contract_canonical_lexical_v1":
            return member
    return members[0] if members else "contract_canonical_lexical_v1"


def _read(source: Any, *names: str, default: Any = "") -> Any:
    if isinstance(source, dict):
        for name in names:
            value = source.get(name)
            if value not in (None, ""):
                return value
        return default
    for name in names:
        value = getattr(source, name, None)
        if value not in (None, ""):
            return value
    return default




def audit_source_text(source: Any) -> str:
    """Return clean evidence body for audit/export without duplicating headers.

    ``LegalSource.content`` already contains the canonical article header in the
    Contract Review corpus. The Cross-Encoder's ``default_source_text`` may add
    metadata for scoring, but audit artifacts should preserve the canonical body
    exactly once.
    """
    body = _read(
        source,
        "content",
        "source_text",
        "text",
        "content_preview",
        "excerpt",
        default="",
    )
    if body not in (None, ""):
        return str(body)
    code = _read(source, "article_code", "law_article_code", default="")
    title = _read(source, "article_title", "title", default="")
    parts = [part for part in (f"Điều {code}" if code else "", str(title or "")) if part]
    return " — ".join(parts)

def _load_benchmark(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("v2_cross_encoder", {}).get("cases", [])
    return {str(row["id"]): row for row in rows}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Export Contract Review V2 source relevance audit from the exact "
            "review_contract() runtime path used by the Cross-Encoder benchmark."
        )
    )
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument(
        "--benchmark-json",
        type=Path,
        default=None,
        help=(
            "Optional contract_review_cross_encoder_ablation.json. When supplied, "
            "the exporter refuses to write the CSV unless every case's V2 article "
            "and chunk sequence exactly matches the benchmark."
        ),
    )
    args = ap.parse_args()

    if args.top_k < 1:
        raise SystemExit("--top-k must be >= 1")

    # This exporter is intentionally V2-only.
    enabled = os.getenv("CONTRACT_REVIEW_RERANKER_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise SystemExit(
            "ERROR: CONTRACT_REVIEW_RERANKER_ENABLED must be true for V2 audit export."
        )

    from app.services import contract_review_service as service
    from app.services.contract_review_reranker import (
        get_contract_review_reranker,
        reset_contract_review_reranker_for_tests,
    )

    reset_contract_review_reranker_for_tests()
    reranker = get_contract_review_reranker()
    if not reranker.enabled:
        raise SystemExit("ERROR: reranker config is disabled after environment parsing.")

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError("cases JSON must be a list or {'cases': [...]} object")

    benchmark = _load_benchmark(args.benchmark_json)
    method = _resolve_method(service)

    preferred_by_category: dict[str, set[str]] = {}
    for rule in service.CATEGORIES:
        preferred_by_category[str(rule.key)] = {
            str(x) for x in getattr(rule, "preferred_article_codes", []) or []
        }

    rows: list[dict[str, Any]] = []
    actual_by_case: dict[str, dict[str, list[str]]] = {}

    for i, case in enumerate(cases, 1):
        cid = _case_id(case, i)
        category = str(case.get("category") or "")
        text = _case_text(case)
        expected = _expected(case)

        draft = service.review_contract(text, method)
        finding = next(
            (f for f in draft.findings if _category_value(f.category) == category),
            None,
        )
        if finding is None:
            raise RuntimeError(f"{cid}: no finding for category={category!r}")

        sources = list(getattr(finding, "sources", []) or [])[: args.top_k]
        article_codes = [str(_read(s, "article_code", "law_article_code")) for s in sources]
        chunk_ids = [str(_read(s, "chunk_id", "source_id", "id")) for s in sources]
        actual_by_case[cid] = {
            "retrieved_article_codes": article_codes,
            "retrieved_chunk_ids": chunk_ids,
        }

        detected_excerpt = str(
            getattr(finding, "contract_excerpt", None)
            or getattr(finding, "excerpt", None)
            or text
        )
        preferred = preferred_by_category.get(category, set())

        for rank, source in enumerate(sources, 1):
            article_code = str(_read(source, "article_code", "law_article_code"))
            article_title = str(_read(source, "article_title", "title"))
            source_score = _read(
                source,
                "reranker_score",
                "score",
                "raw_score",
                "ranking_score",
                default="",
            )
            source_text = audit_source_text(source)

            rows.append(
                {
                    "audit_id": f"{cid}__r{rank}",
                    "case_id": cid,
                    "category": category,
                    "case_text": text,
                    "detected_excerpt": detected_excerpt,
                    "expected_article_codes": ";".join(expected),
                    "source_rank": rank,
                    "article_code": article_code,
                    "article_title": article_title,
                    "is_in_minimum_expected_set": int(article_code in set(expected)),
                    "is_in_current_preferred_set": int(article_code in preferred),
                    "source_score": source_score,
                    "source_text": source_text,
                    "manual_label": "",
                    "manual_reason": "",
                }
            )

    # Strong consistency check: audit must be the SAME V2 result as benchmark.
    mismatches: list[str] = []
    if benchmark:
        for cid, actual in actual_by_case.items():
            if cid not in benchmark:
                mismatches.append(f"{cid}: missing from benchmark JSON")
                continue
            expected_row = benchmark[cid]
            exp_articles = [str(x) for x in expected_row.get("retrieved_article_codes", [])]
            exp_chunks = [str(x) for x in expected_row.get("retrieved_chunk_ids", [])]
            if actual["retrieved_article_codes"] != exp_articles:
                mismatches.append(
                    f"{cid}: article sequence differs\n"
                    f"  benchmark={exp_articles}\n"
                    f"  audit={actual['retrieved_article_codes']}"
                )
            if actual["retrieved_chunk_ids"] != exp_chunks:
                mismatches.append(
                    f"{cid}: chunk sequence differs\n"
                    f"  benchmark={exp_chunks}\n"
                    f"  audit={actual['retrieved_chunk_ids']}"
                )

        if mismatches:
            print("ERROR: V2 audit output does not match V2 benchmark.")
            print("The CSV was NOT written.")
            for item in mismatches[:20]:
                print("-", item)
            if len(mismatches) > 20:
                print(f"... plus {len(mismatches) - 20} more mismatch(es)")
            return 3

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "audit_id",
        "case_id",
        "category",
        "case_text",
        "detected_excerpt",
        "expected_article_codes",
        "source_rank",
        "article_code",
        "article_title",
        "is_in_minimum_expected_set",
        "is_in_current_preferred_set",
        "source_score",
        "source_text",
        "manual_label",
        "manual_reason",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    by_category: dict[str, int] = {}
    for row in rows:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1

    print(
        json.dumps(
            {
                "output": str(args.output),
                "case_count": len(cases),
                "audit_row_count": len(rows),
                "top_k": args.top_k,
                "reranker_enabled": reranker.enabled,
                "reranker_model": reranker.config.model_name,
                "candidate_k": reranker.config.candidate_k,
                "batch_size": reranker.config.batch_size,
                "device": reranker.config.device,
                "fail_open": reranker.config.fail_open,
                "benchmark_consistency": "PASS" if benchmark else "NOT_CHECKED",
                "rows_by_category": by_category,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
