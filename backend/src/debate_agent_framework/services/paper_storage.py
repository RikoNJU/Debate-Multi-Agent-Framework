"""Atomic local storage for source papers and MinerU artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ..persistence.repositories import PaperRepository
from ..schemas import DebateReviewInput, MinerUParseResult
from .review_identity import (
    AUTO_LINEAGE_THRESHOLD,
    RevisionComparison,
    compare_revisions,
)


@dataclass(frozen=True)
class PersistedPaperRevision:
    revision_id: str
    pdf_sha256: str
    revision_dir: Path
    comparison: RevisionComparison


class PaperPersistenceService:
    def __init__(self, data_dir: str | Path, repository: PaperRepository) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.repository = repository
        self.papers_dir = self.data_dir / "papers"
        self.staging_dir = self.data_dir / ".staging"
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def persist(
        self,
        *,
        review_input: DebateReviewInput,
        parsed: MinerUParseResult,
        source_pdf: str | Path,
        source_filename: str,
        comparison: RevisionComparison | None = None,
    ) -> PersistedPaperRevision:
        source_path = Path(source_pdf)
        pdf_sha256 = self._sha256(source_path)
        comparison = comparison or compare_revisions(review_input, None)
        revision_id = uuid4().hex
        paper_key = hashlib.sha256(
            review_input.paper_id.encode("utf-8")
        ).hexdigest()[:24]
        final_dir = self.papers_dir / paper_key / revision_id
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{revision_id}-", dir=self.staging_dir))

        try:
            shutil.copy2(source_path, staging / "source.pdf")
            (staging / "full.md").write_text(parsed.markdown, encoding="utf-8")
            (staging / "structured_input.json").write_text(
                json.dumps(
                    review_input.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            if parsed.content_list_path and Path(parsed.content_list_path).is_file():
                shutil.copy2(parsed.content_list_path, staging / "content_list.json")
            mineru_output = Path(parsed.output_dir)
            if mineru_output.is_dir():
                shutil.copytree(mineru_output, staging / "mineru")

            os.replace(staging, final_dir)
            artifacts = self._artifact_manifest(final_dir)
            self.repository.save_revision(
                review_input=review_input,
                source_filename=source_filename,
                revision_id=revision_id,
                pdf_sha256=pdf_sha256,
                pdf_path=self._relative(final_dir / "source.pdf"),
                structured_input_path=self._relative(
                    final_dir / "structured_input.json"
                ),
                mineru_batch_id=parsed.batch_id,
                artifacts=artifacts,
                content_sha256=comparison.content_sha256,
                parent_revision_id=comparison.parent_revision_id,
                chapter_hashes=comparison.chapter_hashes,
                change_ratio=comparison.change_ratio,
                change_summary={
                    "match_method": comparison.match_method,
                    "similarity": comparison.similarity,
                    "changed_chapter_ids": comparison.changed_chapter_ids,
                },
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(final_dir, ignore_errors=True)
            raise

        return PersistedPaperRevision(
            revision_id=revision_id,
            pdf_sha256=pdf_sha256,
            revision_dir=final_dir,
            comparison=comparison,
        )

    def resolve_revision_identity(
        self,
        review_input: DebateReviewInput,
        *,
        explicit_paper_id: bool,
    ) -> tuple[DebateReviewInput, RevisionComparison]:
        """Link a conservative near-duplicate to a stable paper lineage."""

        if explicit_paper_id:
            paper = self.repository.get_paper(review_input.paper_id)
            if paper is None:
                return review_input, compare_revisions(
                    review_input, None, match_method="explicit_new_paper"
                )
            revision_id = str(paper["current_revision_id"])
            previous = self.load_review_input(revision_id)
            return review_input, compare_revisions(
                review_input,
                previous,
                parent_revision_id=revision_id,
                match_method="explicit_paper_id",
            )

        best: tuple[float, dict[str, object], DebateReviewInput] | None = None
        for candidate in self.repository.list_current_revision_candidates(
            review_input.title
        ):
            previous = self.load_review_input(str(candidate["revision_id"]))
            comparison = compare_revisions(review_input, previous)
            similarity = comparison.similarity or 0.0
            if best is None or similarity > best[0]:
                best = (similarity, candidate, previous)
        if best is None or best[0] < AUTO_LINEAGE_THRESHOLD:
            return review_input, compare_revisions(
                review_input, None, match_method="new_paper"
            )

        similarity, candidate, previous = best
        paper_id = str(candidate["paper_id"])
        revision_id = str(candidate["revision_id"])
        metadata = {
            **review_input.metadata,
            "revision_match_method": "title_and_content_similarity",
            "revision_similarity": f"{similarity:.6f}",
        }
        linked_input = review_input.model_copy(
            update={"paper_id": paper_id, "metadata": metadata}
        )
        return linked_input, compare_revisions(
            linked_input,
            previous,
            parent_revision_id=revision_id,
            match_method="title_and_content_similarity",
        )

    def _artifact_manifest(self, revision_dir: Path) -> list[dict[str, object]]:
        manifest = []
        for path in sorted(item for item in revision_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(revision_dir).as_posix()
            manifest.append(
                {
                    "artifact_type": self._artifact_type(relative),
                    "relative_path": self._relative(path),
                    "file_size": path.stat().st_size,
                    "sha256": self._sha256(path),
                    "metadata": {},
                }
            )
        return manifest

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        if self.data_dir not in resolved.parents and resolved != self.data_dir:
            raise ValueError("持久化文件路径超出 DEBATE_DATA_DIR")
        return resolved.relative_to(self.data_dir).as_posix()

    def resolve_stored_path(self, stored_path: str | Path) -> Path:
        path = Path(stored_path)
        resolved = (path if path.is_absolute() else self.data_dir / path).resolve()
        if self.data_dir not in resolved.parents and resolved != self.data_dir:
            raise ValueError("持久化文件路径超出 DEBATE_DATA_DIR")
        return resolved

    def load_review_input(self, revision_id: str) -> DebateReviewInput:
        revision = self.repository.get_revision(revision_id)
        if revision is None:
            raise FileNotFoundError("论文版本不存在")
        path = self.resolve_stored_path(revision["structured_input_path"])
        if not path.is_file():
            raise FileNotFoundError("论文结构化输入不存在")
        return DebateReviewInput.model_validate_json(path.read_text(encoding="utf-8"))

    def review_table_dir(self, paper_id: str) -> Path:
        """定位该论文当前版本用于存放评审表产物（tex/pdf）的版本目录。"""
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise ValueError("论文不存在")
        revision_id = paper["current_revision_id"]
        paper_key = hashlib.sha256(paper_id.encode("utf-8")).hexdigest()[:24]
        directory = self.papers_dir / paper_key / revision_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _artifact_type(relative_path: str) -> str:
        name = Path(relative_path).name
        if name == "source.pdf":
            return "source_pdf"
        if name == "structured_input.json":
            return "structured_input"
        if name == "content_list.json":
            return "content_list"
        if name == "full.md":
            return "markdown"
        return "mineru_artifact"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def file_sha256(cls, path: str | Path) -> str:
        return cls._sha256(Path(path))
