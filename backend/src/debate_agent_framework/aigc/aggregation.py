from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .schemas import (
    AigcChapterSummary,
    AigcDetectionResult,
    AigcRiskLevel,
    AigcSegment,
    AigcSegmentResult,
)


def aggregate_results(
    segments: Sequence[AigcSegment],
    probabilities: Sequence[float],
    *,
    model_id: str,
    model_revision: str | None,
    preprocessing_version: str,
    medium_threshold: float,
    high_threshold: float,
) -> AigcDetectionResult:
    if not segments:
        raise ValueError("No valid body text was available for AIGC detection")
    if len(segments) != len(probabilities):
        raise ValueError("AIGC probability count does not match segment count")

    results = [
        AigcSegmentResult(
            segment_id=segment.segment_id,
            chapter_id=segment.chapter_id,
            chapter_name=segment.chapter_name,
            content_preview=(segment.text[:180] + ("…" if len(segment.text) > 180 else "")),
            text_sha256=segment.text_sha256,
            token_count=segment.token_count,
            ai_probability=max(0.0, min(1.0, float(probability))),
            risk_level=_risk(float(probability), medium_threshold, high_threshold),
            locators=segment.locators,
        )
        for segment, probability in zip(segments, probabilities)
    ]
    groups: dict[tuple[str | None, str], list[AigcSegmentResult]] = defaultdict(list)
    for item in results:
        groups[(item.chapter_id, item.chapter_name)].append(item)
    chapters = [
        AigcChapterSummary(
            chapter_id=key[0],
            chapter_name=key[1],
            segment_count=len(items),
            token_count=sum(item.token_count for item in items),
            average_risk_score=_weighted_average(items),
            medium_risk_ratio=_ratio(items, AigcRiskLevel.MEDIUM),
            high_risk_ratio=_ratio(items, AigcRiskLevel.HIGH),
        )
        for key, items in groups.items()
    ]
    total_tokens = sum(item.token_count for item in results)
    return AigcDetectionResult(
        model_id=model_id,
        model_revision=model_revision,
        preprocessing_version=preprocessing_version,
        segment_count=len(results),
        token_count=total_tokens,
        average_risk_score=_weighted_average(results),
        medium_risk_ratio=_ratio(results, AigcRiskLevel.MEDIUM),
        high_risk_ratio=_ratio(results, AigcRiskLevel.HIGH),
        chapters=chapters,
        segments=results,
    )


def _risk(value: float, medium: float, high: float) -> AigcRiskLevel:
    if value >= high:
        return AigcRiskLevel.HIGH
    if value >= medium:
        return AigcRiskLevel.MEDIUM
    return AigcRiskLevel.LOW


def _weighted_average(items: Sequence[AigcSegmentResult]) -> float:
    tokens = sum(item.token_count for item in items)
    if not tokens:
        return 0.0
    return round(sum(item.ai_probability * item.token_count for item in items) / tokens, 6)


def _ratio(items: Sequence[AigcSegmentResult], level: AigcRiskLevel) -> float:
    tokens = sum(item.token_count for item in items)
    if not tokens:
        return 0.0
    return round(
        sum(item.token_count for item in items if item.risk_level is level) / tokens,
        6,
    )
