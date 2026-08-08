"""Debate Review Chair 主 Agent。

Review Chair 是 Debate 工作流中的主 Agent，负责把多个 Specialist 的独立
评审组织成有方向的争议讨论，并在最后生成原 Step 4/5 兼容的综合输出。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from backend.env import ModelClient
from ..schemas import (
    DebatePlan,
    DebateResponse,
    GlobalReview,
    IndependentReview,
    ReviewContext,
    ReviewEvidence,
)
from .compat import assemble_review_synthesis
from .json_client import complete_json, review_context_payload
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
            "context": review_context_payload(context),
            "independent_reviews": [
                item.model_dump(mode="json") for item in reviews
            ],
        }
        data = self._complete_json(
            system_prompt=self._system_prompt(),
            user_prompt=(
                "请识别独立评审中的关键争议、遗漏和证据缺口，输出 DebatePlan JSON。"
                "每个 issue 的 participating_roles 必须是至少两个不同角色的列表；"
                "每个 question 的 target_role 必须属于其所属 issue 的"
                "participating_roles。没有必要争议时，issues 和 questions 可以为空。"
            ),
            payload=payload,
            schema=DebatePlan.model_json_schema(),
        )
        return self._validate_plan(data, reviews)

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

        Review Chair 只让模型产出判断部分 ``GlobalReview``，章节评价和工作量
        评价等原 Step 4/5 兼容结构由确定性装配完成，保证字段结构稳定。
        """

        payload = {
            "context": review_context_payload(context),
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
                "请综合原文、独立初审、Debate 回应和外部证据，输出 GlobalReview JSON。"
                "resolved_findings 必须逐条给出证据和最终判断，不能使用多数投票；"
                "高严重度且无证据的问题必须标记为 insufficient 或 human_review 并降低置信度。"
            ),
            payload=payload,
            schema=GlobalReview.model_json_schema(),
        )
        global_review = self._validate_global_review(data)
        return assemble_review_synthesis(context, global_review)

    def _complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """调用统一模型客户端并解析 JSON，最终由 complete_json 完成。"""

        if self.model_client is None:
            raise NotImplementedError("DebateReviewChairAgent 需要注入 ModelClient")
        data = complete_json(
            self.model_client,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            payload=payload,
            schema=schema,
            temperature=self.temperature,
        )
        return data

    @classmethod
    def _validate_plan(
        cls,
        data: dict[str, Any],
        reviews: Sequence[IndependentReview],
    ) -> DebatePlan:
        """校验 Chair 生成的争议路由计划。

        模型的跨字段约束（参与角色数量、question 引用、target_role 归属）容易
        出错，这里先做一次结构修复：用独立初审把 ``finding_id -> role`` 补全，
        再丢弃仍不合规的 issue 与 question，最后才校验。这样模型只要大致给出
        争议方向，就不会因为个别字段不合规而整轮失败。
        """

        repaired = cls._repair_plan(data, reviews)
        try:
            return DebatePlan.model_validate(repaired)
        except ValidationError as exc:
            raise ValueError(
                f"DebateReviewChairAgent 输出不符合 DebatePlan：{exc}"
            ) from exc

    @staticmethod
    def _repair_plan(
        data: dict[str, Any],
        reviews: Sequence[IndependentReview],
    ) -> dict[str, Any]:
        finding_role: dict[str, str] = {
            finding.finding_id: review.role.value
            for review in reviews
            for finding in review.findings
        }

        issues = data.get("issues") or []
        questions = data.get("questions") or []

        issues_by_id: dict[str, dict[str, Any]] = {}
        for issue in issues:
            roles = list(
                dict.fromkeys(issue.get("participating_roles") or [])
            )
            for finding_id in issue.get("conflicting_finding_ids") or []:
                role = finding_role.get(finding_id)
                if role and role not in roles:
                    roles.append(role)
            issue["participating_roles"] = roles
            issues_by_id[issue["issue_id"]] = issue

        kept_questions: list[dict[str, Any]] = []
        for question in questions:
            issue = issues_by_id.get(question.get("issue_id"))
            if issue is None:
                continue
            target_role = question.get("target_role")
            if target_role and target_role not in issue["participating_roles"]:
                issue["participating_roles"].append(target_role)
            kept_questions.append(question)
        questions = kept_questions

        valid_issues = [
            issue
            for issue in issues
            if len(set(issue["participating_roles"])) >= 2
        ]
        valid_issue_ids = {issue["issue_id"] for issue in valid_issues}
        questions = [
            question
            for question in questions
            if question["issue_id"] in valid_issue_ids
        ]
        return {"issues": valid_issues, "questions": questions}

    @staticmethod
    def _validate_global_review(data: dict[str, Any]) -> GlobalReview:
        """校验 Chair 生成的最终裁决判断部分。"""

        try:
            return GlobalReview.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                "DebateReviewChairAgent 输出不符合 GlobalReview"
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
