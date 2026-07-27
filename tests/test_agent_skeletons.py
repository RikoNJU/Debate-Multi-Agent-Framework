from backend.env import ModelResponse
from debate_agent_framework.agents import (
    DebateContextPlannerAgent,
    DebateReviewChairAgent,
    EmpiricalEvidenceSpecialistAgent,
    GlobalQualitySpecialistAgent,
    ScientificSoundnessSpecialistAgent,
)
from debate_agent_framework.models import (
    ChapterInput,
    DebateReviewInput,
    PaperType,
    ReviewContext,
)


class FakeModelClient:
    def complete(self, messages, *, options=None):  # type: ignore[no-untyped-def]
        return ModelResponse(content='{"issues": [], "questions": []}')


def test_agent_skeletons_are_importable():
    assert DebateContextPlannerAgent().__class__.__name__ == "DebateContextPlannerAgent"
    assert ScientificSoundnessSpecialistAgent().role.value == "scientific_soundness"
    assert EmpiricalEvidenceSpecialistAgent().role.value == "empirical_evidence"
    assert GlobalQualitySpecialistAgent().role.value == "global_quality"
    assert DebateReviewChairAgent().__class__.__name__ == "DebateReviewChairAgent"


def test_review_chair_agent_parses_debate_plan():
    agent = DebateReviewChairAgent(model_client=FakeModelClient())
    chapter = ChapterInput(
        chapter_id="C1",
        chapter_name="第一章 绪论",
        stage="引言/绪论",
        content="本章说明研究背景。",
    )
    review_input = DebateReviewInput(
        paper_id="paper-1",
        title="测试论文",
        full_text="本章说明研究背景。",
        paper_type=PaperType.METHOD,
        chapters=[chapter],
    )
    context = ReviewContext(
        paper_id=review_input.paper_id,
        profile={
            "title": review_input.title,
            "paper_type": review_input.paper_type,
            "research_problem": review_input.title,
            "global_summary": review_input.title,
        },
        full_text=review_input.full_text,
        chapters=[chapter],
    )

    plan = agent.plan_debate(context, reviews=[])

    assert plan.issues == []
    assert plan.questions == []
