from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from debate_agent_framework.agents.chapter_rubric import resolve_rubric_assessments
from debate_agent_framework.finding_identity import (
    FINDING_IDENTITY_VERSION,
    assign_source_identities,
    canonicalize_review,
    finding_lineage,
)
from debate_agent_framework.persistence import Database, SqlAlchemyRunStore
from debate_agent_framework.persistence.models import (
    CanonicalFindingMemberRecord,
    CanonicalFindingRecord,
    SourceFindingRecord,
)
from debate_agent_framework.schemas import (
    EvidenceKind,
    FindingResolutionDraft,
    FindingSeverity,
    GlobalReviewDraft,
    IndependentReview,
    ResolutionStatus,
    ReviewEvidence,
    ReviewFinding,
    RubricAssessment,
    RubricJudgement,
    SpecialistRole,
)


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _review(
    role: SpecialistRole,
    *,
    local_ref: str = "F1",
    claim: str = "实验结论缺少统计显著性检验。",
    with_rubric: bool = False,
) -> IndependentReview:
    finding = ReviewFinding(
        finding_id=local_ref,
        dimension="实验与论证",
        claim=claim,
        rationale="论文只比较了点估计，没有报告显著性检验。",
        severity=FindingSeverity.MODERATE,
        evidence=[
            ReviewEvidence(
                evidence_id="MODEL-E1",
                kind=EvidenceKind.PAPER,
                source_title="第四章 实验",
                quote="本模型取得了最好的性能。",
                location="第四章 4.2",
                chapter_id="C4",
            )
        ],
        affected_chapter_ids=["C4"],
        confidence=0.84,
    )
    assessments = (
        [
            RubricAssessment(
                item_id="R16",
                chapter_id="C4",
                role=role,
                judgement=RubricJudgement.POOR,
                rationale="论证严谨性不足。",
                finding_ids=[local_ref],
                dimension_weights={"8": 1.0},
                confidence=0.82,
            )
        ]
        if with_rubric
        else []
    )
    return IndependentReview(
        review_id=f"REVIEW-{role.value}",
        role=role,
        paper_summary="测试论文",
        findings=[finding],
        rubric_assessments=assessments,
        confidence=0.8,
    )


def _draft(source_ids: list[str], *, evidence_ids: list[str] | None = None) -> GlobalReviewDraft:
    return GlobalReviewDraft(
        overall_summary="论文实验论证仍需增强。",
        resolved_findings=[
            FindingResolutionDraft(
                source_finding_ids=source_ids,
                dimension="实验与论证",
                claim="实验结论缺少统计显著性检验。",
                severity=FindingSeverity.MODERATE,
                status=ResolutionStatus.CONFIRMED,
                rationale="多个专家基于同一实验段落确认该问题。",
                evidence_ids=evidence_ids or [],
                affected_chapter_ids=["C4"],
                confidence=0.88,
                merge_rationale="两个判断指向同一实验结论和同一缺陷。",
            )
        ],
        confidence=0.86,
    )


def test_source_ids_are_role_namespaced_and_local_ref_independent() -> None:
    run_id = "run-identity"
    science = assign_source_identities(
        _review(SpecialistRole.SCIENTIFIC_SOUNDNESS),
        run_id=run_id,
        role=SpecialistRole.SCIENTIFIC_SOUNDNESS,
    )
    empirical = assign_source_identities(
        _review(SpecialistRole.EMPIRICAL_EVIDENCE),
        run_id=run_id,
        role=SpecialistRole.EMPIRICAL_EVIDENCE,
    )
    retried = assign_source_identities(
        _review(SpecialistRole.SCIENTIFIC_SOUNDNESS, local_ref="F7"),
        run_id=run_id,
        role=SpecialistRole.SCIENTIFIC_SOUNDNESS,
    )

    assert science.findings[0].finding_id != empirical.findings[0].finding_id
    assert science.findings[0].finding_id == retried.findings[0].finding_id
    assert science.findings[0].local_ref == "F1"
    assert retried.findings[0].local_ref == "F7"
    assert science.findings[0].identity_version == FINDING_IDENTITY_VERSION
    assert science.findings[0].evidence[0].evidence_id.startswith("EV-SS-")


def test_rubric_local_refs_are_rewritten_to_source_then_canonical_ids() -> None:
    review = assign_source_identities(
        _review(SpecialistRole.SCIENTIFIC_SOUNDNESS, with_rubric=True),
        run_id="run-rubric",
        role=SpecialistRole.SCIENTIFIC_SOUNDNESS,
    )
    source_id = review.findings[0].finding_id
    assert review.rubric_assessments[0].finding_ids == [source_id]

    global_review = canonicalize_review(
        _draft([source_id]), [review], run_id="run-rubric"
    )
    resolved = resolve_rubric_assessments([review], global_review)
    assert resolved[0].finding_ids == [global_review.resolved_findings[0].finding_id]
    assert resolved[0].judgement is RubricJudgement.POOR


def test_chair_merge_creates_canonical_lineage_and_rejects_spoofed_evidence() -> None:
    run_id = "run-merge"
    reviews = [
        assign_source_identities(_review(role), run_id=run_id, role=role)
        for role in (
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            SpecialistRole.EMPIRICAL_EVIDENCE,
        )
    ]
    source_ids = [review.findings[0].finding_id for review in reviews]
    global_review = canonicalize_review(_draft(source_ids), reviews, run_id=run_id)
    canonical = global_review.resolved_findings[0]

    assert canonical.finding_id.startswith("CF-")
    assert canonical.source_finding_ids == sorted(source_ids)
    assert finding_lineage(global_review) == {canonical.finding_id: sorted(source_ids)}
    assert len(canonical.evidence) == 2

    with pytest.raises(ValueError, match="未知或伪造 Evidence"):
        canonicalize_review(
            _draft(source_ids, evidence_ids=["EV-SPOOFED"]),
            reviews,
            run_id=run_id,
        )


def test_chair_partition_rejects_missing_or_duplicate_sources() -> None:
    run_id = "run-partition"
    reviews = [
        assign_source_identities(_review(role), run_id=run_id, role=role)
        for role in (
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            SpecialistRole.EMPIRICAL_EVIDENCE,
        )
    ]
    source_ids = [review.findings[0].finding_id for review in reviews]
    with pytest.raises(ValueError, match="未裁决全部"):
        canonicalize_review(_draft(source_ids[:1]), reviews, run_id=run_id)

    duplicate = _draft(source_ids)
    duplicate.resolved_findings.append(
        duplicate.resolved_findings[0].model_copy(
            update={"source_finding_ids": [source_ids[0]]}
        )
    )
    with pytest.raises(ValueError, match="重复归并"):
        canonicalize_review(duplicate, reviews, run_id=run_id)


def test_successful_run_materializes_source_canonical_and_membership_rows(
    tmp_path: Path,
) -> None:
    database = Database(_database_url(tmp_path / "identity.db"))
    database.create_schema()
    store = SqlAlchemyRunStore(database)
    created = store.create()
    review = assign_source_identities(
        _review(SpecialistRole.SCIENTIFIC_SOUNDNESS),
        run_id=created.task_id,
        role=SpecialistRole.SCIENTIFIC_SOUNDNESS,
    )
    global_review = canonicalize_review(
        _draft([review.findings[0].finding_id]),
        [review],
        run_id=created.task_id,
    )
    result = {
        "finding_identity_version": FINDING_IDENTITY_VERSION,
        "independent_reviews": [review.model_dump(mode="json")],
        "synthesis": {
            "global_review": global_review.model_dump(mode="json")
        },
    }

    snapshot = store.mark_succeeded(created.task_id, result)
    with database.session() as session:
        source_count = session.scalar(select(func.count()).select_from(SourceFindingRecord))
        canonical_count = session.scalar(
            select(func.count()).select_from(CanonicalFindingRecord)
        )
        membership_count = session.scalar(
            select(func.count()).select_from(CanonicalFindingMemberRecord)
        )
    database.dispose()

    assert snapshot.finding_identity_version == FINDING_IDENTITY_VERSION
    assert (source_count, canonical_count, membership_count) == (1, 1, 1)
