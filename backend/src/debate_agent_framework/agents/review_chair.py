"""Debate Review Chair 主 Agent。

Review Chair 是 Debate 工作流中的主 Agent，负责把多个 Specialist 的独立
评审组织成有方向的争议讨论，并在最后生成原 Step 4/5 兼容的综合输出。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.env import ModelClient
from ..schemas import (
    DebatePlan,
    DebateResponse,
    DimensionEvaluation,
    FindingSeverity,
    GlobalReview,
    IndependentReview,
    ResolvedFinding,
    ReviewContext,
    ReviewEvidence,
    SpecialistRole,
)
from .compat import assemble_review_synthesis
from .json_client import complete_json, review_context_payload
from ..ports import ReviewChair

# 综合裁决需要为每个章节输出评估与证据锚定，是全流程最长的输出；
# 思考模型的思考 token 也计入输出上限，默认值需留足余量。
DEFAULT_CHAIR_MAX_TOKENS = 16384

# 每篇论文最多进入 Debate 的议题数。
DEBATE_MAX_ISSUES = 3

# 0 议题冷启动时强制开辩论的议题数。
COLD_START_MAX_ISSUES = 2

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {
    FindingSeverity.FATAL: 3,
    FindingSeverity.MAJOR: 2,
    FindingSeverity.MINOR: 1,
    FindingSeverity.INFO: 0,
}
_STOP_TOKENS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "it", "this", "that", "论文",
    "问题", "存在", "本文", "其", "中", "与", "及", "有", "等", "以及",
}


def _claim_tokens(text: str) -> set[str]:
    tokens = {
        token
        for token in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", text.lower())
        if len(token) >= 2 and token not in _STOP_TOKENS
    }
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _finding_match_score(
    left: Any, right: Any, left_role: SpecialistRole, right_role: SpecialistRole
) -> float:
    """跨专家初审判定的匹配度：章节重叠最强，其次维度一致，再其次措辞重叠。"""

    chapter_overlap = len(
        set(left.affected_chapter_ids) & set(right.affected_chapter_ids)
    )
    dimension_match = 1.0 if left.dimension == right.dimension else 0.0
    wording_overlap = _jaccard(
        _claim_tokens(left.claim), _claim_tokens(right.claim)
    )
    return 2.0 * chapter_overlap + 1.0 * dimension_match + 0.5 * wording_overlap


def _wrap(text: str, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip(",;：。，；") + "…"


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
        temperature: float = 0.05,
    ) -> None:
        self.model_client = model_client
        self.temperature = temperature
        self.synthesize_max_tokens = int(
            os.getenv("DEBATE_CHAIR_MAX_TOKENS", str(DEFAULT_CHAIR_MAX_TOKENS))
        )

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
            system_prompt=self._system_prompt(context),
            user_prompt=(
                "请识别独立评审中的关键争议、遗漏和证据缺口，输出 DebatePlan JSON。"
                "最多输出 3 个议题（issue）；若识别出更多争议，按争议优先级保留"
                "最重要的前 3 个，其余丢弃。每个 issue 的 participating_roles "
                "必须是至少两个不同角色的列表；每个 question 的 target_role "
                "必须属于其所属 issue 的 participating_roles。"
                "没有必要争议时，issues 和 questions 可以为空。"
            ),
            payload=payload,
            schema=DebatePlan.model_json_schema(),
        )
        plan = self._validate_plan(data, reviews)
        if not plan.issues:
            logger.warning(
                "Chair 未产出任何争议议题，触发冷启动兜底："
                "用独立初审分歧最大的 %d 个点强制开启 Debate",
                COLD_START_MAX_ISSUES,
            )
            return self._cold_start_fallback(reviews)
        if plan.issues and not plan.questions:
            logger.warning(
                "Chair 产出了 %d 个议题但没有问题，为每个议题补一个定向问题",
                len(plan.issues),
            )
            return self._backfill_questions(plan)
        return plan

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
            system_prompt=self._system_prompt(context),
            user_prompt=(
                "请综合原文、独立初审、Debate 回应和外部证据，输出 GlobalReview JSON。"
                "overall_summary 必填：用 2-4 句话概括论文整体质量和核心缺陷。"
                "confidence 必填：给出综合置信度分数 0.0-1.0。"
                "resolved_findings 必须逐条给出证据和最终判断，不能使用多数投票；"
                "裁决已有问题时应保留独立初审中的 finding_id，以便固定评审小项追踪；"
                "高严重度且无证据的问题必须标记为 insufficient 或 human_review 并降低 confidence。"
            ),
            payload=payload,
            schema=GlobalReview.model_json_schema(),
            max_tokens=self.synthesize_max_tokens,
        )
        global_review = self._validate_global_review(
            self._repair_global_review(data)
        )
        return assemble_review_synthesis(context, global_review, reviews=reviews)

    def _complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        max_tokens: int = 4096,
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
            max_tokens=max_tokens,
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
        valid_issues.sort(key=lambda issue: issue.get("priority", 5))
        valid_issues = valid_issues[:DEBATE_MAX_ISSUES]
        valid_issue_ids = {issue["issue_id"] for issue in valid_issues}
        questions = [
            question
            for question in questions
            if question["issue_id"] in valid_issue_ids
        ]
        return {"issues": valid_issues, "questions": questions}

    @classmethod
    def _cold_start_fallback(
        cls,
        reviews: Sequence[IndependentReview],
        max_issues: int = COLD_START_MAX_ISSUES,
    ) -> DebatePlan:
        """0 议题时的确定性兜底：用独立初审中分歧最大的几个点强制开辩论。

        跨专家按 章节重叠 > 维度一致 > 措辞重叠 匹配同一处判断，
        分歧分 = 严重度差 + 证据有无差 + 双方置信度，取最高的 max_issues 条；
        若第三个专家也命中该点，加入参与角色并让单人一方回应。
        """

        reviews_by_role = {review.role: review for review in reviews}
        roles = [
            review.role
            for review in reviews
            if review.role in {SpecialistRole.SCIENTIFIC_SOUNDNESS,
                               SpecialistRole.EMPIRICAL_EVIDENCE,
                               SpecialistRole.GLOBAL_QUALITY}
        ]
        candidates: list[dict[str, Any]] = []
        used: set[str] = set()

        for i in range(len(reviews)):
            for j in range(i + 1, len(reviews)):
                left, right = reviews[i], reviews[j]
                for finding_a in left.findings:
                    for finding_b in right.findings:
                        match_score = _finding_match_score(
                            finding_a, finding_b, left.role, right.role
                        )
                        if match_score <= 0.5:
                            continue
                        severity_gap = abs(
                            _SEVERITY_RANK[finding_a.severity]
                            - _SEVERITY_RANK[finding_b.severity]
                        )
                        evidence_gap = (
                            bool(finding_a.evidence) != bool(finding_b.evidence)
                        )
                        conflict_score = (
                            severity_gap * 2.0
                            + (1.0 if evidence_gap else 0.0)
                            + max(finding_a.confidence, finding_b.confidence)
                        )
                        candidates.append(
                            {
                                "left": finding_a,
                                "left_role": left.role,
                                "right": finding_b,
                                "right_role": right.role,
                                "match_score": match_score,
                                "conflict_score": conflict_score,
                                "evidence_gap": evidence_gap,
                                "evidence_missing_role": (
                                    left.role if not finding_a.evidence
                                    else right.role if not finding_b.evidence
                                    else None
                                ),
                            }
                        )

        candidates.sort(
            key=lambda item: (
                item["conflict_score"], item["match_score"]
            ),
            reverse=True,
        )

        issues: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        issue_seq = 1
        for candidate in candidates:
            left, right = candidate["left"], candidate["right"]
            left_key, right_key = left.finding_id, right.finding_id
            if left_key in used or right_key in used:
                continue
            used.update((left_key, right_key))
            if len(issues) >= max_issues:
                break

            participating = [
                candidate["left_role"],
                candidate["right_role"],
            ]
            participating_findings = [left_key, right_key]
            for review in reviews:
                if review.role in participating:
                    continue
                for finding in review.findings:
                    if _finding_match_score(
                        finding, left, review.role, candidate["left_role"]
                    ) >= 1.0 and _finding_match_score(
                        finding, right, review.role, candidate["right_role"]
                    ) >= 1.0:
                        participating.append(review.role)
                        participating_findings.append(finding.finding_id)
                        used.add(finding.finding_id)
                        break

            title = _wrap(
                f"{left.dimension}：「{_wrap(left.claim, 50)}」的判断分歧", 70
            )
            description = (
                f"{candidate['left_role'].value} 判定为 {left.severity.value}，"
                f"{candidate['right_role'].value} 判定为 {right.severity.value}"
            )
            if candidate["evidence_gap"]:
                description += "，且双方证据锚定不一致"
            issue_id = f"coldstart-{issue_seq}-{left_key[-8:]}-{right_key[-8:]}"
            issues.append(
                {
                    "issue_id": issue_id,
                    "title": title,
                    "description": description,
                    "participating_roles": participating,
                    "conflicting_finding_ids": participating_findings,
                    "evidence_gap": (
                        "一方有原文证据、另一方缺少证据锚定"
                        if candidate["evidence_gap"]
                        else ""
                    ),
                    "priority": 2,
                }
            )

            left_name = candidate["left_role"].value
            right_name = candidate["right_role"].value
            respondent = next(
                (
                    role
                    for role in (
                        candidate["evidence_missing_role"],
                        candidate["left_role"],
                    )
                    if role in participating
                ),
                candidate["left_role"],
            )
            if respondent == candidate["left_role"]:
                this_finding, other_finding = left, right
                this_name, other_name = left_name, right_name
            else:
                this_finding, other_finding = right, left
                this_name, other_name = right_name, left_name
            questions.append(
                {
                    "question_id": f"coldstart-q{issue_seq}-{respondent.value}",
                    "issue_id": issue_id,
                    "target_role": respondent,
                    "prompt": (
                        f"你判定该项为 {this_finding.severity.value}，而"
                        f"{other_name} 判定为 {other_finding.severity.value}。"
                        f"请基于原文证据澄清：{this_finding.claim}"
                    ),
                    "challenged_finding_ids": participating_findings,
                    "requires_external_evidence": bool(
                        candidate["evidence_gap"]
                    ),
                    "evidence_query": (
                        this_finding.claim
                        if candidate["evidence_gap"]
                        else None
                    ),
                }
            )
            issue_seq += 1

        plan = {"issues": issues, "questions": questions}
        logger.info(
            "冷启动兜底生成 %d 个议题、%d 个问题（分歧排序）",
            len(issues), len(questions),
        )
        return DebatePlan.model_validate(plan)

    @classmethod
    def _backfill_questions(
        cls,
        plan: DebatePlan,
    ) -> DebatePlan:
        """议题存在但问题为空时，为每个议题补一个确定性定向问题。"""

        issues: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        for issue in plan.issues:
            target = issue.participating_roles[0]
            questions.append(
                {
                    "question_id": f"backfill-{issue.issue_id}-{target.value}",
                    "issue_id": issue.issue_id,
                    "target_role": target,
                    "prompt": (
                        f"请基于原文证据重新确认你对该争议「{_wrap(issue.title, 80)}」"
                        "的立场并说明依据。"
                    ),
                    "challenged_finding_ids": list(
                        issue.conflicting_finding_ids
                    ),
                    "requires_external_evidence": False,
                    "evidence_query": None,
                }
            )
            issues.append(issue.model_dump(mode="json"))
        return DebatePlan.model_validate(
            {"issues": issues, "questions": questions}
        )

    @classmethod
    def _repair_global_review(cls, data: dict[str, Any]) -> dict[str, Any]:
        """丢弃模型多输出的未知字段，并为必填字段提供兜底。

        模型会模仿输入载荷的结构（例如把初审 finding 的
        ``requires_human_review`` 复制进 resolved_findings），这些冗余键
        对最终裁决没有意义，直接剥离后交由 pydantic 做严格校验。
        """

        def strip(model: type[BaseModel], item: Any) -> Any:
            if not isinstance(item, dict):
                return item
            allowed = set(model.model_fields)
            return {key: value for key, value in item.items() if key in allowed}

        repaired = strip(GlobalReview, data)
        repaired["dimensions"] = [
            strip(DimensionEvaluation, item)
            for item in repaired.get("dimensions") or []
            if isinstance(item, dict)
        ]
        repaired["resolved_findings"] = [
            strip(ResolvedFinding, item)
            for item in repaired.get("resolved_findings") or []
            if isinstance(item, dict)
        ]
        if "overall_summary" not in repaired:
            repaired["overall_summary"] = (
                "经综合分析，论文存在若干问题需修改，详见各维度评估与问题详情。"
            )
        if "confidence" not in repaired:
            repaired["confidence"] = 0.5
        return repaired

    @staticmethod
    def _validate_global_review(data: dict[str, Any]) -> GlobalReview:
        """校验 Chair 生成的最终裁决判断部分。"""

        try:
            return GlobalReview.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                f"DebateReviewChairAgent 输出不符合 GlobalReview：{exc}"
            ) from exc

    @staticmethod
    def _system_prompt(context: ReviewContext | None = None) -> str:
        """Review Chair 的稳定系统职责说明。"""

        base = (
            "你是论文评审 Debate Multi-Agent 系统的 Review Chair。"
            "你负责汇总独立评审、识别关键争议、生成定向质疑、综合证据并形成最终裁决。"
            "你不能用简单多数投票替代判断，也不能凭空增加原文或外部证据。"
            "最终输出必须严格符合调用方要求的 JSON schema，并保持原评审流程兼容。"
        )
        profile = context.review_profile if context else None
        base_guidance = profile.base_guidance if profile else ""
        guidance = profile.chair_guidance if profile else ""
        sections = [base, base_guidance, str(guidance)]
        return "\n\n".join(section for section in sections if section)
