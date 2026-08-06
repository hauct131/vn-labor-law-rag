"""Safely activate a verified Qdrant collection through an alias.

This module deliberately uses Qdrant's REST API instead of SDK alias models so
the activation path is stable across qdrant-client minor releases. It never
creates, deletes, or mutates a physical collection.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


class AliasActivationError(ValueError):
    """Raised when an alias cannot be activated safely."""


JsonRequest = Callable[
    [str, str, dict[str, Any] | None],
    dict[str, Any],
]


def _json_request_factory(
    base_url: str,
    api_key: str | None,
    timeout_seconds: float,
) -> JsonRequest:
    root = base_url.rstrip("/")

    def request(
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        encoded = (
            json.dumps(body).encode("utf-8")
            if body is not None
            else None
        )
        headers = {"Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        if api_key:
            headers["api-key"] = api_key
        req = urllib.request.Request(
            root + path,
            data=encoded,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                req,
                timeout=timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AliasActivationError(
                f"Qdrant HTTP {exc.code} for {method} {path}: {detail}"
            ) from exc
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise AliasActivationError(
                f"Qdrant request failed for {method} {path}: {exc}"
            ) from exc
        if payload.get("status") not in ("ok", None):
            raise AliasActivationError(
                f"Qdrant returned non-ok status for {method} {path}: "
                f"{payload.get('status')}"
            )
        return payload

    return request


def _physical_collection_names(request: JsonRequest) -> set[str]:
    payload = request("GET", "/collections", None)
    collections = payload.get("result", {}).get("collections", [])
    return {
        item["name"]
        for item in collections
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def _alias_map(request: JsonRequest) -> dict[str, str]:
    payload = request("GET", "/aliases", None)
    aliases = payload.get("result", {}).get("aliases", [])
    return {
        item["alias_name"]: item["collection_name"]
        for item in aliases
        if (
            isinstance(item, dict)
            and isinstance(item.get("alias_name"), str)
            and isinstance(item.get("collection_name"), str)
        )
    }


def _exact_count(
    request: JsonRequest,
    collection: str,
    *,
    corpus_sha256: str | None = None,
) -> int:
    quoted = urllib.parse.quote(collection, safe="")
    body: dict[str, Any] = {"exact": True}
    if corpus_sha256:
        body["filter"] = {
            "must": [
                {
                    "key": "_index_corpus_sha256",
                    "match": {"value": corpus_sha256},
                }
            ]
        }
    payload = request(
        "POST",
        f"/collections/{quoted}/points/count",
        body,
    )
    count = payload.get("result", {}).get("count")
    if not isinstance(count, int):
        raise AliasActivationError(
            f"Qdrant count response for '{collection}' has no integer count"
        )
    return count


def activate_alias(
    *,
    request: JsonRequest,
    collection: str,
    alias: str,
    expected_chunks: int,
    expected_sha256: str,
    verify_only: bool = False,
) -> dict[str, Any]:
    """Verify a physical collection and atomically point an alias at it."""
    if not collection.strip() or not alias.strip():
        raise AliasActivationError("collection and alias must be non-empty")
    if collection == alias:
        raise AliasActivationError(
            "physical collection and active alias must be different"
        )
    if expected_chunks <= 0:
        raise AliasActivationError("expected_chunks must be positive")
    if len(expected_sha256) != 64:
        raise AliasActivationError(
            "expected_sha256 must be a 64-character SHA-256"
        )

    physical_names = _physical_collection_names(request)
    if collection not in physical_names:
        raise AliasActivationError(
            f"Target physical collection '{collection}' does not exist"
        )
    if alias in physical_names:
        raise AliasActivationError(
            f"Alias name '{alias}' collides with an existing physical collection"
        )

    total_count = _exact_count(request, collection)
    fingerprint_count = _exact_count(
        request,
        collection,
        corpus_sha256=expected_sha256,
    )
    if total_count != expected_chunks:
        raise AliasActivationError(
            f"Collection '{collection}' has {total_count} points; "
            f"expected {expected_chunks}"
        )
    if fingerprint_count != expected_chunks:
        raise AliasActivationError(
            f"Collection '{collection}' has only {fingerprint_count}/"
            f"{expected_chunks} points with the expected corpus fingerprint"
        )

    aliases_before = _alias_map(request)
    previous_collection = aliases_before.get(alias)
    if verify_only:
        if previous_collection != collection:
            raise AliasActivationError(
                f"Alias '{alias}' points to {previous_collection!r}; "
                f"expected '{collection}'"
            )
        action = "verified"
    elif previous_collection == collection:
        action = "already_active"
    else:
        actions: list[dict[str, Any]] = []
        if previous_collection is not None:
            actions.append(
                {"delete_alias": {"alias_name": alias}}
            )
        actions.append(
            {
                "create_alias": {
                    "collection_name": collection,
                    "alias_name": alias,
                }
            }
        )
        request(
            "POST",
            "/collections/aliases?timeout=60",
            {"actions": actions},
        )
        action = (
            "switched"
            if previous_collection is not None
            else "created"
        )

    aliases_after = _alias_map(request)
    if aliases_after.get(alias) != collection:
        raise AliasActivationError(
            f"Alias verification failed: '{alias}' does not point to "
            f"'{collection}'"
        )

    return {
        "status": "verified" if verify_only else "active",
        "action": action,
        "timestamp_utc": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "alias": alias,
        "collection": collection,
        "previous_collection": previous_collection,
        "exact_point_count": total_count,
        "fingerprint_point_count": fingerprint_count,
        "corpus_sha256": expected_sha256,
    }


def write_summary(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a versioned Qdrant collection and atomically activate "
            "it through an alias."
        )
    )
    parser.add_argument(
        "--qdrant-url",
        default="http://localhost:6333",
    )
    parser.add_argument("--collection", required=True)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--expected-chunks", type=int, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
    )
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--summary-output", type=Path)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")

    request = _json_request_factory(
        args.qdrant_url,
        os.environ.get("QDRANT_API_KEY") or None,
        args.timeout_seconds,
    )
    try:
        report = activate_alias(
            request=request,
            collection=args.collection,
            alias=args.alias,
            expected_chunks=args.expected_chunks,
            expected_sha256=args.expected_sha256,
            verify_only=args.verify_only,
        )
        if args.summary_output:
            write_summary(args.summary_output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except AliasActivationError as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
