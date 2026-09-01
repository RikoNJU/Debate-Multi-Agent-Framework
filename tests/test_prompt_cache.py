from __future__ import annotations

from collections.abc import Sequence

from backend.env import ChatMessage, ModelCallOptions, ModelResponse
from debate_agent_framework.agents.json_client import (
    build_review_prompt_prefix,
    complete_json,
    review_context_payload,
)
from debate_agent_framework.agents.specialists import DebateSpecialistAgent
from debate_agent_framework.schemas import (
    ChapterInput,
    DebateIssue,
    DebateQuestion,
    FindingSeverity,
    IndependentReview,
    PaperProfile,
    PaperType,
    ReviewContext,
    ReviewFinding,
    SpecialistRole,
)


class CapturingClient:
    def __init__(self) -> None:
        self.calls: list[Sequence[ChatMessage]] = []
        self.options: list[ModelCallOptions | None] = []

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        options: ModelCallOptions | None = None,
    ) -> ModelResponse:
        self.calls.append(messages)
        self.options.append(options)
        return ModelResponse(content="{}")


def _context(*, run_id: str = "run-a", metadata: dict[str, str] | None = None) -> ReviewContext:
    chapters = [
        ChapterInput(
            chapter_id="C1",
            chapter_name="第一章 绪论",
            stage="引言/绪论",
            content="绪论正文唯一内容。",
        ),
        ChapterInput(
            chapter_id="C2",
            chapter_name="第二章 实验",
            stage="实验验证",
            content="实验正文唯一内容。",
        ),
    ]
    return ReviewContext(
        paper_id="paper-1",
        run_id=run_id,
        profile=PaperProfile(
            title="缓存测试论文",
            paper_type=PaperType.METHOD,
            research_problem="测试缓存前缀稳定性",
            global_summary="一篇测试论文",
        ),
        full_text="绪论正文唯一内容。\n实验正文唯一内容。",
        chapters=chapters,
        metadata=metadata or {},
    )


def _review(role: SpecialistRole, finding_id: str, chapter_id: str) -> IndependentReview:
    return IndependentReview(
        review_id=f"R-{role.value}",
        role=role,
        paper_summary="测试",
        findings=[
            ReviewFinding(
                finding_id=finding_id,
                dimension="实验",
                claim="实验问题",
                rationale="证据不足",
                severity=FindingSeverity.MINOR,
                affected_chapter_ids=[chapter_id],
                confidence=0.7,
            )
        ],
        confidence=0.8,
    )


def test_prompt_prefix_ignores_run_metadata_and_deduplicates_full_text() -> None:
    first = build_review_prompt_prefix(
        _context(run_id="run-a", metadata={"attempt": "1"}),
        include_content=True,
    )
    second = build_review_prompt_prefix(
        _context(run_id="run-b", metadata={"attempt": "9"}),
        include_content=True,
    )

    assert first.prefix_hash == second.prefix_hash
    assert first.messages == second.messages
    serialized = "\n".join(item.content for item in first.messages)
    assert serialized.count("实验正文唯一内容。") == 1

    legacy_payload = review_context_payload(_context())
    assert "content_excerpt" not in legacy_payload["chapters"][0]
    assert legacy_payload["full_text"].count("实验正文唯一内容。") == 1


def test_role_tasks_share_exact_prefix_and_only_change_suffix() -> None:
    prefix = build_review_prompt_prefix(_context(), include_content=True)
    client = CapturingClient()
    complete_json(
        client,
        system_prompt="科学性专家",
        user_prompt="独立初审",
        payload={"role": "science"},
        schema={"type": "object"},
        prompt_prefix=prefix,
        operation="specialist_review",
    )
    complete_json(
        client,
        system_prompt="实验专家",
        user_prompt="独立初审",
        payload={"role": "empirical"},
        schema={"type": "object"},
        prompt_prefix=prefix,
        operation="specialist_review",
    )

    assert client.calls[0][:-1] == client.calls[1][:-1]
    assert client.calls[0][-1] != client.calls[1][-1]
    assert client.options[0] is not None
    assert client.options[0].prompt_prefix_hash == prefix.prefix_hash
    assert client.options[0].stream is False


def test_debate_packet_contains_only_challenged_findings_and_chapters() -> None:
    context = _context()
    own = _review(SpecialistRole.EMPIRICAL_EVIDENCE, "SF-EE-1", "C2")
    peer = _review(SpecialistRole.SCIENTIFIC_SOUNDNESS, "SF-SS-1", "C1")
    issue = DebateIssue(
        issue_id="I1",
        title="实验争议",
        description="需要实验专家回答",
        participating_roles=[
            SpecialistRole.EMPIRICAL_EVIDENCE,
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
        ],
        conflicting_finding_ids=["SF-EE-1"],
    )
    question = DebateQuestion(
        question_id="Q1",
        issue_id="I1",
        target_role=SpecialistRole.EMPIRICAL_EVIDENCE,
        prompt="实验是否充分？",
        challenged_finding_ids=["SF-EE-1"],
    )

    packet = DebateSpecialistAgent._debate_context(
        context, own, issue, question, [peer]
    )
    peer_payload = DebateSpecialistAgent._relevant_peer_reviews(
        issue, question, [peer]
    )

    assert [item["chapter_id"] for item in packet["chapters"]] == ["C2"]
    assert [item["finding_id"] for item in packet["findings"]] == ["SF-EE-1"]
    assert peer_payload[0]["findings"] == []
    assert "绪论正文唯一内容" not in str(packet)
