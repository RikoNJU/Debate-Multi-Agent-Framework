"""Debate 专业评审 Agent 的代码骨架。"""

from __future__ import annotations

from collections.abc import Sequence

from backend.env import ModelClient
from ..schemas import (
    DebateIssue,
    DebateQuestion,
    DebateResponse,
    IndependentReview,
    ReviewContext,
    ReviewEvidence,
    SpecialistRole,
)
from ..ports import SpecialistAgent


class DebateSpecialistAgent(SpecialistAgent):
    """专业评审 Agent 基类，保持全文视角但关注不同评价维度。"""

    # 后续实现时应配置多个 Specialist，让不同专家分别负责不同评审维度。
    def __init__(
        self,
        role: SpecialistRole,
        model_client: ModelClient | None = None,
    ) -> None:
        self.role = role
        self.model_client = model_client

    def review(self, context: ReviewContext) -> IndependentReview:
        raise NotImplementedError(
            f"{self.__class__.__name__}.review 还未接入真实评审逻辑"
        )

    def respond(
        self,
        context: ReviewContext,
        *,
        own_review: IndependentReview,
        issue: DebateIssue,
        question: DebateQuestion,
        peer_reviews: Sequence[IndependentReview],
        external_evidence: Sequence[ReviewEvidence],
    ) -> DebateResponse:
        raise NotImplementedError(
            f"{self.__class__.__name__}.respond 还未接入真实 Debate 回应逻辑"
        )
