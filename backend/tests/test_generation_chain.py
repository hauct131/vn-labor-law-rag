"""OpenRouter generation tests that never call the real provider."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.app.chains.generation_chain import (
    GenerationConfigurationError,
    GenerationProviderError,
    create_generation_chain,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeHttpClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


def make_settings(**overrides):
    defaults = {
        "llm_provider": "openrouter",
        "openrouter_api_key": "test-key",
        "llm_api_key": "",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_model": "openrouter/free",
        "openrouter_require_free_model": True,
        "openrouter_timeout_seconds": 30.0,
        "openrouter_max_tokens": 400,
        "openrouter_reasoning_max_tokens": 80,
        "openrouter_exclude_reasoning": True,
        "openrouter_temperature": 0.0,
        "openrouter_app_url": "",
        "openrouter_app_title": "Test RAG",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_openrouter_chain_builds_grounded_non_streaming_request() -> None:
    client = FakeHttpClient(FakeResponse(200, {
        "model": "free/test-model",
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": "Được nghỉ [S1]."},
        }],
        "usage": {
            "completion_tokens": 42,
            "completion_tokens_details": {"reasoning_tokens": 12},
        },
    }))
    chain = create_generation_chain(make_settings(), http_client=client)

    result = asyncio.run(chain.ainvoke({
        "question": "Tôi được nghỉ bao nhiêu ngày?",
        "context": (
            "[S1]\nDẫn chứng: Điều 113 Bộ luật Lao động số "
            "45/2019/QH14\nMã pháp điển: 20.2.LQ.113\nNội dung: ..."
        ),
    }))

    assert result.answer == "Được nghỉ [S1]."
    assert result.model == "free/test-model"
    assert result.finish_reason == "stop"
    assert result.completion_tokens == 42
    assert result.reasoning_tokens == 12
    call = client.calls[0]
    assert call["url"].endswith("/chat/completions")
    assert call["json"]["model"] == "openrouter/free"
    assert call["json"]["stream"] is False
    assert call["json"]["max_tokens"] == 400
    assert call["json"]["reasoning"] == {
        "max_tokens": 80,
        "exclude": True,
    }
    assert "plugins" not in call["json"]
    assert "[S1]" in call["json"]["messages"][1]["content"]
    assert call["headers"]["Authorization"] == "Bearer test-key"


def test_openrouter_chain_requires_key_before_any_request() -> None:
    with pytest.raises(GenerationConfigurationError, match="API_KEY"):
        create_generation_chain(make_settings(openrouter_api_key=""))


def test_paid_model_is_blocked_by_default() -> None:
    with pytest.raises(GenerationConfigurationError, match="hậu tố"):
        create_generation_chain(make_settings(openrouter_model="paid/model"))


def test_openrouter_rate_limit_is_exposed_as_429() -> None:
    client = FakeHttpClient(FakeResponse(429, {
        "error": {"message": "Rate limit exceeded"},
    }))
    chain = create_generation_chain(make_settings(), http_client=client)

    with pytest.raises(GenerationProviderError) as error:
        asyncio.run(chain.ainvoke({
            "question": "Câu hỏi",
            "context": "[S1] Nội dung",
        }))

    assert error.value.status_code == 429
    assert "Rate limit" in str(error.value)


def test_length_limited_answer_is_rejected_instead_of_shown_partially() -> None:
    client = FakeHttpClient(FakeResponse(200, {
        "model": "nvidia/test:free",
        "choices": [{
            "finish_reason": "length",
            "message": {"content": "Câu trả lời đang bị cắt giữa"},
        }],
    }))
    chain = create_generation_chain(make_settings(), http_client=client)

    with pytest.raises(GenerationProviderError, match="hết giới hạn token"):
        asyncio.run(chain.ainvoke({
            "question": "Tuổi nghỉ hưu?",
            "context": "[S1] Nội dung",
        }))


def test_reasoning_override_can_be_disabled_for_other_models() -> None:
    client = FakeHttpClient(FakeResponse(200, {
        "model": "google/test:free",
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": "Đủ căn cứ [S1]."},
        }],
    }))
    chain = create_generation_chain(
        make_settings(openrouter_reasoning_max_tokens=0),
        http_client=client,
    )

    asyncio.run(chain.ainvoke({
        "question": "Câu hỏi",
        "context": "[S1] Nội dung",
    }))

    assert "reasoning" not in client.calls[0]["json"]
