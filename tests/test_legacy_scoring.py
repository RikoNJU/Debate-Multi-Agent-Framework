from __future__ import annotations

from debate_agent_framework.agents.legacy_scoring import calculate_legacy_score
from debate_agent_framework.schemas import CompatibleStructureEvaluation, WorkloadItem


def make_structure(scores: list[int]) -> CompatibleStructureEvaluation:
    return CompatibleStructureEvaluation(
        completeness=WorkloadItem(score=scores[0]),
        abstract_and_keywords=WorkloadItem(score=scores[1]),
        catalog_standardization=WorkloadItem(score=scores[2]),
        chapter_standardization=WorkloadItem(score=scores[3]),
        acknowledgement_standardization=WorkloadItem(score=scores[4]),
    )


def test_legacy_scoring_reuses_original_threshold_boundaries() -> None:
    semantic = {str(index): 60.0 for index in range(1, 13)}
    semantic.update({"1": 61.0, "2": 76.0, "3": 86.0})

    result = calculate_legacy_score(
        semantic_scores=semantic,
        structure=make_structure([85, 75, 60, 59, 85]),
        references=["Author. Title[J]. Journal, 2026."],
    )

    assert result.level_scores[:6] == [3, 2, 1, 0, 2, 3]
    assert result.level_scores[6:9] == [1, 2, 3]
    assert result.level_scores[9:] == [0] * 9


def test_legacy_scoring_all_excellent_matches_original_formula() -> None:
    result = calculate_legacy_score(
        semantic_scores={str(index): 90.0 for index in range(1, 13)},
        structure=make_structure([90, 90, 90, 90, 90]),
        references=["Author. Title[J]. Journal, 2026."],
    )

    assert result.level_scores == [3, 3, 3, 3, 2, 3] + [3] * 12
    assert result.total_score == 98.0
    assert result.grade == "优秀"


def test_legacy_scoring_tracks_missing_and_malformed_references() -> None:
    semantic = {str(index): 80.0 for index in range(1, 13)}
    structure = make_structure([80, 80, 80, 80, 80])

    missing = calculate_legacy_score(
        semantic_scores=semantic,
        structure=structure,
        references=[],
    )
    malformed = calculate_legacy_score(
        semantic_scores=semantic,
        structure=structure,
        references=["缺少类型和年份的参考文献"],
    )

    assert missing.raw_scores[4] == 50.0
    assert missing.level_scores[4] == 0
    assert malformed.raw_scores[4] == 65.0
    assert malformed.level_scores[4] == 1
