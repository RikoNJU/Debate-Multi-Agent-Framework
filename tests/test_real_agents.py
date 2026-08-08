"""真实 Agent 的单元测试：使用 Fake ModelClient，不消耗真实模型调用。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence

import pytest
from pydantic import ValidationError

from backend.env import ChatMessage, ModelCallOptions, ModelResponse
from debate_agent_framework.core.errors import WorkflowExecutionError
from debate_agent_framework.agents import (
    DebateContextPlannerAgent,
    DebateReviewChairAgent,
    DebateSpecialistAgent,
    DemoReviewChair,
    DemoSpecialist,
    RealOriginalPipelineAdapter,
)
from debate_agent_framework.agents.json_client import review_context_payload
from debate_agent_framework.schemas import (
    ChapterInput,
    DebateIssue,
    DebatePlan,
    DebateQuestion,
    DebateResponse,
    DebateReviewInput,
    FindingSeverity,
    IndependentReview,
    PaperType,
    ReviewFinding,
    SpecialistRole,
    SummaryAdviceResult,
)
from debate_agent_framework.workflows import DebateWorkflow


class FakeModelClient:
    """按调用顺序返回预设 JSON 的模型客户端。"""

    def __init__(self, responses: Sequence[str]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.used_schema_guidance: list[bool] = []

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        options: ModelCallOptions | None = None,
    ) -> ModelResponse:
        if self.calls >= len(self.responses):
            raise AssertionError("FakeModelClient 预设响应已耗尽")
        user_message = next(
            message.content for message in messages if message.role == "user"
        )
        self.used_schema_guidance.append(
            "严格按以下 JSON Schema 输出" in user_message
        )
        content = self.responses[self.calls]
        self.calls += 1
        return ModelResponse(content=content)

    async def acomplete(
        self,
        messages: Sequence[ChatMessage],
        *,
        options: ModelCallOptions | None = None,
    ) -> ModelResponse:
        return self.complete(messages, options=options)


PAPER_EVIDENCE = {
    "evidence_id": "PAPER-C2",
    "kind": "paper",
    "source_title": "第二章 方法设计",
    "quote": "本章提出多智能体评审方法。",
    "location": "第二章 方法设计",
    "chapter_id": "C2",
    "relevance": 0.9,
    "confidence": 0.95,
}

REVIEW_JSON = json.dumps(
    {
        "review_id": "REVIEW-SS",
        "role": "scientific_soundness",
        "paper_summary": "测试论文摘要",
        "strengths": ["研究目标与方法路线对应"],
        "findings": [
            {
                "finding_id": "F-SS-1",
                "dimension": "理论与方法",
                "claim": "关键假设的适用边界说明不足。",
                "rationale": "方法章节没有完整讨论失效条件。",
                "severity": "moderate",
                "evidence": [PAPER_EVIDENCE],
                "affected_chapter_ids": ["C2"],
                "confidence": 0.78,
            }
        ],
        "author_questions": ["请说明假设边界"],
        "confidence": 0.78,
    },
    ensure_ascii=False,
)

PLAN_JSON = json.dumps(
    {
        "issues": [
            {
                "issue_id": "ISSUE-1",
                "title": "理论是否足以支持贡献",
                "description": "方法 Agent 认可路线，实验 Agent 认为验证不足。",
                "participating_roles": [
                    "scientific_soundness",
                    "empirical_evidence",
                ],
                "conflicting_finding_ids": ["F-SS-1", "F-EMP-1"],
                "evidence_gap": "需要确认强 Baseline",
                "priority": 5,
            }
        ],
        "questions": [
            {
                "question_id": "Q-SS-1",
                "issue_id": "ISSUE-1",
                "target_role": "scientific_soundness",
                "prompt": "理论成立是否足以支撑整体贡献？",
                "challenged_finding_ids": ["F-SS-1"],
                "requires_external_evidence": False,
                "evidence_query": None,
            }
        ],
    },
    ensure_ascii=False,
)

RESPONSE_JSON = json.dumps(
    {
        "response_id": "RESP-Q-SS-1",
        "issue_id": "ISSUE-1",
        "question_id": "Q-SS-1",
        "role": "scientific_soundness",
        "position": "revise",
        "response": "理论自洽不能替代实验验证。",
        "evidence": [],
        "revised_findings": [],
        "confidence": 0.86,
    },
    ensure_ascii=False,
)

GLOBAL_REVIEW_JSON = json.dumps(
    {
        "overall_summary": "论文具备基本研究价值。",
        "strengths": ["结构完整"],
        "weaknesses": ["实验覆盖不足"],
        "author_questions": ["请补充强 Baseline"],
        "dimensions": [
            {
                "dimension": "理论与方法",
                "summary": "方法自洽但边界说明不足。",
                "strengths": [],
                "weaknesses": ["边界不清"],
                "confidence": 0.78,
            }
        ],
        "resolved_findings": [
            {
                "finding_id": "F-SS-1",
                "dimension": "理论与方法",
                "claim": "关键假设的适用边界说明不足。",
                "severity": "moderate",
                "status": "confirmed",
                "rationale": "方法章节没有完整讨论失效条件。",
                "evidence": [PAPER_EVIDENCE],
                "affected_chapter_ids": ["C2"],
                "dissenting_views": [],
                "confidence": 0.8,
            }
        ],
        "unresolved_issue_ids": [],
        "confidence": 0.8,
    },
    ensure_ascii=False,
)

SCORE_JSON = json.dumps(
    {
        "scores": {str(index): 82.0 for index in range(1, 13)},
        "total_score": 99.0,
        "grade": "优秀",
        "overall_evaluation": "模型给出的综合评语。",
        "calibration_notes": ["未使用历史评分案例"],
        "confidence": 0.82,
    },
    ensure_ascii=False,
)

WORKLOAD_JSON = json.dumps(
    {
        "structure_evaluation": {
            "completeness": {"score": 80, "analysis": "缺少英文摘要"},
            "abstract_and_keywords": {"score": 80, "analysis": "摘要过短"},
            "catalog_standardization": {"score": 80, "analysis": "目录待核对"},
            "chapter_standardization": {"score": 60, "analysis": "正文低于最低参考值"},
            "acknowledgement_standardization": {"score": 60, "analysis": "未识别到致谢"},
        },
        "summary": "结构与篇幅需要补充。",
        "workload_evaluation": "方法与实验链路存在，但正文篇幅不足。",
    },
    ensure_ascii=False,
)

SUMMARY_JSON = json.dumps(
    {
        "summary": "[第二章 方法设计] 补充关键假设的适用边界。",
        "advice_count": 1,
        "items": [
            {
                "position": "第二章 方法设计",
                "suggestion": "补充关键假设的适用边界。",
                "severity": "moderate",
                "finding_ids": ["F-SS-1"],
                "evidence_ids": ["PAPER-C2"],
                "affected_chapter_ids": ["C2"],
                "requires_human_review": False,
            }
        ],
        "rule_version": "legacy_step6_v2",
    },
    ensure_ascii=False,
)


def make_input() -> DebateReviewInput:
    chapters = [
        ChapterInput(
            chapter_id="C1",
            chapter_name="第一章 绪论",
            stage="引言/绪论",
            content="本章说明研究背景。",
            section_titles=["研究背景"],
        ),
        ChapterInput(
            chapter_id="C2",
            chapter_name="第二章 方法设计",
            stage="方法构建",
            content="本章提出多智能体评审方法。",
            section_titles=["总体架构"],
        ),
        ChapterInput(
            chapter_id="C3",
            chapter_name="第三章 实验验证",
            stage="实验验证",
            content="本章报告基础对比。",
            section_titles=["实验设置"],
        ),
    ]
    return DebateReviewInput(
        paper_id="paper-real-test",
        title="证据驱动多智能体评审",
        abstract="测试真实 Agent。",
        full_text="\n".join(chapter.content for chapter in chapters),
        paper_type=PaperType.METHOD,
        chapters=chapters,
    )


def make_context() -> object:
    return DebateContextPlannerAgent().build(make_input())


def test_specialist_review_uses_schema_and_enforces_role() -> None:
    client = FakeModelClient([REVIEW_JSON])
    agent = DebateSpecialistAgent(
        SpecialistRole.SCIENTIFIC_SOUNDNESS, model_client=client
    )
    review = agent.review(make_context())

    assert isinstance(review, IndependentReview)
    assert review.role is SpecialistRole.SCIENTIFIC_SOUNDNESS
    assert review.findings[0].severity is FindingSeverity.MODERATE
    assert client.used_schema_guidance == [True]


def test_specialist_review_overrides_wrong_role() -> None:
    wrong_role_json = json.loads(REVIEW_JSON)
    wrong_role_json["role"] = "global_quality"
    client = FakeModelClient([json.dumps(wrong_role_json, ensure_ascii=False)])
    agent = DebateSpecialistAgent(
        SpecialistRole.SCIENTIFIC_SOUNDNESS, model_client=client
    )
    review = agent.review(make_context())
    assert review.role is SpecialistRole.SCIENTIFIC_SOUNDNESS


def test_specialist_respond_enforces_question_and_issue_ids() -> None:
    context = make_context()
    own_review = DebateSpecialistAgent(
        SpecialistRole.SCIENTIFIC_SOUNDNESS,
        model_client=FakeModelClient([REVIEW_JSON]),
    ).review(context)
    issue = DebateIssue(
        issue_id="ISSUE-1",
        title="理论是否足以支持贡献",
        description="方法 Agent 认可路线，实验 Agent 认为验证不足。",
        participating_roles=[
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            SpecialistRole.EMPIRICAL_EVIDENCE,
        ],
        conflicting_finding_ids=["F-SS-1", "F-EMP-1"],
        evidence_gap="需要确认强 Baseline",
        priority=5,
    )

    wrong_response = json.loads(RESPONSE_JSON)
    wrong_response["issue_id"] = "WRONG-ISSUE"
    wrong_response["question_id"] = "WRONG-QUESTION"
    client = FakeModelClient([json.dumps(wrong_response, ensure_ascii=False)])
    agent = DebateSpecialistAgent(
        SpecialistRole.SCIENTIFIC_SOUNDNESS, model_client=client
    )
    question = DebateQuestion(
        question_id="Q-SS-1",
        issue_id="ISSUE-1",
        target_role=SpecialistRole.SCIENTIFIC_SOUNDNESS,
        prompt="理论成立是否足以支撑整体贡献？",
    )
    response = agent.respond(
        context,
        own_review=own_review,
        issue=issue,
        question=question,
        peer_reviews=[],
        external_evidence=[],
    )

    assert isinstance(response, DebateResponse)
    assert response.issue_id == "ISSUE-1"
    assert response.question_id == "Q-SS-1"


def test_context_planner_packs_long_text() -> None:
    input_data = make_input()
    long_input = input_data.model_copy(
        update={"full_text": "长文" * 30_000}
    )
    context = DebateContextPlannerAgent(full_text_limit=20_000).build(long_input)

    assert context.full_text is None
    assert len(context.content_packets) == 2
    assert context.content_packets[0].dependency_packet_ids == []
    assert context.content_packets[1].dependency_packet_ids == ["PACKET-1"]


def test_model_context_payload_does_not_duplicate_chapter_content() -> None:
    context = make_context()
    payload = review_context_payload(context)

    assert payload["full_text"]
    assert "content" not in payload["chapters"][0]
    assert payload["chapters"][0]["content_chars"] > 0


def test_real_chair_plan_and_synthesis_with_fake_client() -> None:
    context = make_context()
    client = FakeModelClient([PLAN_JSON, GLOBAL_REVIEW_JSON])
    chair = DebateReviewChairAgent(model_client=client)

    reviews = [
        DebateSpecialistAgent(
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            model_client=FakeModelClient([REVIEW_JSON]),
        ).review(context)
    ]
    plan = chair.plan_debate(context, reviews)
    assert len(plan.issues) == 1
    assert plan.questions[0].target_role is SpecialistRole.SCIENTIFIC_SOUNDNESS

    synthesis = chair.synthesize(
        context,
        reviews=reviews,
        debate_plan=plan,
        responses=[],
        external_evidence=[],
    )
    assert list(synthesis.chapter_evaluation) == [
        "chapter_1",
        "chapter_2",
        "chapter_3",
    ]
    assert synthesis.chapter_evaluation["chapter_2"].chapter_data.chapter_name == (
        "第二章 方法设计"
    )


def test_real_workflow_full_chain_with_fake_client() -> None:
    client = FakeModelClient(
        [
            REVIEW_JSON,
            REVIEW_JSON,
            REVIEW_JSON,
            PLAN_JSON,
            RESPONSE_JSON,
            GLOBAL_REVIEW_JSON,
            WORKLOAD_JSON,
            SUMMARY_JSON,
            SCORE_JSON,
        ]
    )
    workflow = DebateWorkflow.real(model_client=client)
    result = workflow.run(make_input())

    assert len(result.independent_reviews) == 3
    assert len(result.debate_plan.issues) == 1
    assert len(result.debate_responses) == 1
    assert result.summary_advice is not None
    assert result.final_score is not None
    assert result.final_score.total_score == 75.0
    assert len(result.final_score.legacy_raw_scores) == 18
    assert len(result.final_score.legacy_level_scores) == 18
    assert result.final_score.scoring_rule == "legacy_step7_v1"
    assert result.historical_score_cases == []
    assert result.external_evidence == []
    assert result.issues == []
    assert client.calls == 9


def test_real_workflow_runs_legacy_step1_and_step2_before_agents() -> None:
    step1 = json.dumps(
        {
            "paper_type": "方法创新",
            "rationale": "论文提出多智能体评审方法并进行实验验证",
            "confidence": 0.91,
        },
        ensure_ascii=False,
    )
    step2 = json.dumps(
        {
            "chapters": [
                {
                    "chapter_id": "C1",
                    "chapter_name": "第一章 绪论",
                    "stage": "引言/绪论",
                },
                {
                    "chapter_id": "C2",
                    "chapter_name": "第二章 方法设计",
                    "stage": "方法构建",
                },
                {
                    "chapter_id": "C3",
                    "chapter_name": "第三章 实验验证",
                    "stage": "实验验证",
                },
            ]
        },
        ensure_ascii=False,
    )
    client = FakeModelClient(
        [
            step1,
            step2,
            REVIEW_JSON,
            REVIEW_JSON,
            REVIEW_JSON,
            PLAN_JSON,
            RESPONSE_JSON,
            GLOBAL_REVIEW_JSON,
            WORKLOAD_JSON,
            SUMMARY_JSON,
            SCORE_JSON,
        ]
    )
    review_input = make_input().model_copy(
        update={
            "paper_type": None,
            "chapters": [
                chapter.model_copy(update={"stage": "正文"})
                for chapter in make_input().chapters
            ],
            "metadata": {
                "paper_type_source": "auto_pending",
                "chapter_stage_source": "markdown_heuristic",
            },
        }
    )

    result = DebateWorkflow.real(model_client=client).run(review_input)

    assert result.context.profile.paper_type is PaperType.METHOD
    assert [chapter.stage for chapter in result.context.chapters] == [
        "引言/绪论",
        "方法构建",
        "实验验证",
    ]
    assert result.context.metadata["paper_type_rule_version"] == "legacy_step1_v1"
    assert result.context.metadata["chapter_stage_rule_version"] == "legacy_step2_v1"
    assert client.calls == 11


def test_real_workflow_rejects_unlocatable_paper_quotes() -> None:
    bad_review = json.loads(REVIEW_JSON)
    bad_review["findings"][0]["evidence"][0]["quote"] = "原文中不存在的引文"
    response = json.dumps(bad_review, ensure_ascii=False)
    workflow = DebateWorkflow.real(
        model_client=FakeModelClient([response, response, response])
    )

    with pytest.raises(WorkflowExecutionError, match="低于最低要求"):
        workflow.run(make_input())


def test_real_scoring_adapter_uses_legacy_total_rule() -> None:
    context = make_context()

    async def collect_reviews() -> list[IndependentReview]:
        return list(
            await asyncio.gather(
                *(DemoSpecialist(role).review(context) for role in SpecialistRole)
            )
        )

    reviews = asyncio.run(collect_reviews())
    synthesis = DemoReviewChair().synthesize(
        context,
        reviews=reviews,
        debate_plan=DebatePlan(),
        responses=[],
        external_evidence=[],
    )
    client = FakeModelClient([SCORE_JSON])
    adapter = RealOriginalPipelineAdapter(model_client=client)
    result = adapter.score(
        make_input(),
        synthesis,
        summary_advice=SummaryAdviceResult(summary="测试建议", advice_count=1),
        historical_cases=[],
    )

    assert result.scores["1"] == 82.0
    assert result.total_score == 82.0
    assert result.grade == "良好"
    assert len(result.legacy_level_scores) == 18
    assert result.overall_evaluation == "模型给出的综合评语。"
    assert result.confidence == 0.82
    assert client.calls == 1
    assert client.used_schema_guidance == [True]


PLAN_BAD_JSON = json.dumps(
    {
        "issues": [
            {
                "issue_id": "I-1",
                "title": "理论是否足以支持贡献",
                "description": "方法 Agent 认可路线，实验 Agent 认为验证不足。",
                "participating_roles": ["scientific_soundness"],
                "conflicting_finding_ids": ["F-SS-1", "F-EMP-1"],
                "evidence_gap": "",
                "priority": 5,
            },
            {
                "issue_id": "I-2",
                "title": "没有参与角色的争议",
                "description": "该争议没有指定参与角色。",
                "participating_roles": [],
                "conflicting_finding_ids": [],
                "evidence_gap": "",
                "priority": 2,
            },
        ],
        "questions": [
            {
                "question_id": "Q-1",
                "issue_id": "I-1",
                "target_role": "empirical_evidence",
                "prompt": "请说明缺失的强 Baseline 验证。",
                "challenged_finding_ids": ["F-EMP-1"],
                "requires_external_evidence": False,
                "evidence_query": None,
            },
            {
                "question_id": "Q-ORPHAN",
                "issue_id": "NO-SUCH-ISSUE",
                "target_role": "global_quality",
                "prompt": "引用了不存在的争议。",
                "challenged_finding_ids": [],
                "requires_external_evidence": False,
                "evidence_query": None,
            },
        ],
    },
    ensure_ascii=False,
)


def _review_with(finding_id: str, role: SpecialistRole) -> IndependentReview:
    return IndependentReview(
        review_id=f"REVIEW-{role.value}",
        role=role,
        paper_summary="测试论文",
        strengths=[],
        findings=[
            ReviewFinding(
                finding_id=finding_id,
                dimension="理论与方法",
                claim="需要验证",
                rationale="缺少依据",
                severity=FindingSeverity.MODERATE,
                evidence=[PAPER_EVIDENCE],
                affected_chapter_ids=["C2"],
                confidence=0.7,
            )
        ],
        author_questions=[],
        confidence=0.7,
    )


def test_plan_debate_repairs_structural_constraints() -> None:
    client = FakeModelClient([PLAN_BAD_JSON])
    chair = DebateReviewChairAgent(model_client=client)
    reviews = [
        _review_with("F-SS-1", SpecialistRole.SCIENTIFIC_SOUNDNESS),
        _review_with("F-EMP-1", SpecialistRole.EMPIRICAL_EVIDENCE),
    ]
    plan = chair.plan_debate(make_context(), reviews)

    assert [issue.issue_id for issue in plan.issues] == ["I-1"]
    assert set(plan.issues[0].participating_roles) == {
        SpecialistRole.SCIENTIFIC_SOUNDNESS,
        SpecialistRole.EMPIRICAL_EVIDENCE,
    }
    assert [q.question_id for q in plan.questions] == ["Q-1"]
