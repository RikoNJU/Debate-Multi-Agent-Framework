"""Evidence-Grounded Debate çš„ LangGraph ç¼–æŽ’ã€‚"""

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


async def _resolve(value: T | Awaitable[T]) -> T:
    """å…¼å®¹åŒæ­¥ Agent å’Œå¼‚æ­¥æ¨¡åž‹ SDKã€‚"""

    if inspect.isawaitable(value):
        return await cast(Awaitable[T], value)
    return value


async def _invoke(call: Any) -> Any:
    """åœ¨çº¿ç¨‹æ± ä¸­è°ƒç”¨åŒæ­¥å®žçŽ°ï¼Œå¹¶å…¼å®¹è¿”å›ž awaitable çš„å¼‚æ­¥å®žçŽ°ã€‚"""

    value = await asyncio.to_thread(call)
    return await _resolve(value)


class DebateWorkflow:
    """æ‰§è¡Œç‹¬ç«‹åˆå®¡ã€ä¸€è½®å®šå‘ Debate å’ŒåŽŸæµç¨‹å…¼å®¹è¾“å‡ºã€‚"""

    @classmethod
    def default(cls) -> "DebateWorkflow":
        """æž„é€ é»˜è®¤ Debate å·¥ä½œæµã€‚

        å½“å‰é¡¹ç›®é‡‡ç”¨å›ºå®š Agent ç»„åˆï¼Œå› æ­¤é»˜è®¤è£…é…é€»è¾‘ç›´æŽ¥æ”¾åœ¨å·¥ä½œæµç±»ä¸­ã€‚
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
            )
        )

    @classmethod
    def real(cls, model_client: ModelClient | None = None) -> "DebateWorkflow":
        """æž„é€ çœŸå®žæ¨¡åž‹é©±åŠ¨çš„ Debate å·¥ä½œæµã€‚

        Specialistã€Review Chair ä¸Ž Step 6/7 ä½¿ç”¨çœŸå®ž LLMã€‚æœªé…ç½®çš„å¤–éƒ¨è¯æ®
        ä¸ŽåŽ†å²è¯„åˆ†æœåŠ¡ä¿æŒä¸ºç©ºï¼Œç¦æ­¢ Demo æ•°æ®æ±¡æŸ“çœŸå®žè¯„å®¡ã€‚
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
                workload_evaluator=RealLegacyWorkloadEvaluator(client),
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
            raise ValueError(f"Specialist æ³¨å†Œè¡¨ä¸å®Œæ•´ï¼Œç¼ºå°‘={missing}ï¼Œå¤šä½™={extra}")
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
        builder.add_node("step5_workload_evaluation", self._step5_workload_evaluation)
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
        builder.add_edge("synthesize_review", "step5_workload_evaluation")
        builder.add_edge("step5_workload_evaluation", "compatibility_gate")
        builder.add_edge("compatibility_gate", "step6_summary_advice")
        builder.add_edge("step6_summary_advice", "retrieve_score_cases")
        builder.add_edge("retrieve_score_cases", "step7_scoring")
        builder.add_edge("step7_scoring", END)
        return builder.compile()

    async def _step1_classify_paper(self, state: DebateState) -> dict[str, Any]:
        """è‡ªåŠ¨è¡¥é½æ—§ Step 1ï¼›æ˜¾å¼ç±»åž‹ä¿æŒä¸å˜å¹¶è®°å½•æ¥æºã€‚"""

        review_input = state["review_input"]
        metadata = dict(review_input.metadata)
        if review_input.paper_type is not None:
            metadata.setdefault("paper_type_source", "provided")
            return {"review_input": review_input.model_copy(update={"metadata": metadata})}

        classifier = self.services.paper_classifier
        if classifier is None:
            raise WorkflowExecutionError("è®ºæ–‡æœªæä¾› paper_typeï¼Œä¸”æœªé…ç½® Step 1 åˆ†ç±»å™¨")
        try:
            result = PaperClassificationResult.model_validate(
                await _invoke(lambda: classifier.classify_paper(review_input))
            )
        except Exception as exc:
            raise WorkflowExecutionError(f"Step 1 è®ºæ–‡ç±»åž‹åˆ†ç±»å¤±è´¥ï¼š{exc}") from exc
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
        """å¯¹ MinerU çš„åˆæ­¥ç« èŠ‚åˆ‡åˆ†æ‰§è¡Œæ—§ Step 2 è¯­ä¹‰é˜¶æ®µåˆ†ç±»ã€‚"""

        review_input = state["review_input"]
        if review_input.paper_type is None:
            raise WorkflowExecutionError("Step 2 å¼€å§‹æ—¶ paper_type ä»ä¸ºç©º")
        source = review_input.metadata.get("chapter_stage_source", "provided")
        requires_classification = (
            source in {"markdown_heuristic", "auto_pending"}
            or review_input.metadata.get("paper_type_source") == "legacy_step1"
            or any(
                chapter.reviewable and chapter.stage in {"æ­£æ–‡", "general"}
                for chapter in review_input.chapters
            )
        )
        if not requires_classification:
            return {}

        classifier = self.services.chapter_classifier
        if classifier is None:
            raise WorkflowExecutionError("MinerU ç« èŠ‚éœ€è¦è‡ªåŠ¨åˆ†ç±»ï¼Œä½†æœªé…ç½® Step 2 åˆ†ç±»å™¨")
        try:
            result = ChapterClassificationResult.model_validate(
                await _invoke(lambda: classifier.classify_chapters(review_input))
            )
        except Exception as exc:
            raise WorkflowExecutionError(f"Step 2 ç« èŠ‚é˜¶æ®µåˆ†ç±»å¤±è´¥ï¼š{exc}") from exc

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
        """è¡¥é½åŽŸ Step 3 å»ºè®®ï¼›æ£€ç´¢å¤±è´¥æ—¶ä¿ç•™è°ƒç”¨æ–¹è¾“å…¥å¹¶ç»§ç»­è¯„å®¡ã€‚"""

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
                raise ValueError(f"æ£€ç´¢ç»“æžœåŒ…å«æœªçŸ¥ç« èŠ‚ï¼š{unknown_chapter_ids}")
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
                        message=f"åŽ†å²è¯„å®¡å»ºè®®æ£€ç´¢å¤±è´¥ï¼Œå·²ä½¿ç”¨çŽ°æœ‰è¾“å…¥ç»§ç»­è¯„å®¡ï¼š{exc}",
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
        """è°ƒç”¨ Agent å¹¶æ ¡éªŒè¾“å‡ºï¼Œå¤±è´¥æ—¶é‡è¯•ã€‚

        çœŸå®žæ¨¡åž‹çš„è¾“å‡ºå­˜åœ¨å¶å‘ä¸åˆè§„ï¼Œé‡è¯•èƒ½æ˜¾è‘—é™ä½Žè¿™ç±»å¤±è´¥çŽ‡ã€‚
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
                        "Agent è¾“å‡ºæ ¡éªŒå¤±è´¥ï¼Œé‡è¯• %d/%dï¼š%s",
                        attempt + 1,
                        attempts,
                        exc,
                    )
        assert last_error is noÛ^=¶‰žËkºwµçAð9½¹”°•‰…Ñ•]½É­™±½Ý%ÍÍÕ”ð9½¹•tè(€€€€€€€€€€€É½±”€ôÅÕ•ÍÑ¥½¸¹Ñ…É•Ñ}É½±”(€€€€€€€€€€€½Ý¹}É•Ù¥•Ü€ôÉ•Ù¥•Ý}‰å}É½±”¹•Ð¡É½±”¤(€€€€€€€€€€€¥˜½Ý¹}É•Ù¥•Ü¥Ì9½¹”è(€€€€€€€€€€€€€€€É•ÑÕÉ¸9½¹”°•‰…Ñ•]½É­™±½Ý%ÍÍÕ” (€€€€€€€€€€€€€€€€€€€¹½‘”ô‰Ñ…É•Ñ•‘}‘•‰…Ñ”ˆ°(€€€€€€€€€€€€€€€€€€€½‘”ô‰Ñ…É•Ñ}ÍÁ•¥…±¥ÍÑ}Õ¹…Ù…¥±…‰±”ˆ°(€€€€€€€€€€€€€€€€€€€µ•ÍÍ…”õ˜‹¦^»¦Š`íÅÕ•ÍÑ¥½¸¹ÅÕ•ÍÑ¥½¹}¥‘ôƒžjžn»š‚MÁ•¥…±¥ÍÐƒš^ƒ–>¿žR£–"w–º„ˆ°(€€€€€€€€€€€€€€€€€€€É½±”õÉ½±”°(€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}¥õÅÕ•ÍÑ¥½¸¹ÅÕ•ÍÑ¥½¹}¥°(€€€€€€€€€€€€€€€€¤((€€€€€€€€€€€¥ÍÍÕ”€ô¥ÍÍÕ•}‰å}¥‘mÅÕ•ÍÑ¥½¸¹¥ÍÍÕ•}¥‘t(€€€€€€€€€€€Á••É}É•Ù¥•ÝÌ€ôl(€€€€€€€€€€€€€€€É•Ù¥•Ü(€€€€€€€€€€€€€€€™½ÈÉ•Ù¥•Ü¥¸ÍÑ…Ñ•l‰¥¹‘•Á•¹‘•¹Ñ}É•Ù¥•ÝÌ‰t(€€€€€€€€€€€€€€€¥˜É•Ù¥•Ü¹É½±”¥¸¥ÍÍÕ”¹Á…ÉÑ¥¥Á…Ñ¥¹}É½±•Ì…¹É•Ù¥•Ü¹É½±”¥Ì¹½ÐÉ½±”(€€€€€€€€€€€t(€€€€€€€€€€€…Íå¹ŒÝ¥Ñ Í•µ…Á¡½É”è(€€€€€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€€€€€É•ÍÁ½¹Í”€ô•‰…Ñ•I•ÍÁ½¹Í”¹µ½‘•±}Ù…±¥‘…Ñ” (€€€€€€€€€€€€€€€€€€€€€€€…Ý…¥Ð}¥¹Ù½­” (€€€€€€€€€€€€€€€€€€€€€€€€€€€±…µ‰‘„èÍ•±˜¹Í•ÉÙ¥•Ì¹ÍÁ•¥…±¥ÍÑÍmÉ½±•t¹É•ÍÁ½¹ (€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ÍÑ…Ñ•l‰½¹Ñ•áÐ‰t°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€½Ý¹}É•Ù¥•Üõ½Ý¹}É•Ù¥•Ü°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€¥ÍÍÕ”õ¥ÍÍÕ”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¸õÅÕ•ÍÑ¥½¸°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€Á••É}É•Ù¥•ÝÌõÁ••É}É•Ù¥•ÝÌ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€•áÑ•É¹…±}•Ù¥‘•¹”õÍÑ…Ñ”¹•Ð ‰•áÑ•É¹…±}•Ù¥‘•¹”ˆ°mt¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€€€€¥˜€ (€€€€€€€€€€€€€€€€€€€€€€€É•ÍÁ½¹Í”¹É½±”¥Ì¹½ÐÉ½±”(€€€€€€€€€€€€€€€€€€€€€€€½ÈÉ•ÍÁ½¹Í”¹ÅÕ•ÍÑ¥½¹}¥€„ôÅÕ•ÍÑ¥½¸¹ÅÕ•ÍÑ¥½¹}¥(€€€€€€€€€€€€€€€€€€€€€€€½ÈÉ•ÍÁ½¹Í”¹¥ÍÍÕ•}¥€„ôÅÕ•ÍÑ¥½¸¹¥ÍÍÕ•}¥(€€€€€€€€€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‰•‰…Ñ•I•ÍÁ½¹Í”ƒ’â;–ºk–BG¦^»¦Šcžj¢žK¢&Ëš"[š‚¢¾’â7’â¢Ðˆ¤(€€€€€€€€€€€€€€€€€€€Í•±˜¹}Ù…±¥‘…Ñ•}É•ÍÁ½¹Í•}É½Õ¹‘¥¹œ¡É•ÍÁ½¹Í”°ÍÑ…Ñ•l‰½¹Ñ•áÐ‰t¤(€€€€€€€€€€€€€€€€€€€É•ÑÕÉ¸É•ÍÁ½¹Í”°9½¹”(€€€€€€€€€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€€€€€€€€€É•ÑÕÉ¸9½¹”°•‰…Ñ•]½É­™±½Ý%ÍÍÕ” (€€€€€€€€€€€€€€€€€€€€€€€¹½‘”ô‰Ñ…É•Ñ•‘}‘•‰…Ñ”ˆ°(€€€€€€€€€€€€€€€€€€€€€€€½‘”ô‰‘•‰…Ñ•}É•ÍÁ½¹Í•}™…¥±•ˆ°(€€€€€€€€€€€€€€€€€€€€€€€µ•ÍÍ…”õ˜‹¦^»¦Š`íÅÕ•ÍÑ¥½¸¹ÅÕ•ÍÑ¥½¹}¥‘ôƒ–n{–êS–’Ç¢Ò—¾òií•áôˆ°(€€€€€€€€€€€€€€€€€€€€€€€É½±”õÉ½±”°(€€€€€€€€€€€€€€€€€€€€€€€ÅÕ•ÍÑ¥½¹}¥õÅÕ•ÍÑ¥½¸¹ÅÕ•ÍÑ¥½¹}¥°(€€€€€€€€€€€€€€€€€€€€¤((€€€€€€€É•ÍÕ±ÑÌ€ô…Ý…¥Ð…Íå¹¥¼¹…Ñ¡•È ¨¡É•ÍÁ½¹‘}½¹”¡ÅÕ•ÍÑ¥½¸¤™½ÈÅÕ•ÍÑ¥½¸¥¸ÅÕ•ÍÑ¥½¹Ì¤¤(€€€€€€€É•ÍÁ½¹Í•Ì€ômÉ•ÍÁ½¹Í”™½ÈÉ•ÍÁ½¹Í”°|¥¸É•ÍÕ±ÑÌ¥˜É•ÍÁ½¹Í”¥Ì¹½Ð9½¹•t(€€€€€€€¥ÍÍÕ•Ì€ôm¥ÍÍÕ”™½È|°¥ÍÍÕ”¥¸É•ÍÕ±ÑÌ¥˜¥ÍÍÕ”¥Ì¹½Ð9½¹•t(€€€€€€€±½•È¹¥¹™¼ (€€€€€€€€€€€€‹–ºk–BD•‰…Ñ”ƒ–º3š"C¾ò3¦^»¦Š`ô•ƒ–n{–êPô•ƒ–’Ç¢Ò”ô•ˆ°(€€€€€€€€€€€±•¸¡ÅÕ•ÍÑ¥½¹Ì¤°±•¸¡É•ÍÁ½¹Í•Ì¤°±•¸¡¥ÍÍÕ•Ì¤°(€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸ì‰‘•‰…Ñ•}É•ÍÁ½¹Í•ÌˆèÉ•ÍÁ½¹Í•Ì°€‰¥ÍÍÕ•Ìˆè¥ÍÍÕ•Íô((€€€…Íå¹Œ‘•˜}Íå¹Ñ¡•Í¥é•}É•Ù¥•Ü¡Í•±˜°ÍÑ…Ñ”è•‰…Ñ•MÑ…Ñ”¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€±½•È¹¥¹™¼ ‰I•Ù¥•Ü¡…¥Èƒš¶–r£žîó–B#šržî#¢Ž–ÌÍå¹Ñ¡•Í¥é•}É•Ù¥•Üˆ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Íå¹Ñ¡•Í¥Ì€ô…Ý…¥ÐÍ•±˜¹}…±±}Ù…±¥‘…Ñ• (€€€€€€€€€€€€€€€±…µ‰‘„èÍ•±˜¹Í•ÉÙ¥•Ì¹É•Ù¥•Ý}¡…¥È¹Íå¹Ñ¡•Í¥é” (€€€€€€€€€€€€€€€€€€€ÍÑ…Ñ•l‰½¹Ñ•áÐ‰t°(€€€€€€€€€€€€€€€€€€€É•Ù¥•ÝÌõÍÑ…Ñ•l‰¥¹‘•Á•¹‘•¹Ñ}É•Ù¥•ÝÌ‰t°(€€€€€€€€€€€€€€€€€€€‘•‰…Ñ•}Á±…¸õÍÑ…Ñ•l‰‘•‰…Ñ•}Á±…¸‰t°(€€€€€€€€€€€€€€€€€€€É•ÍÁ½¹Í•ÌõÍÑ…Ñ”¹•Ð ‰‘•‰…Ñ•}É•ÍÁ½¹Í•Ìˆ°mt¤°(€€€€€€€€€€€€€€€€€€€•áÑ•É¹…±}•Ù¥‘•¹”õÍÑ…Ñ”¹•Ð ‰•áÑ•É¹…±}•Ù¥‘•¹”ˆ°mt¤°(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€I•Ù¥•ÝMå¹Ñ¡•Í¥Ì°(€€€€€€€€€€€€¤(€€€€€€€€€€€Í•±˜¹}Ù…±¥‘…Ñ•}Íå¹Ñ¡•Í¥Í}É½Õ¹‘¥¹œ¡Íå¹Ñ¡•Í¥Ì°ÍÑ…Ñ•l‰½¹Ñ•áÐ‰t¤(€€€€€€€•á•ÁÐ5½‘•±±¥•¹ÑÉÉ½È…Ì•áŒè(€€€€€€€€€€€É…¥Í”]½É­™±½Ýá•ÕÑ¥½¹ÉÉ½È (€€€€€€€€€€€€€€€˜‰I•Ù¥•Ü¡…¥Èƒš¢‡–z/¢ÂžR£–’Ç¢Ò—¾ò#žöGžîp¿¢Úš^Ø½A$ƒ¦Rg¢¾¿¾ò'¾òií•áôˆ(€€€€€€€€€€€€¤™É½´•áŒ(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€É…¥Í”]½É­™±½Ýá•ÕÑ¥½¹ÉÉ½È (€€€€€€€€€€€€€€€˜‰I•Ù¥•Ü¡…¥Èƒžjšržî#¢úO–ë’â7–B#šÎW¾òií•áôˆ(€€€€€€€€€€€€¤™É½´•áŒ(€€€€€€€±½•È¹¥¹™¼ (€€€€€€€€€€€€‹žîó–B#¢Ž–Ï–º3š"C¾ò3ž®ƒ¢*ô•ˆ°(€€€€€€€€€€€±•¸¡Íå¹Ñ¡•Í¥Ì¹¡…ÁÑ•É}•Ù…±Õ…Ñ¥½¸¤°(€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸ì‰Íå¹Ñ¡•Í¥ÌˆèÍå¹Ñ¡•Í¥Íô((€€€…Íå¹Œ‘•˜}ÍÑ•ÀÕ}Ý½É­±½…‘}•Ù…±Õ…Ñ¥½¸¡Í•±˜°ÍÑ…Ñ”è•‰…Ñ•MÑ…Ñ”¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€€ˆˆ‰IÕ¸Ñ¡”½±Á…Á•ÈµÑåÁ”µÍÁ•¥™¥ŒMÑ•À€Ô…™Ñ•È¡…¥ÈÍå¹Ñ¡•Í¥Ì¸ˆˆˆ((€€€€€€€•Ù…±Õ…Ñ½È€ôÍ•±˜¹Í•ÉÙ¥•Ì¹Ý½É­±½…‘}•Ù…±Õ…Ñ½È½È•Ñ•Éµ¥¹¥ÍÑ¥1•…å]½É­±½…‘Ù…±Õ…Ñ½È ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Ý½É­±½…€ô…Ý…¥ÐÍ•±˜¹}…±±}Ù…±¥‘…Ñ• (€€€€€€€€€€€€€€€±…µ‰‘„è•Ù…±Õ…Ñ½È¹•Ù…±Õ…Ñ•}Ý½É­±½… (€€€€€€€€€€€€€€€€€€€ÍÑ…Ñ•l‰É•Ù¥•Ý}¥¹ÁÕÐ‰t°ÍÑ…Ñ•l‰Íå¹Ñ¡•Í¥Ì‰t(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€½µÁ…Ñ¥‰±•]½É­±½…‘Ù…±Õ…Ñ¥½¸°(€€€€€€€€€€€€¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€É…¥Í”]½É­™±½Ýá•ÕÑ¥½¹ÉÉ½È¡˜‰MÑ•À€Ôƒ–Þ—’ös¦?¢¾’òÃ–’Ç¢Ò—¾òií•áôˆ¤™É½´•áŒ(€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€‰Íå¹Ñ¡•Í¥ÌˆèÍÑ…Ñ•l‰Íå¹Ñ¡•Í¥Ì‰t¹µ½‘•±}½Áä (€€€€€€€€€€€€€€€ÕÁ‘…Ñ”õì‰Ý½É­±½…‘}•Ù…±Õ…Ñ¥½¸ˆèÝ½É­±½…‘ô(€€€€€€€€€€€€¤(€€€€€€€ô((€€€‘•˜}½µÁ…Ñ¥‰¥±¥Ñå}…Ñ”¡Í•±˜°ÍÑ…Ñ”è•‰…Ñ•MÑ…Ñ”¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€€ˆˆ‹–r£¢ÂžR£–:|MÑ•À€Ø¼Üƒ–&7šŽš~—ž®ƒ¢*šVÃ¦?Ž¦†ë–ê?¦R»–J0MÑ•À€Ôƒ–¶_šº×Žˆˆˆ((€€€€€€€É•Ù¥•Ý…‰±”€ôl(€€€€€€€€€€€¡…ÁÑ•È™½È¡…ÁÑ•È¥¸ÍÑ…Ñ•l‰É•Ù¥•Ý}¥¹ÁÕÐ‰t¹¡…ÁÑ•ÉÌ¥˜¡…ÁÑ•È¹É•Ù¥•Ý…‰±”(€€€€€€€t(€€€€€€€•áÁ•Ñ•‘}­•åÌ€ôm˜‰¡…ÁÑ•É}í¥¹‘•áôˆ™½È¥¹‘•à¥¸É…¹” Ä°±•¸¡É•Ù¥•Ý…‰±”¤€¬€Ä¥t(€€€€€€€…ÑÕ…±}­•åÌ€ô±¥ÍÐ¡ÍÑ…Ñ•l‰Íå¹Ñ¡•Í¥Ì‰t¹¡…ÁÑ•É}•Ù…±Õ…Ñ¥½¸¤(€€€€€€€¥˜…ÑÕ…±}­•åÌ€„ô•áÁ•Ñ•‘}­•åÌè(€€€€€€€€€€€É…¥Í”]½É­™±½Ýá•ÕÑ¥½¹ÉÉ½È (€€€€€€€€€€€€€€€˜‰¡…ÁÑ•É}•Ù…±Õ…Ñ¥½¸ƒ¦R»’â;–:šÖž¢/’â7–ó–ºç¾ò3šršrlí•áÁ•Ñ•‘}­•åÍ÷¾ò3–º{¦fí…ÑÕ…±}­•åÍôˆ(€€€€€€€€€€€€¤((€€€€€€€™½È­•ä°¡…ÁÑ•È¥¸é¥À¡•áÁ•Ñ•‘}­•åÌ°É•Ù¥•Ý…‰±”°ÍÑÉ¥ÐõQÉÕ”¤è(€€€€€€€€€€€½ÕÑÁÕÑ}¹…µ”€ôÍÑ…Ñ•l‰Íå¹Ñ¡•Í¥Ì‰t¹¡…ÁÑ•É}•Ù…±Õ…Ñ¥½¹m­•åt¹¡…ÁÑ•É}‘…Ñ„¹¡…ÁÑ•É}¹…µ”(€€€€€€€€€€€¥˜½ÕÑÁÕÑ}¹…µ”€„ô¡…ÁÑ•È¹¡…ÁÑ•É}¹…µ”è(€€€€€€€€€€€€€€€É…¥Í”]½É­™±½Ýá•ÕÑ¥½¹ÉÉ½È (€€€€€€€€€€€€€€€€€€€˜‰í­•åôƒž®ƒ¢*–B7’â7’â¢Ó¾òkšršrlí¡…ÁÑ•È¹¡…ÁÑ•É}¹…µ•÷¾ò3–º{¦fí½ÕÑÁÕÑ}¹…µ•ôˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸íô((€€€…Íå¹Œ‘•˜}ÍÑ•ÀÙ}ÍÕµµ…Éå}…‘Ù¥”¡Í•±˜°ÍÑ…Ñ”è•‰…Ñ•MÑ…Ñ”¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€ÑÉäè(€€€€€€€€€€€É•ÍÕ±Ð€ôMÕµµ…Éå‘Ù¥•I•ÍÕ±Ð¹µ½‘•±}Ù…±¥‘…Ñ” (€€€€€€€€€€€€€€€…Ý…¥Ð}¥¹Ù½­” (€€€€€€€€€€€€€€€€€€€±…µ‰‘„èÍ•±˜¹Í•ÉÙ¥•Ì¹½É¥¥¹…±}Á¥Á•±¥¹”¹ÍÕµµ…É¥é•}…‘Ù¥” (€€€€€€€€€€€€€€€€€€€€€€€ÍÑ…Ñ•l‰É•Ù¥•Ý}¥¹ÁÕÐ‰t°ÍÑ…Ñ•l‰Íå¹Ñ¡•Í¥Ì‰t(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤(€€€€€€€€€€€É•ÑÕÉ¸ì‰ÍÕµµ…Éå}…‘Ù¥”ˆèÉ•ÍÕ±Ñô(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€É…¥Í”]½É­™±½Ýá•ÕÑ¥½¹ÉÉ½È¡˜‰MÑ•À€Øƒ¦¦7–f£š&Ÿ¢†3–’Ç¢Ò—¾òií•áôˆ¤™É½´•áŒ((€€€…Íå¹Œ‘•˜}É•ÑÉ¥•Ù•}Í½É•}…Í•Ì¡Í•±˜°ÍÑ…Ñ”è•‰…Ñ•MÑ…Ñ”¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€É•ÑÉ¥•Ù•È€ôÍ•±˜¹Í•ÉÙ¥•Ì¹¡¥ÍÑ½É¥…±}Í½É•}É•ÑÉ¥•Ù•È(€€€€€€€¥˜É•ÑÉ¥•Ù•È¥Ì9½¹”è(€€€€€€€€€€€É•ÑÕÉ¸ì‰¡¥ÍÑ½É¥…±}Í½É•}…Í•Ìˆèmuô((€€€€€€€±½‰…±}É•Ù¥•Ü€ôÍÑ…Ñ•l‰Íå¹Ñ¡•Í¥Ì‰t¹±½‰…±}É•Ù¥•Ü(€€€€€€€ÅÕ•Éä€ôM½É•…±¥‰É…Ñ¥½¹EÕ•Éä (€€€€€€€€€€€Á…Á•É}ÑåÁ”õÍÑ…Ñ•l‰É•Ù¥•Ý}¥¹ÁÕÐ‰t¹Á…Á•É}ÑåÁ”°(€€€€€€€€€€€‘¥µ•¹Í¥½¹Ìõí¥Ñ•´¹‘¥µ•¹Í¥½¸è¥Ñ•´¹ÍÕµµ…Éä™½È¥Ñ•´¥¸±½‰…±}É•Ù¥•Ü¹‘¥µ•¹Í¥½¹Íô°(€€€€€€€€€€€Í•Ù•É•}™¥¹‘¥¹Ìõl(€€€€€€€€€€€€€€€™¥¹‘¥¹œ¹±…¥´(€€€€€€€€€€€€€€€™½È™¥¹‘¥¹œ¥¸±½‰…±}É•Ù¥•Ü¹É•Í½±Ù•‘}™¥¹‘¥¹Ì(€€€€€€€€€€€€€€€¥˜™¥¹‘¥¹œ¹Í•Ù•É¥Ñä¹Ù…±Õ”¥¸ì‰™…Ñ…°ˆ°€‰µ…©½È‰ô(€€€€€€€€€€€t°(€€€€€€€€¤(€€€€€€€ÑÉäè(€€€€€€€€€€€É…Ý}…Í•Ì€ô…Ý…¥Ð}¥¹Ù½­” (€€€€€€€€€€€€€€€±…µ‰‘„èÉ•ÑÉ¥•Ù•È¹É•ÑÉ¥•Ù” (€€€€€€€€€€€€€€€€€€€ÅÕ•Éä°±¥µ¥ÐõÍ•±˜¹½¹™¥œ¹¡¥ÍÑ½É¥…±}…Í•}±¥µ¥Ð(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤(€€€€€€€€€€€…Í•Ì€ôm!¥ÍÑ½É¥…±M½É•…Í”¹µ½‘•±}Ù…±¥‘…Ñ”¡¥Ñ•´¤™½È¥Ñ•´¥¸É…Ý}…Í•Ít(€€€€€€€€€€€…Í•Ì¹Í½ÉÐ¡­•äõ±…µ‰‘„¥Ñ•´è¥Ñ•´¹Í¥µ¥±…É¥Ñä°É•Ù•ÉÍ”õQÉÕ”¤(€€€€€€€€€€€É•ÑÕÉ¸ì‰¡¥ÍÑ½É¥…±}Í½É•}…Í•Ìˆè…Í•ÍlèÍ•±˜¹½¹™¥œ¹¡¥ÍÑ½É¥…±}…Í•}±¥µ¥Ñuô(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€€€€€‰¡¥ÍÑ½É¥…±}Í½É•}…Í•Ìˆèmt°(€€€€€€€€€€€€€€€€‰¥ÍÍÕ•Ìˆèl(€€€€€€€€€€€€€€€€€€€•‰…Ñ•]½É­™±½Ý%ÍÍÕ” (€€€€€€€€€€€€€€€€€€€€€€€¹½‘”ô‰É•ÑÉ¥•Ù•}Í½É•}…Í•Ìˆ°(€€€€€€€€€€€€€€€€€€€€€€€½‘”ô‰Í½É•}É…}™…¥±•ˆ°(€€€€€€€€€€€€€€€€€€€€€€€µ•ÍÍ…”õ˜‹–:–>Ë¢¾–"Iƒš&Ÿ¢†3–’Ç¢Ò—¾òií•áôˆ°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€t°(€€€€€€€€€€€ô((€€€…Íå¹Œ‘•˜}ÍÑ•ÀÝ}Í½É¥¹œ¡Í•±˜°ÍÑ…Ñ”è•‰…Ñ•MÑ…Ñ”¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€ÑÉäè(€€€€€€€€€€€Í½É”€ô½µÁÉ•¡•¹Í¥Ù•M½É•I•ÍÕ±Ð¹µ½‘•±}Ù…±¥‘…Ñ” (€€€€€€€€€€€€€€€…Ý…¥Ð}¥¹Ù½­” (€€€€€€€€€€€€€€€€€€€±…µ‰‘„èÍ•±˜¹Í•ÉÙ¥•Ì¹½É¥¥¹…±}Á¥Á•±¥¹”¹Í½É” (€€€€€€€€€€€€€€€€€€€€€€€ÍÑ…Ñ•l‰É•Ù¥•Ý}¥¹ÁÕÐ‰t°(€€€€€€€€€€€€€€€€€€€€€€€ÍÑ…Ñ•l‰Íå¹Ñ¡•Í¥Ì‰t°(€€€€€€€€€€€€€€€€€€€€€€€ÍÕµµ…Éå}…‘Ù¥”õÍÑ…Ñ•l‰ÍÕµµ…Éå}…‘Ù¥”‰t°(€€€€€€€€€€€€€€€€€€€€€€€¡¥ÍÑ½É¥…±}…Í•ÌõÍÑ…Ñ”¹•Ð ‰¡¥ÍÑ½É¥…±}Í½É•}…Í•Ìˆ°mt¤°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€¤(€€€€€€€€€€€±½•È¹¥¹™¼ ‰MÑ•À€Üƒ¢¾–"–º3š"C¾ò3šï–"ô”¸Å˜ƒž¶'žêœô•Ìˆ°(€€€€€€€€€€€€€€€€€€€€€€€Í½É”¹Ñ½Ñ…±}Í½É”°Í½É”¹É…‘”¤(€€€€€€€€€€€É•ÑÕÉ¸ì‰™¥¹…±}Í½É”ˆèÍ½É•ô(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€É…¥Í”]½É­™±½Ýá•ÕÑ¥½¹ÉÉ½È¡˜‰MÑ•À€Üƒ¦¦7–f£š&Ÿ¢†3–’Ç¢Ò—¾òií•áôˆ¤™É½´•áŒ((€€€…Íå¹Œ‘•˜…ÉÕ¸ (€€€€€€€Í•±˜°É•Ù¥•Ý}¥¹ÁÕÐè•‰…Ñ•I•Ù¥•Ý%¹ÁÕÐð‘¥ÑmÍÑÈ°¹åt(€€€€¤€´ø•‰…Ñ•IÕ¹I•ÍÕ±Ðè(€€€€€€€€ˆˆ‹–òš¶—š&Ÿ¢†3–º3šVÐ•‰…Ñ”ƒ¢¾–º‡¦Nû¢Þ¿Žˆˆˆ((€€€€€€€Ù…±¥‘…Ñ•‘}¥¹ÁÕÐ€ô•‰…Ñ•I•Ù¥•Ý%¹ÁÕÐ¹µ½‘•±}Ù…±¥‘…Ñ”¡É•Ù¥•Ý}¥¹ÁÕÐ¤(€€€€€€€¥¹¥Ñ¥…°è•‰…Ñ•MÑ…Ñ”€ôì(€€€€€€€€€€€€‰É•Ù¥•Ý}¥¹ÁÕÐˆèÙ…±¥‘…Ñ•‘}¥¹ÁÕÐ°(€€€€€€€€€€€€‰¥¹‘•Á•¹‘•¹Ñ}É•Ù¥•ÝÌˆèmt°(€€€€€€€€€€€€‰•áÑ•É¹…±}•Ù¥‘•¹”ˆèmt°(€€€€€€€€€€€€‰‘•‰…Ñ•}É•ÍÁ½¹Í•Ìˆèmt°(€€€€€€€€€€€€‰¡¥ÍÑ½É¥…±}Í½É•}…Í•Ìˆèmt°(€€€€€€€€€€€€‰¥ÍÍÕ•Ìˆèmt°(€€€€€€€ô(€€€€€€€™¥¹…°€ô…Ý…¥ÐÍ•±˜¹É…Á ¹…¥¹Ù½­”¡¥¹¥Ñ¥…°¤(€€€€€€€É•ÅÕ¥É•€ôì(€€€€€€€€€€€€‰½¹Ñ•áÐˆ°(€€€€€€€€€€€€‰‘•‰…Ñ•}Á±…¸ˆ°(€€€€€€€€€€€€‰Íå¹Ñ¡•Í¥Ìˆ°(€€€€€€€€€€€€‰ÍÕµµ…Éå}…‘Ù¥”ˆ°(€€€€€€€€€€€€‰™¥¹…±}Í½É”ˆ°(€€€€€€€ô(€€€€€€€µ¥ÍÍ¥¹œ€ôÉ•ÅÕ¥É•€´Í•Ð¡™¥¹…°¤(€€€€€€€¥˜µ¥ÍÍ¥¹œè(€€€€€€€€€€€É…¥Í”]½É­™±½Ýá•ÕÑ¥½¹ÉÉ½È¡˜‰•‰…Ñ”ƒ–Þ—’ösšÖžîOšvš^Ûžòë–ÂGž*Ûš¾òiíÍ½ÉÑ•¡µ¥ÍÍ¥¹œ¥ôˆ¤((€€€€€€€É•ÑÕÉ¸•‰…Ñ•IÕ¹I•ÍÕ±Ð (€€€€€€€€€€€½¹Ñ•áÐõ™¥¹…±l‰½¹Ñ•áÐ‰t°(€€€€€€€€€€€¥¹‘•Á•¹‘•¹Ñ}É•Ù¥•ÝÌõ™¥¹…°¹•Ð ‰¥¹‘•Á•¹‘•¹Ñ}É•Ù¥•ÝÌˆ°mt¤°(€€€€€€€€€€€‘•‰…Ñ•}Á±…¸õ™¥¹…±l‰‘•‰…Ñ•}Á±…¸‰t°(€€€€€€€€€€€•áÑ•É¹…±}•Ù¥‘•¹”õ™¥¹…°¹•Ð ‰•áÑ•É¹…±}•Ù¥‘•¹”ˆ°mt¤°(€€€€€€€€€€€‘•‰…Ñ•}É•ÍÁ½¹Í•Ìõ™¥¹…°¹•Ð ‰‘•‰…Ñ•}É•ÍÁ½¹Í•Ìˆ°mt¤°(€€€€€€€€€€€Íå¹Ñ¡•Í¥Ìõ™¥¹…±l‰Íå¹Ñ¡•Í¥Ì‰t°(€€€€€€€€€€€ÍÕµµ…Éå}…‘Ù¥”õ™¥¹…°¹•Ð ‰ÍÕµµ…Éå}…‘Ù¥”ˆ¤°(€€€€€€€€€€€¡¥ÍÑ½É¥…±}Í½É•}…Í•Ìõ™¥¹…°¹•Ð ‰¡¥ÍÑ½É¥…±}Í½É•}…Í•Ìˆ°mt¤°(€€€€€€€€€€€™¥¹…±}Í½É”õ™¥¹…°¹•Ð ‰™¥¹…±}Í½É”ˆ¤°(€€€€€€€€€€€¥ÍÍÕ•Ìõ™¥¹…°¹•Ð ‰¥ÍÍÕ•Ìˆ°mt¤°(€€€€€€€€¤((€€€±…ÍÍµ•Ñ¡½(€€€‘•˜}Ù…±¥‘…Ñ•}É•Ù¥•Ý}É½Õ¹‘¥¹œ (€€€€€€€±Ì°É•Ù¥•Üè%¹‘•Á•¹‘•¹ÑI•Ù¥•Ü°½¹Ñ•áÐèI•Ù¥•Ý½¹Ñ•áÐ(€€€€¤€´ø9½¹”è(€€€€€€€™½È™¥¹‘¥¹œ¥¸É•Ù¥•Ü¹™¥¹‘¥¹Ìè(€€€€€€€€€€€±Ì¹}Ù…±¥‘…Ñ•}¡…ÁÑ•É}¥‘Ì¡™¥¹‘¥¹œ¹…™™•Ñ•‘}¡…ÁÑ•É}¥‘Ì°½¹Ñ•áÐ¤(€€€€€€€€€€€±Ì¹}Ù…±¥‘…Ñ•}Á…Á•É}•Ù¥‘•¹”¡™¥¹‘¥¹œ¹•Ù¥‘•¹”°½¹Ñ•áÐ¤((€€€±…ÍÍµ•Ñ¡½(€€€‘•˜}Ù…±¥‘…Ñ•}É•ÍÁ½¹Í•}É½Õ¹‘¥¹œ (€€€€€€€±Ì°É•ÍÁ½¹Í”è•‰…Ñ•I•ÍÁ½¹Í”°½¹Ñ•áÐèI•Ù¥•Ý½¹Ñ•áÐ(€€€€¤€´ø9½¹”è(€€€€€€€±Ì¹}Ù…±¥‘…Ñ•}Á…Á•É}•Ù¥‘•¹”¡É•ÍÁ½¹Í”¹•Ù¥‘•¹”°½¹Ñ•áÐ¤(€€€€€€€™½È™¥¹‘¥¹œ¥¸É•ÍÁ½¹Í”¹É•Ù¥Í•‘}™¥¹‘¥¹Ìè(€€€€€€€€€€€±Ì¹}Ù…±¥‘…Ñ•}¡…ÁÑ•É}¥‘Ì¡™¥¹‘¥¹œ¹…™™•Ñ•‘}¡…ÁÑ•É}¥‘Ì°½¹Ñ•áÐ¤(€€€€€€€€€€€±Ì¹}Ù…±¥‘…Ñ•}Á…Á•É}•Ù¥‘•¹”¡™¥¹‘¥¹œ¹•Ù¥‘•¹”°½¹Ñ•áÐ¤((€€€±…ÍÍµ•Ñ¡½(€€€‘•˜}Ù…±¥‘…Ñ•}Íå¹Ñ¡•Í¥Í}É½Õ¹‘¥¹œ (€€€€€€€±Ì°Íå¹Ñ¡•Í¥ÌèI•Ù¥•ÝMå¹Ñ¡•Í¥Ì°½¹Ñ•áÐèI•Ù¥•Ý½¹Ñ•áÐ(€€€€¤€´ø9½¹”è(€€€€€€€™½È™¥¹‘¥¹œ¥¸•Ñ…ÑÑÈ¡Íå¹Ñ¡•Í¥Ì¹±½‰…±}É•Ù¥•Ü°€‰É•Í½±Ù•‘}™¥¹‘¥¹Ìˆ°mt¤è(€€€€€€€€€€€±Ì¹}Ù…±¥‘…Ñ•}¡…ÁÑ•É}¥‘Ì¡™¥¹‘¥¹œ¹…™™•Ñ•‘}¡…ÁÑ•É}¥‘Ì°½¹Ñ•áÐ¤(€€€€€€€€€€€±Ì¹}Ù…±¥‘…Ñ•}Á…Á•É}•Ù¥‘•¹”¡™¥¹‘¥¹œ¹•Ù¥‘•¹”°½¹Ñ•áÐ¤((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}Ù…±¥‘…Ñ•}¡…ÁÑ•É}¥‘Ì¡¡…ÁÑ•É}¥‘Ìè±¥ÍÑmÍÑÉt°½¹Ñ•áÐèI•Ù¥•Ý½¹Ñ•áÐ¤€´ø9½¹”è(€€€€€€€­¹½Ý¸€ôí¡…ÁÑ•È¹¡…ÁÑ•É}¥™½È¡…ÁÑ•È¥¸½¹Ñ•áÐ¹¡…ÁÑ•ÉÍô(€€€€€€€Õ¹­¹½Ý¸€ôÍ½ÉÑ•¡Í•Ð¡¡…ÁÑ•É}¥‘Ì¤€´­¹½Ý¸¤(€€€€€€€¥˜Õ¹­¹½Ý¸è(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È¡˜‹¢¾–º‡žîO¢ºë–òWžR£’êšr«ž~—ž®ƒ¢*¾òiíÕ¹­¹½Ý¹ôˆ¤((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}Ù…±¥‘…Ñ•}Á…Á•É}•Ù¥‘•¹” (€€€€€€€•Ù¥‘•¹•}¥Ñ•µÌè±¥ÍÑmI•Ù¥•ÝÙ¥‘•¹•t°½¹Ñ•áÐèI•Ù¥•Ý½¹Ñ•áÐ(€€€€¤€´ø9½¹”è(€€€€€€€¡…ÁÑ•ÉÌ€ôí¡…ÁÑ•È¹¡…ÁÑ•É}¥è¡…ÁÑ•È™½È¡…ÁÑ•È¥¸½¹Ñ•áÐ¹¡…ÁÑ•ÉÍô(€€€€€€€‰±½­Ì€ôì(€€€€€€€€€€€‰±½¬¹‰±½­}¥è‰±½¬(€€€€€€€€€€€™½È‰±½¬¥¸€ (€€€€€€€€€€€€€€€½¹Ñ•áÐ¹ÍÑÉÕÑÕÉ•‘}‘½Õµ•¹Ð¹‰±½­Ì(€€€€€€€€€€€€€€€¥˜½¹Ñ•áÐ¹ÍÑÉÕÑÕÉ•‘}‘½Õµ•¹Ð¥Ì¹½Ð9½¹”(€€€€€€€€€€€€€€€•±Í”mt(€€€€€€€€€€€€¤(€€€€€€€ô((€€€€€€€‘•˜¹½Éµ…±¥é”¡Ù…±Õ”èÍÑÈ¤€´øÍÑÈè(€€€€€€€€€€€É•ÑÕÉ¸€ˆˆ¹©½¥¸¡Ù…±Õ”¹ÍÁ±¥Ð ¤¤¹…Í•™½± ¤((€€€€€€€™½È•Ù¥‘•¹”¥¸•Ù¥‘•¹•}¥Ñ•µÌè(€€€€€€€€€€€¥˜•Ù¥‘•¹”¹­¥¹¹Ù…±Õ”€„ô€‰Á…Á•Èˆè(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€¥˜¹½Ð•Ù¥‘•¹”¹¡…ÁÑ•É}¥½È•Ù¥‘•¹”¹¡…ÁÑ•É}¥¹½Ð¥¸¡…ÁÑ•ÉÌè(€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€˜‹¢ºëšZ¢¾š6¸í•Ù¥‘•¹”¹•Ù¥‘•¹•}¥‘ôƒ–þ¦†ï–òWžR£šr'šV ¡…ÁÑ•É}¥ˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€ÅÕ½Ñ”€ô¹½Éµ…±¥é”¡•Ù¥‘•¹”¹ÅÕ½Ñ”¤(€€€€€€€€€€€Í½ÕÉ”€ô¹½Éµ…±¥é”¡¡…ÁÑ•ÉÍm•Ù¥‘•¹”¹¡…ÁÑ•É}¥‘t¹½¹Ñ•¹Ð¤(€€€€€€€€€€€¥˜ÅÕ½Ñ”¹½Ð¥¸Í½ÕÉ”è(€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€˜‹¢ºëšZ¢¾š6¸í•Ù¥‘•¹”¹•Ù¥‘•¹•}¥‘ôƒžj–òWšZš^ƒšÎW–r£ž®ƒ¢*–:šZ’â·–ºk’ö4ˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€‰±½¬€ô‰±½­Ì¹•Ð¡•Ù¥‘•¹”¹‰±½­}¥¤¥˜•Ù¥‘•¹”¹‰±½­}¥•±Í”9½¹”(€€€€€€€€€€€¥˜•Ù¥‘•¹”¹‰±½­}¥…¹‰±½¬¥Ì9½¹”è(€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€˜‹¢ºëšZ¢¾š6¸í•Ù¥‘•¹”¹•Ù¥‘•¹•}¥‘ôƒ–òWžR£’êšr«ž~”‰±½­}¥ˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜‰±½¬¥Ì9½¹”…¹‰±½­Ìè(€€€€€€€€€€€€€€€µ…Ñ¡•Ì€ôl(€€€€€€€€€€€€€€€€€€€¥Ñ•´™½È¥Ñ•´¥¸‰±½­Ì¹Ù…±Õ•Ì ¤(€€€€€€€€€€€€€€€€€€€¥˜¥Ñ•´¹¡…ÁÑ•É}¥€ôô•Ù¥‘•¹”¹¡…ÁÑ•É}¥(€€€€€€€€€€€€€€€€€€€…¹¥Ñ•´¹Ñ•áÐ(€€€€€€€€€€€€€€€€€€€…¹ÅÕ½Ñ”¥¸¹½Éµ…±¥é”¡¥Ñ•´¹Ñ•áÐ¤(€€€€€€€€€€€€€€€t(€€€€€€€€€€€€€€€¥˜±•¸¡µ…Ñ¡•Ì¤€ôô€Äè(€€€€€€€€€€€€€€€€€€€‰±½¬€ôµ…Ñ¡•ÍlÁt(€€€€€€€€€€€¥˜‰±½¬¥Ì¹½Ð9½¹”è(€€€€€€€€€€€€€€€¥˜‰±½¬¹¡…ÁÑ•É}¥€„ô•Ù¥‘•¹”¹¡…ÁÑ•É}¥è(€€€€€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€€€€€˜‹¢ºëšZ¢¾š6¸í•Ù¥‘•¹”¹•Ù¥‘•¹•}¥‘ôƒžj‰±½­}¥ƒ’â8¡…ÁÑ•É}¥ƒ’â7’â¢Ðˆ(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€¥˜‰±½¬¹Ñ•áÐ…¹ÅÕ½Ñ”¹½Ð¥¸¹½Éµ…±¥é”¡‰±½¬¹Ñ•áÐ¤è(€€€€€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È (€€€€€€€€€€€€€€€€€€€€€€€˜‹¢ºëšZ¢¾š6¸í•Ù¥‘•¹”¹•Ù¥‘•¹•}¥‘ôƒžj–òWšZš^ƒšÎW–r£š2–ºk––ºç–v_’â·–ºk’ö4ˆ(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€•Ù¥‘•¹”¹‰±½­}¥€ô‰±½¬¹‰±½­}¥(€€€€€€€€€€€€€€€•Ù¥‘•¹”¹¡Õ¹­}¥€ô‰±½¬¹¡Õ¹­}¥(€€€€€€€€€€€€€€€•Ù¥‘•¹”¹Á…•}¹Õµ‰•È€ô‰±½¬¹Á…•}¹Õµ‰•È(€€€€€€€€€€€€€€€•Ù¥‘•¹”¹‰‰½à€ô‰±½¬¹‰‰½à((€€€‘•˜ÉÕ¸¡Í•±˜°É•Ù¥•Ý}¥¹ÁÕÐè•‰…Ñ•I•Ù¥•Ý%¹ÁÕÐð‘¥ÑmÍÑÈ°¹åt¤€´ø•‰…Ñ•IÕ¹I•ÍÕ±Ðè(€€€€€€€€ˆˆ‹–B3š¶—–—–>¾òo–òš¶—–êSžR£¢¾ß¢ÂžR …ÉÕ»Žˆˆˆ((€€€€€€€ÑÉäè(€€€€€€€€€€€…Íå¹¥¼¹•Ñ}ÉÕ¹¹¥¹}±½½À ¤(€€€€€€€•á•ÁÐIÕ¹Ñ¥µ•ÉÉ½Èè(€€€€€€€€€€€É•ÑÕÉ¸…Íå¹¥¼¹ÉÕ¸¡Í•±˜¹…ÉÕ¸¡É•Ù¥•Ý}¥¹ÁÕÐ¤¤(€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‹šŽšÖ/–"Ãš¶–r£¢þC¢†3žj’ê/’îÛ–ú«ž:¿¾ò3¢¾ßšRçžR …Ý…¥ÐÝ½É­™±½Ü¹…ÉÕ¸ ¸¸¸¤€ˆ¤(()‘•˜‰Õ¥±‘}Ý½É­™±½Ü¡ÉÕ¹Ñ¥µ”èÍÑÈ€ô€‰‘•µ¼ˆ¤€´ø•‰…Ñ•]½É­™±½Üè(€€€€ˆˆ‹š2'¢þC¢†3š¢‡–ò?šz¦€•‰…Ñ”ƒ–Þ—’ösšÖŽ((€€€€´‘•µ½ƒ¾òkž†»–ºkšœ•µ¼•¹Ó¾ò3žR£’ê;šÖ/¢¾W–J3–n{–öK–~ëžêÿ¾òl(€€€€´É•…±ƒ¾òkžr–º{š¢‡–z/¦¦Ç–*£žj•¹Ó¾ò3žR£’ê;žR’êŸ¢¾–º‡Ž(€€€€ˆˆˆ((€€€¥˜ÉÕ¹Ñ¥µ”€ôô€‰É•…°ˆè(€€€€€€€É•ÑÕÉ¸•‰…Ñ•]½É­™±½Ü¹É•…° ¤(€€€¥˜ÉÕ¹Ñ¥µ”€ôô€‰‘•µ¼ˆè(€€€€€€€É•ÑÕÉ¸•‰…Ñ•]½É­™±½Ü¹‘•™…Õ±Ð ¤(€€€É…¥Í”Y…±Õ•ÉÉ½È¡˜‹šr«ž~—žj•‰…Ñ”ƒ¢þC¢†3š¢‡–ò<èíÉÕ¹Ñ¥µ•ôˆ¤