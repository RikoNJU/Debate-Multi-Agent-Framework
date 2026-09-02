"""真实 Agent 复用 OpenAI 兼容模型客户端并产出合法 JSON 的公共能力。"""

from __future__ import annotations

import json
from typing import Any

from backend.env import ChatMessage, ModelCallOptions, ModelClient

from ..schemas import ReviewContext


def review_context_payload(context: ReviewContext) -> dict[str, Any]:
    """序列化评审上下文，避免正文同时出现在 chapters 和内容载体中。

    真实 LLM 需要看到章节正文才能形成可锚定的引文，这里给每个章节附带
    前 ``CHAPTER_EXCERPT_CHARS`` 字符的正文摘录，并把内容包截断到
    ``PACKET_EXCERPT_CHARS`` 字符，控制总输入在模型上下文窗口内。
    """

    chapter_excerpt_chars = 800
    packet_excerpt_chars = 2500

    payload = context.model_dump(mode="json")
    if context.review_profile:
        profile = context.review_profile
        payload["review_profile"] = {
            **profile.audit_summary(),
            "rules": [item.model_dump(mode="json") for item in profile.rules],
        }
    payload["chapters"] = [
        {
            "chapter_id": chapter.chapter_id,
            "chapter_name": chapter.chapter_name,
            "stage": chapter.stage,
            "section_titles": chapter.section_titles,
            "reviewable": chapter.reviewable,
            "content_excerpt": chapter.content[:chapter_excerpt_chars],
            "content_chars": len(chapter.content),
            "metadata": chapter.metadata,
        }
        for chapter in context.chapters
    ]
    if context.content_packets:
        payload["content_packets"] = [
            {
                **packet.model_dump(mode="json"),
                "content": packet.content[:packet_excerpt_chars],
            }
            for packet in context.content_packets
        ]
    if context.structured_document is not None:
        document = context.structured_document
        indexed_blocks = document.blocks[:250]
        payload["structured_document"] = {
            "source": document.source,
            "page_count": document.page_count,
            "quality": document.quality.model_dump(mode="json"),
            "blocks": [
                {
                    "block_id": block.block_id,
                    "chunk_id": block.chunk_id,
                    "block_type": block.block_type,
                    "text_excerpt": block.text[:120],
                    "page_number": block.page_number,
                    "bbox": block.bbox.model_dump(mode="json") if block.bbox else None,
                    "asset_path": block.asset_path,
                    "latex": block.latex,
                    "chapter_id": block.chapter_id,
                }
                for block in indexed_blocks
            ],
            "index_truncated": len(indexed_blocks) < len(document.blocks),
            "total_blocks": len(document.blocks),
        }
    return payload


def extract_json_object(text: str) -> str:
    """从模型回复中提取 JSON 对象文本。

    即使声明了 json_object 输出，部分模型仍会用 ```json 围栏包裹，
    或在前后附带说明文字，这里做容错提取。
    """

    stripped = text.strip()
    if stripped.startswith("```"):
        # 去掉首行围栏（``` 或 ```json）与结尾围栏
        first_newline = stripped.find("\n")
        if first_newline != -1:
            candidate = stripped[first_newline + 1 :]
            if candidate.rstrip().endswith("```"):
                candidate = candidate.rstrip()[:-3]
            stripped = candidate.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def complete_json(
    model_client: ModelClient,
    *,
    system_prompt: str,
    user_prompt: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    temperature: float = 0.2,
    max_tokens: int = 4096,
    ) -> dict[str, Any]:
    """调用统一模型客户端，并把回复解析为 JSON dict。

    ``schema`` 会把目标 Pydantic 模型的 JSON Schema 写入 prompt，强制模型按
    既有字段输出，避免模型自造与协作协议不一致的字段。
    """

    response = model_client.complete(
        [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(
                role="user",
                content=(
                    f"{user_prompt}\n\n"
                    "严格按以下 JSON Schema 输出，只输出 schema 中声明过的字段，"
                    "枚举字段必须使用 schema 中给出的取值，不要新增任何字段：\n"
                    f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
                    f"输入数据：\n{json.dumps(payload, ensure_ascii=False)}"
                ),
            ),
        ],
        options=ModelCallOptions(
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            stream=True,
        ),
    )
    finish_reason = response.raw.get("finish_reason")
    if finish_reason == "length":
        raise ValueError(
            "模型输出因达到 max_tokens 上限被截断，"
            "请增大该 Agent 的 max_tokens 配置后重试"
        )
    try:
        data = json.loads(extract_json_object(response.content))
    except json.JSONDecodeError as exc:
        preview = response.content[:200]
        raise ValueError(f"模型返回内容不是合法 JSON（开头片段：{preview}）") from exc
    if not isinstance(data, dict):
        raise ValueError("模型返回 JSON 顶层必须是对象")
    return data
