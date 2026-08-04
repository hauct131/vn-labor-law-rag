#!/usr/bin/env python3
"""Fetch one canonical source per configured labour-law document.

Policy:

1. Prefer exact Word files from ``congbao.chinhphu.vn``.
2. Fall back to the official VBPL registry only for an explicitly allowlisted
   availability reason.
3. Never fall back after an identity mismatch, corrupt attachment, metadata
   conflict, checksum failure, or transient network/tooling error.
4. Produce a single run manifest; this command does not build or promote a
   Qdrant collection.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from scripts import congbao_docx as gazette
    from scripts import vbpl_portal as vbpl
except ImportError:  # pragma: no cover - direct script execution
    import congbao_docx as gazette  # type: ignore[no-redef]
    import vbpl_portal as vbpl  # type: ignore[no-redef]


SCHEMA_VERSION = "canonical-source-policy-run-v1"
POLICY_NAME = "official-gazette-word-then-vbpl-v1"
DEFAULT_CONFIG = Path("config/vbpl_corpus.json")
DEFAULT_REPORT = Path("data/raw/canonical_sources/run_manifest.json")
DEFAULT_GAZETTE_OUTPUT = Path("data/raw/official_docx")
DEFAULT_GAZETTE_SOURCE_DIR = Path("data/sources/official_docx")
DEFAULT_VBPL_OUTPUT = Path("data/raw/vbpl")


class CanonicalPolicyError(RuntimeError):
    """Invalid configuration or a fail-closed source-policy outcome."""


@dataclass(frozen=True)
class PolicyDocument:
    item: Mapping[str, Any]
    preferred_adapter: str
    preferred_status: str | None
    fallback_adapter: str | None
    fallback_reason: str | None

    @property
    def document_number(self) -> str:
        return str(self.item["document_number"])


Fetch = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def load_policy(config_path: Path) -> tuple[dict[str, Any], list[PolicyDocument]]:
    config = vbpl.load_json(config_path)
    global_policy = config.get("canonical_source_policy") or {}
    if global_policy.get("name") != POLICY_NAME:
        raise CanonicalPolicyError(
            f"Expected canonical_source_policy.name={POLICY_NAME!r}"
        )
    if global_policy.get("priority") != [
        "official_gazette_word",
        "vbpl",
    ]:
        raise CanonicalPolicyError("Canonical source priority must be Gazette then VBPL")
    documents: list[PolicyDocument] = []
    seen: set[str] = set()
    for raw in config.get("documents") or []:
        number = str(raw.get("document_number") or "").strip()
        if not number:
            raise CanonicalPolicyError("Document without document_number")
        normalized = vbpl.normalize_document_number(number)
        if normalized in seen:
            raise CanonicalPolicyError(f"Duplicate document_number: {number}")
        seen.add(normalized)
        policy = raw.get("canonical_source_policy") or {}
        preferred = str(policy.get("preferred_adapter") or "")
        if preferred != "official_gazette_word":
            raise CanonicalPolicyError(
                f"{number}: preferred_adapter must be official_gazette_word"
            )
        preferred_status = (
            str(policy["preferred_status"])
            if policy.get("preferred_status")
            else None
        )
        fallback_adapter = (
            str(policy["fallback_adapter"])
            if policy.get("fallback_adapter")
            else None
        )
        fallback_reason = (
            str(policy["fallback_reason"])
            if policy.get("fallback_reason")
            else None
        )
        if preferred_status == "not_found":
            if raw.get("gazette_page_url"):
                raise CanonicalPolicyError(
                    f"{number}: not_found policy cannot declare gazette_page_url"
                )
            if fallback_adapter != "vbpl" or not fallback_reason:
                raise CanonicalPolicyError(
                    f"{number}: not_found Gazette record requires an explicit VBPL fallback"
                )
        elif not raw.get("gazette_page_url"):
            raise CanonicalPolicyError(
                f"{number}: Gazette-preferred source misses gazette_page_url"
            )
        if fallback_adapter == "vbpl" and raw.get("item_id") in (None, ""):
            raise CanonicalPolicyError(
                f"{number}: VBPL fallback requires exact item_id"
            )
        documents.append(
            PolicyDocument(
                item=raw,
                preferred_adapter=preferred,
                preferred_status=preferred_status,
                fallback_adapter=fallback_adapter,
                fallback_reason=fallback_reason,
            )
        )
    if not documents:
        raise CanonicalPolicyError("Config contains no documents")
    return config, documents


def gazette_spec(item: Mapping[str, Any]) -> gazette.DocumentSpec:
    number = str(item["document_number"])
    return gazette.DocumentSpec(
        document_number=number,
        canonical_document_id=str(item["canonical_document_id"]),
        title=str(item["title"]),
        source_file=str(
            item.get("source_file")
            or f"{vbpl.safe_slug(number)}.docx"
        ),
        gazette_page_url=str(item["gazette_page_url"]),
        metadata_page_url=(
            str(item["official_page_url"])
            if item.get("official_page_url")
            else None
        ),
        expected_articles=(
            int(item["expected_articles"])
            if item.get("expected_articles") is not None
            else None
        ),
    )


def _snapshot_hash(result: Mapping[str, Any], provider: str) -> str | None:
    manifest = result.get("manifest")
    if not isinstance(manifest, Mapping):
        snapshot = result.get("snapshot_dir")
        manifest_path = Path(str(snapshot)) / "manifest.json" if snapshot else None
        if manifest_path and manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hashes = manifest.get("content_hashes") if isinstance(manifest, Mapping) else {}
    if not isinstance(hashes, Mapping):
        return None
    if provider == "official_gazette_word":
        return str(hashes.get("source_fingerprint_sha256") or "") or None
    return str(
        hashes.get("full_text_text_sha256")
        or hashes.get("normalized_content_sha256")
        or ""
    ) or None


def _record(
    document: PolicyDocument,
    result: Mapping[str, Any],
    *,
    provider: str,
    fallback_reason: str | None,
) -> dict[str, Any]:
    return {
        "document_number": document.document_number,
        "canonical_document_id": str(document.item["canonical_document_id"]),
        "status": str(result.get("status") or "created"),
        "canonical_provider": provider,
        "fallback_used": provider == "vbpl",
        "fallback_reason": fallback_reason,
        "snapshot_dir": result.get("snapshot_dir"),
        "source_path": result.get("source_path"),
        "source_paths": list(result.get("source_paths") or []),
        "content_sha256": _snapshot_hash(result, provider),
    }


def run_policy(
    config_path: Path,
    *,
    fetch_gazette: Fetch,
    fetch_vbpl: Fetch,
) -> dict[str, Any]:
    config, documents = load_policy(config_path)
    global_policy = config["canonical_source_policy"]
    allowed_fallback_reasons = set(global_policy.get("fallback_on") or [])
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for document in documents:
        fallback_reason: str | None = None
        if document.preferred_status == "not_found":
            fallback_reason = document.fallback_reason or "gazette_record_not_found"
        else:
            try:
                preferred_result = fetch_gazette(document.item)
                results.append(
                    _record(
                        document,
                        preferred_result,
                        provider="official_gazette_word",
                        fallback_reason=None,
                    )
                )
                continue
            except gazette.CongbaoUnavailableError as exc:
                fallback_reason = exc.reason
            except Exception as exc:
                failures.append(
                    {
                        "document_number": document.document_number,
                        "stage": "official_gazette_word",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "fallback_attempted": False,
                        "reason": "fail_closed_non_availability_error",
                    }
                )
                continue

        if (
            fallback_reason not in allowed_fallback_reasons
            or document.fallback_adapter != "vbpl"
        ):
            failures.append(
                {
                    "document_number": document.document_number,
                    "stage": "source_policy",
                    "error_type": "FallbackNotAllowed",
                    "error": f"Fallback is not allowed for {fallback_reason!r}",
                    "fallback_attempted": False,
                }
            )
            continue
        try:
            fallback_result = fetch_vbpl(document.item)
            results.append(
                _record(
                    document,
                    fallback_result,
                    provider="vbpl",
                    fallback_reason=fallback_reason,
                )
            )
        except Exception as exc:
            failures.append(
                {
                    "document_number": document.document_number,
                    "stage": "vbpl_fallback",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "fallback_attempted": True,
                    "fallback_reason": fallback_reason,
                }
            )

    requested = len(documents)
    gazette_count = sum(
        result["canonical_provider"] == "official_gazette_word"
        for result in results
    )
    fallback_count = sum(
        result["canonical_provider"] == "vbpl" for result in results
    )
    unique_numbers = {
        vbpl.normalize_document_number(result["document_number"])
        for result in results
    }
    coverage_ok = (
        len(results) + len(failures) == requested
        and len(unique_numbers) == len(results)
        and not failures
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": vbpl.utc_now(),
        "policy_name": POLICY_NAME,
        "config": str(config_path),
        "documents_requested": requested,
        "documents_succeeded": len(results),
        "documents_failed": len(failures),
        "official_gazette_documents": gazette_count,
        "vbpl_fallback_documents": fallback_count,
        "one_canonical_snapshot_per_document": coverage_ok,
        "production_promotion_performed": False,
        "results": results,
        "failures": failures,
        "status": "PASS" if coverage_ok else "FAIL",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--gazette-output", type=Path, default=DEFAULT_GAZETTE_OUTPUT)
    parser.add_argument(
        "--gazette-source-dir", type=Path, default=DEFAULT_GAZETTE_SOURCE_DIR
    )
    parser.add_argument("--vbpl-output", type=Path, default=DEFAULT_VBPL_OUTPUT)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--backoff-seconds", type=float, default=1.0)
    parser.add_argument("--max-mib", type=int, default=100)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--replace-source", action="store_true")
    parser.add_argument("--force-snapshot", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout <= 0 or args.retries <= 0 or args.max_mib <= 0:
        print("timeout, retries and max-mib must be positive", file=sys.stderr)
        return 2
    config, _ = load_policy(args.config)
    source = config.get("source") or {}
    portal = source.get("portal") or {}
    sitemap_url = str(portal.get("sitemap_url") or vbpl.DEFAULT_SITEMAP)
    api_substring = str(
        portal.get("api_url_substring") or vbpl.DEFAULT_API_SUBSTRING
    )
    client = vbpl.RetryingHttpClient(
        timeout=args.timeout,
        retries=args.retries,
        backoff_seconds=args.backoff_seconds,
        max_bytes=args.max_mib * 1024 * 1024,
    )

    def fetch_gazette(item: Mapping[str, Any]) -> Mapping[str, Any]:
        return gazette.ingest_one(
            gazette_spec(item),
            client=client,
            output_root=args.gazette_output,
            source_dir=args.gazette_source_dir,
            replace_source=args.replace_source,
            force_snapshot=args.force_snapshot,
        )

    def fetch_vbpl(item: Mapping[str, Any]) -> Mapping[str, Any]:
        return vbpl.fetch_one(
            document_number=str(item["document_number"]),
            output_root=args.vbpl_output,
            expected_articles=(
                int(item["expected_articles"])
                if item.get("expected_articles") is not None
                else None
            ),
            sitemap_url=sitemap_url,
            portal_url=(str(item["portal_url"]) if item.get("portal_url") else None),
            item_id=str(item["item_id"]),
            api_substring=api_substring,
            headed=args.headed,
            timeout=args.timeout,
            settle_seconds=args.settle_seconds,
            attachment_policy=str(item.get("attachment_policy") or "best_effort"),
            configured_attachments=tuple(item.get("official_attachments") or ()),
            retries=args.retries,
            backoff_seconds=args.backoff_seconds,
            resume=not args.no_resume,
            force_snapshot=args.force_snapshot,
        )

    report = run_policy(
        args.config,
        fetch_gazette=fetch_gazette,
        fetch_vbpl=fetch_vbpl,
    )
    vbpl.write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Canonical source report: {args.report}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
