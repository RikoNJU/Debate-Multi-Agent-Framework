"""真实 Agent 复用 OpenAI 兼容模型客户端并产出合法 JSON 的公共能力。"""

from __future__ import annotations

import json
from typing import Any

from backend.env import ChatMessage, ModelCallOptions, ModelClient

from ..schemas import ReviewContext


def _segment_excerpt(text: str, budget: int) -> str:
    """对单段正文做首尾均衡摘录，保留下结论密集的开头与结尾。

    长章节的核心结论、公式与实验结果通常落在正文中部偏后，仅保留开头
    会让模型误判“文本缺失”。当文本超过预算时，取前 60%、后 40% 并在
    中间留出省略标记，使首尾内容始终可追溯。
    """

    if not text or len(text) <= budget:
        return text
    if budget <= 0:
        return ""
    head_cut = int(budget * 0.6)
    tail_cut = budget - head_cut
    return text[:head_cut] + "\n……(中段内容省略，详见对应章节)……\n" + text[-tail_cut:]


def _coverage_excerpt(chunks: list[str], budget: int) -> str:
    """把总预算按各章正文长度比例分配到相邻章节，再逐章首尾摘录。

    避免大段落（如方法+实验）合包后只截取开头，导致后置章节在
    评审上下文中完全不可见。
    """

    total = sum(len(chunk) for chunk in chunks)
    if total <= budget:
        return "\n\n".join(chunks)
    parts: list[str] = []
    for chunk in chunks:
        share = max(int(budget * len(chunk) / total), 1)
        parts.append(_segment_excerpt(chunk, share))
    return "\n\n".join(parts)


def review_context_payload(context: ReviewContext) -> dict[str, Any]:
    """序列化评审上下文，避免正文同时出现在 chapters 和内容载体中。

    真实 LLM 需要看到章节正文才能形成可锚定的引文，这里给每个章节附带
    ``CHAPTER_EXCERPT_CHARS`` 字符的首尾均衡摘录，并把内容包按章节权重
    分配 ``PACKET_EXCERPT_CHARS`` 预算，控制总输入在模型上下文窗口内，
    同时保证方法与实验章节的中后段内容可见。
    """

    chapter_excerpt_chars = 2400
    packet_excerpt_chars = 8000

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
            "content_excerpt": _segment_excerpt(
                chapter.content, chapter_excerpt_chars
            ),
            "content_chars": len(chapter.content),
            "metadata": chapter.metadata,
        }
        for chapter in context.chapters
    ]
    if context.content_packets:
        chapters_by_id = {chapter.chapter_id: chapter for chapter in context.chapters}
        payload["content_packets"] = [
            {
                **packet.model_dump(mode="json"),
                "content": _coverage_excerpt(
                    [
                        chapters_by_id[chapter_id].content
                        for chapter_id in packet.chapter_ids
                        if chapter_id in chapters_by_id
                    ],
                    packet_excerpt_chars,
                ),
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
    try:
        data = json.loads(extract_json_object(response.content))
    except json.JSONDecodeError as exc:
        if finish_reason == "length":
            raise ValueError(
                "模型输出因达到 max_tokens 上限被截断，"
                "请增大该 Agent 的 max_tokens 配置后重试"
            ) from exc
        preview = response.content[:200]
        raise ValueError(f"模型返回内容不是合法 JSON（开头片段：{preview}）") from exc
    if not isinstance(data, dict):
        raise ValueError("模型返回 JSON 顶层必须是对象")
    return data
