"""将具体 Specialist、Chair、RAG 和原流程装配为 Debate 工作流。"""

from debate_agent_framework.agents import (
    DemoContextPlanner,
    DemoEvidenceRetriever,
    DemoHistoricalScoreRetriever,
    DemoOriginalPipelineAdapter,
    DemoReviewChair,
    DemoSpecialist,
)
from debate_agent_framework.models import SpecialistRole

from .debate import DebateWorkflow
from .state import DebateWorkflowServices


def build_debate_workflow() -> DebateWorkflow:
    """V0 使用 Demo 实现；生产环境在此替换真实 Agent 与原流程适配器。"""

    return DebateWorkflow(
        DebateWorkflowServices(
            context_planner=DemoContextPlanner(),
            specialists={role: DemoSpecialist(role) for role in SpecialistRole},
            review_chair=DemoReviewChair(),
            evidence_retriever=DemoEvidenceRetriever(),
            historical_score_retriever=DemoHistoricalScoreRetriever(),
            original_pipeline=DemoOriginalPipelineAdapter(),
        )
    )
