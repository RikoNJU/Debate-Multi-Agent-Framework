"""统一模型调用入口。

该模块只负责模型运行配置和通用调用协议，不放具体 Debate 评审业务逻辑。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


class ModelClientError(RuntimeError):
    """模型客户端调用失败。"""


logger = logging.getLogger("debate.model_client")
TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelCallOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    response_format: Mapping[str, Any] | None = None
    stream: bool = False
    thinking_budget: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    content: str
    raw: Mapping[str, Any] = field(default_factory=dict)
    usage: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRuntimeConfig:
    provider: str = "openai_compatible"
    model: str = "deepseek-ai/DeepSeek-V4-Pro"
    base_url: str = "https://api.siliconflow.cn/v1"
    api_key: str | None = None
    default_temperature: float = 0.2
    default_timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 8.0
    default_thinking_budget: int | None = None

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries 不能小于 0")
        if self.retry_base_seconds < 0:
            raise ValueError("retry_base_seconds 不能小于 0")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry_max_seconds 不能小于 retry_base_seconds")
        if (
            self.default_thinking_budget is not None
            and not 128 <= self.default_thinking_budget <= 32768
        ):
            raise ValueError("default_thinking_budget 必须位于 128 到 32768 之间")

    @classmethod
    def from_env(cls, prefix: str = "DEBATE") -> "ModelRuntimeConfig":
        """从项目专属环境变量读取配置，并回退到通用 LLM_* 配置。"""

        def read(name: str, default: str | None = None) -> str | None:
            return os.getenv(f"{prefix}_{name}") or os.getenv(f"LLM_{name}") or default

        temperature = read("TEMPERATURE")
        timeout = read("TIMEOUT_SECONDS")
        max_retries = read("MAX_RETRIES")
        retry_base = read("RETRY_BASE_SECONDS")
        retry_max = read("RETRY_MAX_SECONDS")
        thinking_budget = read("THINKING_BUDGET")
        return cls(
            provider=read("PROVIDER", cls.provider) or cls.provider,
            model=read("MODEL", cls.model) or cls.model,
            base_url=read("BASE_URL", cls.base_url) or cls.base_url,
            api_key=read("API_KEY"),
            default_temperature=(
                float(temperature) if temperature else cls.default_temperature
            ),
            default_timeout_seconds=(
                float(timeout) if timeout else cls.default_timeout_seconds
            ),
            max_retries=int(max_retries) if max_retries else cls.max_retries,
            retry_base_seconds=(
                float(retry_base) if retry_base else cls.retry_base_seconds
            ),
            retry_max_seconds=(
                float(retry_max) if retry_max else cls.retry_max_seconds
            ),
            default_thinking_budget=(
                int(thinking_budget)
                if thinking_budget
                else cls.default_thinking_budget
            ),
        )


class ModelClient(Protocol):
    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        options: ModelCallOptions | None = None,
    ) -> ModelResponse:
        ...

    async def acomplete(
        self,
        messages: Sequence[ChatMessage],
        *,
        options: ModelCallOptions | None = None,
    ) -> ModelResponse:
        ...


class OpenAICompatibleChatClient:
    """兼容 OpenAI chat completions 格式的模型客户端。"""

    def __init__(self, config: ModelRuntimeConfig) -> None:
        self.config = config

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        options: ModelCallOptions | None = None,
    ) -> ModelResponse:
        if not self.config.api_key:
            raise ModelClientError("缺少模型 API Key，请配置 DEBATE_API_KEY 或 LLM_API_KEY")

        call_options = options or ModelCallOptions()
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "temperature": (
                call_options.temperature
                if call_options.temperature is not None
                else self.config.default_temperature
            ),
        }
        if call_options.max_tokens is not None:
            payload["max_tokens"] = call_options.max_tokens
        if call_options.response_format is not None:
            payload["response_format"] = dict(call_options.response_format)
        if call_options.stream:
            payload["stream"] = True
        thinking_budget = (
            call_options.thinking_budget
            if call_options.thinking_budget is not None
            else self.config.default_thinking_budget
        )
        if thinking_budget is not None:
            payload["thinking_budget"] = thinking_budget

        endpoint = f"{self.config.base_url.rstrip('/')}/chat/completions"
        timeout = (
            call_options.timeout_seconds
            if call_options.timeout_seconds is not None
            else self.config.default_timeout_seconds
        )
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        raw: Mapping[str, Any] | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    if call_options.stream:
                        return self._read_stream(response)
                    raw = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if (
                    exc.code in TRANSIENT_HTTP_STATUS_CODES
                    and attempt < self.config.max_retries
                ):
                    self._wait_before_retry(
                        attempt,
                        reason=f"HTTP {exc.code}",
                        retry_after=(
                            exc.headers.get("Retry-After") if exc.headers else None
                        ),
                    )
                    continue
                raise ModelClientError(
                    f"模型 HTTP 调用失败: {exc.code} {detail}"
                ) from exc
            except OSError as exc:
                if attempt < self.config.max_retries:
                    self._wait_before_retry(attempt, reason=str(exc))
                    continue
                raise ModelClientError(f"模型网络调用失败: {exc}") from exc

        if raw is None:
            raise ModelClientError("模型调用未返回结果")

        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelClientError(f"模型返回格式不符合 chat completions: {raw}") from exc

        return ModelResponse(
            content=content,
            raw=raw,
            usage=raw.get("usage", {}),
        )

    @staticmethod
    def _read_stream(response: Any) -> ModelResponse:
        """Consume an OpenAI-compatible SSE response without buffering it whole."""

        content_parts: list[str] = []
        usage: Mapping[str, Any] = {}
        chunk_count = 0
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ModelClientError("模型流式响应包含非法 JSON 数据") from exc
            chunk_count += 1
            if chunk.get("usage"):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                content_parts.append(content)

        content = "".join(content_parts)
        if not content:
            raise ModelClientError("模型流式调用未返回正文内容")
        return ModelResponse(
            content=content,
            raw={"streamed": True, "chunk_count": chunk_count},
            usage=usage,
        )

    def _wait_before_retry(
        self,
        attempt: int,
        *,
        reason: str,
        retry_after: str | None = None,
    ) -> None:
        delay = min(
            self.config.retry_max_seconds,
            self.config.retry_base_seconds * (2**attempt)
            + random.uniform(0, self.config.retry_base_seconds),
        )
        if retry_after:
            try:
                delay = min(
                    self.config.retry_max_seconds,
                    max(delay, float(retry_after)),
                )
            except ValueError:
                pass
        logger.warning(
            "模型调用出现瞬时错误，第 %d/%d 次重试将在 %.2f 秒后进行：%s",
            attempt + 1,
            self.config.max_retries,
            delay,
            reason,
        )
        time.sleep(delay)

    async def acomplete(
        self,
        messages: Sequence[ChatMessage],
        *,
        options: ModelCallOptions | None = None,
    ) -> ModelResponse:
        return await asyncio.to_thread(self.complete, messages, options=options)


def build_model_client(
    config: ModelRuntimeConfig | None = None,
    *,
    prefix: str = "DEBATE",
) -> ModelClient:
    runtime_config = config or ModelRuntimeConfig.from_env(prefix)
    if runtime_config.provider != "openai_compatible":
        raise ModelClientError(f"暂不支持模型供应商: {runtime_config.provider}")
    return OpenAICompatibleChatClient(runtime_config)
