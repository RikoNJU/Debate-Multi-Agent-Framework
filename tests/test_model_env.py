import io
import json
import urllib.error

import pytest

from backend.env import (
    ChatMessage,
    ModelCallOptions,
    ModelClientError,
    ModelRuntimeConfig,
    OpenAICompatibleChatClient,
)


def test_model_runtime_config_prefers_project_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "shared-model")
    monkeypatch.setenv("DEBATE_MODEL", "debate-model")
    monkeypatch.setenv("DEBATE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("DEBATE_API_KEY", "project-key")
    monkeypatch.setenv("DEBATE_MAX_RETRIES", "4")
    monkeypatch.setenv("DEBATE_RETRY_BASE_SECONDS", "0.5")
    monkeypatch.setenv("DEBATE_RETRY_MAX_SECONDS", "3")
    monkeypatch.setenv("DEBATE_THINKING_BUDGET", "1024")

    config = ModelRuntimeConfig.from_env("DEBATE")

    assert config.model == "debate-model"
    assert config.base_url == "https://example.test/v1"
    assert config.api_key == "project-key"
    assert config.max_retries == 4
    assert config.retry_base_seconds == 0.5
    assert config.retry_max_seconds == 3
    assert config.default_thinking_budget == 1024


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args):  # type: ignore[no-untyped-def]
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FakeStreamResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args):  # type: ignore[no-untyped-def]
        return None

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.lines)


def test_model_client_retries_transient_network_errors(monkeypatch) -> None:
    attempts = 0

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("remote disconnected")
        return FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        ModelRuntimeConfig(
            api_key="test-key",
            base_url="https://example.test/v1",
            max_retries=2,
            retry_base_seconds=0,
            retry_max_seconds=0,
        )
    )

    response = client.complete([ChatMessage(role="user", content="test")])

    assert response.content == "ok"
    assert attempts == 3


def test_model_client_does_not_retry_non_transient_http_error(monkeypatch) -> None:
    attempts = 0

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "bad request",
            {},
            io.BytesIO(b'{"error":"invalid"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        ModelRuntimeConfig(api_key="test-key", max_retries=3)
    )

    with pytest.raises(ModelClientError, match="400"):
        client.complete([ChatMessage(role="user", content="test")])

    assert attempts == 1


def test_model_client_reassembles_streamed_content(monkeypatch) -> None:
    captured_payload: dict = {}

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        captured_payload.update(json.loads(request.data.decode("utf-8")))
        return FakeStreamResponse(
            [
                b'data: {"choices":[{"delta":{"reasoning_content":"checking"}}]}\n',
                b'data: {"choices":[{"delta":{"content":"{\\"ok\\":"}}]}\n',
                b'data: {"choices":[{"delta":{"content":"true}"}}]}\n',
                b'data: {"choices":[],"usage":{"total_tokens":12}}\n',
                b'data: [DONE]\n',
            ]
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        ModelRuntimeConfig(
            api_key="test-key",
            max_retries=0,
            default_thinking_budget=2048,
        )
    )

    response = client.complete(
        [ChatMessage(role="user", content="test")],
        options=ModelCallOptions(stream=True, max_tokens=4096),
    )

    assert captured_payload["stream"] is True
    assert captured_payload["max_tokens"] == 4096
    assert captured_payload["thinking_budget"] == 2048
    assert response.content == '{"ok":true}'
    assert response.raw == {"streamed": True, "chunk_count": 4, "finish_reason": None}
    assert response.usage == {"total_tokens": 12}
