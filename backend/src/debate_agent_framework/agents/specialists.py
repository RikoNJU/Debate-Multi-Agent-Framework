"""Debate 三个专业评审 Agent 的代码骨架。"""

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

    role: SpecialistRole

    def __init__(self, model_client: ModelClient | None = None) -> None:
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


class ScientificSoundnessSpecialistAgent(DebateSpecialistAgent):
    """评价理论基础、方法合理性、推导和结论一致性。"""

    role = SpecialistRole.SCIENTIFIC_SOUNDNESS


class EmpiricalEvidenceSpecialistAgent(DebateSpecialistAgent):
    """评价实验设计、数据、Baseline、消融和可复现性。"""

    role = SpecialistRole.EMPIRICAL_EVIDENCE


class GlobalQualitySpecialistAgent(DebateSpecialistAgent):
    """评价全文结构、章节关系、工作量和表达质量。"""

    role = SpecialistRole.GLOBAL_QUALITY
