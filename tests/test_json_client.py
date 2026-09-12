"""JSON 客户端在 max_tokens 截断下的容错回归测试。

背景：Step 5 本地工作量模型（Qwen3-8B + QLoRA）在某些论文上输出达到
max_tokens 上限（finish_reason=length）。修复目标：
1. JSON 已完整时不再因停留在上限而误判失败；
2. 真正截断时 Step 5 回退到确定性基线，不让整篇评审失败。
"""

from __future__ import annotations

from debate_agent_framework.agents.json_client import complete_json
from debate_agent_framework.agents.legacy_workload import (
    DeterministicLegacyWorkloadEvaluator,
    RealLegacyWorkloadEvaluator,
)
from backend.env import ModelResponse

from debate_agent_framework.schemas import (
    ChapterInput,
    CompatibleChapterEnvelope,
    CompatibleChapterData,
    CompatibleStructureEvaluation,
    CompatibleWorkloadEvaluation,
    DebateReviewInput,
    GlobalReview,
    PaperType,
    ReviewSynthesis,
    WorkloadItem,
)


class _FakeClient:
    def __init__(self, content: str, *, finish_reason: str = "stop") -> None:
        self._content = content
        self._finish_reason = finish_reason

    def complete(self, messages, *, options=None) -> ModelResponse:
        return ModelResponse(
            content=self._content,
            raw={"finish_reason": self._finish_reason},
        )


def _synthesis() -> ReviewSynthesis:
    def item(score: int) -> WorkloadItem:
        return WorkloadItem(score=score)

    return ReviewSynthesis(
        global_review=GlobalReview(overall_summary="论文整体一般。", confidence=0.8),
        chapter_evaluation={
            "chapter_1": CompatibleChapterEnvelope(
                chapter_data=CompatibleChapterData(
                    chapter_name="方法",
                    chapter_type="方法构建",
                    chapter_summary="方法概述",
                    chapter_remark="无特别说明",
                )
            )
        },
        workload_evaluation=CompatibleWorkloadEvaluation(
            structure_evaluation=CompatibleStructureEvaluation(
                completeness=item(60),
                abstract_and_keywords=item(70),
                catalog_standardization=item(50),
                chapter_standardization=item(50),
                acknowledgement_standardization=item(40),
            ),
            summary="工作量一般。",
        ),
    )


def _review_input() -> DebateReviewInput:
    return DebateReviewInput(
        paper_id="p-1",
        title="测试论文",
        full_text="正文内容",
        paper_type=PaperType.METHOD,
        chapters=[
            ChapterInput(
                chapter_id="C1",
                chapter_name="3. 方法",
                stage="方法构建",
                content="方法介绍",
                reviewable=True,
            )
        ],
    )


def test_complete_json_accepts_complete_json_at_max_tokens() -> None:
    client = _FakeClient(
        '{"summary": "完成"}',
        finish_reason="length",
    )
    data = complete_json(
        client,
        system_prompt="s",
        user_prompt="u",
        payload={"a": 1},
        schema={"type": "object"},
    )
    assert data == {"summary": "完成"}


def test_complete_json_raises_when_truncated_and_invalid() -> None:
    client = _FakeClient(
        '{"summary": "被截断',  # JSON 未闭合且达到上限
        finish_reason="length",
    )
    try:
        complete_json(
            client,
            system_prompt="s",
            user_prompt="u",
            payload={"a": 1},
            schema={"type": "object"},
        )
    except ValueError as exc:
        assert "max_tokens" in str(exc)
    else:
        raise AssertionError("截断且不完整时应抛出 ValueError")


def test_workload_evaluator_falls_back_to_baseline_on_truncation() -> None:
    class _FaultyClient:
        def complete(self, messages, *, options=None) -> ModelResponse:
            raise ValueError(
                "模型输出因达到 max_tokens 上限被截断，请增大该 Agent 的 max_tokens 配置后重试"
            )

    review_input = _review_input()
    synthesis = _synthesis()
    evaluator = RealLegacyWorkloadEvaluator(_FaultyClient())

    result = evaluator.evaluate_workload(review_input, synthesis)
    baseline = DeterministicLegacyWorkloadEvaluator().evaluate_workload(
        review_input, synthesis
    )

    assert result.structure_evaluation == baseline.structure_evaluation
    assert result.summary == baseline.summary
    assert result.workload_evaluation == baseline.workload_evaluation