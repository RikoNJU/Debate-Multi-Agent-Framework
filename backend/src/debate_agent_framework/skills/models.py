"""Strict, data-only contracts for versioned discipline review Skills."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SpecialistRoleId = Literal[
    "scientific_soundness", "empirical_evidence", "global_quality"
]
SkillStatus = Literal["active", "draft", "disabled"]
RuleKind = Literal["mandatory", "recommended", "heuristic"]
FilterMode = Literal["none", "prefer", "strict"]


class SkillModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SkillRule(SkillModel):
    rule_id: str = Field(min_length=1)
    kind: RuleKind
    check: str = Field(min_length=1)
    source: str | None = None

    @model_validator(mode="after")
    def mandatory_requires_source(self) -> "SkillRule":
        if self.kind == "mandatory" and not self.source:
            raise ValueError("mandatory rule must declare a source")
        return self


class SpecialistPersona(SkillModel):
    role: SpecialistRoleId
    persona_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    display_name: str = Field(min_length=1)
    prompt_file: str = Field(min_length=1)
    checks: list[str] = Field(min_length=1)


class RubricDimension(SkillModel):
    dimension: str = Field(pattern=r"^(?:[1-9]|1[0-2])$")
    weight: float = Field(gt=0)


class RubricItem(SkillModel):
    key: str = Field(pattern=r"^[a-z0-9_.-]+$")
    label: str = Field(min_length=1)
    criteria: str = Field(min_length=1)
    role: SpecialistRoleId
    stages: list[str] = Field(min_length=1)
    dimensions: list[RubricDimension] = Field(min_length=1)
    evidence_required_for_negative: bool = True


class PaperTypeDefinition(SkillModel):
    paper_type_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    legacy_value: str = Field(min_length=1)
    description: str = Field(min_length=1)
    signals: list[str] = Field(min_length=1)


class ChapterStageRule(SkillModel):
    label: str = Field(min_length=1)
    any_terms: list[str] = Field(default_factory=list)
    all_terms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_terms(self) -> "ChapterStageRule":
        if not self.any_terms and not self.all_terms:
            raise ValueError("chapter stage rule must contain matching terms")
        return self


class ChapterTaxonomy(SkillModel):
    allowed_labels: list[str] = Field(min_length=1)
    deterministic_rules: list[ChapterStageRule] = Field(min_length=1)
    fallback_label: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_labels(self) -> "ChapterTaxonomy":
        allowed = set(self.allowed_labels)
        if len(allowed) != len(self.allowed_labels):
            raise ValueError("chapter taxonomy labels must be unique")
        unknown = {rule.label for rule in self.deterministic_rules} - allowed
        if self.fallback_label not in allowed or unknown:
            raise ValueError(f"chapter taxonomy references unknown labels: {sorted(unknown)}")
        return self


class RetrievalTopK(SkillModel):
    dense: int = Field(default=5, ge=1, le=20)
    bm25: int = Field(default=5, ge=1, le=20)
    rrf: int = Field(default=3, ge=1, le=20)
    max_per_finding: int = Field(default=2, ge=1, le=2)

    @model_validator(mode="after")
    def fusion_cannot_expand_candidates(self) -> "RetrievalTopK":
        if self.rrf > self.dense + self.bm25:
            raise ValueError("rrf top-k cannot exceed merged candidate count")
        return self


class PhysicalRetrievalRoute(SkillModel):
    route_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    content_collection: str = Field(min_length=1)
    format_collection: str = Field(min_length=1)
    bm25_index_id: str = Field(min_length=1)
    terminology_version: str = Field(min_length=1)
    top_k: RetrievalTopK = Field(default_factory=RetrievalTopK)


class RetrievalOverlay(SkillModel):
    paper_type_values: list[str] = Field(min_length=1)
    filter_mode: FilterMode = "prefer"
    preferred_boost: float = Field(default=1.15, ge=1.0, le=2.0)
    rerank_instruction_file: str = Field(min_length=1)


class BaseSkillManifest(SkillModel):
    skill_id: Literal["review.base"] = "review.base"
    version: str = Field(min_length=1)
    score_schema_id: str = "legacy_18_dimensions_v1"
    guidance_file: str = Field(min_length=1)


class DisciplineManifest(SkillModel):
    skill_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    discipline_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    version: str = Field(min_length=1)
    status: SkillStatus = "active"
    classification_version: str = Field(min_length=1)
    paper_types: list[PaperTypeDefinition] = Field(min_length=1)
    paper_classifier_prompt_file: str
    specialists_file: str
    rubric_file: str
    rules_file: str
    retrieval_file: str
    terminology_file: str

    @model_validator(mode="after")
    def unique_paper_types(self) -> "DisciplineManifest":
        ids = [item.paper_type_id for item in self.paper_types]
        values = [item.legacy_value for item in self.paper_types]
        if len(ids) != len(set(ids)) or len(values) != len(set(values)):
            raise ValueError("discipline paper type IDs and legacy values must be unique")
        return self


class SkillManifest(SkillModel):
    """Paper-type Skill manifest; physical resources belong to Discipline Common."""

    skill_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    discipline_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    paper_type_id: str = Field(pattern=r"^[a-z0-9_.-]+$")
    legacy_paper_type: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: SkillStatus = "active"
    classification_version: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    score_schema_id: str = "legacy_18_dimensions_v1"
    specialists_file: str
    chapter_taxonomy_file: str
    chapter_classifier_prompt_file: str
    rubric_file: str
    rules_file: str
    retrieval_overlay_file: str
    chair_guidance_file: str


class ResolvedDisciplineProfile(SkillModel):
    skill_id: str
    discipline_id: str
    version: str
    status: SkillStatus
    classification_version: str
    profile_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    paper_types: list[PaperTypeDefinition]
    paper_classifier_prompt: str

    def audit_summary(self) -> dict[str, str]:
        return {
            "discipline_skill_id": self.skill_id,
            "discipline_version": self.version,
            "discipline_classification_version": self.classification_version,
            "discipline_profile_hash": self.profile_hash,
        }


class ResolvedReviewProfile(SkillModel):
    base_version: str
    base_guidance: str
    discipline_skill_id: str
    discipline_version: str
    skill_id: str
    discipline_id: str
    paper_type_id: str
    legacy_paper_type: str
    version: str
    status: SkillStatus
    classification_version: str
    rubric_version: str
    score_schema_id: str
    profile_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    specialists: dict[SpecialistRoleId, SpecialistPersona]
    specialist_prompts: dict[SpecialistRoleId, str]
    chapter_taxonomy: ChapterTaxonomy
    chapter_classifier_prompt: str
    rubric_items: list[RubricItem]
    rules: list[SkillRule]
    physical_retrieval: PhysicalRetrievalRoute
    retrieval_overlay: RetrievalOverlay
    rerank_instruction: str
    chair_guidance: str
    terminology: list[str]

    @model_validator(mode="after")
    def validate_composition(self) -> "ResolvedReviewProfile":
        required = {
            "scientific_soundness", "empirical_evidence", "global_quality"
        }
        if set(self.specialists) != required or set(self.specialist_prompts) != required:
            raise ValueError("review Skill must configure exactly the three fixed roles")
        keys = [item.key for item in self.rubric_items]
        if len(keys) != len(set(keys)):
            raise ValueError("resolved rubric item keys must be unique")
        return self

    def audit_summary(self) -> dict[str, str]:
        return {
            "skill_id": self.skill_id,
            "base_version": self.base_version,
            "discipline_skill_id": self.discipline_skill_id,
            "discipline_version": self.discipline_version,
            "discipline_id": self.discipline_id,
            "paper_type_id": self.paper_type_id,
            "skill_version": self.version,
            "classification_version": self.classification_version,
            "rubric_version": self.rubric_version,
            "retrieval_route_id": self.physical_retrieval.route_id,
            "score_schema_id": self.score_schema_id,
            "profile_hash": self.profile_hash,
        }
