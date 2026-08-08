from __future__ import annotations

import asyncio
from pathlib import Path

import debate_agent_framework.services.historical_advice as historical_advice
import pytest
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
    assert "【章节阶段】方法构建" in embeddings.queries[0]
    assert content.calls[0]["query_embeddings"] == [[0.1, 0.2]]
    assert content.calls[0]["include"] == ["documents", "metadatas", "distances"]


def test_legacy_repository_root_resolves_user_result_database(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    backend_root = tmp_path / "paper-review-backend" / "backend"
    database = backend_root / "data" / "databases" / "user_result_cloud"
    database.mkdir(parents=True)
    monkeypatch.delenv("DEBATE_RAG_CHROMA_PATH", raising=False)
    monkeypatch.setenv("PAPER_REVIEW_BACKEND_ROOT", str(backend_root.parent))

    assert historical_advice._legacy_chroma_path() == str(database.resolve())


def test_legacy_rag_factory_uses_original_embedding_and_collection_defaults(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    database = tmp_path / "user_result_cloud"
    database.mkdir()
    captured = {}

    def fake_open(cls, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return "retriever"

    monkeypatch.setenv("DEBATE_RAG_CHROMA_PATH", str(database))
    monkeypatch.setenv("CLOUD_API_KEY", "legacy-key")
    monkeypatch.delenv("DEBATE_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("DEBATE_RAG_COLLECTIONS", raising=False)
    monkeypatch.setattr(
        historical_advice.LegacyChromaHistoricalAdviceRetriever,
        "from_persistent_path",
        classmethod(fake_open),
    )

    result = historical_advice.build_historical_advice_retriever_from_env()

    assert result == "retriever"
    assert captured["path"] == str(database)
    assert captured["collection_names"] == [
        "user_result_content_collection_cloud_4b",
        "user_result_format_collection_cloud_4b",
    ]
    provider = captured["embedding_provider"]
    assert provider.endpoint.endswith("dashscope.aliyuncs.com/compatible-mode/v1/embeddings")
    assert provider.model == "text-embedding-v4"
    assert provider.dimensions == 2048


def test_adapter_opens_and_queries_persistent_chroma_collections(tmp_path: Path) -> None:
    chromadb = pytest.importorskip("chromadb")
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(tmp_path),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )
    names = [
        "user_result_content_collection_cloud_4b",
        "user_result_format_collection_cloud_4b",
    ]
    for index, name in enumerate(names):
        collection = client.create_collection(name=name)
        collection.add(
            ids=[f"legacy-{index}"],
            documents=["旧评审建议"],
            metadatas=[{"suggestion": f"建议-{index}", "position": "第1章"}],
            embeddings=[[0.1, 0.2]],
        )

    retriever = LegacyChromaHistoricalAdviceRetriever.from_persistent_path(
        path=tmp_path,
        collection_names=names,
        embedding_provider=FakeEmbeddings(),
    )
    result = asyncio.run(retriever.retrieve(make_input(), limit_per_chapter=2))

    assert result[0].suggestions == ["建议-0", "建议-1"]
