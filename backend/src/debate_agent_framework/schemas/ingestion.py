from __future__ import annotations

from pydantic import Field

from .domain import StrictModel


class MinerUParseResult(StrictModel):
    batch_id: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    output_dir: str = Field(min_length=1)
    markdown_path: str = Field(min_length=1)
    content_list_path: str | None = None
    artifacts: list[str] = Field(default_factory=list)


class MinerUParseResponse(StrictModel):
    """Public API shape; local storage paths remain server-side."""

    batch_id: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    artifacts: list[str] = Field(default_factory=list)
    has_content_list: bool = False

    @classmethod
    def from_result(cls, result: MinerUParseResult) -> "MinerUParseResponse":
        return cls(
            batch_id=result.batch_id,
            markdown=result.markdown,
            artifacts=result.artifacts,
            has_content_list=result.content_list_path is not None,
        )


class PaperReviewSubmission(StrictModel):
    task_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    chapter_count: int = Field(ge=1)
    batch_id: str = Field(min_length=1)
