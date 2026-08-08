"""Legacy Step 6 selection rules with multi-agent provenance preservation."""

from __future__ import annotations

from collections.abc import Sequence

from ..schemas import (
    DebateReviewInput,
    FindingSeverity,
    ResolvedFinding,
    ReviewSynthesis,
    SummaryAdviceItem,
    SummaryAdviceResult,
)

STEP6_RULE_VERSION = "legacy_step6_v2"
_SEVERITY = {
    FindingSeverity.FATAL: 0,
    FindingSeverity.MAJOR: 1,
    FindingSeverity.MODERATE: 2,
    FindingSeverity.MINOR: 3,
    FindingSeverity.INFO: 4,
}
_ELIGIBLE_STATUS = {"confirmed", "mostly_confirmed", "human_review"}


def build_summary_advice(
    review_input: DebateReviewInput,
    synthesis: ReviewSynthesis,
    proposed: Sequence[SummaryAdviceItem] | None = None,
) -> SummaryAdviceResult:
    """Select at most five important suggestions and bind them to known findings."""

    findings = {
        finding.finding_id: finding
        for finding in synthesis.global_review.resolved_findings
        if finding.status.value in _ELIGIBLE_STATUS
    }
    ranked = sorted(
        findings.values(), key=lambda item: (_SEVERITY[item.severity], -item.confidence)
    )
    items = _normalize_proposed(proposed or [], findings, review_input)
    selected_ids = {finding_id for item in items for finding_id in item.finding_ids}

    candidates = [finding for finding in ranked if finding.finding_id not in selected_ids]
    represented = {chapter_id for item in items for chapter_id in item.affected_chapter_ids}
    available_chapters = {
        chapter_id for finding in ranked for chapter_id in finding.affected_chapter_ids
    }
    while candidates and len(items) < 5:
        if len(represented) < min(2, len(available_chapters)):
            candidate = next(
                (
                    item for item in candidates
                    if set(item.affected_chapter_ids) - represented
                ),
                candidates[0],
            )
        else:
            candidate = candidates[0]
        candidates.remove(candidate)
        items.append(_from_finding(candidate, review_input))
        represented.update(candidate.affected_chapter_ids)

    items = items[:5]
    summary = (
        "\n".join(f"[{item.position}] {item.suggestion}" for item in items)
        if items
        else "未发现需要修改的问题。"
    )
    return SummaryAdviceResult(
        summary=summary,
        advice_count=len(items),
        items=items,
        rule_version=STEP6_RULE_VERSION,
    )


def _normalize_proposed(
    proposed: Sequence[SummaryAdviceItem],
    findings: dict[str, ResolvedFinding],
    review_input: DebateReviewInput,
) -> list[SummaryAdviceItem]:
    normalized = []
    used: set[str] = set()
    for item in proposed:
        valid_ids = [item_id for item_id in item.finding_ids if item_id in findings and item_id not in used]
        if not valid_ids:
            continue
        bound = [findings[item_id] for item_id in valid_ids]
        used.update(valid_ids)
        chapters = list(dict.fromkeys(
            chapter_id for finding in bound for chapter_id in finding.affected_chapter_ids
        ))
        evidence = list(dict.fromkeys(
            evidence.evidence_id for finding in bound for evidence in finding.evidence
        ))
        severity = min((finding.severity for finding in bound), key=_SEVERITY.__getitem__)
        normalized.append(
            SummaryAdviceItem(
                position=_position(chapters, review_input),
                suggestion=item.suggestion,
                severity=severity,
                finding_ids=valid_ids,
                evidence_ids=evidence,
                affected_chapter_ids=chapters,
                requires_human_review=any(
                    finding.status.value == "human_review" for finding in bound
                ),
            )
        )
    return normalized[:5]


def _from_finding(
    finding: ResolvedFinding, review_input: DebateReviewInput
) -> SummaryAdviceItem:
    return SummaryAdviceItem(
        position=_position(finding.affected_chapter_ids, review_input),
        suggestion=f"针对“{finding.claim}”修改正文，并补充可核验的说明或证据。",
        severity=finding.severity,
        finding_ids=[finding.finding_id],
        evidence_ids=[item.evidence_id for item in finding.evidence],
        affected_chapter_ids=finding.affected_chapter_ids,
        requires_human_review=finding.status.value == "human_review",
    )


def _position(chapter_ids: Sequence[str], review_input: DebateReviewInput) -> str:
    names = {chapter.chapter_id: chapter.chapter_name for chapter in review_input.chapters}
    return "、".join(names.get(item, item) for item in chapter_ids) or "全文"
