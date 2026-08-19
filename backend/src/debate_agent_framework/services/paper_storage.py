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


@dataclass(frozen=True)
class PersistedPaperRevision:
    revision_id: str
    pdf_sha256: str
    revision_dir: Path


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
    ) -> PersistedPaperRevision:
        source_path = Path(source_pdf)
        pdf_sha256 = self._sha256(source_path)
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
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(final_dir, ignore_errors=True)
            raise

        return PersistedPaperRevision(
            revision_id=revision_id,
            pdf_sha256=pdf_sha256,
            revision_dir=final_dir,
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
