"""论文评审 Debate 工作流的数据契约。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """拒绝未声明字段，防止 Agent 静默改变协作协议。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PaperType(StrEnum):
    THEORY = "理论研究"
    METHOD = "方法创新"
    ENGINEERING = "工程实现"


class SpecialistRole(StrEnum):
    SCIENTIFIC_SOUNDNESS = "scientific_soundness"
    EMPIRICAL_EVIDENCE = "empirical_evidence"
    GLOBAL_QUALITY = "global_quality"


class FindingSeverity(StrEnum):
    FATAL = "fatal"
    MAJOR = "major"
    MODERATE = "moderate"
    MINOR = "minor"
    INFO = "info"


class EvidenceKind(StrEnum):
    PAPER = "paper"
    EXTERNAL = "external"


class DebatePosition(StrEnum):
    MAINTAIN = "maintain"
    REVISE = "revise"
    CONCEDE = "concede"
    INSUFFICIENT = "insufficient"


class ResolutionStatus(StrEnum):
    CONFIRMED = "confirmed"
    MOSTLY_CONFIRMED = "mostly_confirmed"
    DISPUTED = "disputed"
    INSUFFICIENT = "insufficient"
    HUMAN_REVIEW = "human_review"


class IssueSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class ChapterInput(StrictModel):
    """原 Step 2 章节识别和语义分组结果。"""

    chapter_id: str = Field(min_length=1)
    chapter_name: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    content: str = Field(min_length=1)
    section_titles: list[str] = Field(default_factory=list)
    reviewable: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)


class RetrievedAdvice(StrictModel):
    """原 Step 3 针对章节检索到的历史建议。"""

    chapter_id: str = Field(min_length=1)
    stage: str = "general"
    suggestions: list[str] = Field(default_factory=list)


class DebateReviewInput(StrictModel):
    """Debate 模块承接原 Step 1、Step 2 和 Step 3 的输入。"""

    paper_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)
    full_text: str = Field(min_length=1)
    paper_type: PaperType
    chapters: list[ChapterInput] = Field(min_length=1)
    step3_advice: list[RetrievedAdvice] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class PaperProfile(StrictModel):
    """供三个 Specialist 共享的全局论文档案。"""

    title: str = Field(min_length=1)
    paper_type: PaperType
    research_problem: str = Field(min_length=1)
    claimed_contributions: list[str] = Field(default_factory=list)
    global_summary: str = Field(min_length=1)
    chapter_relationships: list[str] = Field(default_factory=list)


class ContentPacket(StrictModel):
    """论文过长时使用的语义完整内容包。"""

    packet_id: str = Field(min_length=1)
    chapter_ids: list[str] = Field(min_length=1)
    purpose: str = Field(min_length=1)
    content: str = Field(min_length=1)
    dependency_packet_ids: list[str] = Field(default_factory=list)


class ReviewContext(StrictModel):
    """Context Planner 的输出。"""

    paper_id: str = Field(min_length=1)
    profile: PaperProfile
    full_text: str | None = None
    content_packets: list[ContentPacket] = Field(default_factory=list)
    chapters: list[ChapterInput] = Field(min_length=1)
    step3_advice: list[RetrievedAdvice] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_readable_content(self) -> "ReviewContext":
        if not self.full_text and not self.content_packets:
            raise ValueError("ReviewContext 必须包含全文或语义内容包")
        return self


class ReviewEvidence(StrictModel):
    """论文原文或外部文献中的可追溯证据。"""

    evidence_id: str = Field(min_length=1)
    kind: EvidenceKind
    source_title: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    location: str = Field(min_length=1)
    chapter_id: str | None = None
    doi: str | None = None
    url: str | None = None
    relevance: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def external_source_requires_locator(self) -> "ReviewEvidence":
        if self.kind is EvidenceKind.EXTERNAL and not (self.doi or self.url):
            raise ValueError("外部证据必须包含 DOI 或 URL")
        return self


class ReviewFinding(StrictModel):
    """Specialist 对论文问题的结构化判断。"""

    finding_id: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    severity: FindingSeverity
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    affected_chapter_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_external_verification: bool = False
    verification_query: str | None = None
    requires_human_review: bool = False

    @model_validator(mode="after")
    def enforce_evidence_boundary(self) -> "ReviewFinding":
        if self.needs_external_verification and not self.verification_query:
            raise ValueError("需要外部查证的问题必须给出 verification_query")
        if self.severity in {FindingSeverity.FATAL, FindingSeverity.MAJOR} and not self.evidence:
            if self.confidence > 0.5 or not self.requires_human_review:
                raise ValueError("无证据的高严重度问题必须降低置信度并标记人工复核")
        return self


class IndependentReview(StrictModel):
    """一个 Specialist 在不读取其他意见时形成的独立初审。"""

    review_id: str = Field(min_length=1)
    role: SpecialistRole
    paper_summary: str = Field(min_length=1)
    strengths: list[str] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    author_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class DebateIssue(StrictModel):
    """Review Chair 从独立意见中识别出的关键争议。"""

    issue_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    participating_roles: list[SpecialistRole] = Field(min_length=2)
    conflicting_finding_ids: list[str] = Field(default_factory=list)
    evidence_gap: str = ""
    priority: int = Field(default=1, ge=1, le=5)

    @model_validator(mode="after")
    def require_distinct_roles(self) -> "DebateIssue":
        if len(set(self.participating_roles)) != len(self.participating_roles):
            raise ValueError("DebateIssue.participating_roles 不能重复")
        return self


class DebateQuestion(StrictModel):
    """Chair 发给特定 Specialist 的定向质疑。"""

    question_id: str = Field(min_length=1)
    issue_id: str = Field(min_length=1)
    target_role: SpecialistRole
    prompt: str = Field(min_length=1)
    challenged_finding_ids: list[str] = Field(default_factory=list)
    requires_external_evidence: bool = False
    evidence_query: str | None = None

    @model_validator(mode="after")
    def require_query_when_retrieving(self) -> "DebateQuestion":
        if self.requires_external_evidence and not self.evidence_query:
            raise ValueError("触发证据 RAG 的问题必须提供 evidence_query")
        return self


class DebatePlan(StrictModel):
    """V0 一轮 Debate 的路由计划。"""

    issues: list[DebateIssue] = Field(default_factory=list)
    questions: list[DebateQuestion] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "DebatePlan":
        issue_by_id = {issue.issue_id: issue for issue in self.issues}
        if len(issue_by_id) != len(self.issues):
            raise ValueError("DebateIssue.issue_id 不能重复")
        if len({question.question_id for question in self.questions}) != len(self.questions):
            raise ValueError("DebateQuestion.question_id 不能重复")
        for question in self.questions:
            issue = issue_by_id.get(question.issue_id)
            if issue is None:
                raise ValueError(f"问题 {question.question_id} 引用了未知 DebateIssue")
            if question.target_role not in issue.participating_roles:
                raise ValueError(f"问题 {question.question_id} 的目标 Agent 未参与该争议")
        return self


class DebateResponse(StrictModel):
    """Specialist 阅读对方观点和证据后的回应。"""

    response_id: str = Field(min_length=1)
    issue_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    role: SpecialistRole
    position: DebatePosition
    response: str = Field(min_length=1)
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    revised_findings: list[ReviewFinding] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ResolvedFinding(StrictModel):
    """Chair 根据证据而非多数投票形成的最终问题判断。"""

    finding_id: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    severity: FindingSeverity
    status: ResolutionStatus
    rationale: str = Field(min_length=1)
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    affected_chapter_ids: list[str] = Field(default_factory=list)
    dissenting_views: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def enforce_final_evidence_boundary(self) -> "ResolvedFinding":
        if self.severity in {FindingSeverity.FATAL, FindingSeverity.MAJOR} and not self.evidence:
            allowed = {ResolutionStatus.INSUFFICIENT, ResolutionStatus.HUMAN_REVIEW}
            if self.status not in allowed or self.confidence > 0.5:
                raise ValueError("最终高严重度结论缺少证据时必须降级并降低置信度")
        return self


class DimensionEvaluation(StrictModel):
    dimension: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class GlobalReview(StrictModel):
    """先从全文形成的评审，不受章节输出格式限制。"""

    overall_summary: str = Field(min_length=1)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    author_questions: list[str] = Field(default_factory=list)
    dimensions: list[DimensionEvaluation] = Field(default_factory=list)
    resolved_findings: list[ResolvedFinding] = Field(default_factory=list)
    unresolved_issue_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class SectionStructure(StrictModel):
    section_title: str = Field(min_length=1)
    section_purpose: str = ""
    key_points: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)


class ChapterAdvice(StrictModel):
    position: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)


class CompatibleChapterData(StrictModel):
    """原 Step 4 解析器和 Step 6 可直接消费的章节结构。"""

    chapter_name: str = Field(min_length=1)
    chapter_type: str = Field(min_length=1)
    chapter_summary: str = Field(min_length=1)
    chapter_remark: str = Field(min_length=1)
    section_structure: list[SectionStructure] = Field(default_factory=list)
    extracted_info: dict[str, str] = Field(default_factory=dict)
    evaluation_items: dict[str, str] = Field(default_factory=dict)
    scoring_impact: str = ""
    advice: list[ChapterAdvice] = Field(default_factory=list)


class CompatibleChapterEnvelope(StrictModel):
    chapter_data: CompatibleChapterData


class WorkloadItem(StrictModel):
    score: int = Field(ge=0, le=100)
    analysis: str = ""


class CompatibleStructureEvaluation(StrictModel):
    """字段名与原 Step 5 的 JSON Schema 完全一致。"""

    completeness: WorkloadItem
    abstract_and_keywords: WorkloadItem
    catalog_standardization: WorkloadItem
    chapter_standardization: WorkloadItem
    acknowledgement_standardization: WorkloadItem


class CompatibleWorkloadEvaluation(StrictModel):
    structure_evaluation: CompatibleStructureEvaluation
    summary: str = ""
    workload_evaluation: str = ""


class ReviewSynthesis(StrictModel):
    """Chair 的全文裁决和原 Step 4/5 兼容输出。"""

    global_review: GlobalReview
    chapter_evaluation: dict[str, CompatibleChapterEnvelope]
    workload_evaluation: CompatibleWorkloadEvaluation

    @model_validator(mode="after")
    def validate_chapter_keys(self) -> "ReviewSynthesis":
        if not self.chapter_evaluation:
            raise ValueError("chapter_evaluation 不能为空")
        if any(not key.startswith("chapter_") for key in self.chapter_evaluation):
            raise ValueError("chapter_evaluation 必须使用原项目的 chapter_N 键")
        return self


class SummaryAdviceResult(StrictModel):
    summary: str = Field(min_length=1)
    advice_count: int = Field(default=0, ge=0)


class HistoricalScoreCase(StrictModel):
    case_id: str = Field(min_length=1)
    paper_type: PaperType
    score: float = Field(ge=0.0, le=100.0)
    grade: str = Field(min_length=1)
    similarity: float = Field(ge=0.0, le=1.0)
    comparable_dimensions: list[str] = Field(default_factory=list)
    rationale: str = ""


class ScoreCalibrationQuery(StrictModel):
    paper_type: PaperType
    dimensions: dict[str, str] = Field(default_factory=dict)
    severe_findings: list[str] = Field(default_factory=list)


class ComprehensiveScoreResult(StrictModel):
    """Step 7 的框架级输出，保留原十二项语义评分。"""

    scores: dict[str, float]
    total_score: float = Field(ge=0.0, le=100.0)
    grade: str = Field(min_length=1)
    overall_evaluation: str = Field(min_length=1)
    calibration_notes: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_original_dimensions(self) -> "ComprehensiveScoreResult":
        expected = {str(index) for index in range(1, 13)}
        if set(self.scores) != expected:
            raise ValueError("scores 必须包含原 Step 7 的 1 到 12 共十二项")
        if any(not 0.0 <= value <= 100.0 for value in self.scores.values()):
            raise ValueError("scores 中的每项分数必须位于 0 到 100 之间")
        return self


class DebateWorkflowIssue(StrictModel):
    node: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: IssueSeverity = IssueSeverity.WARNING
    role: SpecialistRole | None = None
    question_id: str | None = None


class DebateRunResult(StrictModel):
    context: ReviewContext
    independent_reviews: list[IndependentReview]
    debate_plan: DebatePlan
    external_evidence: list[ReviewEvidence]
    debate_responses: list[DebateResponse]
    synthesis: ReviewSynthesis
    summary_advice: SummaryAdviceResult | None = None
    historical_score_cases: list[HistoricalScoreCase] = Field(default_factory=list)
    final_score: ComprehensiveScoreResult | None = None
    issues: list[DebateWorkflowIssue] = Field(default_factory=list)
