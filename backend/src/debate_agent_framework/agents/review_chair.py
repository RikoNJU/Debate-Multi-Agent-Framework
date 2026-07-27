"""Debate Review Chair 主 Agent。

Review Chair 是 Debate 工作流中的主 Agent，负责把多个 Specialist 的独立
评审组织成有方向的争议讨论，并在最后生成原 Step 4/5 兼容的综合输出。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from backend.env import ChatMessage, ModelCallOptions, ModelClient
from ..schemas import (
    DebatePlan,
    DebateResponse,
    IndependentReview,
    ReviewContext,
    ReviewEvidence,
    ReviewSynthesis,
)
from ..ports import ReviewChair


class DebateReviewChairAgent(ReviewChair):
    """负责争议识别、定向路由、证据综合和最终裁决。

    它不替代三个 Specialist 做专业初审，而是完成更高层的协作控制：

    1. 归并重复问题；
    2. 找出方法、实验、结构等视角之间的冲突；
    3. 判断哪些争议需要外部证据；
    4. 生成发给指定 Specialist 的 Debate 问题；
    5. 综合原文、初审、回应和外部证据，形成最终裁决。
    """

    def __init__(
        self,
        model_client: ModelClient | None = None,
        *,
        temperature: float = 0.2,
    ) -> None:
        self.model_client = model_client
        self.temperature = temperature

    def plan_debate(
        self,
        context: ReviewContext,
        reviews: Sequence[IndependentReview],
    ) -> DebatePlan:
        """根据独立初审结果生成 Debate 计划。

        该函数只决定“哪些问题值得进入 Debate、问谁、为什么问”。它不直接
        修改 Specialist 的初审结论，也不提前给出最终裁决。
        """

        payload = {
            "context": context.model_dump(mode="json"),
            "independent_reviews": [
                item.model_dump(mode="json") for item in reviews
            ],
        }
        data = self._complete_json(
            system_prompt=self._system_prompt(),
            user_prompt=(
                "请识别独立评审中的关键争议、遗漏和证据缺口，输出 DebatePlan JSON。"
                "没有必要争议时，issues 和 questions 可以为空。"
            ),
            payload=payload,
        )
        return self._validate_plan(data)

    def synthesize(
        self,
        context: ReviewContext,
        *,
        reviews: Sequence[IndependentReview],
        debate_plan: DebatePlan,
        responses: Sequence[DebateResponse],
        external_evidence: Sequence[ReviewEvidence],
    ) -> ReviewSynthesis:
        """综合 Debate 结果并生成原流程兼容输出。

        最终输出要同时服务两个目标：一是形成全文级评审裁决，二是保持原
        Step 4/5 的字段结构，让后续 Step 6/7 可以继续复用。
        """

        payload = {
            "context": context.model_dump(mode="json"),
            "independent_reviews": [
                item.model_dump(mode="json") for item in reviews
            ],
            "debate_plan": debate_plan.model_dump(mode="json"),
            "responses": [item.model_dump(mode="json") for item in responses],
            "external_evidence": [
                item.model_dump(mode="json") for item in external_evidence
            ],
        }
        data = self._complete_json(
            system_prompt=self._system_prompt(),
            user_prompt=(
                "请综合原文、独立初审、Debate 回应和外部证据，输出 ReviewSynthesis JSON。"
                "输出必须包含 global_review、chapter_evaluation 和 workload_evaluation，"
                "并保持原 Step 4/5 兼容字段。"
            ),
            payload=payload,
        )
        return self._validate_synthesis(data)

    def _complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """调用统一模型客户端，并把回复解析为 JSON dict。"""

        if self.model_client is None:
            raise NotImplementedError("DebateReviewChairAgent 需要注入 ModelClient")

        response = self.model_client.complete(
            [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(
                    role="user",
                    content=f"{user_prompt}\n\n输入数据：\n{json.dumps(payload, ensure_ascii=False)}",
                ),
            ],
            options=ModelCallOptions(
                temperature=self.temperature,
                response_format={"type": "json_object"},
            ),
        )
        try:
            data = json.loads(response.content)
        except json.JSONDecodeError as exc:
            raise ValueError("DebateReviewChairAgent 返回内容不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("DebateReviewChairAgent 返回 JSON 顶层必须是对象")
        return data

    @staticmethod
    def _validate_plan(data: dict[str, Any]) -> DebatePlan:
        """校验 Chair 生成的争议路由计划。"""

        try:
            return DebatePlan.model_validate(data)
        except ValidationError as exc:
            raise ValueError("DebateReviewChairAgent 输出不符合 DebatePlan") from exc

    @staticmethod
    def _validate_synthesis(data: dict[str, Any]) -> ReviewSynthesis:
        """校验最终综合评审，防止破坏 Step 4/5 兼容结构。"""

        try:
            return ReviewSynthesis.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                "DebateReviewChairAgent 输出不符合 ReviewSynthesis"
            ) from exc

    @staticmethod
    def _system_prompt() -> str:
        """Review Chair 的稳定系统职责说明。"""

        return (
            "你是论文评审 Debate Multi-Agent 系统的 Review Chair。"
            "你负责汇总独立评审、识别关键争议、生成定向质疑、综合证据并形成最终裁决。"
            "你不能用简单多数投票替代判断，也不能凭空增加原文或外部证据。"
            "最终输出必须严格符合调用方要求的 JSON schema，并保持原评审流程兼容。"
        )
