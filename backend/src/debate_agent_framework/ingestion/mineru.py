"""Async MinerU cloud adapter extracted from the legacy upload router."""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..schemas import MinerUParseResult


class MinerUError(RuntimeError):
    pass


class MinerUTimeoutError(MinerUError):
    pass


class MinerUConfigurationError(MinerUError):
    pass


class InvalidPdfError(MinerUError):
    pass


@dataclass(frozen=True)
class MinerUConfig:
    api_base: str
    token: str
    model_version: str = "vlm"
    is_ocr: bool = False
    enable_table: bool = True
    enable_formula: bool = True
    language: str = "ch"
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 600.0
    request_timeout_seconds: float = 60.0
    max_pdf_bytes: int = 50 * 1024 * 1024
    max_archive_bytes: int = 200 * 1024 * 1024
    max_archive_files: int = 5_000

    @classmethod
    def from_env(cls) -> "MinerUConfig":
        return cls(
            api_base=_env_value(
                "DEBATE_MINERU_API_BASE", "MINERU_API_BASE", default="https://mineru.net/api/v4"
            ),
            token=_env_value("DEBATE_MINERU_TOKEN", "MINERU_TOKEN"),
            model_version=_env_value(
                "DEBATE_MINERU_MODEL_VERSION", "MINERU_MODEL_VERSION", default="vlm"
            ),
            is_ocr=_env_bool("DEBATE_MINERU_IS_OCR", True, "MINERU_IS_OCR"),
            enable_table=_env_bool(
                "DEBATE_MINERU_ENABLE_TABLE", True, "MINERU_ENABLE_TABLE"
            ),
            enable_formula=_env_bool(
                "DEBATE_MINERU_ENABLE_FORMULA", True, "MINERU_ENABLE_FORMULA"
            ),
            language=_env_value(
                "DEBATE_MINERU_LANGUAGE", "MINERU_LANGUAGE", default="ch"
            ),
            poll_interval_seconds=float(
                os.getenv("DEBATE_MINERU_POLL_INTERVAL_SECONDS", "5")
            ),
            timeout_seconds=float(os.getenv("DEBATE_MINERU_TIMEOUT_SECONDS", "600")),
            max_pdf_bytes=int(
                os.getenv("DEBATE_MINERU_MAX_PDF_BYTES", str(50 * 1024 * 1024))
            ),
        )

    def __post_init__(self) -> None:
        if not self.api_base.strip():
            raise ValueError("MinerU api_base cannot be empty")
        if self.poll_interval_seconds <= 0 or self.timeout_seconds <= 0:
            raise ValueError("MinerU polling intervals and timeout must be positive")
        if self.max_pdf_bytes < 1 or self.max_archive_bytes < 1:
            raise ValueError("MinerU file limits must be positive")


class MinerUClient:
    """Convert a local PDF into MinerU Markdown and structured artifacts."""

    def __init__(self, config: MinerUConfig, *, http_client: Any | None = None) -> None:
        self.config = config
        self.http_client = http_client

    async def parse_pdf(
        self,
        pdf_path: str | Path,
        *,
        output_root: str | Path,
    ) -> MinerUParseResult:
        path = Path(pdf_path)
        self._validate_pdf(path)
        if not self.config.token:
            raise MinerUConfigurationError("DEBATE_MINERU_TOKEN is not configured")

        if self.http_client is not None:
            return await self._parse_with_client(self.http_client, path, Path(output_root))

        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional integration dependency
            raise MinerUError(
                "httpx is required; install the project with the 'ingestion' extra"
            ) from exc

        async with httpx.AsyncClient(
            timeout=self.config.request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            return await self._parse_with_client(client, path, Path(output_root))

    async def _parse_with_client(
        self, client: Any, pdf_path: Path, output_root: Path
    ) -> MinerUParseResult:
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
        }
        create_response = await client.post(
            f"{self.config.api_base.rstrip('/')}/file-urls/batch",
            headers=headers,
            json={
                "files": [{"name": pdf_path.name, "is_ocr": self.config.is_ocr}],
                "model_version": self.config.model_version,
                "enable_table": self.config.enable_table,
                "enable_formula": self.config.enable_formula,
                "language": self.config.language,
            },
        )
        payload = self._api_payload(create_response, "create upload batch")
        data = payload.get("data") or {}
        batch_id = str(data.get("batch_id") or "")
        file_urls = data.get("file_urls") or []
        if not batch_id or not file_urls:
            raise MinerUError("MinerU upload batch response is missing batch_id or file_urls")

        upload_response = await client.put(
            file_urls[0],
            content=await asyncio.to_thread(pdf_path.read_bytes),
        )
        self._raise_http_error(upload_response, "upload PDF")

        archive_url = await self._poll_result(client, headers, batch_id)
        archive_response = await client.get(archive_url)
        self._raise_http_error(archive_response, "download MinerU archive")
        archive = bytes(archive_response.content)
        if len(archive) > self.config.max_archive_bytes:
            raise MinerUError("MinerU result archive exceeds configured size limit")

        output_dir = output_root / f"{_safe_stem(pdf_path.stem)}-{batch_id}"
        await asyncio.to_thread(self._extract_archive, archive, output_dir)
        markdown_path = _find_artifact(output_dir, "full.md")
        if markdown_path is None:
            raise MinerUError("MinerU result does not contain full.md")
        content_list_path = _find_artifact(output_dir, "content_list.json")
        markdown = await asyncio.to_thread(markdown_path.read_text, encoding="utf-8")
        artifacts = sorted(
            str(item.relative_to(output_dir)).replace("\\", "/")
            for item in output_dir.rglob("*")
            if item.is_file()
        )
        return MinerUParseResult(
            batch_id=batch_id,
            markdown=markdown,
            output_dir=str(output_dir),
            markdown_path=str(markdown_path),
            content_list_path=str(content_list_path) if content_list_path else None,
            artifacts=artifacts,
        )

    async def _poll_result(
        self, client: Any, headers: dict[str, str], batch_id: str
    ) -> str:
        endpoint = (
            f"{self.config.api_base.rstrip('/')}/extract-results/batch/{batch_id}"
        )
        deadline = time.monotonic() + self.config.timeout_seconds
        while time.monotonic() < deadline:
            response = await client.get(endpoint, headers=headers)
            payload = self._api_payload(response, "poll extraction result")
            results = (payload.get("data") or {}).get("extract_result") or []
            if not results:
                raise MinerUError("MinerU result response is missing extract_result")
            result = results[0]
            state = result.get("state")
            if state == "done":
                archive_url = result.get("full_zip_url")
                if not archive_url:
                    raise MinerUError("MinerU completed without full_zip_url")
                return str(archive_url)
            if state == "failed":
                raise MinerUError(f"MinerU extraction failed: {result.get('err_msg', 'unknown')}")
            await asyncio.sleep(self.config.poll_interval_seconds)
        raise MinerUTimeoutError(
            f"MinerU extraction timed out after {self.config.timeout_seconds:g} seconds"
        )

    def _validate_pdf(self, path: Path) -> None:
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise InvalidPdfError("input must be an existing PDF file")
        size = path.stat().st_size
        if size == 0 or size > self.config.max_pdf_bytes:
            raise InvalidPdfError("PDF is empty or exceeds configured size limit")
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise InvalidPdfError("file does not have a valid PDF signature")

    def _api_payload(self, response: Any, operation: str) -> dict[str, Any]:
        self._raise_http_error(response, operation)
        try:
            payload = response.json()
        except Exception as exc:
            raise MinerUError(f"MinerU {operation} returned invalid JSON") from exc
        if payload.get("code") != 0:
            raise MinerUError(
                f"MinerU {operation} failed: {payload.get('msg', 'unknown error')}"
            )
        return payload

    @staticmethod
    def _raise_http_error(response: Any, operation: str) -> None:
        status_code = int(getattr(response, "status_code", 0))
        if not 200 <= status_code < 300:
            raise MinerUError(f"MinerU {operation} failed with HTTP {status_code}")

    def _extract_archive(self, archive: bytes, output_dir: Path) -> None:
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            members = [item for item in source.infolist() if not item.is_dir()]
            if len(members) > self.config.max_archive_files:
                raise MinerUError("MinerU archive contains too many files")
            total_size = sum(item.file_size for item in members)
            if total_size > self.config.max_archive_bytes:
                raise MinerUError("MinerU archive expands beyond configured size limit")

            output_dir.mkdir(parents=True, exist_ok=False)
            try:
                for member in members:
                    relative = PurePosixPath(member.filename)
                    if relative.is_absolute() or ".." in relative.parts:
                        raise MinerUError("MinerU archive contains an unsafe path")
                    destination = output_dir.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(member) as reader, destination.open("wb") as writer:
                        shutil.copyfileobj(reader, writer)
            except Exception:
                shutil.rmtree(output_dir, ignore_errors=True)
                raise


def _env_value(primary: str, legacy: str, *, default: str = "") -> str:
    return os.getenv(primary) or os.getenv(legacy) or default


def _env_bool(name: str, default: bool, legacy_name: str | None = None) -> bool:
    value = os.getenv(name) or (os.getenv(legacy_name) if legacy_name else None)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_stem(stem: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in stem)
    return safe[:80] or "paper"


def _find_artifact(root: Path, filename: str) -> Path | None:
    return next((item for item in root.rglob(filename) if item.is_file()), None)
