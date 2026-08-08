from __future__ import annotations

from debate_agent_framework.agents.compat import assemble_review_synthesis
from debate_agent_framework.agents.context_planner import DebateContextPlannerAgent
from debate_agent_framework.agents.legacy_summary import build_summary_advice
from debate_agent_framework.agents.legacy_workload import DeterministicLegacyWorkloadEvaluator
from debate_agent_framework.schemas import (
    ChapterInput,
    DebateReviewInput,
    FindingSeverity,
    GlobalReview,
    PaperType,
    ResolutionStatus,
    ResolvedFinding,
    ReviewEvidence,
    EvidenceKind,
)


def make_input(paper_type: PaperType, body_chars: int) -> DebateReviewInput:
    chapters = [
        ChapterInput(chapter_id="C1", chapter_name="第一章 绪论", stage="引言/绪论", content="引" * (body_chars // 3)),
        ChapterInput(chapter_id="C2", chapter_name="第二章 核心工作", stage={PaperType.THEORY: "模型与证明", PaperType.METHOD: "方法构建", PaperType.ENGINEERING: "系统设计"}[paper_type], content="核" * (body_chars // 3)),
        ChapterInput(chapter_id="C3", chapter_name="第三章 结论", stage="结论展望", content="结" * (body_chars - 2 * (body_chars // 3))),
    ]
    return DebateReviewInput(
        paper_id="P1", title="测试论文", abstract="摘" * 350,
        keywords=["甲", "乙", "丙"], full_text="关键词：甲；乙；丙\n" + "\n".join(c.content for c in chapters),
        paper_type=paper_type, chapters=chapters, references=["A. Title[J]. Journal, 2025."],
    )


def empty_synthesis(review_input: DebateReviewInput):  # type: ignore[no-untyped-def]
    context = DebateContextPlannerAgent().build(review_input)
    review = GlobalReview(overall_summary="测试", confidence=0.8)
    return assemble_review_synthesis(context, review)


def test_step5_uses_different_minimums_for_three_paper_types() -> None:
    evaluator = DeterministicLegacyWorkloadEvaluator()
    theory = make_input(PaperType.THEORY, 13000)
    method = make_input(PaperType.METHOD, 13000)
    engineering = make_input(PaperType.ENGINEERING, 13000)

    theory_result = evaluator.evaluate_workload(theory, empty_synthesis(theory))
    method_result = evaluator.evaluate_workload(method, empty_synthesis(method))
    engineering_result = evaluator.evaluate_workload(engineering, empty_synthesis(engineering))

    assert theory_result.structure_evaluation.chapter_standardization.score == 100
    assert method_result.structure_evaluation.chapter_standardization.score == 60
    assert engineering_result.structure_evaluation.chapter_standardization.score == 100


def test_step6_preserves_severity_evidence_and_cross_chapter_coverage() -> None:
    review_input = make_input(PaperType.METHOD, 15000)
    evidence = ReviewEvidence(
        evidence_id="E1", kind=EvidenceKind.PAPER, source_title="第二章",
        quote="核", location="第5页", chapter_id="C2",
    )
    findings = [
        ResolvedFinding(
            finding_id="F1", dimension="方法", claim="方法边界不清", severity=FindingSeverity.MAJOR,
            status=ResolutionStatus.CONFIRMED, rationale="缺少边界", evidence=[evidence],
            affected_chapter_ids=["C2"], confidence=0.9,
        ),
        ResolvedFinding(
            finding_id="F2", dimension="结论", claim="结论外推过度", severity=FindingSeverity.MODERATE,
            status=ResolutionStatus.MOSTLY_CONFIRMED, rationale="证据不足", evidence=[],
            affected_chapter_ids=["C3"], confidence=0.8,
        ),
    ]
    global_review = GlobalReview(overall_summary="测试", resolved_findings=findings, confidence=0.8)
    synthesis = assemble_review_synthesis(DebateContextPlannerAgent().build(review_input), global_review)

    result = build_summary_advice(review_input, synthesis)

    assert result.advice_count == 2
    assert {item.affected_chapter_ids[0] for item in result.items} == {"C2", "C3"}
    assert result.items[0].severity is FindingSeverity.MAJOR
    assert result.items[0].evidence_ids == ["E1"]
    assert result.items[0].finding_ids == ["F1"]
