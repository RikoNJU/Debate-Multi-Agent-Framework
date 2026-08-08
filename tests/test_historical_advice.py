from __future__ import annotations

import asyncio

from debate_agent_framework.schemas import ChapterInput, DebateReviewInput, PaperType
from debate_agent_framework.services import LegacyChromaHistoricalAdviceRetriever


class FakeEmbeddings:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2]


class FakeCollection:
    def __init__(self, result):  # type: ignore[no-untyped-def]
        self.result = result
        self.calls: list[dict] = []

    def query(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self.result


def make_input() -> DebateReviewInput:
    chapter = ChapterInput(
        chapter_id="C1",
        chapter_name="第一章 方法设计",
        stage="方法构建",
        content="提出评审方法和协作机制。",
    )
    return DebateReviewInput(
        paper_id="paper-1",
        title="多智能体论文评审",
        full_text=chapter.content,
        paper_type=PaperType.METHOD,
        chapters=[chapter],
    )


def test_legacy_chroma_adapter_maps_and_ranks_old_records() -> None:
    embeddings = FakeEmbeddings()
    content = FakeCollection(
        {
            "documents": [["fallback suggestion", "wrong paper"]],
            "metadatas": [[
                {"suggestion": "补充消融实验", "paper_type": "方法创新"},
                {"suggestion": "不应返回", "paper_type": "理论研究"},
            ]],
            "distances": [[0.2, 0.1]],
        }
    )
    formatting = FakeCollection(
        {
            "documents": [["统一图表编号"]],
            "metadatas": [[{}]],
            "distances": [[0.3]],
        }
    )
    retriever = LegacyChromaHistoricalAdviceRetriever(
        collections=[content, formatting],
        embedding_provider=embeddings,
    )

    result = asyncio.run(retriever.retrieve(make_input(), limit_per_chapter=2))

    assert result[0].chapter_id == "C1"
    assert result[0].suggestions == ["补充消融实验", "统一图表编号"]
    assert "章节阶段：方法构建" in embeddings.queries[0]
    assert content.calls[0]["query_embeddings"] == [[0.1, 0.2]]
    assert content.calls[0]["include"] == ["documents", "metadatas", "distances"]
