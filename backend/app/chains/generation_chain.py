"""Small async generation chain backed by OpenRouter Chat Completions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import httpx

from .prompts import LEGAL_QA_PROMPT


class GenerationError(RuntimeError):
    """Base error for answer generation."""


class GenerationConfigurationError(GenerationError):
    """Raised when the selected LLM provider is not configured."""


class GenerationProviderError(GenerationError):
    """Raised when OpenRouter or an upstream free provider fails."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class AsyncAnswerGenerator(Protocol):
    async def ainvoke(self, values: Mapping[str, str]) -> "GenerationResult": ...


@dataclass(frozen=True, slots=True)
class GenerationResult:
    answer: str
    model: str
    finish_reason: str | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None


def _message_role(message: Any) -> str:
    role = getattr(message, "type", "")
    return "system" if role == "system" else "user"


def _message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def _extract_answer(payload: Any) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GenerationProviderError(
            "OpenRouter trả về dữ liệu không có nội dung câu trả lời."
        ) from exc

    if isinstance(content, str):
        answer = content.strip()
    elif isinstance(content, list):
        answer = "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict)
        ).strip()
    else:
        answer = ""
    if not answer:
        raise GenerationProviderError("OpenRouter trả về câu trả lời rỗng.")
    return answer


def _optional_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _extract_generation_result(payload: Any, *, fallback_model: str) -> GenerationResult:
    """Validate a non-streaming completion before exposing it to the UI."""
    try:
        choice = payload["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise GenerationProviderError(
            "OpenRouter trả về dữ liệu không có lựa chọn câu trả lời."
        ) from exc

    finish_reason_value = choice.get("finish_reason")
    finish_reason = (
        str(finish_reason_value).strip().casefold()
        if finish_reason_value is not None
        else None
    )
    if finish_reason == "length":
        raise GenerationProviderError(
            "Câu trả lời bị dừng do hết giới hạn token. Hãy tăng "
            "OPENROUTER_MAX_TOKENS hoặc giảm ngân sách reasoning."
        )
    if finish_reason == "content_filter":
        raise GenerationProviderError(
            "Câu trả lời bị bộ lọc nội dung của nhà cung cấp dừng lại."
        )
    if finish_reason == "error" or choice.get("error"):
        error = choice.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        raise GenerationProviderError(
            str(message).strip()[:500]
            if isinstance(message, str) and message.strip()
            else "Nhà cung cấp dừng khi đang sinh câu trả lời."
        )

    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    completion_details = usage.get("completion_tokens_details")
    completion_details = (
        completion_details if isinstance(completion_details, dict) else {}
    )
    used_model = payload.get("model") if isinstance(payload, dict) else None
    return GenerationResult(
        answer=_extract_answer(payload),
        model=str(used_model or fallback_model),
        finish_reason=finish_reason,
        completion_tokens=_optional_non_negative_int(
            usage.get("completion_tokens")
        ),
        reasoning_tokens=_optional_non_negative_int(
            completion_details.get("reasoning_tokens")
        ),
    )


def _safe_provider_message(response: Any) -> str:
    """Extract a useful provider error without exposing request secrets."""
    try:
        payload = response.json()
        message = payload.get("error", {}).get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()[:500]
    except (TypeError, ValueError, AttributeError):
        pass
    return "OpenRouter tạm thời không thể sinh câu trả lời."


class OpenRouterGenerationChain:
    """Render the legal prompt and call the non-streaming OpenRouter API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        require_free_model: bool = True,
        timeout_seconds: float = 90.0,
        max_tokens: int = 1200,
        reasoning_max_tokens: int = 128,
        exclude_reasoning: bool = True,
        temperature: float = 0.0,
        app_url: str = "",
        app_title: str = "Vietnamese Labor Law RAG",
        http_client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise GenerationConfigurationError(
                "Thiếu OPENROUTER_API_KEY trong file .env."
            )
        if not base_url.strip() or not model.strip():
            raise GenerationConfigurationError(
                "OPENROUTER_BASE_URL và OPENROUTER_MODEL không được để trống."
            )
        normalized_model = model.strip().casefold()
        if require_free_model and not (
            normalized_model == "openrouter/free"
            or normalized_model.endswith(":free")
        ):
            raise GenerationConfigurationError(
                "OPENROUTER_MODEL phải là openrouter/free hoặc có hậu tố "
                ":free khi OPENROUTER_REQUIRE_FREE_MODEL=true."
            )
        if (
            timeout_seconds <= 0
            or max_tokens <= 0
            or reasoning_max_tokens < 0
            or reasoning_max_tokens >= max_tokens
        ):
            raise GenerationConfigurationError(
                "Timeout và số token sinh phải lớn hơn 0; ngân sách reasoning "
                "phải từ 0 đến nhỏ hơn tổng số token sinh."
            )
        self.api_key = api_key.strip()
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model.strip()
        self.timeout_seconds = float(timeout_seconds)
        self.max_tokens = int(max_tokens)
        self.reasoning_max_tokens = int(reasoning_max_tokens)
        self.exclude_reasoning = bool(exclude_reasoning)
        self.temperature = float(temperature)
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

    async def _post(self, *, json: dict[str, Any]) -> Any:
        if self.http_client is not None:
            return await self.http_client.post(
                self.endpoint,
                headers=self._headers(),
                json=json,
                timeout=self.timeout_seconds,
            )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await client.post(
                self.endpoint,
                headers=self._headers(),
                json=json,
            )

    async def ainvoke(self, values: Mapping[str, str]) -> GenerationResult:
        question = str(values.get("question", "")).strip()
        context = str(values.get("context", "")).strip()
        if not question or not context:
            raise ValueError("question và context không được để trống")

        prompt_value = LEGAL_QA_PROMPT.invoke({
            "question": question,
            "context": context,
        })
        messages = [
            {
                "role": _message_role(message),
                "content": _message_content(message),
            }
            for message in prompt_value.to_messages()
        ]
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if self.reasoning_max_tokens > 0:
            body["reasoning"] = {
                "max_tokens": self.reasoning_max_tokens,
                "exclude": self.exclude_reasoning,
            }

        try:
            response = await self._post(json=body)
        except httpx.TimeoutException as exc:
            raise GenerationProviderError(
                "OpenRouter phản hồi quá thời gian cho phép.",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise GenerationProviderError(
                "Không thể kết nối đến OpenRouter."
            ) from exc

        if response.status_code >= 400:
            status = int(response.status_code)
            public_status = status if status in {401, 402, 403, 429} else 502
            raise GenerationProviderError(
                _safe_provider_message(response),
                status_code=public_status,
            )

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise GenerationProviderError(
                "OpenRouter trả về dữ liệu không hợp lệ."
            ) from exc
        return _extract_generation_result(payload, fallback_model=self.model)


def create_generation_chain(
    settings_obj: Any | None = None,
    *,
    http_client: Any | None = None,
) -> OpenRouterGenerationChain:
    """Create the configured answer generator without spending any quota."""
    if settings_obj is None:
        from ..core.config import settings as settings_obj

    provider = str(getattr(settings_obj, "llm_provider", "")).casefold()
    if provider != "openrouter":
        raise GenerationConfigurationError(
            "MVP hiện hỗ trợ LLM_PROVIDER=openrouter."
        )
    api_key = getattr(settings_obj, "openrouter_api_key", "") or getattr(
        settings_obj,
        "llm_api_key",
        "",
    )
    return OpenRouterGenerationChain(
        api_key=api_key,
        base_url=getattr(
            settings_obj,
            "openrouter_base_url",
            "https://openrouter.ai/api/v1",
        ),
        model=getattr(settings_obj, "openrouter_model", "openrouter/free"),
        require_free_model=getattr(
            settings_obj,
            "openrouter_require_free_model",
            True,
        ),
        timeout_seconds=getattr(
            settings_obj,
            "openrouter_timeout_seconds",
            90.0,
        ),
        max_tokens=getattr(settings_obj, "openrouter_max_tokens", 1200),
        reasoning_max_tokens=getattr(
            settings_obj,
            "openrouter_reasoning_max_tokens",
            128,
        ),
        exclude_reasoning=getattr(
            settings_obj,
            "openrouter_exclude_reasoning",
            True,
        ),
        temperature=getattr(settings_obj, "openrouter_temperature", 0.0),
        app_url=getattr(settings_obj, "openrouter_app_url", ""),
        app_title=getattr(
            settings_obj,
            "openrouter_app_title",
            "Vietnamese Labor Law RAG",
        ),
        http_client=http_client,
    )
