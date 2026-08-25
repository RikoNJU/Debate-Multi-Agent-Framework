"""Safe loader and deterministic composer for bundled review Skills."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .models import (
    BaseSkillManifest,
    ChapterTaxonomy,
    DisciplineManifest,
    PhysicalRetrievalRoute,
    ResolvedDisciplineProfile,
    ResolvedReviewProfile,
    RetrievalOverlay,
    RubricItem,
    SkillManifest,
    SkillRule,
    SpecialistPersona,
)

MAX_CONFIG_BYTES = 1_000_000
MAX_PROMPT_BYTES = 200_000
_PERSONAS = TypeAdapter(list[SpecialistPersona])
_RUBRIC = TypeAdapter(list[RubricItem])
_RULES = TypeAdapter(list[SkillRule])


class ReviewSkillResolver:
    """Compose Base + Discipline Common + Paper Type into frozen profiles."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        self._base = BaseSkillManifest.model_validate(
            self._read_json(self._path("base/manifest.json"))
        )
        self._disciplines = self._discover_disciplines()
        self._manifests = self._discover_paper_types()

    def resolve_discipline(self, discipline_id: str) -> ResolvedDisciplineProfile:
        manifest = self._disciplines.get(discipline_id)
        if manifest is None or manifest.status != "active":
            raise ValueError(f"no active Discipline Skill for {discipline_id}")
        raw: dict[str, Any] = {
            **manifest.model_dump(mode="json"),
            "paper_classifier_prompt": self._read_text(
                self._path(manifest.paper_classifier_prompt_file), MAX_PROMPT_BYTES
            ),
        }
        self._drop_file_fields(raw)
        raw["profile_hash"] = self._hash(raw)
        return ResolvedDisciplineProfile.model_validate(raw)

    def resolve(self, discipline_id: str, paper_type: object) -> ResolvedReviewProfile:
        value = getattr(paper_type, "value", paper_type)
        matches = [
            manifest
            for manifest in self._manifests.values()
            if manifest.discipline_id == discipline_id
            and (manifest.paper_type_id == value or manifest.legacy_paper_type == value)
            and manifest.status == "active"
        ]
        if len(matches) != 1:
            raise ValueError(
                f"cannot resolve active review Skill for {discipline_id}/{value}"
            )
        return self._load_profile(self._disciplines[discipline_id], matches[0])

    def selection_hash(self, discipline_id: str, paper_type: object | None) -> str:
        if paper_type is not None:
            return self.resolve(discipline_id, paper_type).profile_hash
        discipline = self._disciplines.get(discipline_id)
        if discipline is None or discipline.status != "active":
            raise ValueError(f"no active Discipline Skill for {discipline_id}")
        hashes = sorted(
            self._load_profile(discipline, item).profile_hash
            for item in self._manifests.values()
            if item.discipline_id == discipline_id and item.status == "active"
        )
        if not hashes:
            raise ValueError(f"no active review Skills for discipline {discipline_id}")
        return hashlib.sha256("|".join(hashes).encode()).hexdigest()

    def _discover_disciplines(self) -> dict[str, DisciplineManifest]:
        result: dict[str, DisciplineManifest] = {}
        for path in sorted(self.root.glob("disciplines/*/common/manifest.json")):
            manifest = DisciplineManifest.model_validate(self._read_json(path))
            if manifest.discipline_id in result:
                raise ValueError(f"duplicate discipline_id: {manifest.discipline_id}")
            result[manifest.discipline_id] = manifest
        if not result:
            raise ValueError(f"no Discipline Skill manifests found below {self.root}")
        return result

    def _discover_paper_types(self) -> dict[str, SkillManifest]:
        manifests: dict[str, SkillManifest] = {}
        for path in sorted(self.root.glob("disciplines/*/*/manifest.json")):
            if path.parent.name == "common":
                continue
            manifest = SkillManifest.model_validate(self._read_json(path))
            discipline = self._disciplines.get(manifest.discipline_id)
            if discipline is None:
                raise ValueError(f"paper type references unknown discipline: {manifest.skill_id}")
            declared = {
                (item.paper_type_id, item.legacy_value) for item in discipline.paper_types
            }
            if (manifest.paper_type_id, manifest.legacy_paper_type) not in declared:
                raise ValueError(f"paper type is not declared by Discipline Skill: {manifest.skill_id}")
            if manifest.skill_id in manifests:
                raise ValueError(f"duplicate skill_id: {manifest.skill_id}")
            manifests[manifest.skill_id] = manifest
        if not manifests:
            raise ValueError(f"no Paper Type Skill manifests found below {self.root}")
        return manifests

    def _load_profile(
        self, discipline: DisciplineManifest, manifest: SkillManifest
    ) -> ResolvedReviewProfile:
        if manifest.score_schema_id != self._base.score_schema_id:
            raise ValueError("paper-type Skill cannot override the Base score schema in V1")
        personas = {
            **self._load_personas(discipline.specialists_file),
            **self._load_personas(manifest.specialists_file),
        }
        prompts = {
            role: self._read_text(self._path(persona.prompt_file), MAX_PROMPT_BYTES)
            for role, persona in personas.items()
        }
        common_rubric = _RUBRIC.validate_python(
            self._read_json(self._path(discipline.rubric_file))
        )
        type_rubric = _RUBRIC.validate_python(
            self._read_json(self._path(manifest.rubric_file))
        )
        common_rules = _RULES.validate_python(
            self._read_json(self._path(discipline.rules_file))
        )
        type_rules = _RULES.validate_python(
            self._read_json(self._path(manifest.rules_file))
        )
        physical = PhysicalRetrievalRoute.model_validate(
            self._read_json(self._path(discipline.retrieval_file))
        )
        overlay = RetrievalOverlay.model_validate(
            self._read_json(self._path(manifest.retrieval_overlay_file))
        )
        raw: dict[str, Any] = {
            **manifest.model_dump(mode="json"),
            "base_version": self._base.version,
            "base_guidance": self._read_text(
                self._path(self._base.guidance_file), MAX_PROMPT_BYTES
            ),
            "discipline_skill_id": discipline.skill_id,
            "discipline_version": discipline.version,
            "specialists": personas,
            "specialist_prompts": prompts,
            "chapter_taxonomy": ChapterTaxonomy.model_validate(
                self._read_json(self._path(manifest.chapter_taxonomy_file))
            ),
            "chapter_classifier_prompt": self._read_text(
                self._path(manifest.chapter_classifier_prompt_file), MAX_PROMPT_BYTES
            ),
            "rubric_items": [*common_rubric, *type_rubric],
            "rules": [*common_rules, *type_rules],
            "physical_retrieval": physical,
            "retrieval_overlay": overlay,
            "rerank_instruction": self._read_text(
                self._path(overlay.rerank_instruction_file), MAX_PROMPT_BYTES
            ),
            "chair_guidance": self._read_text(
                self._path(manifest.chair_guidance_file), MAX_PROMPT_BYTES
            ),
            "terminology": self._load_terminology(discipline.terminology_file),
        }
        self._drop_file_fields(raw)
        raw["profile_hash"] = self._hash(raw)
        return ResolvedReviewProfile.model_validate(raw)

    def _load_personas(self, relative: str) -> dict[str, SpecialistPersona]:
        items = _PERSONAS.validate_python(self._read_json(self._path(relative)))
        if len({item.role for item in items}) != len(items):
            raise ValueError(f"duplicate specialist role in {relative}")
        return {item.role: item for item in items}

    def _load_terminology(self, relative: str) -> list[str]:
        return [
            line.strip()
            for line in self._read_text(self._path(relative), MAX_CONFIG_BYTES).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    @staticmethod
    def _drop_file_fields(raw: dict[str, Any]) -> None:
        for key in tuple(raw):
            if key.endswith("_file"):
                raw.pop(key)

    @staticmethod
    def _hash(raw: dict[str, Any]) -> str:
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _path(self, relative: str) -> Path:
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"invalid Skill resource path: {relative}")
        unresolved = self.root / relative_path
        if any(part.is_symlink() for part in (unresolved, *unresolved.parents)):
            raise ValueError(f"Skill resource cannot use symlinks: {relative}")
        candidate = unresolved.resolve(strict=True)
        if not candidate.is_relative_to(self.root):
            raise ValueError(f"Skill resource escapes configured root: {relative}")
        return candidate

    def _read_json(self, path: Path) -> Any:
        return json.loads(self._read_text(path, MAX_CONFIG_BYTES))

    @staticmethod
    def _read_text(path: Path, limit: int) -> str:
        if path.stat().st_size > limit:
            raise ValueError(f"Skill resource is too large: {path}")
        return path.read_text(encoding="utf-8")


def build_default_skill_resolver() -> ReviewSkillResolver:
    root = Path(__file__).resolve().parent.parent / "review_skills"
    return ReviewSkillResolver(root)
