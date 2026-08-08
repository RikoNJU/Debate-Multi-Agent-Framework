"""Debate LangGraph 的共享状态和运行时配置。"""

from __future__ import annotations

from dataclasses import dataclass
from operator import add
from typing import Annotated, TypedDict

from ..schemas import (
    ComprehensiveScoreResult,
    DebatePlan,
    DebateResponse,
    DebateReviewInput,
    DebateWorkflowIssue,
    HistoricalScoreCase,
    IndependentReview,
    ReviewContext,
    ReviewEvidence,
    ReviewSynthesis,
    SummaryAdviceResult,
)
from ..ports import (
    ContextPlanner,
    EvidenceRetriever,
    HistoricalAdviceRetriever,
    HistoricalScoreRetriever,
    OriginalPipelineAdapter,
    ReviewChair,
    SpecialistRegistry,
)


class DebateState(TypedDict, total=False):
    review_input: DebateReviewInput
    context: ReviewContext
    independent_reviews: list[IndependentReview]
    debate_plan: DebatePlan
    external_evidence: list[ReviewEvidence]
    debate_responses: list[DebateResponse]
    synthesis: ReviewSynthesis
    summary_advice: SummaryAdviceResult
    historical_score_cases: list[HistoricalScoreCase]
    final_score: ComprehensiveScoreResult
    issues: Annotated[list[DebateWorkflowIssue], add]


@dataclass(frozen=True)
class DebateWorkflowConfig:
    """V0 固定一轮 Debate，只暴露成本与降级相关参数。"""

    max_concurrency: int = 3
    minimum_independent_reviews: int = 2
    evidence_limit: int = 8
    historical_advice_limit_per_chapter: int = 5
    historical_case_limit: int = 5

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrency <= 3:
            raise ValueError("max_concurrency 必须位于 1 到 3 之间")
        if not 1 <= self.minimum_independent_reviews <= 3:
            raise ValueError("minimum_independent_reviews 必须位于 1 到 3 之间")
        if self.evidence_limit < 1:
            raise ValueError("evidence_limit 必须至少为 1")
        if self.historical_advice_limit_per_chapter < 1:
            raise ValueError("historical_advice_limit_per_chapter 必须至少为 1")
        if self.historical_case_limit < 1:
            raise ValueError("historical_case_limit 必须至少为 1")


@dataclass(frozen=True)
class DebateWorkflowServices:
    context_planner: ContextPlanner
    specialists: SpecialistRegistry
    review_chair: ReviewChair
    original_pipeline: OriginalPipelineAdapter
    evidence_retriever: EvidenceRetriever | None = None
    historical_advice_retriever: HistoricalAdviceRetriever | None = None
    historical_score_retriever: HistoricalScoreRetriever | None = None
