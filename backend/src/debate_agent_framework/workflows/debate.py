"""Evidence-Grounded Debate 的 LangGraph 编排。"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Literal, TypeVar, cast
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
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
    DeterministicLegacyWorkloadEvaluator,
    LegacyStep12ClassificationAdapter,
    RealOriginalPipelineAdapter,
    RealLegacyWorkloadEvaluator,
    STEP1_RULE_VERSION,
    STEP2_RULE_VERSION,
)
from ..schemas import (
    CompatibleWorkloadEvaluation,
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

ProgressStatus = Literal["running", "succeeded", "failed"]
ProgressCallback = Callable[
    [str, str, ProgressStatus, int, str | None],
    None | Awaitable[None],
]
_progress_callback: ContextVar[ProgressCallback | None] = ContextVar(
    "debate_progress_callback", default=None
)

WORKFLOW_STAGES: dict[str, tuple[str, int, int]] = {
    "step1_classify_paper": ("识别论文类型", 2, 8),
    "step2_classify_chapters": ("识别章节阶段", 8, 14),
    "retrieve_historical_advice": ("检索历史评审建议", 14, 18),
    "build_context": ("构造评审上下文", 18, 25),
    "independent_review": ("三位专家独立初审", 25, 55),
    "plan_debate": ("Chair 识别争议", 55, 63),
    "retrieve_debate_evidence": ("检索外部证据", 63, 68),
    "targeted_debate": ("专家定向讨论", 68, 75),
    "synthesize_review": ("Chair 综合裁决", 75, 83),
    "step5_workload_evaluation": ("评价论文结构与工作量", 83, 88),
    "compatibility_gate": ("校验评审结果完整性", 88, 90),
    "step6_summary_advice": ("汇总关键修改建议", 90, 94),
    "retrieve_score_cases": ("检索历史评分案例", 94, 96),
    "step7_scoring": ("生成最终评分", 96, 99),
}

SPECIALIST_LABELS = {
    SpecialistRole.SCIENTIFIC_SOUNDNESS: "科学严谨性专家初审",
    SpecialistRole.EMPIRICAL_EVIDENCE: "实证证据专家初审",
    SpecialistRole.GLOBAL_QUALITY: "全局质量专家初审",
}


async def _resolve(value: T | Awaitable[T]) -> T:
    """兼容同步 Agent 和异步模型 SDK。"""

    if inspect.isawaitable(value):
        return await cast(Awaitable[T], value)
    return value


async def _invoke(call: Any) -> Any:
    """在线程池中调用同步实现，并兼容返回 awaitable 的异步实现。"""

    value = await asyncio.to_thread(call)
    return await _resolve(value)


async def _emit_progress(
    stage: str,
    label: str,
    status: ProgressStatus,
    progress_percent: int,
    detail: str | None = None,
) -> None:
    callback = _progress_callback.get()
    if callback is None:
        return
    result = callback(stage, label, status, progress_percent, detail)
    if inspect.isawaitable(result):
        await result


class DebateWorkflow:
    """执行独立初审、一轮定向 Debate 和原流程兼容输出。"""

    @classmethod
    def default(
        cls, *, checkpointer: BaseCheckpointSaver | None = None
    ) -> "DebateWorkflow":
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
                workload_evaluator=DeterministicLegacyWorkloadEvaluator(),
            ),
            checkpointer=checkpointer,
        )

    @classmethod
    def real(
        cls,
        model_client: ModelClient | None = None,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> "DebateWorkflow":
        """构造真实模型驱动的 Debate 工作流。

        Specialist、Review Chair 与 Step 6/7 使用真实 LLM。未配置的外部证据
        与历史评分服务保持为空，禁止 Demo 数据污染真实评审。
        """

        from ..services.external_evidence import build_evidence_retriever_from_env
        from ..services.historical_advice import (
            build_historical_advice_retriever_from_env,
        )
        from ..services.historical_score import (
            build_historical_score_retriever_from_env,
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
                evidence_retriever=build_evidence_retriever_from_env(),
                historical_advice_retriever=(
                    build_historical_advice_retriever_from_env()
                ),
                historical_score_retriever=build_historical_score_retriever_from_env(),
                original_pipeline=RealOriginalPipelineAdapter(model_client=client),
                workload_evaluator=RealLegacyWorkloadEvaluator(client),
            ),
            config=DebateWorkflowConfig.from_env(),
            checkpointer=checkpointer,
        )

    def __init__(
        self,
        services: DebateWorkflowServices,
        config: DebateWorkflowConfig | None = None,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.services = services
        self.config = config or DebateWorkflowConfig()
        self.checkpointer = checkpointer or MemorySaver()
        registered_roles = set(services.specialists)
        if registered_roles != REQUIRED_ROLES:
            missing = sorted(role.value for role in REQUIRED_ROLES - registered_roles)
            extra = sorted(str(role) for role in registered_roles - REQUIRED_ROLES)
            raise ValueError(f"Specialist 注册表不完整，缺少={missing}，多余={extra}")
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        builder = StateGraph(DebateState)
        nodes = {
            "step1_classify_paper": self._step1_classify_paper,
            "step2_classify_chapters": self._step2_classify_chapters,
            "retrieve_historical_advice": self._retrieve_historical_advice,
            "build_context": self._build_context,
            "independent_review": self._independent_review,
            "plan_debate": self._plan_debate,
            "retrieve_debate_evidence": self._retrieve_debate_evidence,
            "targeted_debate": self._targeted_debate,
            "synthesize_review": self._synthesize_review,
            "step5_workload_evaluation": self._step5_workload_evaluation,
            "compatibility_gate": self._compatibility_gate,
            "step6_summary_advice": self._step6_summary_advice,
            "retrieve_score_cases": self._retrieve_score_cases,
            "step7_scoring": self._step7_scoring,
        }
        for name, handler in nodes.items():
            builder.add_node(name, self._with_progress(name, handler))

        builder.add_edge(START, "step1_classify_paper")
        builder.add_edge("step1_classify_paper", "step2_classify_chapters")
        builder.add_edge("step2_classify_chapters", "retrieve_historical_advice")
        builder.add_edge("retrieve_historical_advice", "build_context")
        builder.add_edge("build_context", "independent_review")
        builder.add_edge("independent_review", "plan_debate")
        builder.add_edge("plan_debate", "retrieve_debate_evidence")
        builder.add_edge("retrieve_debate_evidence", "targeted_debate")
        builder.add_edge("targeted_debate", "synthesize_review")
        builder.add_edge("synthesize_review", "step5_workload_evaluation")
        builder.add_edge("step5_workload_evaluation", "compatibility_gate")
        builder.add_edge("compatibility_gate", "step6_summary_advice")
        builder.add_edge("step6_summary_advice", "retrieve_score_cases")
        builder.add_edge("retrieve_score_cases", "step7_scoring")
        builder.add_edge("step7_scoring", END)
        return builder.compile(checkpointer=self.checkpointer)

    @staticmethod
    def _with_progress(name: str, handler: Any) -> Any:
        label, started_progress, completed_progress = WORKFLOW_STAGES[name]

        async def tracked(state: DebateState) -> dict[str, Any]:
            await _emit_progress(name, label, "running", started_progress)
            try:
                result = await _resolve(handler(state))
            except Exception as exc:
                await _emit_progress(
                    name,
                    label,
                    "failed",
                    started_progress,
                    str(exc)[:1000],
                )
                raise
            await _emit_progress(name, label, "succeeded", completed_progress)
            return result

        return tracked

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
            except ModelClientError:
                raise
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
        except (ValidationError, TypeError, ValueError) as exc:
            raise WorkflowExecutionError(f"Context Planner 输出不合法：{exc}") from exc
        if context.paper_id != state["review_input"].paper_id:
            raise WorkflowExecutionError("ReviewContext.paper_id 与输入论文不一致")
        logger.info("上下文构造完成，章节数=%d", len(context.chapters))
        return {"context": context}

    async def _independent_review(self, state: DebateState) -> dict[str, Any]:
        semaphore = asyncio.Semaphore(self.config.max_concurrency)

        async def run_one(
            role: SpecialistRole,
        ) -> tuple[IndependentReview | None, DebateWorkflowIssue | None]:
            stage = f"specialist_{role.value}"
            label = SPECIALIST_LABELS[role]
            async with semaphore:
                await _emit_progress(stage, label, "running", 30)
                last_error: Exception | None = None
                for attempt in range(
                    1, self.config.review_attempts + 1
                ):
                    try:
                        review = IndependentReview.model_validate(
                            await _invoke(
                                lambda: self.services.specialists[role].review(state["context"])
                            )
                        )
                        if review.role is not role:
                            raise ValueError(
                                f"注册为 {role.value} 的 Agent 返回了 {review.role.value}"
                            )
                        degraded = self._validate_review_grounding(
                            review, state["context"]
                        )
                        await _emit_progress(stage, label, "succeeded", 50)
                        if degraded:
                            return review, DebateWorkflowIssue(
                                node="independent_review",
                                code="specialist_evidence_needs_review",
                                message=(
                                    f"{role.value} 部分论文证据未能完全锚定到原文，"
                                    f"已标记 {len(degraded)} 条证据需人工复核："
                                    + "；".join(
                                        f"{item.evidence_id}"
                                        for item in degraded
                                    )
                                ),
                                severity=IssueSeverity.WARNING,
                                role=role,
                            )
                        return review, None
                    except ModelClientError as exc:
                        last_error = exc
                        break
                    except Exception as exc:  # 一个视角失败时保留其他独立意见
                        last_error = exc
                        if attempt < self.config.review_attempts:
                            logger.warning(
                                "%s 初审校验失败，重试 %d/%d：%s",
                                role.value, attempt + 1,
                                self.config.review_attempts, exc,
                            )
                assert last_error is not None
                await _emit_progress(
                    stage, label, "failed", 50, str(last_error)[:1000]
                )
                return None, DebateWorkflowIssue(
                    node="independent_review",
                    code="specialist_review_failed",
                    message=f"{role.value} 独立初审失败：{last_error}",
                    severity=IssueSeverity.WARNING,
                    role=role,
                )

        results = await asyncio.gather(*(run_one(role) for role in SpecialistRole))
        reviews = [review for review, _ in results if review is not None]
        issues = [issue for _, issue in results if issue is not None]
        if len(reviews) < self.config.minimum_independent_reviews:
            failure_details = "；".join(issue.message for issue in issues)
            raise WorkflowExecutionError(
                f"仅获得 {len(reviews)} 份独立初审，低于最低要求 "
                f"{self.config.minimum_independent_reviews}。失败详情：{failure_details}"
            )
        logger.info(
            "独立初审完成，成功=%d，失败=%d", len(reviews), len(issues)
        )
        return {"independent_reviews": reviews, "issues": issues}

    async def _plan_debate(self, state: DebateState) -> dict[str, Any]:
        logger.info("Review Chair 正在识别争议 plan_debate")
        try:
            plan = await self._call_validated(
                lambda: self.services.review_chair.plan_debate(
                    state["context"], state["independent_reviews"]
                ),
                DebatePlan,
                attempts=3,
            )
        except Exception as exc:
            logger.warning("DebatePlan 生成失败，本轮跳过 Debate：%s", exc)
            return {
                "debate_plan": DebatePlan(),
                "issues": [
                    DebateWorkflowIssue(
                        node="plan_debate",
                        code="plan_debate_failed",
                        message=(
                            "Review Chair 的 DebatePlan 生成失败，本轮跳过 Debate："
                            f"{exc}"
                        ),
                        severity=IssueSeverity.WARNING,
                    )
                ],
            }
        logger.info("DebatePlan 完成，issues=%d questions=%d",
                    len(plan.issues), len(plan.questions))
        return {"debate_plan": plan}

    async def _retrieve_debate_evidence(self, state: DebateState) -> dict[str, Any]:
        queries = list(
            dict.fromkeys(
                question.evidence_query
                for question in state["debate_plan"].questions
                if question.requires_external_evidence and question.evidence_query
            )
        )
        if not queries:
            return {"external_evidence": []}
        if self.services.evidence_retriever is None:
            return {
                "external_evidence": [],
                "issues": [
                    DebateWorkflowIssue(
                        node="retrieve_debate_evidence",
                        code="evidence_retriever_unavailable",
                        message="Debate 需要外部证据，但未配置 EvidenceRetriever",
                    )
                ],
            }

        try:
            raw_evidence = await _invoke(
                lambda: self.services.evidence_retriever.retrieve(
                    queries,
                    context=state["context"],
                    limit=self.config.evidence_limit,
                )
            )
            evidence = [ReviewEvidence.model_validate(item) for item in raw_evidence]
            external = [item for item in evidence if item.kind.value == "external"]
            return {"external_evidence": external[: self.config.evidence_limit]}
        except Exception as exc:
            return {
                "external_evidence": [],
                "issues": [
                    DebateWorkflowIssue(
                        node="retrieve_debate_evidence",
                        code="evidence_retrieval_failed",
                        message=f"外部证据检索失败：{exc}",
                    )
                ],
            }

    async def _targeted_debate(self, state: DebateState) -> dict[str, Any]:
        questions = state["debate_plan"].questions
        if not questions:
            return {"debate_responses": []}

        issue_by_id = {issue.issue_id: issue for issue in state["debate_plan"].issues}
        review_by_role = {
            review.role: review for review in state["independent_reviews"]
        }
        semaphore = asyncio.Semaphore(self.config.max_concurrency)

        async def respond_one(
            question: Any,
        ) -> tuple[DebateResponse | None, DebateWorkflowIssue | None]:
            role = question.target_role
            own_review = review_by_role.get(role)
            if own_review is None:
                return None, DebateWorkflowIssue(
                    node="targeted_debate",
                    code="target_specialist_unavailable",
                    message=f"问题 {question.question_id} 的目标 Specialist 无可用初审",
                    role=role,
                    question_id=question.question_id,
                )

            issue = issue_by_id[question.issue_id]
            peer_reviews = [
                review
                for review in state["independent_reviews"]
                if review.role in issue.participating_roles and review.role is not role
            ]
            async with semaphore:
                try:
                    response = DebateResponse.model_validate(
                        await _invoke(
                            lambda: self.services.specialists[role].respond(
                                state["context"],
                                own_review=own_review,
                                issue=issue,
                                question=question,
                                peer_reviews=peer_reviews,
                                external_evidence=state.get("external_evidence", []),
                            )
                        )
                    )
                    if (
                        response.role is not role
                        or response.question_id != question.question_id
                        or response.issue_id != question.issue_id
                    ):
                        raise ValueError("DebateResponse 与定向问题的角色或标识不一致")
                    degraded = self._validate_response_grounding(
                        response, state["context"]
                    )
                    if degraded:
                        return response, DebateWorkflowIssue(
                            node="targeted_debate",
                            code="debate_evidence_needs_review",
                            message=(
                                f"问题 {question.question_id} 回应中 {len(degraded)} "
                                "条证据未能完全锚定到原文，已标记人工复核："
                                + "；".join(
                                    f"{item.evidence_id}" for item in degraded
                                )
                            ),
                            severity=IssueSeverity.WARNING,
                            role=role,
                            question_id=question.question_id,
                        )
                    return response, None
                except Exception as exc:
                    return None, DebateWorkflowIssue(
                        node="targeted_debate",
                        code="debate_response_failed",
                        message=f"问题 {question.question_id} 回应失败：{exc}",
                        role=role,
                        question_id=question.question_id,
                    )

        results = await asyncio.gather(*(respond_one(question) for question in questions))
        responses = [response for response, _ in results if response is not None]
        issues = [issue for _, issue in results if issue is not None]
        logger.info(
            "定向 Debate 完成，问题=%d 回应=%d 失败=%d",
            len(questions), len(responses), len(issues),
        )
        return {"debate_responses": responses, "issues": issues}

    async def _synthesize_review(self, state: DebateState) -> dict[str, Any]:
        logger.info("Review Chair 正在综合最终裁决 synthesize_review")
        try:
            synthesis = await self._call_validated(
                lambda: self.services.review_chair.synthesize(
                    state["context"],
                    reviews=state["independent_reviews"],
                    debate_plan=state["debate_plan"],
                    responses=state.get("debate_responses", []),
                    external_evidence=state.get("external_evidence", []),
                ),
                ReviewSynthesis,
            )
            degraded = self._validate_synthesis_grounding(
                synthesis, state["context"]
            )
            if degraded:
                issues = state.get("issues", []) + [
                    DebateWorkflowIssue(
                        node="synthesize_review",
                        code="synthesis_evidence_needs_review",
                        message=(
                            "综合裁决中有 " + str(len(degraded)) +
                            " 条论文证据未能完全锚定到原文，已标记人工复核："
                            + "；".join(f"{item.evidence_id}" for item in degraded)
                        ),
                        severity=IssueSeverity.WARNING,
                    )
                ]
                return {"synthesis": synthesis, "issues": issues}
        except ModelClientError as exc:
            raise WorkflowExecutionError(
                f"Review Chair 模型调用失败（网络/超时/API 错误）：{exc}"
            ) from exc
        except Exception as exc:
            raise WorkflowExecutionError(
                f"Review Chair 的最终输出不合法：{exc}"
            ) from exc
        logger.info(
            "综合裁决完成，章节=%d",
            len(synthesis.chapter_evaluation),
        )
        return {"synthesis": synthesis}

    async def _step5_workload_evaluation(self, state: DebateState) -> dict[str, Any]:
        """Run the old paper-type-specific Step 5 after Chair synthesis."""

        evaluator = self.services.workload_evaluator or DeterministicLegacyWorkloadEvaluator()
        try:
            workload = await self._call_validated(
                lambda: evaluator.evaluate_workload(
                    state["review_input"], state["synthesis"]
                ),
                CompatibleWorkloadEvaluation,
            )
        except Exception as exc:
            raise WorkflowExecutionError(f"Step 5 工作量评估失败：{exc}") from exc
        return {
            "synthesis": state["synthesis"].model_copy(
                update={"workload_evaluation": workload}
            )
        }

    def _compatibility_gate(self, state: DebateState) -> dict[str, Any]:
        """在调用原 Step 6/7 前检查章节数量、顺序键和 Step 5 字段。"""

        reviewable = [
            chapter for chapter in state["review_input"].chapters if chapter.reviewable
        ]
        expected_keys = [f"chapter_{index}" for index in range(1, len(reviewable) + 1)]
        actual_keys = list(state["synthesis"].chapter_evaluation)
        if actual_keys != expected_keys:
            raise WorkflowExecutionError(
                f"chapter_evaluation 键与原流程不兼容，期望 {expected_keys}，实际 {actual_keys}"
            )

        for key, chapter in zip(expected_keys, reviewable, strict=True):
            output_name = state["synthesis"].chapter_evaluation[key].chapter_data.chapter_name
            if output_name != chapter.chapter_name:
                raise WorkflowExecutionError(
                    f"{key} 章节名不一致：期望 {chapter.chapter_name}，实际 {output_name}"
                )
        return {}

    async def _step6_summary_advice(self, state: DebateState) -> dict[str, Any]:
        try:
            result = SummaryAdviceResult.model_validate(
                await _invoke(
                    lambda: self.services.original_pipeline.summarize_advice(
                        state["review_input"], state["synthesis"]
                    )
                )
            )
            return {"summary_advice": result}
        except Exception as exc:
            raise WorkflowExecutionError(f"Step 6 适配器执行失败：{exc}") from exc

    async def _retrieve_score_cases(self, state: DebateState) -> dict[str, Any]:
        retriever = self.services.historical_score_retriever
        if retriever is None:
            return {"historical_score_cases": []}

        global_review = state["synthesis"].global_review
        query = ScoreCalibrationQuery(
            paper_type=state["review_input"].paper_type,
            dimensions={item.dimension: item.summary for item in global_review.dimensions},
            severe_findings=[
                finding.claim
                for finding in global_review.resolved_findings
                if finding.severity.value in {"fatal", "major"}
            ],
        )
        try:
            raw_cases = await _invoke(
                lambda: retriever.retrieve(
                    query, limit=self.config.historical_case_limit
                )
            )
            cases = [HistoricalScoreCase.model_validate(item) for item in raw_cases]
            cases.sort(key=lambda item: item.similarity, reverse=True)
            return {"historical_score_cases": cases[: self.config.historical_case_limit]}
        except Exception as exc:
            return {
                "historical_score_cases": [],
                "issues": [
                    DebateWorkflowIssue(
                        node="retrieve_score_cases",
                        code="score_rag_failed",
                        message=f"历史评分 RAG 执行失败：{exc}",
                    )
                ],
            }

    async def _step7_scoring(self, state: DebateState) -> dict[str, Any]:
        try:
            score = ComprehensiveScoreResult.model_validate(
                await _invoke(
                    lambda: self.services.original_pipeline.score(
                        state["review_input"],
                        state["synthesis"],
                        summary_advice=state["summary_advice"],
                        historical_cases=state.get("historical_score_cases", []),
                    )
                )
            )
            logger.info("Step 7 评分完成，总分=%.1f 等级=%s",
                        score.total_score, score.grade)
            return {"final_score": score}
        except Exception as exc:
            raise WorkflowExecutionError(f"Step 7 适配器执行失败：{exc}") from exc

    async def arun(
        self,
        review_input: DebateReviewInput | dict[str, Any],
        *,
        progress_callback: ProgressCallback | None = None,
        thread_id: str | None = None,
    ) -> DebateRunResult:
        """异步执行完整 Debate 评审链路。"""

        validated_input = DebateReviewInput.model_validate(review_input)
        token = _progress_callback.set(progress_callback)
        try:
            config = {"configurable": {"thread_id": thread_id or uuid4().hex}}
            final = await self.graph.ainvoke(
                self._initial_state(validated_input), config
            )
        finally:
            _progress_callback.reset(token)
        return self._result_from_state(final)

    async def aresume(
        self,
        review_input: DebateReviewInput | dict[str, Any],
        *,
        thread_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> DebateRunResult:
        """从上次失败的步骤恢复执行。

        LangGraph checkpointer 会在每个节点成功后保存状态快照；失败重试时
        若存在未完成的待执行节点，则从断点继续，只重跑失败及之后的步骤。
        没有任何快照（如服务重启且使用内存检查点）时退回完整执行。
        """

        validated_input = DebateReviewInput.model_validate(review_input)
        config = {"configurable": {"thread_id": thread_id}}
        token = _progress_callback.set(progress_callback)
        try:
            state = await self.graph.aget_state(config)
            if state.next:
                final = await self.graph.ainvoke(None, config)
            else:
                final = await self.graph.ainvoke(
                    self._initial_state(validated_input), config
                )
        finally:
            _progress_callback.reset(token)
        return self._result_from_state(final)

    @staticmethod
    def _initial_state(review_input: DebateReviewInput) -> DebateState:
        return {
            "review_input": review_input,
            "independent_reviews": [],
            "external_evidence": [],
            "debate_responses": [],
            "historical_score_cases": [],
            "issues": [],
        }

    @staticmethod
    def _result_from_state(final: DebateState) -> DebateRunResult:
        required = {
            "context",
            "debate_plan",
            "synthesis",
            "summary_advice",
            "final_score",
        }
        missing = required - set(final)
        if missing:
            raise WorkflowExecutionError(f"Debate 工作流结束时缺少状态：{sorted(missing)}")

        return DebateRunResult(
            context=final["context"],
            independent_reviews=final.get("independent_reviews", []),
            debate_plan=final["debate_plan"],
            external_evidence=final.get("external_evidence", []),
            debate_responses=final.get("debate_responses", []),
            synthesis=final["synthesis"],
            summary_advice=final.get("summary_advice"),
            historical_score_cases=final.get("historical_score_cases", []),
            final_score=final.get("final_score"),
            issues=final.get("issues", []),
        )

    @classmethod
    def _validate_review_grounding(
        cls, review: IndependentReview, context: ReviewContext
    ) -> list[ReviewEvidence]:
        degraded: list[ReviewEvidence] = []
        for finding in review.findings:
            cls._validate_chapter_ids(finding.affected_chapter_ids, context)
            degraded.extend(
                cls._validate_paper_evidence(finding.evidence, context)
            )
            if any(item for item in finding.evidence if item in degraded):
                finding.requires_human_review = True
        return degraded

    @classmethod
    def _validate_response_grounding(
        cls, response: DebateResponse, context: ReviewContext
    ) -> list[ReviewEvidence]:
        degraded: list[ReviewEvidence] = []
        degraded.extend(
            cls._validate_paper_evidence(response.evidence, context)
        )
        for finding in response.revised_findings:
            cls._validate_chapter_ids(finding.affected_chapter_ids, context)
            finding_degraded = cls._validate_paper_evidence(
                finding.evidence, context
            )
            degraded.extend(finding_degraded)
            if finding_degraded:
                finding.requires_human_review = True
        return degraded

    @classmethod
    def _validate_synthesis_grounding(
        cls, synthesis: ReviewSynthesis, context: ReviewContext
    ) -> list[ReviewEvidence]:
        degraded: list[ReviewEvidence] = []
        for finding in getattr(synthesis.global_review, "resolved_findings", []):
            cls._validate_chapter_ids(finding.affected_chapter_ids, context)
            finding_degraded = cls._validate_paper_evidence(
                finding.evidence, context
            )
            degraded.extend(finding_degraded)
            if finding_degraded:
                finding.requires_human_review = True
        return degraded

    @staticmethod
    def _validate_chapter_ids(chapter_ids: list[str], context: ReviewContext) -> None:
        known = {chapter.chapter_id for chapter in context.chapters}
        unknown = sorted(set(chapter_ids) - known)
        if unknown:
            raise ValueError(f"评审结论引用了未知章节：{unknown}")

    @classmethod
    def _fuzzy_anchor(cls, quote: str, content: str) -> tuple[str, float] | None:
        """在章节原文中寻找与引文最相似的片段并回填。

        真实 LLM 形成的引文常带公式或轻微改写，无法逐字匹配。这里对整章
        做最长公共子串匹配，返回 ``(原文章节片段, 覆盖比例)``，其中覆盖
        比例 = 最长连续公共块长度 / 规范化引文长度；连续公共块过短
        （< 6 个字符）视为无法定位，返回 None。为防止 LaTeX 归一化产生
        长度变化，规范化仅做"去空白 + 小写"，并用原文下标映射回填片段，
        保证回填内容与原文一致。
        """
        import difflib

        def build_mapping(raw: str) -> tuple[list[int], str]:
            mapping: list[int] = []
            chars: list[str] = []
            for index, char in enumerate(raw):
                if char.isspace():
                    continue
                mapping.append(index)
                chars.append(char.casefold())
            return mapping, "".join(chars)

        quote_map, norm_quote = build_mapping(quote)
        content_map, norm_content = build_mapping(content)
        if not norm_quote or not norm_content:
            return None

        matcher = difflib.SequenceMatcher(
            None, norm_quote, norm_content, autojunk=False
        )
        best_block: difflib.Match | None = None
        matched_blocks = matcher.get_matching_blocks()
        for block in matched_blocks:
            if best_block is None or block.size > best_block.size:
                best_block = block

        if best_block is None:
            return None
        coverage = best_block.size / max(1, len(norm_quote))
        coverage = min(1.0, coverage)
        min_block_size = max(3, min(6, len(norm_quote) // 2))
        if best_block.size < min_block_size or coverage < 0.1:
            return None

        begin = content_map[best_block.b]
        last_idx = min(best_block.b + best_block.size - 1, len(content_map) - 1)
        end = content_map[last_idx] + 1
        best_fragment = content[begin:end]
        return best_fragment, coverage

    @classmethod
    def _locate_paper_evidence(
        cls,
        evidence: ReviewEvidence,
        context: ReviewContext,
    ) -> tuple[str, str, float] | None:
        """把证据锚定到章节原文，返回 ``(chapter_id, quote, coverage)``。

        优先使用模型给出的 chapter_id；逐字匹配失败时对候选章节做模糊锚定，
        命中后把证据的 quote 回填为可追溯的原文片段。coverage 是规范化引文
        在原文中的覆盖比例（0~1）；返回 None 表示完全无法定位。
        """

        def normalize(value: str) -> str:
            return "".join(value.split()).casefold()

        chapters = {chapter.chapter_id: chapter for chapter in context.chapters}
        requested = evidence.chapter_id
        if requested and requested in chapters:
            candidates = [requested]
        else:
            candidates = list(chapters)

        best: tuple[float, str, str] | None = None
        for chapter_id in candidates:
            content = chapters[chapter_id].content
            quote = normalize(evidence.quote)
            if quote in normalize(content):
                return chapter_id, quote, 1.0
            result = cls._fuzzy_anchor(evidence.quote, content)
            if result is None:
                continue
            fragment, coverage = result
            if coverage < 0.1:
                continue
            if best is None or coverage > best[0]:
                best = (coverage, chapter_id, normalize(fragment))
        if best is None:
            return None
        coverage, chapter_id, normalized_fragment = best
        evidence.quote = normalized_fragment
        evidence.chapter_id = chapter_id
        return chapter_id, normalized_fragment, coverage

    @classmethod
    def _validate_paper_evidence(
        cls,
        evidence_items: list[ReviewEvidence],
        context: ReviewContext,
    ) -> list[ReviewEvidence]:
        """校正并锚定论文证据，返回需要人工复核的证据列表。

        对每条论文证据：能精确定位或高覆盖度模糊定位的，回填可追溯片段；
        仅能部分定位（覆盖度不足）的，降低置信度并标记需要人工复核，
        不会整体丢弃该条证据；完全无法定位的才会抛错，阻止编造引文混入。
        """
        chapters = {chapter.chapter_id: chapter for chapter in context.chapters}
        blocks = {
            block.block_id: block
            for block in (
                context.structured_document.blocks
                if context.structured_document is not None
                else []
            )
        }

        def normalize(value: str) -> str:
            return "".join(value.split()).casefold()

        degraded: list[ReviewEvidence] = []
        for evidence in evidence_items:
            if evidence.kind.value != "paper":
                continue
            located = cls._locate_paper_evidence(evidence, context)
            if located is None:
                raise ValueError(
                    f"论文证据 {evidence.evidence_id} 的引文无法在章节原文中定位"
                )
            chapter_id, quote, coverage = located

            if coverage < 0.5:
                evidence.confidence = min(evidence.confidence, 0.4)
                degraded.append(evidence)

            block = blocks.get(evidence.block_id) if evidence.block_id else None
            if evidence.block_id and block is None:
                raise ValueError(
                    f"论文证据 {evidence.evidence_id} 引用了未知 block_id"
                )
            if block is None and blocks:
                matches = [
                    item for item in blocks.values()
                    if item.chapter_id == chapter_id
                    and item.text
                    and quote in normalize(item.text)
                ]
                if len(matches) == 1:
                    block = matches[0]
            if block is not None:
                if block.chapter_id != chapter_id:
                    raise ValueError(
                        f"论文证据 {evidence.evidence_id} 的 block_id 与 chapter_id 不一致"
                    )
                if block.text and quote not in normalize(block.text):
                    raise ValueError(
                        f"论文证据 {evidence.evidence_id} 的引文无法在指定内容块中定位"
                    )
                evidence.block_id = block.block_id
                evidence.chunk_id = block.chunk_id
                evidence.page_number = block.page_number
                evidence.bbox = block.bbox
        return degraded

    def run(self, review_input: DebateReviewInput | dict[str, Any]) -> DebateRunResult:
        """同步入口；异步应用请调用 arun。"""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.arun(review_input))
        raise RuntimeError("检测到正在运行的事件循环，请改用 await workflow.arun(...) ")


def build_workflow(
    runtime: str = "demo",
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> DebateWorkflow:
    """按运行模式构造 Debate 工作流。

    - ``demo``：确定性 Demo Agent，用于测试和回归基线；
    - ``real``：真实模型驱动的 Agent，用于生产评审。
    """

    if runtime == "real":
        return DebateWorkflow.real(checkpointer=checkpointer)
    if runtime == "demo":
        return DebateWorkflow.default(checkpointer=checkpointer)
    raise ValueError(f"未知的 Debate 运行模式: {runtime}")
