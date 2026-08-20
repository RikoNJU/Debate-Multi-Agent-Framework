import io
import json
import urllib.error

import pytest

from backend.env import (
    ChatMessage,
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

    config = ModelRuntimeConfig.from_env("DEBATE")

    assert config.model == "debate-model"
    assert config.base_url == "https://example.test/v1"
    assert config.api_key == "project-key"
    assert config.max_retries == 4
    assert config.retry_base_seconds == 0.5
    assert config.retry_max_seconds == 3


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args):  # type: ignore[no-untyped-def]
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


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
