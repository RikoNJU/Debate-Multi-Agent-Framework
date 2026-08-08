from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest

from debate_agent_framework.ingestion import (
    InvalidPdfError,
    MinerUClient,
    MinerUConfig,
    MinerUError,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, *, payload=None, content=b""):  # type: ignore[no-untyped-def]
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):  # type: ignore[no-untyped-def]
        return self._payload


class FakeMinerUHttpClient:
    def __init__(self, archive: bytes) -> None:
        self.archive = archive
        self.polls = 0
        self.uploaded = b""

    async def post(self, url, **kwargs):  # type: ignore[no-untyped-def]
        return FakeResponse(
            payload={
                "code": 0,
                "data": {
                    "batch_id": "batch-1",
                    "file_urls": ["https://upload.example/paper"],
                },
            }
        )

    async def put(self, url, **kwargs):  # type: ignore[no-untyped-def]
        self.uploaded = kwargs["content"]
        return FakeResponse(status_code=200)

    async def get(self, url, **kwargs):  # type: ignore[no-untyped-def]
        if url == "https://download.example/result.zip":
            return FakeResponse(content=self.archive)
        self.polls += 1
        state = "pending" if self.polls == 1 else "done"
        result = {"state": state}
        if state == "done":
            result["full_zip_url"] = "https://download.example/result.zip"
        return FakeResponse(
            payload={"code": 0, "data": {"extract_result": [result]}}
        )


def make_archive(*, unsafe: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.md" if unsafe else "full.md", "# parsed paper")
        archive.writestr("content_list.json", "[]")
        archive.writestr("images/figure.png", b"image")
    return buffer.getvalue()


def make_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.7\nminimal test content")


def test_mineru_client_uploads_polls_and_extracts_artifacts(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    make_pdf(pdf_path)
    http_client = FakeMinerUHttpClient(make_archive())
    client = MinerUClient(
        MinerUConfig(
            api_base="https://mineru.example/api/v4",
            token="test-token",
            poll_interval_seconds=0.001,
        ),
        http_client=http_client,
    )

    result = asyncio.run(client.parse_pdf(pdf_path, output_root=tmp_path / "output"))

    assert result.batch_id == "batch-1"
    assert result.markdown == "# parsed paper"
    assert result.content_list_path is not None
    assert result.artifacts == ["content_list.json", "full.md", "images/figure.png"]
    assert http_client.uploaded.startswith(b"%PDF-")
    assert http_client.polls == 2


def test_mineru_client_rejects_non_pdf_content(tmp_path: Path) -> None:
    path = tmp_path / "fake.pdf"
    path.write_text("not a pdf", encoding="utf-8")
    client = MinerUClient(
        MinerUConfig(api_base="https://mineru.example", token="token"),
        http_client=FakeMinerUHttpClient(make_archive()),
    )

    with pytest.raises(InvalidPdfError, match="signature"):
        asyncio.run(client.parse_pdf(path, output_root=tmp_path / "output"))


def test_mineru_client_rejects_zip_slip(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    make_pdf(pdf_path)
    client = MinerUClient(
        MinerUConfig(api_base="https://mineru.example", token="token"),
        http_client=FakeMinerUHttpClient(make_archive(unsafe=True)),
    )

    with pytest.raises(MinerUError, match="unsafe path"):
        asyncio.run(client.parse_pdf(pdf_path, output_root=tmp_path / "output"))
    assert not (tmp_path / "escape.md").exists()
