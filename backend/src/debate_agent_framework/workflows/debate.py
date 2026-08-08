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
    RealOriginalPipelineAdapter,
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
        return cls(
            DebateWorkflowServices(
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

        builder.add_edge(START, "retrieve_historical_advice")
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
            async with semaphore:
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
                    self._validate_review_grounding(review, state["context"])
                    return review, None
                except Exception as exc:  # 一个视角失败时保留其他独立意见
                    return None, DebateWorkflowIssue(
                        node="independent_review",
                        code="specialist_review_failed",
                        message=f"{role.value} 独立初审失败：{exc}",
                        severity=IssueSeverity.WARNING,
                        role=role,
                    )

        results = await asyncio.gather(*(run_one(role) for role in SpecialistRole))
        reviews = [review for review, _ in results if review is not None]
        issues = [issue for _, issue in results if issue is not None]
        if len(reviews) < self.config.minimum_independent_reviews:
            raise WorkflowExecutionError(
                f"仅获得 {len(reviews)} 份独立初审，低于最低要求 "
                f"{self.config.minimum_independent_reviews}"
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
                    self._validate_response_grounding(response, state["context"])
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
            self._validate_synthesis_grounding(synthesis, state["context"])
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
        self, review_input: DebateReviewInput | dict[str, Any]
    ) -> DebateRunResult:
        """异步执行完整 Debate 评审链路。"""

        validated_input = DebateReviewInput.model_validate(review_input)
        initial: DebateState = {
            "review_input": validated_input,
            "independent_reviews": [],
            "external_evidence": [],
            "debate_responses": [],
            "historical_score_cases": [],
            "issues": [],
        }
        final = await self.graph.ainvoke(initial)
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
    ) -> None:
        for finding in review.findings:
            cls._validate_chapter_ids(finding.affected_chapter_ids, context)
            cls._validate_paper_evidence(finding.evidence, context)

    @classmethod
    def _validate_response_grounding(
        cls, response: DebateResponse, context: ReviewContext
    ) -> None:
        cls._validate_paper_evidence(response.evidence, context)
        for finding in response.revised_findings:
            cls._validate_chapter_ids(finding.affected_chapter_ids, context)
            cls._validate_paper_evidence(finding.evidence, context)

    @classmethod
    def _validate_synthesis_grounding(
        cls, synthesis: ReviewSynthesis, context: ReviewContext
    ) -> None:
        for finding in getattr(synthesis.global_review, "resolved_findings", []):
            cls._validate_chapter_ids(finding.affected_chapter_ids, context)
            cls._validate_paper_evidence(finding.evidence, context)

    @staticmethod
    def _validate_chapter_ids(chapter_ids: list[str], context: ReviewContext) -> None:
        known = {chapter.chapter_id for chapter in context.chapters}
        unknown = sorted(set(chapter_ids) - known)
        if unknown:
            raise ValueError(f"评审结论引用了未知章节：{unknown}")

    @staticmethod
    def _validate_paper_evidence(
        evidence_items: list[ReviewEvidence], context: ReviewContext
    ) -> None:
        chapters = {chapter.chapter_id: chapter for chapter in context.chapters}

        def normalize(value: str) -> str:
            return "".join(value.split()).casefold()

        for evidence in evidence_items:
            if evidence.kind.value != "paper":
                continue
            if not evidence.chapter_id or evidence.chapter_id not in chapters:
                raise ValueError(
                    f"论文证据 {evidence.evidence_id} 必须引用有效 chapter_id"
                )
            quote = normalize(evidence.quote)
            source = normalize(chapters[evidence.chapter_id].content)
            if quote not in source:
                raise ValueError(
                    f"论文证据 {evidence.evidence_id} 的引文无法在章节原文中定位"
                )

    def run(self, review_input: DebateReviewInput | dict[str, Any]) -> DebateRunResult:
        """同步入口；异步应用请调用 arun。"""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.arun(review_input))
        raise RuntimeError("检测到正在运行的事件循环，请改用 await workflow.arun(...) ")


def build_workflow(runtime: str = "demo") -> DebateWorkflow:
    """按运行模式构造 Debate 工作流。

    - ``demo``：确定性 Demo Agent，用于测试和回归基线；
    - ``real``：真实模型驱动的 Agent，用于生产评审。
    """

    if runtime == "real":
        return DebateWorkflow.real()
    if runtime == "demo":
        return DebateWorkflow.default()
    raise ValueError(f"未知的 Debate 运行模式: {runtime}")
