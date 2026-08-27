#!/usr/bin/env python3
"""Query current OpenRouter metadata; never guess or hard-code panel models."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


class ModelDiscoveryError(RuntimeError):
    """Raised when current OpenRouter model metadata cannot be verified."""


def _is_zero_price(pricing: Any) -> bool:
    if not isinstance(pricing, dict):
        return False
    for field in ("prompt", "completion", "request"):
        raw = pricing.get(field, "0")
        try:
            if Decimal(str(raw)) != 0:
                return False
        except InvalidOperation:
            return False
    return True


def fetch_compatible_models(
    *,
    base_url: str = "https://openrouter.ai/api/v1",
    minimum_context: int = 20000,
    free_only: bool = False,
    http_client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    if minimum_context < 1:
        raise ValueError("minimum_context must be positive")
    endpoint = f"{base_url.rstrip('/')}/models"
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=30.0)
    try:
        response = client.get(
            endpoint,
            params={"supported_parameters": "structured_outputs"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        raise ModelDiscoveryError(
            "could not query current OpenRouter model metadata"
        ) from exc
    finally:
        if owns_client:
            client.close()

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ModelDiscoveryError("OpenRouter models response has invalid shape")
    compatible: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", "")).strip()
        parameters = item.get("supported_parameters", [])
        architecture = item.get("architecture", {})
        context_length = item.get("context_length", 0)
        if (
            not model_id
            or "structured_outputs" not in parameters
            or "text" not in architecture.get("output_modalities", [])
            or not isinstance(context_length, int)
            or context_length < minimum_context
        ):
            continue
        if free_only and not _is_zero_price(item.get("pricing")):
            continue
        compatible.append({
            "id": model_id,
            "canonical_slug": str(item.get("canonical_slug", "")),
            "name": str(item.get("name", "")),
            "organization": model_id.split("/", 1)[0],
            "context_length": context_length,
            "pricing": item.get("pricing", {}),
            "supported_parameters": parameters,
        })
    return sorted(
        compatible,
        key=lambda item: (item["organization"], item["id"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimum-context", type=int, default=20000)
    parser.add_argument("--free-only", action="store_true")
    parser.add_argument(
        "--base-url",
        default="https://openrouter.ai/api/v1",
    )
    args = parser.parse_args()
    try:
        models = fetch_compatible_models(
            base_url=args.base_url,
            minimum_context=args.minimum_context,
            free_only=args.free_only,
        )
    except (ModelDiscoveryError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps({
        "status": "PASS",
        "queried_live": True,
        "free_only": args.free_only,
        "minimum_context": args.minimum_context,
        "model_count": len(models),
        "models": models,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
