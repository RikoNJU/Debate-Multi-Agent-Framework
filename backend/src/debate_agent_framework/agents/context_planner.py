"""Debate 上下文构造 Agent 的代码骨架。"""

from __future__ import annotations

from backend.env import ModelClient
from ..models import DebateReviewInput, ReviewContext
from ..ports import ContextPlanner


class DebateContextPlannerAgent(ContextPlanner):
    """负责把原流程输入整理为全文上下文或语义内容包。"""

    def __init__(self, model_client: ModelClient | None = None) -> None:
        self.model_client = model_client

    def build(self, review_input: DebateReviewInput) -> ReviewContext:
        raise NotImplementedError(
            "DebateContextPlannerAgent.build 还未接入真实上下文构造逻辑"
        )
