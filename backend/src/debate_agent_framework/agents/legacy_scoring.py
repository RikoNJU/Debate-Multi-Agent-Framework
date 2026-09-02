"""从旧项目 evaluation.py 迁出的 Step 7 评分规则。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas import CompatibleStructureEvaluation


@dataclass(frozen=True)
class LegacyScoreCalculation:
    raw_scores: list[float]
    level_scores: list[int]
    total_score: float
    grade: str


def calculate_legacy_score(
    *,
    semantic_scores: dict[str, float],
    structure: CompatibleStructureEvaluation,
    references: list[str],
) -> LegacyScoreCalculation:
    """复用旧项目的 6 个结构项 + 12 个语义项及总分公式。"""

    ordered_semantic = [float(semantic_scores[str(index)]) for index in range(1, 13)]
    structure_scores = [
        float(structure.completeness.score),
        float(structure.abstract_and_keywords.score),
        float(structure.catalog_standardization.score),
        float(structure.chapter_standardization.score),
        float(structure.acknowledgement_standardization.score),
    ]
    reference_raw, reference_level = _reference_score(references)
    raw_scores = [
        *structure_scores[:4],
        reference_raw,
        structure_scores[4],
        *ordered_semantic,
    ]
    level_scores = [
        *[_structure_level(value) for value in structure_scores[:4]],
        reference_level,
        _structure_level(structure_scores[4]),
        *[_semantic_level(value) for value in ordered_semantic],
    ]
    total, grade = _evaluate_levels(level_scores)
    return LegacyScoreCalculation(
        raw_scores=raw_scores,
        level_scores=level_scores,
        total_score=total,
        grade=grade,
    )


def _semantic_level(score: float) -> int:
    """旧 Step 7 对十二项模型分数使用严格大于边界。"""

    if score > 85:
        return 3
    if score > 75:
        return 2
    if score > 60:
        return 1
    return 0


def _structure_level(score: float) -> int:
    """旧 Step 5 结构项使用大于等于边界。"""

    if score >= 85:
        return 3
    if score >= 75:
        return 2
    if score >= 60:
        return 1
    return 0


def _reference_score(references: list[str]) -> tuple[float, int]:
    """替代旧 checkbody 的文件依赖，并修正其错误数越多分数越高的问题。"""

    if not references:
        return 50.0, 0
    valid = sum(
        bool(re.search(r"\[[A-Z]{1,2}(?:/[A-Z]{1,2})?\]", item))
        and bool(re.search(r"(?:19|20)\s*\d{2}", item))
        for item in references
    )
    if valid == len(references):
        return 80.0, 2
    return 65.0, 1


def _evaluate_levels(level_scores: list[int]) -> tuple[float, str]:
    """原 evaluate_paper_score 的等级条件和加减分公式。"""

    if len(level_scores) != 18:
        raise ValueError("旧评分规则要求 18 个等级项")
    excellent = level_scores.count(3)
    good = level_scores.count(2)
    average = level_scores.count(1)
    poor = level_scores.count(0)

    if excellent > 9 and average + poor <= 3:
        grade = "优秀"
        base = 85.0
        excellent -= 9
        excellent_bonus, good_bonus = 1.5, 0.5
        average_cost, poor_cost = -1.0, -1.5
    elif excellent + good >= 12:
        grade = "良好"
        base = 70.0
        excellent_bonus, good_bonus = 1.2, 0.8
        average_cost, poor_cost = -1.0, -1.5
    elif excellent + good >= 8:
        grade = "一般"
        base = 60.0
        excellent_bonus, good_bonus = 1.2, 0.8
        average_cost = poor_cost = 0.0
    else:
        grade = "较差"
        base = 50.0
        excellent_bonus, good_bonus = 1.5, 1.0
        average_cost = poor_cost = 0.0

    total = (
        base
        + excellent * excellent_bonus
        + good * good_bonus
        + average * average_cost
        + poor * poor_cost
    )
    return float(round(total)), grade
