from __future__ import annotations

import pytest
from pydantic import ValidationError

from debate_agent_framework.schemas import PaperType
from debate_agent_framework.services.review_identity import build_review_fingerprint
from debate_agent_framework.skills import build_default_skill_resolver
from debate_agent_framework.skills.models import SkillRule


def test_ai_profiles_resolve_with_fixed_roles_and_retrieval_contract() -> None:
    resolver = build_default_skill_resolver()

    profiles = [
        resolver.resolve("artificial_intelligence", paper_type)
        for paper_type in PaperType
    ]

    assert {profile.paper_type_id for profile in profiles} == {
        "theory",
        "method",
        "engineering",
    }
    for profile in profiles:
        assert set(profile.specialists) == {
            "scientific_soundness",
            "empirical_evidence",
            "global_quality",
        }
        assert profile.physical_retrieval.top_k.model_dump() == {
            "dense": 5,
            "bm25": 5,
            "rrf": 3,
            "max_per_finding": 2,
        }
        assert profile.rubric_items
        assert profile.base_version == "1.0.0"
        assert "人工复核" in profile.base_guidance
        assert len(profile.profile_hash) == 64

    assert len({profile.physical_retrieval.route_id for profile in profiles}) == 1
    assert {profile.retrieval_overlay.filter_mode for profile in profiles} == {"prefer"}
    assert len({profile.rerank_instruction for profile in profiles}) == 3
    assert len({tuple(profile.specialist_prompts.values()) for profile in profiles}) == 3
    assert len({tuple(item.key for item in profile.rubric_items) for profile in profiles}) == 3


def test_ai_discipline_common_is_resolved_independently() -> None:
    profile = build_default_skill_resolver().resolve_discipline(
        "artificial_intelligence"
    )

    assert profile.skill_id == "ai.common.v1"
    assert {item.paper_type_id for item in profile.paper_types} == {
        "theory", "method", "engineering"
    }
    assert profile.paper_classifier_prompt
    assert len(profile.profile_hash) == 64


def test_profile_hash_and_auto_selection_hash_are_deterministic() -> None:
    first = build_default_skill_resolver()
    second = build_default_skill_resolver()

    assert first.resolve(
        "artificial_intelligence", PaperType.METHOD
    ).profile_hash == second.resolve(
        "artificial_intelligence", PaperType.METHOD
    ).profile_hash
    assert first.selection_hash(
        "artificial_intelligence", None
    ) == second.selection_hash("artificial_intelligence", None)


def test_skill_selection_participates_in_review_fingerprint() -> None:
    baseline = build_review_fingerprint(
        "a" * 64, PaperType.METHOD, skill_selection_hash="1" * 64
    )
    changed = build_review_fingerprint(
        "a" * 64, PaperType.METHOD, skill_selection_hash="2" * 64
    )

    assert baseline != changed


def test_loader_rejects_path_traversal() -> None:
    resolver = build_default_skill_resolver()

    with pytest.raises(ValueError, match="invalid Skill resource path"):
        resolver._path("../outside.json")


def test_mandatory_rule_requires_a_source() -> None:
    with pytest.raises(ValidationError, match="must declare a source"):
        SkillRule(rule_id="AI-INVALID", kind="mandatory", check="citation")
