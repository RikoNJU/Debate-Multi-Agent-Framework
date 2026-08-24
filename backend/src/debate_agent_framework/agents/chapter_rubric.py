"""Versioned chapter-level rubric used to stabilize semantic scoring."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from ..schemas import (
    GlobalReview,
    IndependentReview,
    ResolutionStatus,
    ReviewContext,
    RubricAssessment,
    RubricJudgement,
    SpecialistRole,
)


RUBRIC_VERSION = "chapter_rubric_v1"
MODEL_SCORE_WEIGHT = 0.2
MAX_ANCHOR_DEVIATION = 1.0


@dataclass(frozen=True)
class RubricItemDefinition:
    key: str
    label: str
    criteria: str
    role: SpecialistRole
    dimensions: tuple[tuple[str, float], ...]


_COMMON_ITEMS = (
    RubricItemDefinition(
        "chapter.expression_quality",
        "章节结构与表达",
        "章节组织应清晰，论述连贯，术语和语言表达符合本科论文要求。",
        SpecialistRole.GLOBAL_QUALITY,
        (("11", 1.0),),
    ),
)

_STAGE_ITEMS: dict[str, tuple[RubricItemDefinition, ...]] = {
    "introduction": (
        RubricItemDefinition(
            "introduction.research_problem",
            "研究问题与目标",
            "研究问题、目标和研究边界应明确，并由论文背景合理引出。",
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            (("1", 0.4), ("5", 0.6)),
        ),
        RubricItemDefinition(
            "introduction.topic_value",
            "选题价值",
            "选题应说明学术或应用价值，并与论文实际工作相匹配。",
            SpecialistRole.GLOBAL_QUALITY,
            (("1", 0.4), ("3", 0.6)),
        ),
    ),
    "related_work": (
        RubricItemDefinition(
            "related_work.coverage",
            "文献覆盖与代表性",
            "应覆盖与研究问题直接相关的代表性工作，并形成清晰技术脉络。",
            SpecialistRole.GLOBAL_QUALITY,
            (("4", 1.0),),
        ),
        RubricItemDefinition(
            "related_work.analysis",
            "文献分析深度",
            "不应只罗列文献，应比较方法差异、局限并支撑本文研究动机。",
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            (("4", 0.6), ("5", 0.4)),
        ),
    ),
    "background": (
        RubricItemDefinition(
            "background.concept_accuracy",
            "概念与理论准确性",
            "关键概念、公式和理论关系应准确，并与后续方法保持一致。",
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            (("5", 0.5), ("10", 0.5)),
        ),
    ),
    "data": (
        RubricItemDefinition(
            "data.data_quality",
            "数据来源与质量",
            "数据来源、规模、划分和质量控制应清楚且适合研究问题。",
            SpecialistRole.EMPIRICAL_EVIDENCE,
            (("6", 0.4), ("10", 0.6)),
        ),
        RubricItemDefinition(
            "data.preprocessing",
            "数据处理可复现性",
            "预处理步骤、参数和数据泄漏防护应充分说明并可复现。",
            SpecialistRole.EMPIRICAL_EVIDENCE,
            (("6", 0.5), ("7", 0.5)),
        ),
    ),
    "methodology": (
        RubricItemDefinition(
            "methodology.design_rationale",
            "方法设计依据",
            "技术路线和关键设计选择应有理论或实证依据。",
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            (("5", 0.5), ("10", 0.5)),
        ),
        RubricItemDefinition(
            "methodology.feasibility",
            "方法可行性与完整性",
            "核心流程、假设、边界和实现细节应足以支持方法可行性。",
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            (("6", 0.5), ("7", 0.5)),
        ),
        RubricItemDefinition(
            "methodology.innovation",
            "方法创新与贡献",
            "创新点应明确，并能与已有工作区分且具有实际贡献。",
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            (("9", 0.7), ("12", 0.3)),
        ),
        RubricItemDefinition(
            "methodology.workload_depth",
            "核心工作量与研究深度",
            "核心章节应体现足够的设计、分析和实现工作量。",
            SpecialistRole.GLOBAL_QUALITY,
            (("2", 0.5), ("5", 0.5)),
        ),
        RubricItemDefinition(
            "methodology.technical_application",
            "技术应用能力",
            "应体现专业工具、编程、建模或系统实现能力及必要细节。",
            SpecialistRole.GLOBAL_QUALITY,
            (("8", 0.6), ("12", 0.4)),
        ),
    ),
    "experiment": (
        RubricItemDefinition(
            "experiment.setup",
            "实验设置完整性",
            "软硬件环境、关键参数和训练设置应完整并支持复现。",
            SpecialistRole.EMPIRICAL_EVIDENCE,
            (("7", 1.0),),
        ),
        RubricItemDefinition(
            "experiment.metrics",
            "评价指标合理性",
            "评价指标应定义清楚、选择合理并覆盖研究目标。",
            SpecialistRole.EMPIRICAL_EVIDENCE,
            (("6", 0.4), ("10", 0.6)),
        ),
        RubricItemDefinition(
            "experiment.baselines",
            "基线代表性与公平性",
            "基线应有代表性，对比设置和参数调优应公平。",
            SpecialistRole.EMPIRICAL_EVIDENCE,
            (("4", 0.3), ("10", 0.7)),
        ),
        RubricItemDefinition(
            "experiment.validation",
            "验证流程科学性",
            "应使用适当的消融、重复实验或统计分析支撑结论。",
            SpecialistRole.EMPIRICAL_EVIDENCE,
            (("10", 1.0),),
        ),
        RubricItemDefinition(
            "experiment.analysis",
            "结果分析深度",
            "结果解释应基于证据，分析差异原因、局限和异常现象。",
            SpecialistRole.EMPIRICAL_EVIDENCE,
            (("5", 0.4), ("10", 0.6)),
        ),
        RubricItemDefinition(
            "experiment.workload",
            "实验工作量",
            "实验规模和覆盖范围应足以验证论文的核心贡献。",
            SpecialistRole.GLOBAL_QUALITY,
            (("2", 0.5), ("7", 0.5)),
        ),
    ),
    "result_analysis": (
        RubricItemDefinition(
            "result.interpretation",
            "结果解释与结论支撑",
            "分析应客观解释结果，并明确证据能够支持的结论边界。",
            SpecialistRole.EMPIRICAL_EVIDENCE,
            (("10", 0.7), ("12", 0.3)),
        ),
        RubricItemDefinition(
            "result.robustness",
            "稳健性与局限分析",
            "应分析不同场景、边界条件、失败案例或方法局限。",
            SpecialistRole.EMPIRICAL_EVIDENCE,
            (("7", 0.4), ("10", 0.6)),
        ),
    ),
    "conclusion": (
        RubricItemDefinition(
            "conclusion.consistency",
            "结论与证据一致性",
            "结论应回应研究目标，不夸大实验或论证能够支持的范围。",
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            (("10", 0.7), ("12", 0.3)),
        ),
        RubricItemDefinition(
            "conclusion.contribution_value",
            "成果价值与贡献完整性",
            "应准确总结完成的工作、成果价值、局限和后续方向。",
            SpecialistRole.GLOBAL_QUALITY,
            (("3", 0.3), ("12", 0.7)),
        ),
    ),
}

_DEFINITIONS = {
    item.key: item
    for items in (_COMMON_ITEMS, *_STAGE_ITEMS.values())
    for item in items
}


def expected_rubric_items(
    context: ReviewContext, role: SpecialistRole
) -> list[dict[str, object]]:
    """Return a stable, compact checklist for one specialist."""

    result: list[dict[str, object]] = []
    for chapter in context.chapters:
        if not chapter.reviewable:
            continue
        stage = _stage_key(chapter.stage, chapter.chapter_name)
        definitions = (*_COMMON_ITEMS, *_STAGE_ITEMS.get(stage, ()))
        for definition in definitions:
            if definition.role is not role:
                continue
            result.append(
                {
                    "item_id": f"{chapter.chapter_id}:{definition.key}",
                    "chapter_id": chapter.chapter_id,
                    "chapter_name": chapter.chapter_name,
                    "stage": chapter.stage,
                    "label": definition.label,
                    "criteria": definition.criteria,
                    "score_dimensions": [item[0] for item in definition.dimensions],
                }
            )
    return result


def normalize_specialist_assessments(
    *,
    expected: Sequence[dict[str, object]],
    supplied: Sequence[RubricAssessment],
    role: SpecialistRole,
    finding_ids: set[str],
) -> list[RubricAssessment]:
    """Repair missing/unsafe model checklist output without inventing a pass."""

    allowed = {str(item["item_id"]): item for item in expected}
    supplied_by_id = {item.item_id: item for item in supplied if item.item_id in allowed}
    normalized: list[RubricAssessment] = []
    for item_id, prompt_item in allowed.items():
        assessment = supplied_by_id.get(item_id)
        if assessment is None:
            normalized.append(
                RubricAssessment(
                    item_id=item_id,
                    chapter_id=str(prompt_item["chapter_id"]),
                    role=role,
                    judgement=RubricJudgement.HUMAN_REVIEW,
                    rationale="模型未返回该固定评审小项，不能据此加分或扣分。",
                    confidence=0.0,
                    requires_human_review=True,
                )
            )
            continue
        linked = [finding_id for finding_id in assessment.finding_ids if finding_id in finding_ids]
        update: dict[str, object] = {
            "chapter_id": str(prompt_item["chapter_id"]),
            "role": role,
            "finding_ids": linked,
        }
        if assessment.judgement in {RubricJudgement.POOR, RubricJudgement.CRITICAL} and not linked:
            update.update(
                judgement=RubricJudgement.HUMAN_REVIEW,
                rationale=(
                    f"{assessment.rationale}（负面判断未关联可核验 Finding，已转人工复核）"
                ),
                requires_human_review=True,
                confidence=min(assessment.confidence, 0.5),
            )
        normalized.append(assessment.model_copy(update=update))
    return normalized


def resolve_rubric_assessments(
    reviews: Sequence[IndependentReview], global_review: GlobalReview
) -> list[RubricAssessment]:
    """Allow negative checklist judgements to score only after Chair confirmation."""

    confirmed = {
        finding.finding_id
        for finding in global_review.resolved_findings
        if finding.status in {ResolutionStatus.CONFIRMED, ResolutionStatus.MOSTLY_CONFIRMED}
    }
    result: list[RubricAssessment] = []
    for assessment in sorted(
        (item for review in reviews for item in review.rubric_assessments),
        key=lambda item: item.item_id,
    ):
        if assessment.judgement not in {RubricJudgement.POOR, RubricJudgement.CRITICAL}:
            result.append(assessment)
            continue
        if any(finding_id in confirmed for finding_id in assessment.finding_ids):
            result.append(assessment)
            continue
        result.append(
            assessment.model_copy(
                update={
                    "judgement": RubricJudgement.HUMAN_REVIEW,
                    "requires_human_review": True,
                    "confidence": min(assessment.confidence, 0.5),
                    "rationale": (
                        f"{assessment.rationale}（Chair 未确认关联问题，已排除出自动评分）"
                    ),
                }
            )
        )
    return result


def rubric_anchor_scores(
    assessments: Sequence[RubricAssessment],
) -> dict[str, float]:
    """Aggregate finite checklist judgements into the twelve semantic dimensions."""

    judgement_scores = {
        RubricJudgement.EXCELLENT: 92.0,
        RubricJudgement.GOOD: 82.0,
        RubricJudgement.ACCEPTABLE: 68.0,
        RubricJudgement.POOR: 55.0,
        RubricJudgement.CRITICAL: 35.0,
    }
    weighted: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for assessment in assessments:
        score = judgement_scores.get(assessment.judgement)
        definition = _definition_for_item_id(assessment.item_id)
        if score is None or definition is None:
            continue
        for dimension, weight in definition.dimensions:
            weighted[dimension].append((score, weight))
    return {
        dimension: round(
            sum(score * weight for score, weight in values)
            / sum(weight for _, weight in values),
            1,
        )
        for dimension, values in weighted.items()
    }


def stabilize_semantic_scores(
    model_scores: dict[str, float], anchors: dict[str, float]
) -> dict[str, float]:
    """Keep model nuance while preventing random crossings of legacy thresholds."""

    stabilized = {key: float(value) for key, value in model_scores.items()}
    for dimension, anchor in anchors.items():
        candidate = float(model_scores[dimension])
        blended = anchor * (1.0 - MODEL_SCORE_WEIGHT) + candidate * MODEL_SCORE_WEIGHT
        lower, upper = _legacy_level_interval(anchor)
        stabilized[dimension] = round(
            min(
                max(blended, max(lower, anchor - MAX_ANCHOR_DEVIATION)),
                min(upper, anchor + MAX_ANCHOR_DEVIATION),
            ),
            1,
        )
    return stabilized


def _definition_for_item_id(item_id: str) -> RubricItemDefinition | None:
    _, separator, key = item_id.partition(":")
    return _DEFINITIONS.get(key if separator else item_id)


def _legacy_level_interval(score: float) -> tuple[float, float]:
    if score > 85:
        return 85.1, 100.0
    if score > 75:
        return 75.1, 85.0
    if score > 60:
        return 60.1, 75.0
    return 0.0, 60.0


def _stage_key(stage: str, chapter_name: str) -> str:
    text = f"{stage} {chapter_name}".casefold()
    ordered = (
        ("related_work", ("相关工作", "文献综述")),
        ("data", ("数据来源", "数据处理", "预处理")),
        ("result_analysis", ("结果分析", "性能评估", "系统评估")),
        ("experiment", ("实验", "验证", "系统实现")),
        ("methodology", ("方法", "模型", "系统设计", "理论", "证明")),
        ("background", ("背景知识", "前置知识", "理论基础")),
        ("introduction", ("引言", "绪论")),
        ("conclusion", ("结论", "总结", "展望")),
    )
    for key, markers in ordered:
        if any(marker.casefold() in text for marker in markers):
            return key
    return "general"
