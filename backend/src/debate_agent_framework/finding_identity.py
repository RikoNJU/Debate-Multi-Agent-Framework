"""Server-owned Source and Canonical Finding identities."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

from .schemas import (
    FindingResolutionDraft,
    GlobalReview,
    GlobalReviewDraft,
    IndependentReview,
    ResolutionStatus,
    ResolvedFinding,
    ReviewEvidence,
    ReviewFinding,
    SpecialistRole,
)

FINDING_IDENTITY_VERSION = "finding_identity_v2"


def assign_source_identities(
    review: IndependentReview,
    *,
    run_id: str,
    role: SpecialistRole,
) -> IndependentReview:
    """Replace model-local refs with server-owned, role-namespaced Source IDs."""

    local_refs = [finding.finding_id for finding in review.findings]
    if len(local_refs) != len(set(local_refs)):
        raise ValueError(f"{role.value} 输出了重复的局部 Finding 引用")

    fingerprints = [_finding_fingerprint(finding, role) for finding in review.findings]
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError(f"{role.value} 输出了重复语义的 Finding")

    mapping: dict[str, str] = {}
    findings = []
    for finding, fingerprint in zip(review.findings, fingerprints, strict=True):
        local_ref = finding.finding_id
        source_id = _source_id(run_id, role, fingerprint)
        mapping[local_ref] = source_id
        evidence = [
            item.model_copy(
                update={"evidence_id": _evidence_id(source_id, index)}
            )
            for index, item in enumerate(finding.evidence, start=1)
        ]
        findings.append(
            finding.model_copy(
                update={
                    "finding_id": source_id,
                    "local_ref": local_ref,
                    "source_role": role,
                    "identity_version": FINDING_IDENTITY_VERSION,
                    "fingerprint": fingerprint,
                    "evidence": evidence,
                }
            )
        )

    assessments = []
    for assessment in review.rubric_assessments:
        unknown = sorted(set(assessment.finding_ids) - set(mapping))
        if unknown:
            raise ValueError(
                f"Rubric {assessment.item_id} 引用了未知局部 Finding：{unknown}"
            )
        assessments.append(
            assessment.model_copy(
                update={
                    "finding_ids": [mapping[item] for item in assessment.finding_ids]
                }
            )
        )
    return review.model_copy(
        update={"role": role, "findings": findings, "rubric_assessments": assessments}
    )


def canonicalize_review(
    draft: GlobalReviewDraft,
    reviews: Sequence[IndependentReview],
    *,
    run_id: str,
    additional_evidence: Sequence[ReviewEvidence] = (),
) -> GlobalReview:
    """Validate a complete Source partition and mint Canonical Finding IDs."""

    sources = {
        finding.finding_id: finding
        for review in reviews
        for finding in review.findings
    }
    expected = set(sources)
    referenced = [
        source_id
        for resolution in draft.resolved_findings
        for source_id in resolution.source_finding_ids
    ]
    duplicates = sorted(
        source_id
        for source_id in set(referenced)
        if referenced.count(source_id) > 1
    )
    if duplicates:
        raise ValueError(f"Chair 将 Source Finding 重复归并：{duplicates}")
    unknown = sorted(set(referenced) - expected)
    if unknown:
        raise ValueError(f"Chair 引用了未知 Source Finding：{unknown}")
    missing = sorted(expected - set(referenced))
    if missing:
        raise ValueError(f"Chair 未裁决全部 Source Finding：{missing}")

    resolved = [
        _canonical_finding(
            item,
            sources,
            run_id=run_id,
            additional_evidence=additional_evidence,
        )
        for item in draft.resolved_findings
    ]
    return GlobalReview(
        overall_summary=draft.overall_summary,
        strengths=draft.strengths,
        weaknesses=draft.weaknesses,
        author_questions=draft.author_questions,
        dimensions=draft.dimensions,
        resolved_findings=resolved,
        confidence=draft.confidence,
    )


def finding_lineage(global_review: GlobalReview) -> dict[str, list[str]]:
    return {
        finding.finding_id: list(finding.source_finding_ids)
        for finding in global_review.resolved_findings
        if finding.source_finding_ids
    }


def _canonical_finding(
    draft: FindingResolutionDraft,
    sources: dict[str, ReviewFinding],
    *,
    run_id: str,
    additional_evidence: Sequence[ReviewEvidence],
) -> ResolvedFinding:
    members = [sources[source_id] for source_id in draft.source_finding_ids]
    evidence_by_id: dict[str, ReviewEvidence] = {
        evidence.evidence_id: evidence
        for finding in members
        for evidence in finding.evidence
    }
    for evidence in additional_evidence:
        existing = evidence_by_id.get(evidence.evidence_id)
        if existing is not None and existing != evidence:
            raise ValueError(f"Evidence ID 对应了不同内容：{evidence.evidence_id}")
        evidence_by_id[evidence.evidence_id] = evidence
    unknown_evidence = sorted(set(draft.evidence_ids) - set(evidence_by_id))
    if unknown_evidence:
        raise ValueError(f"Chair 引用了未知或伪造 Evidence：{unknown_evidence}")
    evidence_ids = list(dict.fromkeys(draft.evidence_ids))
    if draft.status is ResolutionStatus.CONFIRMED and not evidence_ids:
        evidence_ids = list(evidence_by_id)
    evidence = [evidence_by_id[evidence_id] for evidence_id in evidence_ids]
    allowed_chapters = {
        chapter_id
        for finding in members
        for chapter_id in finding.affected_chapter_ids
    }
    unknown_chapters = sorted(set(draft.affected_chapter_ids) - allowed_chapters)
    if unknown_chapters:
        raise ValueError(f"Chair 为Finding增加了无来源章节：{unknown_chapters}")
    chapters = draft.affected_chapter_ids or sorted(allowed_chapters)
    canonical_id = _canonical_id(run_id, draft.source_finding_ids)
    return ResolvedFinding(
        finding_id=canonical_id,
        source_finding_ids=sorted(draft.source_finding_ids),
        identity_version=FINDING_IDENTITY_VERSION,
        fingerprint=_canonical_fingerprint(members),
        merge_rationale=draft.merge_rationale,
        dimension=draft.dimension,
        claim=draft.claim,
        severity=draft.severity,
        status=draft.status,
        rationale=draft.rationale,
        evidence=evidence,
        affected_chapter_ids=chapters,
        dissenting_views=draft.dissenting_views,
        confidence=draft.confidence,
    )


def _source_id(run_id: str, role: SpecialistRole, fingerprint: str) -> str:
    value = uuid5(NAMESPACE_URL, f"debate:{run_id}:{role.value}:{fingerprint}")
    role_code = {
        SpecialistRole.SCIENTIFIC_SOUNDNESS: "SS",
        SpecialistRole.EMPIRICAL_EVIDENCE: "EE",
        SpecialistRole.GLOBAL_QUALITY: "GQ",
    }[role]
    return f"SF-{role_code}-{value.hex[:20]}"


def _canonical_id(run_id: str, source_ids: Sequence[str]) -> str:
    members = "|".join(sorted(source_ids))
    value = uuid5(NAMESPACE_URL, f"debate:{run_id}:canonical:{members}")
    return f"CF-{value.hex[:24]}"


def _evidence_id(source_id: str, index: int) -> str:
    return f"EV-{source_id[3:]}-{index}"


def _finding_fingerprint(finding: ReviewFinding, role: SpecialistRole) -> str:
    evidence = sorted(
        [
        {
            "chapter_id": item.chapter_id,
            "block_id": item.block_id,
            "chunk_id": item.chunk_id,
            "quote": _normalize(item.quote),
        }
        for item in finding.evidence
        ],
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
    )
    payload = {
        "role": role.value,
        "dimension": finding.dimension,
        "chapters": sorted(finding.affected_chapter_ids),
        "evidence": evidence,
        "claim": _normalize(finding.claim),
    }
    return _hash(payload)


def _canonical_fingerprint(findings: Sequence[ReviewFinding]) -> str:
    return _hash(sorted(finding.fingerprint or finding.finding_id for finding in findings))


def _hash(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()
