"""旧 Step 1/2 分类标准的适配器测试。"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from backend.env import ChatMessage, ModelCallOptions, ModelResponse
from debate_agent_framework.agents import LegacyStep12ClassificationAdapter
from debate_agent_framework.schemas import ChapterInput, DebateReviewInput, PaperType


class FakeModelClient:
    def __init__(self, responses: Sequence[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        options: ModelCallOptions | None = None,
    ) -> ModelResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return ModelResponse(content=json.dumps(response, ensure_ascii=False))


def make_unclassified_input() -> DebateReviewInput:
    chapters = [
        ChapterInput(
            chapter_id="C1",
            chapter_name="第一章 绪论",
            stage="正文",
            content="本章介绍研究背景与相关工作。",
            section_titles=["研究背景", "相关工作"],
        ),
        ChapterInput(
            chapter_id="C2",
            chapter_name="第二章 方法与实验",
            stage="正文",
            content="本章提出新算法，并完成实验验证和结果分析。",
            section_titles=["算法设计", "实验结果"],
        ),
    ]
    return DebateReviewInput(
        paper_id="paper-classification",
        title="一种新的优化算法",
        abstract="提出新算法并通过实验验证性能。",
        keywords=["优化算法", "实验验证"],
        full_text="\n".join(chapter.content for chapter in chapters),
        paper_type=None,
        chapters=chapters,
        metadata={"chapter_stage_source": "markdown_heuristic"},
    )


def test_model_adapter_reuses_step1_and_method_step2_labels() -> None:
    client = FakeModelClient(
        [
            {
                "paper_type": "方法创新",
                "rationale": "核心贡献是提出并验证新算法",
                "confidence": 0.92,
            },
            {
                "chapters": [
                    {
                        "chapter_id": "C1",
                        "chapter_name": "第一章 绪论",
                        "stage": "引言/绪论（包含相关工作）",
                    },
                    {
                        "chapter_id": "C2",
                        "chapter_name": "第二章 方法与实验",
                        "stage": "实验验证与结果分析",
                    },
                ]
            },
        ]
    )
    adapter = LegacyStep12ClassificationAdapter(model_client=client)
    review_input = make_unclassified_input()

    paper = adapter.classify_paper(review_input)
    classified_input = review_input.model_copy(update={"paper_type": paper.paper_type})
    chapters = adapter.classify_chapters(classified_input)

    assert paper.paper_type is PaperType.METHOD
    assert [item.stage for item in chapters.chapters] == [
        "引言/绪论（包含相关工作）",
        "实验验证与结果分析",
    ]
    assert client.calls == 2


def test_step2_rejects_label_from_another_paper_type() -> None:
    client = FakeModelClient(
        [
            {
                "chapters": [
                    {
                        "chapter_id": "C1",
                        "chapter_name": "第一章 绪论",
                        "stage": "引言/绪论",
                    },
                    {
                        "chapter_id": "C2",
                        "chapter_name": "第二章 方法与实验",
                        "stage": "系统实现",
                    },
                ]
            }
        ]
    )
    adapter = LegacyStep12ClassificationAdapter(model_client=client)
    review_input = make_unclassified_input().model_copy(
        update={"paper_type": PaperType.METHOD}
    )

    with pytest.raises(ValueError, match=r"不属于\s*方法创新"):
        adapter.classify_chapters(review_input)


def test_demo_adapter_classifies_from_full_paper_context() -> None:
    adapter = LegacyStep12ClassificationAdapter()
    review_input = make_unclassified_input()

    paper = adapter.classify_paper(review_input)
    chapters = adapter.classify_chapters(
        review_input.model_copy(update={"paper_type": paper.paper_type})
    )

    assert paper.paper_type is PaperType.METHOD
    assert chapters.chapters[0].stage == "引言/绪论（包含相关工作）"
