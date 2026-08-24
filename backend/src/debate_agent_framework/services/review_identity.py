"""Stable review fingerprints and lightweight thesis revision comparison."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass

from ..agents.chapter_rubric import RUBRIC_VERSION
from ..schemas import DebateReviewInput, PaperType


PIPELINE_VERSION = "debate_stable_scoring_v1"
SCORING_RULE_VERSION = "legacy_step7_rubric_stabilized_v2"
AUTO_LINEAGE_THRESHOLD = 0.97


@dataclass(frozen=True)
class RevisionComparison:
    parent_revision_id: str | None
    content_sha256: str
    chapter_hashes: dict[str, str]
    similarity: float | None
    changed_chapter_ids: list[str]
    match_method: str

    @property
    def change_ratio(self) -> float | None:
        return None if self.similarity is None else round(1.0 - self.similarity, 6)


def normalize_review_text(text: str) -> str:
    """Remove parser/layout noise while preserving substantive wording."""

    value = text.casefold()
    value = re.sub(r"<!--.*?-->", " ", value, flags=re.DOTALL)
    value = re.sub(r"!\[[^]]*\]\([^)]*\)", "[image]", value)
    value = re.sub(r"(?:[a-z]:)?[/\\][\w\-./\\ ]+", "[path]", value)
    value = re.sub(r"\s+", "", value)
    return value


def content_sha256(review_input: DebateReviewInput) -> str:
    return _sha256_text(normalize_review_text(review_input.full_text))


def chapter_hashes(review_input: DebateReviewInput) -> dict[str, str]:
    return {
        chapter.chapter_id: _sha256_text(normalize_review_text(chapter.content))
        for chapter in review_input.chapters
    }


def compare_revisions(
    current: DebateReviewInput,
    previous: DebateReviewInput | None,
    *,
    parent_revision_id: str | None = None,
    match_method: str = "new_paper",
) -> RevisionComparison:
    current_hashes = chapter_hashes(current)
    if previous is None:
        return RevisionComparison(
            parent_revision_id=None,
            content_sha256=content_sha256(current),
            chapter_hashes=current_hashes,
            similarity=None,
            changed_chapter_ids=list(current_hashes),
            match_method=match_method,
        )
    previous_hashes = chapter_hashes(previous)
    changed = sorted(
        chapter_id
        for chapter_id in set(current_hashes) | set(previous_hashes)
        if current_hashes.get(chapter_id) != previous_hashes.get(chapter_id)
    )
    similarity = text_similarity(current.full_text, previous.full_text)
    return RevisionComparison(
        parent_revision_id=parent_revision_id,
        content_sha256=content_sha256(current),
        chapter_hashes=current_hashes,
        similarity=similarity,
        changed_chapter_ids=changed,
        match_method=match_method,
    )


def text_similarity(left: str, right: str) -> float:
    """Compare normalized fixed windows in linear time."""

    left_value = normalize_review_text(left)
    right_value = normalize_review_text(right)
    if left_value == right_value:
        return 1.0
    left_windows = _window_hashes(left_value)
    right_windows = _window_hashes(right_value)
    if not left_windows or not right_windows:
        longest = max(len(left_value), len(right_value), 1)
        common = sum(a == b for a, b in zip(left_value, right_value))
        return round(common / longest, 6)
    overlap = len(left_windows & right_windows)
    return round(2.0 * overlap / (len(left_windows) + len(right_windows)), 6)


def build_review_fingerprint(
    normalized_content_sha256: str,
    paper_type: PaperType | str | None,
) -> str:
    """Fingerprint every input/version that is allowed to affect a saved result."""

    type_value = paper_type.value if isinstance(paper_type, PaperType) else paper_type
    payload = {
        "content_sha256": normalized_content_sha256,
        "paper_type": type_value or "auto",
        "model": os.getenv("DEBATE_MODEL", "deepseek-ai/DeepSeek-V4-Pro"),
        "pipeline_version": PIPELINE_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "scoring_rule_version": SCORING_RULE_VERSION,
    }
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _window_hashes(text: str, *, window: int = 32) -> set[str]:
    """Return position-independent character shingles for edit-tolerant matching."""

    if not text:
        return set()
    if len(text) <= window:
        return {text}
    return {text[start : start + window] for start in range(len(text) - window + 1)}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
