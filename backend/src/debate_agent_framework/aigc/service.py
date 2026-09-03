from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..ingestion import MarkdownPaperParser, MinerUClient, MinerUConfig, MinerUContentListAdapter
from .aggregation import aggregate_results
from .detector import AigcDetectorConfig, HuggingFaceAigcDetector
from .preprocessing import AigcWindowBuilder, PREPROCESSING_VERSION
from .repository import AigcTaskRepository
from .schemas import AigcAvailability, AigcTaskCreated, AigcTaskStatus


class AigcDetectionService:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        repository: AigcTaskRepository,
        detector: Any | None = None,
        config: AigcDetectorConfig | None = None,
        mineru_client_factory: Any = MinerUClient,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.root = self.data_dir / "aigc"
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = repository
        self.config = config or AigcDetectorConfig.from_env()
        self.detector = detector or HuggingFaceAigcDetector(self.config)
        self.window_builder = AigcWindowBuilder()
        self.mineru_client_factory = mineru_client_factory
        self.max_pdf_bytes = MinerUConfig.from_env().max_pdf_bytes
        self._semaphore = asyncio.Semaphore(
            max(1, int(os.getenv("DEBATE_AIGC_MAX_CONCURRENCY", "1")))
        )

    def availability(self) -> AigcAvailability:
        dependencies = bool(getattr(self.detector, "dependencies_installed", True))
        mineru_configured = bool(MinerUConfig.from_env().token)
        if not self.config.enabled:
            message = "AIGC 检测尚未在后端配置中启用。"
        elif not dependencies:
            message = "缺少本地模型依赖，请安装项目的 aigc 可选依赖。"
        elif not mineru_configured:
            message = "请先配置 DEBATE_MINERU_TOKEN。"
        else:
            message = "服务配置完成，首次检测时将加载本地模型。"
        return AigcAvailability(
            enabled=self.config.enabled,
            ready=self.config.enabled and dependencies and mineru_configured,
            model_id=self.config.model_id,
            dependencies_installed=dependencies,
            mineru_configured=mineru_configured,
            message=message,
        )

    def allocate(self, source_filename: str) -> tuple[AigcTaskCreated, Path]:
        if not self.config.enabled:
            raise RuntimeError("AIGC detection is disabled")
        task_id = uuid4().hex
        access_code = secrets.token_urlsafe(32)
        task_dir = self._task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=False)
        source_path = task_dir / "source.pdf"
        snapshot = self.repository.create(
            task_id=task_id,
            access_code=access_code,
            source_filename=Path(source_filename).name[:255] or "paper.pdf",
            source_pdf_path=self._relative(source_path),
            model_id=self.config.model_id,
            model_revision=self.config.model_revision,
            preprocessing_version=PREPROCESSING_VERSION,
        )
        return (
            AigcTaskCreated(
                task_id=task_id,
                access_code=access_code,
                status=snapshot.status,
                created_at=snapshot.created_at,
            ),
            source_path,
        )

    async def execute(self, task_id: str) -> None:
        async with self._semaphore:
            try:
                source_path = self.resolve_path(self.repository.source_path(task_id))
                task_dir = self._task_dir(task_id)
                self.repository.mark_stage(task_id, AigcTaskStatus.PARSING, "parsing", 10)
                parsed = await self.mineru_client_factory(MinerUConfig.from_env()).parse_pdf(
                    source_path, output_root=task_dir / "mineru"
                )
                review_input = MarkdownPaperParser().parse(
                    parsed.markdown,
                    paper_id=f"aigc-{task_id}",
                    source_filename=self._source_filename(task_id),
                    mineru_batch_id=parsed.batch_id,
                )
                if parsed.content_list_path:
                    review_input = MinerUContentListAdapter().enrich(
                        review_input, parsed.content_list_path
                    )
                self.repository.mark_stage(
                    task_id, AigcTaskStatus.DETECTING, "building_windows", 45
                )
                segments = await asyncio.to_thread(
                    self.window_builder.build, review_input, self.detector
                )
                if not segments:
                    raise ValueError("No valid body text was available for AIGC detection")
                self.repository.mark_stage(
                    task_id, AigcTaskStatus.DETECTING, "model_inference", 65
                )
                probabilities = await asyncio.to_thread(
                    self.detector.predict_probabilities,
                    [segment.text for segment in segments],
                )
                result = aggregate_results(
                    segments,
                    probabilities,
                    model_id=self.config.model_id,
                    model_revision=self.config.model_revision,
                    preprocessing_version=PREPROCESSING_VERSION,
                    medium_threshold=self.config.medium_threshold,
                    high_threshold=self.config.high_threshold,
                )
                (task_dir / "result.json").write_text(
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.repository.mark_succeeded(task_id, result)
            except Exception as exc:
                self.repository.mark_failed(task_id, str(exc))

    def delete(self, task_id: str) -> None:
        task_dir = self._task_dir(task_id).resolve()
        if task_dir.parent != self.root.resolve():
            raise ValueError("AIGC task path escapes the configured data directory")
        if task_dir.exists():
            shutil.rmtree(task_dir)
        self.repository.delete(task_id)

    def _task_dir(self, task_id: str) -> Path:
        if len(task_id) != 32 or any(char not in "0123456789abcdef" for char in task_id):
            raise ValueError("Invalid AIGC task id")
        return self.root / task_id

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        if self.data_dir not in resolved.parents:
            raise ValueError("AIGC file path escapes the configured data directory")
        return resolved.relative_to(self.data_dir).as_posix()

    def resolve_path(self, stored_path: str | Path) -> Path:
        path = Path(stored_path)
        resolved = (path if path.is_absolute() else self.data_dir / path).resolve()
        if self.data_dir not in resolved.parents:
            raise ValueError("AIGC file path escapes the configured data directory")
        return resolved

    def _source_filename(self, task_id: str) -> str:
        snapshot = self.repository.get(task_id)
        if snapshot is None:
            raise KeyError(task_id)
        return snapshot.source_filename
