"""不调用真实模型的 Evidence-Grounded Debate 演示实现。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from ..schemas import (
    ComprehensiveScoreResult,
    ContentPacket,
    DebateIssue,
    DebatePlan,
    DebatePosition,
    DebateQuestion,
    DebateResponse,
    DebateReviewInput,
    DimensionEvaluation,
    EvidenceKind,
    FindingSeverity,
    GlobalReview,
    HistoricalScoreCase,
    IndependentReview,
    PaperProfile,
    ResolutionStatus,
    ResolvedFinding,
    ReviewContext,
    ReviewEvidence,
    ReviewFinding,
    ReviewSynthesis,
    ScoreCalibrationQuery,
    SpecialistRole,
    SummaryAdviceResult,
)
from .compat import assemble_review_synthesis


class DemoContextPlanner:
    """短论文使用全文，长论文按相邻章节构造语义内容包。"""

    def __init__(self, full_text_limit: int = 20_000) -> None:
        self.full_text_limit = full_text_limit

    def build(self, review_input: DebateReviewInput) -> ReviewContext:
        profile = PaperProfile(
            title=review_input.title,
            paper_type=review_input.paper_type,
            research_problem=review_input.abstract or review_input.title,
            claimed_contributions=[review_input.abstract] if review_input.abstract else [],
            global_summary=review_input.abstract or review_input.title,
            chapter_relationships=[
                f"{left.chapter_name} → {right.chapter_name}"
                for left, right in zip(
                    review_input.chapters, review_input.chapters[1:], strict=False
                )
            ],
        )
        if len(review_input.full_text) <= self.full_text_limit:
            full_text = review_input.full_text
            packets: list[ContentPacket] = []
        else:
            full_text = None
            packets = []
            for index in range(0, len(review_input.chapters), 2):
                group = review_input.chapters[index : index + 2]
                packets.append(
                    ContentPacket(
                        packet_id=f"PACKET-{index // 2 + 1}",
                        chapter_ids=[chapter.chapter_id for chapter in group],
                        purpose="保留相邻章节之间的方法、实验或结论关系",
                        content="\n\n".join(chapter.content for chapter in group),
                        dependency_packet_ids=(
                            [f"PACKET-{index // 2}"] if index >= 2 else []
                        ),
                    )
                )
        return ReviewContext(
            paper_id=review_input.paper_id,
            profile=profile,
            full_text=full_text,
            content_packets=packets,
            chapters=review_input.chapters,
            step3_advice=review_input.step3_advice,
            structured_document=review_input.structured_document,
            metadata=review_input.metadata,
        )


class DemoSpecialist:
    """按角色生成确定性独立意见和定向回应。"""

    def __init__(self, role: SpecialistRole) -> None:
        self.role = role

    async def review(self, context: ReviewContext) -> IndependentReview:
        await asyncio.sleep(0.01)
        chapter = self._focus_chapter(context)
        evidence = self._paper_evidence(chapter.chapter_id, chapter.chapter_name, chapter.content)

        if self.role is SpecialistRole.SCIENTIFIC_SOUNDNESS:
            finding = ReviewFinding(
                finding_id="F-SCIENCE-1",
                dimension="理论与方法",
                claim="方法设计在论文内部基本自洽，但关键假设的适用边界说明不足。",
                rationale="方法章节给出了流程描述，但没有完整讨论失效条件。",
                severity=FindingSeverity.MODERATE,
                evidence=[evidence],
                affected_chapter_ids=[chapter.chapter_id],
                confidence=0.78,
            )
            strengths = ["研究目标与方法路线具有直接对应关系"]
        elif self.role is SpecialistRole.EMPIRICAL_EVIDENCE:
            finding = ReviewFinding(
                finding_id="F-EMPIRICAL-1",
                dimension="实验与证据",
                claim="现有实验缺少关键强 Baseline，尚不足以支持方法优越性。",
                rationale="实验章节只报告主要结果，没有覆盖同类强方法。",
                severity=FindingSeverity.MAJOR,
                evidence=[evidence],
                affected_chapter_ids=[chapter.chapter_id],
                confidence=0.84,
                needs_external_verification=True,
                verification_query="该研究方向常用的强 Baseline 与标准实验设置",
            )
            strengths = ["论文报告了主要实验指标和基础对比结果"]
        else:
            finding = ReviewFinding(
                finding_id="F-GLOBAL-1",
                dimension="结构、工作量与表达",
                claim="章节顺序完整，但方法贡献与实验验证之间的回指不够清晰。",
                rationale="读者需要跨章节推断实验指标对应的具体贡献。",
                severity=FindingSeverity.MINOR,
                evidence=[evidence],
                affected_chapter_ids=[chapter.chapter_id],
                confidence=0.75,
            )
            strengths = ["全文结构覆盖引言、方法、实验和结论"]

        return IndependentReview(
            review_id=f"REVIEW-{self.role.value}",
            role=self.role,
            paper_summary=context.profile.global_summary,
            strengths=strengths,
            findings=[finding],
            author_questions=[f"请说明：{finding.claim}"],
            confidence=finding.confidence,
        )

    async def respond(
        self,
        context: ReviewContext,
        *,
        own_review: IndependentReview,
        issue: DebateIssue,
        question: DebateQuestion,
        peer_reviews: Sequence[IndependentReview],
        external_evidence: Sequence[ReviewEvidence],
    ) -> DebateResponse:
        await asyncio.sleep(0.01)
        if self.role is SpecialistRole.SCIENTIFIC_SOUNDNESS:
            position = DebatePosition.REVISE
            response = "理论自洽只能证明方法可解释，不能替代对核心贡献的充分实验验证。"
        else:
            position = DebatePosition.MAINTAIN
            response = "外部证据显示该方向通常需要更强对比，因此保留实验不足判断。"
        return DebateResponse(
            response_id=f"RESPONSE-{question.question_id}",
            issue_id=issue.issue_id,
            question_id=question.question_id,
            role=self.role,
            position=position,
            response=response,
            evidence=list(external_evidence) if question.requires_external_evidence else [],
            revised_findings=[],
            confidence=0.86,
        )

    def _focus_chapter(self, context: ReviewContext):
        preferred = {
            SpecialistRole.SCIENTIFIC_SOUNDNESS: ("方法", "模型", "系统设计"),
            SpecialistRole.EMPIRICAL_EVIDENCE: ("实验", "评估", "结果"),
            SpecialistRole.GLOBAL_QUALITY: ("引言", "绪论", "结论"),
        }[self.role]
        return next(
            (
                chapter
                for chapter in context.chapters
                if any(word in chapter.stage or word in chapter.chapter_name for word in preferred)
            ),
            context.chapters[0],
        )

    @staticmethod
    def _paper_evidence(chapter_id: str, chapter_name: str, content: str) -> ReviewEvidence:
        return ReviewEvidence(
            evidence_id=f"PAPER-{chapter_id}",
            kind=EvidenceKind.PAPER,
            source_title=chapter_name,
            quote=content[:160],
            location=chapter_name,
            chapter_id=chapter_id,
            relevance=0.9,
            confidence=0.95,
        )


class DemoReviewChair:
    """用规则演示争议路由和非多数投票式证据综合。"""

    def plan_debate(
        self,
        context: ReviewContext,
        reviews: Sequence[IndependentReview],
    ) -> DebatePlan:
        roles = {review.role for review in reviews}
        required = {
            SpecialistRole.SCIENTIFIC_SOUNDNESS,
            SpecialistRole.EMPIRICAL_EVIDENCE,
        }
        if not required.issubset(roles):
            return DebatePlan()

        issue = DebateIssue(
            issue_id="ISSUE-METHOD-EVIDENCE",
            title="理论成立是否足以支持核心贡献",
            description="方法 Agent 认可理论路线，实验 Agent 认为验证不足。",
            participating_roles=[
                SpecialistRole.SCIENTIFIC_SOUNDNESS,
                SpecialistRole.EMPIRICAL_EVIDENCE,
            ],
            conflicting_finding_ids=["F-SCIENCE-1", "F-EMPIRICAL-1"],
            evidence_gap="需要确认该方向应采用的强 Baseline 和标准实验设置",
            priority=5,
        )
        return DebatePlan(
            issues=[issue],
            questions=[
                DebateQuestion(
                    question_id="Q-SCIENCE-1",
                    issue_id=issue.issue_id,
                    target_role=SpecialistRole.SCIENTIFIC_SOUNDNESS,
                    prompt="理论成立是否足以支撑论文声称的整体贡献？",
                    challenged_finding_ids=["F-SCIENCE-1"],
                ),
                DebateQuestion(
                    question_id="Q-EMPIRICAL-1",
                    issue_id=issue.issue_id,
                    target_role=SpecialistRole.EMPIRICAL_EVIDENCE,
                    prompt="请说明缺失的关键验证并给出依据。",
                    challenged_finding_ids=["F-EMPIRICAL-1"],
                    requires_external_evidence=True,
                    evidence_query="该研究方向常用的强 Baseline 与标准实验设置",
                ),
            ],
        )

    def synthesize(
        self,
        context: ReviewContext,
        *,
        reviews: Sequence[IndependentReview],
        debate_plan: DebatePlan,
        responses: Sequence[DebateResponse],
        external_evidence: Sequence[ReviewEvidence],
    ) -> ReviewSynthesis:
        findings = [finding for review in reviews for finding in review.findings]
        response_issue_ids = {response.issue_id for response in responses}
        resolved: list[ResolvedFinding] = []
        for finding in findings:
            evidence = list(finding.evidence)
            if finding.needs_external_verification:
                evidence.extend(external_evidence)
            resolved.append(
                ResolvedFinding(
                    finding_id=finding.finding_id,
                    dimension=finding.dimension,
                    claim=finding.claim,
                    severity=finding.severity,
                    status=(
                        ResolutionStatus.CONFIRMED
                        if not finding.needs_external_verification or external_evidence
                        else ResolutionStatus.INSUFFICIENT
                    ),
                    rationale=finding.rationale,
                    evidence=evidence,
                    affected_chapter_ids=finding.affected_chapter_ids,
                    dissenting_views=[
                        response.response
                        for response in responses
                        if response.position is DebatePosition.REVISE
                    ],
                    confidence=min(0.9, finding.confidence + (0.05 if responses else 0.0)),
                )
            )

        global_review = GlobalReview(
            overall_summary=context.profile.global_summary,
            strengths=[item for review in reviews for item in review.strengths],
            weaknesses=[finding.claim for finding in findings],
            author_questions=[item for review in reviews for item in review.author_questions],
            dimensions=[
                DimensionEvaluation(
                    dimension=review.findings[0].dimension,
                    summary=review.findings[0].rationale,
                    strengths=review.strengths,
                    weaknesses=[finding.claim for finding in review.findings],
                    confidence=review.confidence,
                )
                for review in reviews
                if review.findings
            ],
            resolved_findings=resolved,
            unresolved_issue_ids=[
                issue.issue_id
                for issue in debate_plan.issues
                if issue.issue_id not in response_issue_ids
            ],
            confidence=sum(review.confidence for review in reviews) / len(reviews),
        )
        return assemble_review_synthesis(context, global_review)


class DemoEvidenceRetriever:
    def retrieve(
        self,
        queries: Sequence[str],
        *,
        context: ReviewContext,
        limit: int,
    ) -> Sequence[ReviewEvidence]:
        return [
            ReviewEvidence(
                evidence_id="EXTERNAL-BASELINE-1",
                kind=EvidenceKind.EXTERNAL,
                source_title="Demo Baseline Survey",
                quote="Strong baselines and ablation studies are required for comparison.",
                location="Evaluation Protocol, paragraph 2",
                url="https://example.org/demo-baseline-survey",
                relevance=0.88,
                confidence=0.8,
            )
        ][:limit]


class DemoHistoricalScoreRetriever:
    def retrieve(
        self,
        query: ScoreCalibrationQuery,
        *,
        limit: int,
    ) -> Sequence[HistoricalScoreCase]:
        return [
            HistoricalScoreCase(
                case_id="CASE-001",
                paper_type=query.paper_type,
                score=79,
                grade="良好",
                similarity=0.81,
                comparable_dimensions=list(query.dimensions),
                rationale="方法完整，但实验覆盖不足。",
            )
        ][:limit]


class DemoOriginalPipelineAdapter:
    """模拟复用原 Step 6/7；生产实现应直接包装原项目函数。"""

    def summarize_advice(
        self,
        review_input: DebateReviewInput,
        synthesis: ReviewSynthesis,
    ) -> SummaryAdviceResult:
        from .legacy_summary import build_summary_advice

        return build_summary_advice(review_input, synthesis)

    def score(
        self,
        review_input: DebateReviewInput,
        synthesis: ReviewSynthesis,
        *,
        summary_advice: SummaryAdviceResult,
        historical_cases: Sequence[HistoricalScoreCase],
    ) -> ComprehensiveScoreResult:
        scores = {str(index): float(84 - (index % 5)) for index in range(1, 13)}
        scores["6"] = 76.0
        scores["9"] = 78.0
        total = round(sum(scores.values()) / len(scores), 1)
        notes = [
            f"参考案例 {case.case_id}（相似度 {case.similarity:.2f}），仅用于尺度校准"
            for case in historical_cases
        ]
        return ComprehensiveScoreResult(
            scores=scores,
            total_score=total,
            grade="良好" if total >= 75 else "一般",
            overall_evaluation=synthesis.global_review.overall_summary,
            calibration_notes=notes,
            confidence=0.79 if historical_cases else 0.7,
        )
