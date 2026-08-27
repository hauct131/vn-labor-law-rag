#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
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


def _category_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    n = len(rows) or 1
    recalls = []
    mrrs = []
    any_hits = []
    for row in rows:
        expected = set(row["expected_article_codes"])
        got = row["retrieved_article_codes"]
        hit_set = expected.intersection(got)
        recalls.append(len(hit_set) / len(expected) if expected else 1.0)
        any_hits.append(1.0 if hit_set else 0.0)
        rr = 0.0
        for rank, code in enumerate(got, start=1):
            if code in expected:
                rr = 1.0 / rank
                break
        mrrs.append(rr)
    lat = [float(row["latency_ms"]) for row in rows]
    return {
        "case_count": len(rows),
        "any_hit_at_4": round(sum(any_hits) / n, 6),
        "macro_article_recall_at_4": round(sum(recalls) / n, 6),
        "article_mrr_at_4": round(sum(mrrs) / n, 6),
        "mean_latency_ms": round(statistics.fmean(lat), 3) if lat else 0.0,
        "mean_sources": round(statistics.fmean(len(r["retrieved_article_codes"]) for r in rows), 3) if rows else 0.0,
    }


def _run(cases: list[dict[str, Any]], *, enabled: bool) -> dict[str, Any]:
    os.environ["CONTRACT_REVIEW_RERANKER_ENABLED"] = "true" if enabled else "false"
    from app.services import contract_review_service as service
    from app.services.contract_review_reranker import reset_contract_review_reranker_for_tests

    reset_contract_review_reranker_for_tests()
    method = _resolve_method(service)
    rows: list[dict[str, Any]] = []
    for i, case in enumerate(cases, 1):
        started = time.perf_counter()
        draft = service.review_contract(_case_text(case), method)
        category = str(case.get("category") or "")
        finding = next((f for f in draft.findings if _category_value(f.category) == category), None)
        sources = list(getattr(finding, "sources", []) or []) if finding is not None else []
        rows.append({
            "id": _case_id(case, i),
            "category": category,
            "expected_article_codes": _expected(case),
            "retrieved_article_codes": [str(getattr(s, "article_code", "")) for s in sources],
            "retrieved_chunk_ids": [str(getattr(s, "chunk_id", "")) for s in sources],
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        })
    return {"summary": _metrics(rows), "cases": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B benchmark Contract Review V1 vs Cross-Encoder V2")
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError("cases JSON must be a list or {'cases': [...]} object")

    v1 = _run(cases, enabled=False)
    v2 = _run(cases, enabled=True)
    result = {
        "schema_version": "contract-review-cross-encoder-ablation-v1",
        "model": os.getenv("CONTRACT_REVIEW_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
        "candidate_k": int(os.getenv("CONTRACT_REVIEW_RERANKER_CANDIDATE_K", "20")),
        "source_k": 4,
        "v1_lexical": v1,
        "v2_cross_encoder": v2,
        "delta": {
            key: round(float(v2["summary"][key]) - float(v1["summary"][key]), 6)
            for key in ("any_hit_at_4", "macro_article_recall_at_4", "article_mrr_at_4", "mean_latency_ms")
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "contract_review_cross_encoder_ablation.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md = [
        "# Contract Review Cross-Encoder V2 Ablation",
        "",
        "| Variant | AnyHit@4 | Recall@4 | MRR@4 | Mean latency ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, obj in (("V1 lexical", v1), ("V2 + cross-encoder", v2)):
        s = obj["summary"]
        md.append(f"| {label} | {s['any_hit_at_4']:.4f} | {s['macro_article_recall_at_4']:.4f} | {s['article_mrr_at_4']:.4f} | {s['mean_latency_ms']:.1f} |")
    md += ["", "> DEV engineering ablation only; this is not independent legal validation.", ""]
    (args.output_dir / "benchmark_summary.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"v1": v1["summary"], "v2": v2["summary"], "delta": result["delta"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
