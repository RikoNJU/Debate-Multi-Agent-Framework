"""Debate 专业评审 Agent：按角色接入真实模型。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

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
from .json_client import complete_json, review_context_payload

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "specialists"


class DebateSpecialistAgent(SpecialistAgent):
    """按角色配置的真实专业评审 Agent，保持全文视角但关注不同评价维度。

    每个角色从 ``prompts/specialists/<role>.md`` 读取系统提示，并通过
    ``backend.env.ModelClient`` 调用统一模型，输出经过 Pydantic 校验。
    """

    def __init__(
        self,
        role: SpecialistRole,
        model_client: ModelClient | None = None,
        *,
        temperature: float = 0.2,
    ) -> None:
        self.role = role
        self.model_client = model_client
        self.temperature = temperature

    def review(self, context: ReviewContext) -> IndependentReview:
        if self.model_client is None:
            raise NotImplementedError(
                "DebateSpecialistAgent 需要注入 ModelClient"
            )

        payload = {"context": review_context_payload(context)}
        data = complete_json(
            self.model_client,
            system_prompt=self._system_prompt(),
            user_prompt=(
                "请以本角色视角独立完成论文初审，输出 IndependentReview JSON。"
                "review_id、paper_summary、strengths、findings、author_questions 和 "
                "confidence 由你根据论文内容生成；role 必须使用 schema 中给出的枚举。"
                "findings 中的 evidence 引用论文原文章节，severity 为 fatal/major 的"
                "问题必须附带可追溯的论文证据。存在 structured_document 时，优先填写"
                "对应的 block_id；系统将校正 chunk_id、page_number 和 bbox。"
            ),
            payload=payload,
            schema=IndependentReview.model_json_schema(),
            temperature=self.temperature,
        )
        try:
            review = IndependentReview.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                "DebateSpecialistAgent 输出不符合 IndependentReview"
            ) from exc
        review.role = self.role
        return review

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
        if self.model_client is None:
            raise NotImplementedError(
                "DebateSpecialistAgent 需要注入 ModelClient"
            )

        payload = {
            "context": review_context_payload(context),
            "own_review": own_review.model_dump(mode="json"),
            "issue": issue.model_dump(mode="json"),
            "question": question.model_dump(mode="json"),
            "peer_reviews": [
                item.model_dump(mode="json") for item in peer_reviews
            ],
            "external_evidence": [
                item.model_dump(mode="json") for item in external_evidence
            ],
        }
        data = complete_json(
            self.model_client,
            system_prompt=self._system_prompt(),
            user_prompt=(
                "请回应 Review Chair 定向发送的争议问题，输出 DebateResponse JSON。"
                "response_id、response、position、revised_findings 和 confidence 由你"
                "生成；role、issue_id、question_id 必须与输入中的 question 保持一致。"
                "只有当问题要求外部证据时，才把 external_evidence 放入 evidence 字段。"
            ),
            payload=payload,
            schema=DebateResponse.model_json_schema(),
            temperature=self.temperature,
        )
        try:
            response = DebateResponse.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                "DebateSpecialistAgent 输出不符合 DebateResponse"
            ) from exc
        response.role = self.role
        response.issue_id = question.issue_id
        response.question_id = question.question_id
        return response

    def _system_prompt(self) -> str:
        prompt_file = _PROMPTS_DIR / f"{self.role.value}.md"
        return prompt_file.read_text(encoding="utf-8")
