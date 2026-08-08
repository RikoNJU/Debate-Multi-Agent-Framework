"""Evidence-Grounded Debate 的 LangGraph 编排。"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable
from typing import Any, TypeVar, cast

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from debate_agent_framework.core.errors import WorkflowExecutionError

from backend.env import ModelClient, ModelClientError, build_model_client

from ..agents import (
    DebateContextPlannerAgent,
    DebateReviewChairAgent,
    DebateSpecialistAgent,
    DemoContextPlanner,
    DemoEvidenceRetriever,
    DemoHistoricalScoreRetriever,
    DemoOriginalPipelineAdapter,
    DemoReviewChair,
    DemoSpecialist,
    LegacyStep12ClassificationAdapter,
    RealOriginalPipelineAdapter,
    STEP1_RULE_VERSION,
    STEP2_RULE_VERSION,
)
from ..schemas import (
    ComprehensiveScoreResult,
    DebatePlan,
    DebateResponse,
    DebateReviewInput,
    DebateRunResult,
    DebateWorkflowIssue,
    HistoricalScoreCase,
    IndependentReview,
    IssueSeverity,
    ChapterClassificationResult,
    PaperClassificationResult,
    ReviewContext,
    ReviewEvidence,
    ReviewSynthesis,
    RetrievedAdvice,
    ScoreCalibrationQuery,
    SpecialistRole,
    SummaryAdviceResult,
)
from .state import DebateState, DebateWorkflowConfig, DebateWorkflowServices

T = TypeVar("T")
REQUIRED_ROLES = frozenset(SpecialistRole)
logger = logging.getLogger("debate.workflow")


async def _resolve(value: T | Awaitable[T]) -> T:
    """兼容同步 Agent 和异步模型 SDK。"""

    if inspect.isawaitable(value):
        return await cast(Awaitable[T], value)
    return value


async def _invoke(call: Any) -> Any:
    """在线程池中调用同步实现，并兼容返回 awaitable 的异步实现。"""

    value = await asyncio.to_thread(call)
    return await _resolve(value)


class DebateWorkflow:
    """执行独立初审、一轮定向 Debate 和原流程兼容输出。"""

    @classmethod
    def default(cls) -> "DebateWorkflow":
        """构造默认 Debate 工作流。

        当前项目采用固定 Agent 组合，因此默认装配逻辑直接放在工作流类中。
        """

        return cls(
            DebateWorkflowServices(
                paper_classifier=LegacyStep12ClassificationAdapter(),
                chapter_classifier=LegacyStep12ClassificationAdapter(),
                context_planner=DemoContextPlanner(),
                specialists={role: DemoSpecialist(role) for role in SpecialistRole},
                review_chair=DemoReviewChair(),
                evidence_retriever=DemoEvidenceRetriever(),
                historical_score_retriever=DemoHistoricalScoreRetriever(),
                original_pipeline=DemoOriginalPipelineAdapter(),
            )
        )

    @classmethod
    def real(cls, model_client: ModelClient | None = None) -> "DebateWorkflow":
        """构造真实模型驱动的 Debate 工作流。

        Specialist、Review Chair 与 Step 6/7 使用真实 LLM。未配置的外部证据
        与历史评分服务保持为空，禁止 Demo 数据污染真实评审。
        """

        from ..services.historical_advice import (
            build_historical_advice_retriever_from_env,
        )

        client = model_client or build_model_client()
        classification = LegacyStep12ClassificationAdapter(model_client=client)
        return cls(
            DebateWorkflowServices(
                paper_classifier=classification,
                chapter_classifier=classification,
                context_planner=DebateContextPlannerAgent(model_client=client),
                specialists={
                    role: DebateSpecialistAgent(role, model_client=client)
                    for role in SpecialistRole
                },
                review_chair=DebateReviewChairAgent(model_client=client),
                evidence_retriever=None,
                historical_advice_retriever=(
                    build_historical_advice_retriever_from_env()
                ),
                historical_score_retriever=None,
                original_pipeline=RealOriginalPipelineAdapter(model_client=client),
            )
        )

    def __init__(
        self,
        services: DebateWorkflowServices,
        config: DebateWorkflowConfig | None = None,
    ) -> None:
        self.services = services
        self.config = config or DebateWorkflowConfig()
        registered_roles = set(services.specialists)
        if registered_roles != REQUIRED_ROLES:
            missing = sorted(role.value for role in REQUIRED_ROLES - registered_roles)
            extra = sorted(str(role) for role in registered_roles - REQUIRED_ROLES)
            raise ValueError(f"Specialist 注册表不完整，缺少={missing}，多余={extra}")
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        builder = StateGraph(DebateState)
        builder.add_node("step1_classify_paper", self._step1_classify_paper)
        builder.add_node("step2_classify_chapters", self._step2_classify_chapters)
        builder.add_node("retrieve_historical_advice", self._retrieve_historical_advice)
        builder.add_node("build_context", self._build_context)
        builder.add_node("independent_review", self._independent_review)
        builder.add_node("plan_debate", self._plan_debate)
        builder.add_node("retrieve_debate_evidence", self._retrieve_debate_evidence)
        builder.add_node("targeted_debate", self._targeted_debate)
        builder.add_node("synthesize_review", self._synthesize_review)
        builder.add_node("compatibility_gate", self._compatibility_gate)
        builder.add_node("step6_summary_advice", self._step6_summary_advice)
        builder.add_node("retrieve_score_cases", self._retrieve_score_cases)
        builder.add_node("step7_scoring", self._step7_scoring)

        builder.add_edge(START, "step1_classify_paper")
        builder.add_edge("step1_classify_paper", "step2_classify_chapters")
        builder.add_edge("step2_classify_chapters", "retrieve_historical_advice")
        builder.add_edge("retrieve_historical_advice", "build_context")
        builder.add_edge("build_context", "independent_review")
        builder.add_edge("independent_review", "plan_debate")
        builder.add_edge("plan_debate", "retrieve_debate_evidence")
        builder.add_edge("retrieve_debate_evidence", "targeted_debate")
        builder.add_edge("targeted_debate", "synthesize_review")
        builder.add_edge("synthesize_review", "compatibility_gate")
        builder.add_edge("compatibility_gate", "step6_summary_advice")
        builder.add_edge("step6_summary_advice", "retrieve_score_cases")
        builder.add_edge("retrieve_score_cases", "step7_scoring")
        builder.add_edge("step7_scoring", END)
        return builder.compile()

    async def _step1_classify_paper(self, state: DebateState) -> dict[str, Any]:
        """自动补齐旧 Step 1；显式类型保持不变并记录来源。"""

        review_input = state["review_input"]
        metadata = dict(review_input.metadata)
        if review_input.paper_type is not None:
            metadata.setdefault("paper_type_source", "provided")
            return {"review_input": review_input.model_copy(update={"metadata": metadata})}

        classifier = self.services.paper_classifier
        if classifier is None:
            raise WorkflowExecutionError("论文未提供 paper_type，且未配置 Step 1 分类器")
        try:
            result = PaperClassificationResult.model_validate(
                await _invoke(lambda: classifier.classify_paper(review_input))
            )
        except Exception as exc:
            raise WorkflowExecutionError(f"Step 1 论文类型分类失败：{exc}") from exc
        metadata.update(
            {
                "paper_type_source": "legacy_step1",
                "paper_type_rule_version": STEP1_RULE_VERSION,
                "paper_type_confidence": f"{result.confidence:.4f}",
                "paper_type_rationale": result.rationale,
            }
        )
        return {
            "review_input": review_input.model_copy(
                update={"paper_type": result.paper_type, "metadata": metadata}
            )
        }

    async def _step2_classify_chapters(self, state: DebateState) -> dict[str, Any]:
        """对 MinerU 的初步章节切分执行旧 Step 2 语义阶段分类。"""

        review_input = state["review_input"]
        if review_input.paper_type is None:
            raise WorkflowExecutionError("Step 2 开始时 paper_type 仍为空")
        source = review_input.metadata.get("chapter_stage_source", "provided")
        requires_classification = (
            source in {"markdown_heuristic", "auto_pending"}
            or review_input.metadata.get("paper_type_source") == "legacy_step1"
            or any(
                chapter.reviewable and chapter.stage in {"正文", "general"}
                for chapter in review_input.chapters
            )
        )
        if not requires_classification:
            return {}

        classifier = self.services.chapter_classifier
        if classifier is None:
            raise WorkflowExecutionError("MinerU 章节需要自动分类，但未配置 Step 2 分类器")
        try:
            result = ChapterClassificationResult.model_validate(
                await _invoke(lambda: classifier.classify_chapters(review_input))
            )
        except Exception as exc:
            raise WorkflowExecutionError(f"Step 2 章节阶段分类失败：{exc}") from exc

        stage_by_id = {item.chapter_id: item.stage for item in result.chapters}
        chapters = [
            chapter.model_copy(update={"stage": stage_by_id[chapter.chapter_id]})
            if chapter.reviewable
            else chapter
            for chapter in review_input.chapters
        ]
        metadata = dict(review_input.metadata)
        metadata["chapter_stage_source"] = "legacy_step2"
        metadata["chapter_stage_rule_version"] = STEP2_RULE_VERSION
        return {
            "review_input": review_input.model_copy(
                update={"chapters": chapters, "metadata": metadata}
            )
        }

    async def _retrieve_historical_advice(self, state: DebateState) -> dict[str, Any]:
        """补齐原 Step 3 建议；检索失败时保留调用方输入并继续评审。"""

        retriever = self.services.historical_advice_retriever
        if retriever is None:
            return {}

        review_input = state["review_input"]
        try:
            retrieved = [
                RetrievedAdvice.model_validate(item)
                for item in await _invoke(
                    lambda: retriever.retrieve(
                        review_input,
                        limit_per_chapter=self.config.historical_advice_limit_per_chapter,
                    )
                )
            ]
            chapter_ids = {chapter.chapter_id for chapter in review_input.chapters}
            unknown_chapter_ids = sorted(
                {item.chapter_id for item in retrieved} - chapter_ids
            )
            if unknown_chapter_ids:
                raise ValueError(f"检索结果包含未知章节：{unknown_chapter_ids}")
            merged = self._merge_historical_advice(
                review_input.step3_advice,
                retrieved,
                limit_per_chapter=self.config.historical_advice_limit_per_chapter,
            )
            return {"review_input": review_input.model_copy(update={"step3_advice": merged})}
        except Exception as exc:
            return {
                "issues": [
                    DebateWorkflowIssue(
                        node="retrieve_historical_advice",
                        code="historical_advice_retrieval_failed",
                        message=f"历史评审建议检索失败，已使用现有输入继续评审：{exc}",
                        severity=IssueSeverity.WARNING,
                    )
                ]
            }

    @staticmethod
    def _merge_historical_advice(
        existing: list[RetrievedAdvice],
        retrieved: list[RetrievedAdvice],
        *,
        limit_per_chapter: int,
    ) -> list[RetrievedAdvice]:
        chapter_order: list[str] = []
        stages: dict[str, str] = {}
        suggestions: dict[str, list[str]] = {}

        for advice in existing:
            if advice.chapter_id not in suggestions:
                chapter_order.append(advice.chapter_id)
                suggestions[advice.chapter_id] = []
                stages[advice.chapter_id] = advice.stage
            for suggestion in advice.suggestions:
                if suggestion not in suggestions[advice.chapter_id]:
                    suggestions[advice.chapter_id].append(suggestion)

        for advice in retrieved:
            if advice.chapter_id not in suggestions:
                chapter_order.append(advice.chapter_id)
                suggestions[advice.chapter_id] = []
                stages[advice.chapter_id] = advice.stage
            for suggestion in advice.suggestions:
                if len(suggestions[advice.chapter_id]) >= limit_per_chapter:
                    break
                if suggestion not in suggestions[advice.chapter_id]:
                    suggestions[advice.chapter_id].append(suggestion)

        return [
            RetrievedAdvice(
                chapter_id=chapter_id,
                stage=stages[chapter_id],
                suggestions=suggestions[chapter_id],
            )
            for chapter_id in chapter_order
            if suggestions[chapter_id]
        ]

    async def _call_validated(
        self,
        call: Any,
        model: Any,
        *,
        attempts: int = 2,
    ) -> Any:
        """调用 Agent 并校验输出，失败时重试。

        真实模型的输出存在偶发不合规，重试能显著降低这类失败率。
        """

        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                value = await _invoke(call)
                return model.model_validate(value)
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    logger.warning(
                        "Agent 输出校验失败，重试 %d/%d：%s",
                        attempt + 1,
                        attempts,
                        exc,
                    )
        assert last_error is not None
        raise last_error

    async def _build_context(self, state: DebateState) -> dict[str, Any]:
        logger.info("开始构造评审上下文 build_context")
        try:
            context = ReviewContext.model_validate(
                await _invoke(
                    lambda: self.services.context_planner.build(state["review_input"])
                )
            )
        except (ValidationError, TypeEr