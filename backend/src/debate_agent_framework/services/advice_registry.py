"""Discipline-level physical routing for historical-advice retrieval."""

from __future__ import annotations

import os

from ..schemas.domain import DebateReviewInput, FindingAdviceItem, ReviewSynthesis
from ..skills.models import ResolvedReviewProfile
from .clean_advice import CleanAdviceRetriever, build_clean_advice_retriever_from_env


class DisciplineAdviceRetrieverRegistry:
    """Select one physical Dense/BM25 corpus by discipline, then apply type overlay."""

    def __init__(self, retrievers: dict[str, CleanAdviceRetriever]) -> None:
        self._retrievers = dict(retrievers)

    async def retrieve_from_findings(
        self,
        synthesis: ReviewSynthesis,
        review_input: DebateReviewInput,
        review_profile: ResolvedReviewProfile | None = None,
    ) -> list[FindingAdviceItem]:
        if review_profile is None:
            raise ValueError("historical-advice routing requires a resolved review profile")
        retriever = self._retrievers.get(review_profile.discipline_id)
        if retriever is None:
            raise ValueError(
                f"no physical historical-advice route for discipline "
                f"{review_profile.discipline_id!r}"
            )
        return await retriever.retrieve_from_findings(
            synthesis, review_input, review_profile
        )


def build_advice_registry_from_env() -> DisciplineAdviceRetrieverRegistry | None:
    """Build currently available physical routes without pretending draft routes exist."""

    retriever = build_clean_advice_retriever_from_env()
    if retriever is None:
        return None
    discipline_id = os.getenv(
        "DEBATE_V2_DISCIPLINE_ID", "artificial_intelligence"
    )
    return DisciplineAdviceRetrieverRegistry({discipline_id: retriever})
