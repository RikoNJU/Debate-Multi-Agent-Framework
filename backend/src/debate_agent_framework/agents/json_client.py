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


def complete_json(
    model_client: ModelClient,
    *,
    system_prompt: str,
    user_prompt: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    temperature: float = 0.2,
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
            response_format={"type": "json_object"},
        ),
    )
    try:
        data = json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise ValueError("模型返回内容不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("模型返回 JSON 顶层必须是对象")
    return data
