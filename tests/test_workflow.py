"""Evidence-Grounded Debate 工作流的行为测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from debate_agent_framework.core.errors import WorkflowExecutionError
from debate_agent_framework.agents import (
    DemoContextPlanner,
    DemoEvidenceRetriever,
    DemoHistoricalScoreRetriever,
    DemoOriginalPipelineAdapter,
    DemoReviewChair,
    DemoSpecialist,
    LegacyStep12ClassificationAdapter,
)
from debate_agent_framework.schemas import (
    ChapterInput,
    DebatePlan,
    DebateReviewInput,
    EvidenceKind,
    FindingSeverity,
    PaperType,
    ReviewEvidence,
    ReviewFinding,
    ReviewSynthesis,
    RetrievedAdvice,
    SpecialistRole,
)
from debate_agent_framework.workflows import (
    DebateWorkflow,
    DebateWorkflowConfig,
    DebateWorkflowServices,
)


def make_input() -> DebateReviewInput:
    chapters = [
        ChapterInput(
            chapter_id="C1",
            chapter_name="第一章 绪论",
            stage="引言/绪论",
            content="本章说明研究背景、问题和研究目标。",
            section_titles=["研究背景"],
        ),
        ChapterInput(
            chapter_id="C2",
            chapter_name="第二章 方法设计",
            stage="方法构建",
            content="本章提出多智能体评审方法及其协作机制。",
            section_titles=["总体架构"],
        ),
        ChapterInput(
            chapter_id="C3",
            chapter_name="第三章 实验验证",
            stage="实验验证",
            content="本章报告基础对比，但缺少强 Baseline 和消融实验。",
            section_titles=["实验设置"],
        ),
    ]
    return DebateReviewInput(
        paper_id="paper-debate-test",
        title="证据驱动多智能体评审",
        abstract="测试 Debate 工作流。",
        full_text="\n".join(chapter.content for chapter in chapters),
        paper_type=PaperType.METHOD,
        chapters=chapters,
    )


def make_services(
    *,
    specialists: dict[SpecialistRole, DemoSpecialist] | None = None,
    chair: DemoReviewChair | None = None,
    evidence_retriever: object | None = None,
    historical_advice_retriever: object | None = None,
    original_pipeline: DemoOriginalPipelineAdapter | None = None,
    paper_classifier: object | None = None,
    chapter_classifier: object | None = None,
) -> DebateWorkflowServices:
    return DebateWorkflowServices(
        context_planner=DemoContextPlanner(),
        specialists=specialists
        or {role: DemoSpecialist(role) for role in SpecialistRole},
        review_chair=chair or DemoReviewChair(),
        evidence_retriever=(
            evidence_retriever
            if evidence_retriever is not None
            else DemoEvidenceRetriever()
        ),  # type: ignore[arg-type]
        historical_advice_retriever=historical_advice_retriever,  # type: ignore[arg-type]
        historical_score_retriever=DemoHistoricalScoreRetriever(),
        original_pipeline=original_pipeline or DemoOriginalPipelineAdapter(),
        paper_classifier=paper_classifier,  # type: ignore[arg-type]
        chapter_classifier=chapter_classifier,  # type: ignore[arg-type]
    )


class RecordingHistoricalAdviceRetriever:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.limit: int | None = None
        self.paper_type: PaperType | None = None
        self.chapter_stages: list[str] = []

    def retrieve(self, review_input, *, limit_per_chapter):  # type: ignore[no-untyped-def]
        self.limit = limit_per_chapter
        self.paper_type = review_input.paper_type
        self.chapter_stages = [chapter.stage for chapter in review_input.chapters]
        if self.fail:
            raise RuntimeError("vector store unavailable")
        return [
            {
                "chapter_id": "C2",
                "stage": "方法构建",
                "suggestions": ["补充方法适用边界", "补充复杂度分析"],
            }
        ]


def test_historical_advice_is_retrieved_before_context_planning() -> None:
    retriever = RecordingHistoricalAdviceRetriever()
    review_input = make_input().model_copy(
        update={
            "step3_advice": [
                RetrievedAdvice(
                    chapter_id="C2",
                    stage="方法构建",
                    suggestions=["补充方法适用边界"],
                )
            ]
        }
    )

    result = DebateWorkflow(
        make_services(historical_advice_retriever=retriever)
    ).run(review_input)

    assert retriever.limit == 5
    assert result.context.step3_advice[0].suggestions == [
        "补充方法适用边界",
        "补充复杂度分析",
    ]


def test_step1_and_step2_run_before_historical_advice() -> None:
    retriever = RecordingHistoricalAdviceRetriever()
    classifier = LegacyStep12ClassificationAdapter()
    review_input = make_input().model_copy(
        update={
            "paper_type": None,
            "chapters": [
                chapter.model_copy(update={"stage": "正文"})
                for chapter in make_input().chapters
            ],
            "metadata": {
                "paper_type_source": "auto_pending",
                "chapter_stage_source": "markdown_heuristic",
            },
        }
    )

    DebateWorkflow(
        make_services(
            historical_advice_retriever=retriever,
            paper_classifier=classifier,
            chapter_classifier=classifier,
        )
    ).run(review_input)

    assert retriever.paper_type is PaperType.METHOD
    assert retriever.chapter_stages == ["引言/绪论", "方法构建", "实验验证"]


def test_historical_advice_failure_degrades_to_existing_input() -> None:
    retriever = RecordingHistoricalAdviceRetriever(fail=True)

    result = DebateWorkflow(
        make_services(historical_advice_retriever=retriever)
    ).run(make_input())

    assert result.context.step3_advice == []
    assert any(
        issue.code == "historical_advice_retrieval_failed" for issue in result.issues
    )


def test_demo_runs_full_original_pipeline_compatible_flow() -> None:
    result = DebateWorkflow(make_services()).run(make_input())

    assert len(result.independent_reviews) == 3
    assert len(result.debate_plan.issues) == 1
    assert len(result.debate_responses) == 2
    assert len(result.external_evidence) == 1
    assert list(result.synthesis.chapter_evaluation) == [
        "chapter_1",
        "chapter_2",
        "chapter_3",
    ]
    assert set(result.synthesis.workload_evaluation.model_dump()) == {
        "structure_evaluation",
        "summary",
        "workload_evaluation",
    }
    assert result.summary_advice is not None
    assert result.final_score is not None
    assert set(result.final_score.scores) == {str(index) for index in range(1, 13)}
    assert result.issues == []


class CallTracker:
    def __init__(self) -> None:
        self.active_reviews = 0
        self.max_active_reviews = 0
        self.responses: dict[SpecialistRole, int] = {
            role: 0 for role in SpecialistRole
        }


class RecordingSpecialist(DemoSpecialist):
    def __init__(self, role: SpecialistRole, tracker: CallTracker) -> None:
        super().__init__(role)
        self.tracker = tracker

    async def review(self, context):  # type: ignore[no-untyped-def]
        self.tracker.active_reviews += 1
        self.tracker.max_active_reviews = max(
            self.tracker.max_active_reviews, self.tracker.active_reviews
        )
        await asyncio.sleep(0.03)
        result = await super().review(context)
        self.tracker.active_reviews -= 1
        return result

    async def respond(self, context, **kwargs):  # type: ignore[no-untyped-def]
        self.tracker.responses[self.role] += 1
        return await super().respond(context, **kwargs)


def test_reviews_are_parallel_and_debate_is_targeted() -> None:
    tracker = CallTracker()
    specialists = {
        role: RecordingSpecialist(role, tracker) for role in SpecialistRole
    }
    workflow = DebateWorkflow(
        make_services(specialists=specialists),
        DebateWorkflowConfig(max_concurrency=3),
    )
    workflow.run(make_input())

    assert tracker.max_active_reviews == 3
    assert tracker.responses[SpecialistRole.SCIENTIFIC_SOUNDNESS] == 1
    assert tracker.responses[SpecialistRole.EMPIRICAL_EVIDENCE] == 1
    assert tracker.responses[SpecialistRole.GLOBAL_QUALITY] == 0


class CountingEvidenceRetriever(DemoEvidenceRetriever):
    def __init__(self) -> None:
        self.calls = 0

    def retrieve(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return super().retrieve(*args, **kwargs)


class NoDebateChair(DemoReviewChair):
    def plan_debate(self, context, reviews):  # type: ignore[no-untyped-def]
        return DebatePlan()


def test_evidence_rag_is_not_called_without_external_question() -> None:
    retriever = CountingEvidenceRetriever()
    result = DebateWorkflow(
        make_services(chair=NoDebateChair(), evidence_retriever=retriever)
    ).run(make_input())

    assert retriever.calls == 0
    assert result.external_evidence == []
    assert result.debate_responses == []


def test_evidence_rag_is_called_once_for_deduplicated_queries() -> None:
    retriever = CountingEvidenceRetriever()
    result = DebateWorkflow(
        make_services(evidence_retriever=retriever)
    ).run(make_input())

    assert retriever.calls == 1
    assert len(result.external_evidence) == 1


class FailingSpecialist(DemoSpecialist):
    async def review(self, context):  # type: ignore[no-untyped-def]
        raise RuntimeError("模拟 Specialist 模型不可用")


def test_one_specialist_failure_preserves_two_other_reviews() -> None:
    specialists = {role: DemoSpecialist(role) for role in SpecialistRole}
    specialists[SpecialistRole.GLOBAL_QUALITY] = FailingSpecialist(
        SpecialistRole.GLOBAL_QUALITY
    )
    result = DebateWorkflow(make_services(specialists=specialists)).run(make_input())

    assert len(result.independent_reviews) == 2
    assert any(issue.code == "specialist_review_failed" for issue in result.issues)
    assert result.final_score is not None


class BrokenCompatibilityChair(DemoReviewChair):
    def synthesize(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = super().synthesize(*args, **kwargs)
        payload = result.model_dump()
        payload["chapter_evaluation"].pop("chapter_3")
        return ReviewSynthesis.model_construct(**payload)


def test_compatibility_gate_rejects_missing_step4_chapter() -> None:
    workflow = DebateWorkflow(make_services(chair=BrokenCompatibilityChair()))

    with pytest.raises(WorkflowExecutionError, match="chapter_evaluation 键"):
        workflow.run(make_input())


def test_high_severity_finding_without_evidence_requires_low_confidence_and_human_review() -> None:
    with pytest.raises(ValidationError, match="无证据的高严重度问题"):
        ReviewFinding(
            finding_id="F-BROKEN",
            dimension="实验",
            claim="实验完全无效",
            rationale="没有给出可追溯依据",
            severity=FindingSeverity.MAJOR,
            evidence=[],
            confidence=0.9,
            requires_human_review=False,
        )


class RecordingPipelineAdapter(DemoOriginalPipelineAdapter):
    def __init__(self) -> None:
        self.chapter_shape: list[str] = []
        self.workload_shape: set[str] = set()

    def summarize_advice(self, review_input, synthesis):  # type: ignore[no-untyped-def]
        self.chapter_shape = list(synthesis.chapter_evaluation)
        self.workload_shape = set(synthesis.workload_evaluation.model_dump())
        return super().summarize_advice(review_input, synthesis)


def test_original_pipeline_adapter_receives_exact_step4_step5_shapes() -> None:
    adapter = RecordingPipelineAdapter()
    DebateWorkflow(make_services(original_pipeline=adapter)).run(make_input())

    assert adapter.chapter_shape == ["chapter_1", "chapter_2", "chapter_3"]
    assert adapter.workload_shape == {
        "structure_evaluation",
        "summary",
        "workload_evaluation",
    }
