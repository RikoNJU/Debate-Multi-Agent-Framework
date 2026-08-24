"""Scoring anchors and revision identity regression tests."""

from __future__ import annotations

from debate_agent_framework.agents.chapter_rubric import (
    expected_rubric_items,
    normalize_specialist_assessments,
    rubric_anchor_scores,
    stabilize_semantic_scores,
)
from debate_agent_framework.schemas import (
    ChapterInput,
    DebateReviewInput,
    PaperProfile,
    PaperType,
    ReviewContext,
    RubricAssessment,
    RubricJudgement,
    SpecialistRole,
)
from debate_agent_framework.services.review_identity import (
    AUTO_LINEAGE_THRESHOLD,
    build_review_fingerprint,
    compare_revisions,
)


def _context() -> ReviewContext:
    chapter = ChapterInput(
        chapter_id="C3",
        chapter_name="第三章 方法设计",
        stage="方法设计",
        content="本文给出方法设计依据、实现细节与复杂度分析。",
    )
    return ReviewContext(
        paper_id="paper-stable",
        profile=PaperProfile(
            title="稳定评分测试",
            paper_type=PaperType.METHOD,
            research_problem="验证固定评审小项能否约束评分波动",
            global_summary="论文提出并验证一种新方法。",
        ),
        full_text=chapter.content,
        chapters=[chapter],
    )


def _review_input(text: str) -> DebateReviewInput:
    return DebateReviewInput(
        paper_id="paper-stable",
        title="稳定评分测试",
        full_text=text,
        paper_type=PaperType.METHOD,
        chapters=[
            ChapterInput(
                chapter_id="C3",
                chapter_name="第三章 方法设计",
                stage="方法设计",
                content=text,
            )
        ],
    )


def test_missing_or_unsupported_negative_rubric_items_require_human_review() -> None:
    expected = expected_rubric_items(
        _context(), SpecialistRole.SCIENTIFIC_SOUNDNESS
    )
    assert expected
    first = expected[0]
    supplied = RubricAssessment(
        item_id=str(first["item_id"]),
        chapter_id="C3",
        role=SpecialistRole.SCIENTIFIC_SOUNDNESS,
        judgement=RubricJudgement.POOR,
        rationale="方法依据不足。",
        confidence=0.9,
    )

    normalized = normalize_specialist_assessments(
        expected=expected,
        supplied=[supplied],
        role=SpecialistRole.SCIENTIFIC_SOUNDNESS,
        finding_ids=set(),
    )

    assert len(normalized) == len(expected)
    assert all(item.judgement is RubricJudgement.HUMAN_REVIEW for item in normalized)
    assert all(item.requires_human_review for item in normalized)


def test_model_scores_keep_small_variation_without_crossing_anchor_level() -> None:
    assessment = RubricAssessment(
        item_id="C3:methodology.design_rationale",
        chapter_id="C3",
        role=SpecialistRole.SCIENTIFIC_SOUNDNESS,
        judgement=RubricJudgement.GOOD,
        rationale="方法设计依据完整。",
        confidence=0.9,
    )
    anchors = rubric_anchor_scores([assessment])
    low = stabilize_semantic_scores(
        {str(index): 20.0 for index in range(1, 13)}, anchors
    )
    high = stabilize_semantic_scores(
        {str(index): 99.0 for index in range(1, 13)}, anchors
    )

    for dimension in anchors:
        assert abs(low[dimension] - anchors[dimension]) <= 1.0
        assert abs(high[dimension] - anchors[dimension]) <= 1.0
        assert high[dimension] - low[dimension] <= 2.0
        assert 75 < low[dimension] <= 85
        assert 75 < high[dimension] <= 85


def test_small_edit_creates_new_revision_identity_and_new_review_fingerprint() -> None:
    paragraphs = [
        f"第{index}段说明实验配置、方法依据和结果分析，编号为{index:04d}。"
        for index in range(240)
    ]
    original = _review_input("\n".join(paragraphs))
    changed_paragraphs = paragraphs.copy()
    changed_paragraphs[120] += "补充随机种子与重复实验次数。"
    revised = _review_input("\n".join(changed_paragraphs))

    comparison = compare_revisions(
        revised,
        original,
        parent_revision_id="revision-v1",
        match_method="title_and_content_similarity",
    )

    assert comparison.parent_revision_id == "revision-v1"
    assert comparison.similarity is not None
    assert comparison.similarity >= AUTO_LINEAGE_THRESHOLD
    assert comparison.changed_chapter_ids == ["C3"]
    assert comparison.change_ratio is not None and comparison.change_ratio > 0
    assert build_review_fingerprint(
        comparison.content_sha256, revised.paper_type
    ) != build_review_fingerprint(
        compare_revisions(original, None).content_sha256, original.paper_type
    )


def test_layout_only_changes_can_reuse_the_same_review_fingerprint() -> None:
    compact = _review_input("第一章介绍方法。第二章报告实验。")
    reformatted = _review_input(" 第一章介绍方法。\n\n第二章报告实验。 ")
    compact_comparison = compare_revisions(compact, None)
    reformatted_comparison = compare_revisions(reformatted, compact)

    assert reformatted_comparison.similarity == 1.0
    assert reformatted_comparison.content_sha256 == compact_comparison.content_sha256
    assert build_review_fingerprint(
        reformatted_comparison.content_sha256, reformatted.paper_type
    ) == build_review_fingerprint(
        compact_comparison.content_sha256, compact.paper_type
    )
