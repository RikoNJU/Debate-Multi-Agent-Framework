"""Debate 工作流的可替换 Agent、RAG 和原流程接口。"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from typing import Protocol, TypeAlias, TypeVar

from .schemas import (
    ComprehensiveScoreResult,
    DebateIssue,
    DebatePlan,
    DebateQuestion,
    DebateResponse,
    DebateReviewInput,
    HistoricalScoreCase,
    IndependentReview,
    ReviewContext,
    ReviewEvidence,
    ReviewSynthesis,
    ScoreCalibrationQuery,
    SpecialistRole,
    SummaryAdviceResult,
)

T = TypeVar("T")
MaybeAwaitable: TypeAlias = T | Awaitable[T]


class ContextPlanner(Protocol):
    """决定使用全文还是语义完整内容包。"""

    def build(self, review_input: DebateReviewInput) -> MaybeAwaitable[ReviewContext]:
        ...


class SpecialistAgent(Protocol):
    """一个具备全文视角的专业评审 Agent。"""

    def review(self, context: ReviewContext) -> MaybeAwaitable[IndependentReview]:
        """独立初审，接口不接收其他 Agent 的意见。"""

    def respond(
        self,
        context: ReviewContext,
        *,
        own_review: IndependentReview,
        issue: DebateIssue,
        question: DebateQuestion,
        peer_reviews: Sequence[IndependentReview],
        external_evidence: Sequence[ReviewEvidence],
    ) -> MaybeAwaitable[DebateResponse]:
        """只回应 Chair 定向发送的争议问题。"""


class ReviewChair(Protocol):
    """负责争议路由、证据综合和最终裁决。"""

    def plan_debate(
        self,
        context: ReviewContext,
        reviews: Sequence[IndependentReview],
    ) -> MaybeAwaitable[DebatePlan]:
        ...

    def synthesize(
        self,
        context: ReviewContext,
        *,
        reviews: Sequence[IndependentReview],
        debate_plan: DebatePlan,
        responses: Sequence[DebateResponse],
        external_evidence: Sequence[ReviewEvidence],
    ) -> MaybeAwaitable[ReviewSynthesis]:
        ...


class EvidenceRetriever(Protocol):
    """只为需要外部事实的 Debate 问题提供证据。"""

    def retrieve(
        self,
        queries: Sequence[str],
        *,
        context: ReviewContext,
        limit: int,
    ) -> MaybeAwaitable[Sequence[ReviewEvidence]]:
        ...


class HistoricalScoreRetriever(Protocol):
    """在事实评审完成后检索可比历史评分案例。"""

    def retrieve(
        self,
        query: ScoreCalibrationQuery,
        *,
        limit: int,
    ) -> MaybeAwaitable[Sequence[HistoricalScoreCase]]:
        ...


class OriginalPipelineAdapter(Protocol):
    """复用原 Step 6 和 Step 7 的适配边界。"""

    def summarize_advice(
        self,
        review_input: DebateReviewInput,
        synthesis: ReviewSynthesis,
    ) -> MaybeAwaitable[SummaryAdviceResult]:
        ...

    def score(
        self,
        review_input: DebateReviewInput,
        synthesis: ReviewSynthesis,
        *,
        summary_advice: SummaryAdviceResult,
        historical_cases: Sequence[HistoricalScoreCase],
    ) -> MaybeAwaitable[ComprehensiveScoreResult]:
        ...


SpecialistRegistry: TypeAlias = Mapping[SpecialistRole, SpecialistAgent]
