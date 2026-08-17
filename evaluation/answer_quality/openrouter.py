"""Minimal OpenRouter adapter used only by the offline annotation panel."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from evaluation.answer_quality.annotations import (
    AnnotationPayload,
    ProviderUsage,
    annotation_response_schema,
)


class OpenRouterAnnotationError(RuntimeError):
    """Safe provider or response validation failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        raw_content: str = "",
        provider_response: dict[str, Any] | None = None,
        request_sha256: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.raw_content = raw_content
        self.provider_response = provider_response
        self.request_sha256 = request_sha256


class OpenRouterAnnotationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_model: str
    resolved_model: str = Field(min_length=1)
    response_id: str = Field(min_length=1)
    raw_content: str
    parsed_annotation: AnnotationPayload
    usage: ProviderUsage | None = None
    provider_response: dict[str, Any]
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_error(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:500]
    return "OpenRouter annotation request failed"


def _redact_secret(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]") if secret else value
    if isinstance(value, list):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _redact_secret(item, secret)
            for key, item in value.items()
        }
    return value


class OpenRouterAnnotationClient:
    """Call one exact model; no model routing or hidden fallback is allowed."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 120.0,
        max_tokens: int = 2400,
        app_url: str = "",
        app_title: str = "Vietnam Labor Law Answer Evaluation",
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENROUTER_API_KEY is required")
        if not base_url.strip():
            raise ValueError("OpenRouter base URL is required")
        if timeout_seconds <= 0 or max_tokens <= 0:
            raise ValueError("timeout and max_tokens must be positive")
        self.api_key = api_key.strip()
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self.timeout_seconds = float(timeout_seconds)
        self.max_tokens = int(max_tokens)
        self.app_url = app_url.strip()
        self.app_title = app_title.strip()
        self.http_client = http_client

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.app_url:
            headers["HTTP-Referer"] = self.app_url
        if self.app_title:
            headers["X-OpenRouter-Title"] = self.app_title
        return headers

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        if self.http_client is not None:
            return self.http_client.post(
                self.endpoint,
                headers=self._headers(),
                json=body,
                timeout=self.timeout_seconds,
            )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return client.post(
                self.endpoint,
                headers=self._headers(),
                json=body,
            )

    def annotate(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
    ) -> OpenRouterAnnotationResult:
        requested_model = model.strip()
        if not requested_model:
            raise ValueError("model ID must not be blank")
        body: dict[str, Any] = {
            "model": requested_model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "answer_golden_annotation",
                    "strict": True,
                    "schema": annotation_response_schema(),
                },
            },
            "provider": {"require_parameters": True},
        }
        request_sha256 = _canonical_hash(body)
        try:
            response = self._post(body)
        except httpx.TimeoutException as exc:
            raise OpenRouterAnnotationError(
                "OpenRouter annotation request timed out",
                status_code=504,
                request_sha256=request_sha256,
            ) from exc
        except httpx.HTTPError as exc:
            raise OpenRouterAnnotationError(
                "Could not connect to OpenRouter",
                request_sha256=request_sha256,
            ) from exc

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OpenRouterAnnotationError(
                "OpenRouter returned non-JSON content",
                status_code=response.status_code,
                request_sha256=request_sha256,
            ) from exc
        if not isinstance(payload, dict):
            raise OpenRouterAnnotationError(
                "OpenRouter response must be a JSON object",
                status_code=response.status_code,
                request_sha256=request_sha256,
            )
        payload = _redact_secret(payload, self.api_key)
        if response.status_code >= 400:
            raise OpenRouterAnnotationError(
                _safe_error(payload),
                status_code=response.status_code,
                provider_response=payload,
                request_sha256=request_sha256,
            )

        try:
            choice = payload["choices"][0]
            if choice.get("error"):
                raise TypeError("choice contains an error")
            if choice.get("finish_reason") != "stop":
                raise TypeError("completion did not finish with stop")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise TypeError("empty content")
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterAnnotationError(
                "OpenRouter response is incomplete or has no assistant content",
                status_code=response.status_code,
                provider_response=payload,
                request_sha256=request_sha256,
            ) from exc

        try:
            parsed_json = json.loads(content)
            annotation = AnnotationPayload.model_validate(parsed_json)
        except (json.JSONDecodeError, ValueError) as exc:
            raise OpenRouterAnnotationError(
                "OpenRouter content violates annotation schema",
                status_code=response.status_code,
                raw_content=content,
                provider_response=payload,
                request_sha256=request_sha256,
            ) from exc

        usage_payload = payload.get("usage")
        usage = (
            ProviderUsage.model_validate(usage_payload)
            if isinstance(usage_payload, dict)
            else None
        )
        resolved_model = str(payload.get("model", "")).strip()
        response_id = str(payload.get("id", "")).strip()
        if not resolved_model or not response_id:
            raise OpenRouterAnnotationError(
                "OpenRouter response lacks model or response ID",
                status_code=response.status_code,
                raw_content=content,
                provider_response=payload,
                request_sha256=request_sha256,
            )
        return OpenRouterAnnotationResult(
            requested_model=requested_model,
            resolved_model=resolved_model,
            response_id=response_id,
            raw_content=content,
            parsed_annotation=annotation,
            usage=usage,
            provider_response=payload,
            request_sha256=request_sha256,
        )
