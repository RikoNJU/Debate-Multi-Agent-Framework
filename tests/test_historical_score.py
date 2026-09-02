from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from debate_agent_framework.schemas import PaperType, ScoreCalibrationQuery
from debate_agent_framework.services import ChromaHistoricalScoreRetriever
from debate_agent_framework.services import historical_score


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


def make_query() -> ScoreCalibrationQuery:
    return ScoreCalibrationQuery(
        paper_type=PaperType.METHOD,
        dimensions={"实验验证": "实验覆盖充分性", "方法创新性": "提出新方法"},
        severe_findings=["缺少强 Baseline"],
    )


def metadata(case_id: str, score: float, grade: str, paper_type: str = "方法创新") -> dict:
    return {
        "case_id": case_id,
        "paper_type": paper_type,
        "total_score": score,
        "grade": grade,
        "dimensions": json.dumps({"实验验证": "充分", "方法创新性": "较强"}),
    }


def test_chroma_score_adapter_maps_and_ranks_cases() -> None:
    collection = FakeCollection(
        {
            "documents": [["方法完整但实验覆盖不足", "theory case"]],
            "metadatas": [[metadata("CASE-001", 79, "良好"), metadata("CASE-002", 82, "良好", "理论研究")]],
            "distances": [[0.2, 0.1]],
        }
    )
    retriever = ChromaHistoricalScoreRetriever(
        collection=collection, embedding_provider=FakeEmbeddings()
    )

    cases = asyncio.run(retriever.retrieve(make_query(), limit=5))

    assert [case.case_id for case in cases] == ["CASE-001"]
    assert cases[0].paper_type is PaperType.METHOD
    assert cases[0].score == 79
    assert cases[0].grade == "良好"
    assert cases[0].similarity > 0
    assert cases[0].comparable_dimensions == ["实验验证", "方法创新性"]
    assert "实验覆盖不足" in cases[0].rationale
    assert collection.calls[0]["where"] == {"paper_type": "方法创新"}


def test_chroma_score_adapter_drops_anomalous_and_polluted_records() -> None:
    collection = FakeCollection(
        {
            "documents": [["ok summary"] * 5],
            "metadatas": [
                [
                    metadata("OK-1", 80, "良好"),
                    metadata("BAD-GRADE", 55, "优秀"),  # 等级与总分矛盾
                    metadata("BAD-RANGE", 120, "优秀"),  # 总分越界
                    metadata("BAD-JSON", 80, "良好", "方法创新"),
                    metadata("DUP", 78, "良好"),
                ]
            ],
            "distances": [[0.1] * 5],
        }
    )
    collection.result["metadatas"][0][3]["dimensions"] = "{{{{not-json"
    # 同一 case_id DUP 出现冲突总分（数据污染）
    collection.result["metadatas"][0].append(
        dict(collection.result["metadatas"][0][4], total_score=85, grade="优秀")
    )

    retriever = ChromaHistoricalScoreRetriever(
        collection=collection, embedding_provider=FakeEmbeddings()
    )

    cases = asyncio.run(retriever.retrieve(make_query(), limit=10))

    assert [case.case_id for case in cases] == ["OK-1"]


def test_query_text_contains_dimensions_and_findings() -> None:
    collection = FakeCollection({"documents": [[]], "metadatas": [[]], "distances": [[]]})
    embeddings = FakeEmbeddings()
    retriever = ChromaHistoricalScoreRetriever(
        collection=collection, embedding_provider=embeddings
    )
    asyncio.run(retriever.retrieve(make_query(), limit=1))
    text = embeddings.queries[0]
    assert "论文类型：方法创新" in text
    assert "实验验证：实验覆盖充分性" in text
    assert "缺少强 Baseline" in text


def test_env_builder_returns_none_without_chroma_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DEBATE_RAG_CHROMA_PATH", raising=False)
    monkeypatch.delenv("PAPER_REVIEW_BACKEND_ROOT", raising=False)
    assert historical_score.build_historical_score_retriever_from_env() is None


def test_grade_for_score_boundaries() -> None:
    assert historical_score._grade_for_score(85) == "优秀"
    assert historical_score._grade_for_score(84.9) == "良好"
    assert historical_score._grade_for_score(75) == "良好"
    assert historical_score._grade_for_score(60) == "一般"
    assert historical_score._grade_for_score(59) == "较差"


def test_persistent_chroma_roundtrip(tmp_path: Path) -> None:
    chromadb = pytest.importorskip("chromadb")
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(tmp_path),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )
    collection = client.create_collection(name="paper_review_score_cases")
    collection.add(
        ids=["CASE-001"],
        documents=["方法完整但实验覆盖不足"],
        metadatas=[metadata("CASE-001", 79, "良好")],
        embeddings=[[0.1, 0.2]],
    )

    retriever = ChromaHistoricalScoreRetriever.from_persistent_path(
        path=tmp_path,
        collection_name="paper_review_score_cases",
        embedding_provider=FakeEmbeddings(),
    )
    cases = asyncio.run(retriever.retrieve(make_query(), limit=5))

    assert cases[0].case_id == "CASE-001"
    assert cases[0].score == 79
