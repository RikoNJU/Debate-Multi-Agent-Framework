"""历史评分校准 RAG（M5）：读取匿名评分案例 Chroma 集合，产出 Step 7 校准参照。"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..schemas import HistoricalScoreCase, PaperType, ScoreCalibrationQuery
from .historical_advice import (
    OpenAICompatibleEmbeddingProvider,
    QueryEmbeddingProvider,
    _legacy_chroma_path,
)

logger = logging.getLogger("debate.score_rag")

GRADES = ("优秀", "良好", "一般", "较差")


class ChromaHistoricalScoreRetriever:
    """从匿名历史评分索引中检索可比案例，并过滤异常与污染数据。

    索引契约（元数据）：
    - ``case_id``：匿名案例 ID（必填）
    - ``paper_type``：论文类型，取 PaperType 枚举值（必填，用于过滤）
    - ``total_score``：0-100 总分（必填）
    - ``grade``：优秀/良好/一般/较差（必填）
    - ``dimensions``：JSON 对象字符串，维度名到评分或评价的映射
    - 文档内容：匿名化裁决摘要，作为相关性语义来源

    异常/污染检测：总分越界、等级与总分不符、维度 JSON 损坏的记录被丢弃；
    同一 ``case_id`` 出现冲突总分的记录全部丢弃。
    """

    def __init__(
        self,
        *,
        collection: Any,
        embedding_provider: QueryEmbeddingProvider,
        max_query_chars: int = 6_000,
        max_quote_chars: int = 400,
    ) -> None:
        if max_query_chars < 500:
            raise ValueError("max_query_chars must be at least 500")
        self.collection = collection
        self.embedding_provider = embedding_provider
        self.max_query_chars = max_query_chars
        self.max_quote_chars = max_quote_chars

    @classmethod
    def from_persistent_path(
        cls,
        *,
        path: str | Path,
        collection_name: str,
        embedding_provider: QueryEmbeddingProvider,
        max_query_chars: int = 6_000,
    ) -> "ChromaHistoricalScoreRetriever":
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "chromadb is required; install the project with the 'rag' extra"
            ) from exc

        database_path = Path(path).expanduser().resolve()
        if not database_path.is_dir():
            raise RuntimeError(
                f"legacy Chroma directory does not exist: {database_path}"
            )
        client = chromadb.PersistentClient(
            path=str(database_path),
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )
        available = {item.name for item in client.list_collections()}
        if collection_name not in available:
            raise RuntimeError(
                f"score collection {collection_name!r} is missing; "
                f"available={sorted(available)}"
            )
        return cls(
            collection=client.get_collection(name=collection_name),
            embedding_provider=embedding_provider,
            max_query_chars=max_query_chars,
        )

    async def retrieve(
        self,
        query: ScoreCalibrationQuery,
        *,
        limit: int,
    ) -> Sequence[HistoricalScoreCase]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        text = self._build_query_text(query)
        embedding = await self._embed(text)
        raw = await asyncio.to_thread(
            self.collection.query,
            query_embeddings=[embedding],
            n_results=max(limit * 3, 10),
            where={"paper_type": query.paper_type.value},
            include=["documents", "metadatas", "distances"],
        )
        cases = self._map_cases(raw, query)
        cases.sort(key=lambda item: item.similarity, reverse=True)
        return cases[:limit]

    async def _embed(self, query: str) -> list[float]:
        embed = self.embedding_provider.embed_query
        if inspect.iscoroutinefunction(embed):
            value = await embed(query)  # type: ignore[misc]
        else:
            value = await asyncio.to_thread(embed, query)
        return [float(item) for item in value]

    def _build_query_text(self, query: ScoreCalibrationQuery) -> str:
        lines = [f"论文类型：{query.paper_type.value}"]
        if query.dimensions:
            lines.append("评价维度：")
            for dimension, summary in query.dimensions.items():
                lines.append(f"- {dimension}：{summary}")
        if query.severe_findings:
            lines.append("严重问题：")
            lines.extend(f"- {finding}" for finding in query.severe_findings)
        text = "\n".join(lines)
        return text[: self.max_query_chars]

    def _map_cases(
        self, raw: Mapping[str, Any], query: ScoreCalibrationQuery
    ) -> list[HistoricalScoreCase]:
        documents = self._first_row(raw.get("documents"))
        metadatas = self._first_row(raw.get("metadatas"))
        distances = self._first_row(raw.get("distances"))
        row_count = max(len(documents), len(metadatas), len(distances))

        valid: list[tuple[float, dict[str, Any], str]] = []
        for index in range(row_count):
            metadata = (
                metadatas[index] if index < len(metadatas) else {}
            )
            document = documents[index] if index < len(documents) else ""
            distance = distances[index] if index < len(distances) else float("inf")
            if not isinstance(metadata, Mapping):
                continue
            normalized = self._normalize_metadata(metadata)
            if normalized is None:
                continue
            if normalized["paper_type"] and normalized["paper_type"] != query.paper_type.value:
                continue
            summary = document[: self.max_quote_chars] if isinstance(document, str) else ""
            valid.append((float(distance), normalized, summary))

        clean = self._drop_polluted(valid)
        return [
            self._to_case(query, distance, metadata, summary)
            for distance, metadata, summary in clean
        ]

    @staticmethod
    def _normalize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
        raw_score = metadata.get("total_score")
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            return None
        if not 0.0 <= score <= 100.0:
            return None

        grade = str(metadata.get("grade") or "").strip()
        if grade not in GRADES:
            return None
        if _grade_for_score(score) != grade:
            return None

        dimensions = {}
        raw_dimensions = metadata.get("dimensions")
        if raw_dimensions:
            if isinstance(raw_dimensions, str):
                try:
                    parsed = json.loads(raw_dimensions)
                except (ValueError, TypeError):
                    return None
            else:
                parsed = raw_dimensions
            if not isinstance(parsed, Mapping):
                return None
            dimensions = {
                str(key): str(value)
                for key, value in parsed.items()
                if str(key).strip() and str(value).strip()
            }

        case_id = str(metadata.get("case_id") or "").strip()
        if not case_id:
            return None

        paper_type = str(metadata.get("paper_type") or "").strip()
        return {
            "case_id": case_id,
            "paper_type": paper_type,
            "score": score,
            "grade": grade,
            "dimensions": dimensions,
        }

    @staticmethod
    def _drop_polluted(
        rows: Sequence[tuple[float, dict[str, Any], str]],
    ) -> list[tuple[float, dict[str, Any], str]]:
        scores_by_case: dict[str, set[float]] = {}
        for _, metadata, _ in rows:
            case_id = metadata["case_id"]
            if case_id:
                scores_by_case.setdefault(case_id, set()).add(metadata["score"])
        polluted = {case_id for case_id, scores in scores_by_case.items() if len(scores) > 1}
        if polluted:
            logger.warning("检测到冲突历史评分案例，已丢弃：%s", sorted(polluted))
        return [
            (distance, metadata, summary)
            for distance, metadata, summary in rows
            if metadata["case_id"] not in polluted
        ]

    def _to_case(
        self,
        query: ScoreCalibrationQuery,
        distance: float,
        metadata: dict[str, Any],
        summary: str,
    ) -> HistoricalScoreCase:
        comparable = sorted(set(metadata["dimensions"]) & set(query.dimensions))
        rationale = summary.strip() or (
            f"历史 {metadata['paper_type']} 案例，等级 {metadata['grade']}，"
            f"总分 {metadata['score']:.1f}。"
        )
        return HistoricalScoreCase(
            case_id=metadata["case_id"],
            paper_type=PaperType(metadata["paper_type"]),
            score=metadata["score"],
            grade=metadata["grade"],
            similarity=_similarity(distance),
            comparable_dimensions=comparable,
            rationale=rationale,
        )

    @staticmethod
    def _first_row(value: Any) -> list[Any]:
        if not value:
            return []
        first = value[0]
        return list(first) if isinstance(first, (list, tuple)) else list(value)


def build_historical_score_retriever_from_env(
) -> ChromaHistoricalScoreRetriever | None:
    """按环境变量构建历史评分检索器；未配置 Chroma 时返回 None。"""

    chroma_path = _legacy_chroma_path()
    if not chroma_path:
        return None
    api_key = (
        os.getenv("DEBATE_EMBEDDING_API_KEY")
        or os.getenv("CLOUD_API_KEY")
        or os.getenv("DEBATE_API_KEY", "")
    )
    if not api_key:
        raise RuntimeError(
            "DEBATE_EMBEDDING_API_KEY or DEBATE_API_KEY is required when RAG is enabled"
        )
    endpoint = os.getenv(
        "DEBATE_EMBEDDING_ENDPOINT",
        os.getenv(
            "CLOUD_EMBEDDING_ENDPOINT",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        ),
    )
    dimensions_value = os.getenv("DEBATE_EMBEDDING_DIMENSIONS") or os.getenv(
        "EMBEDDING_DIMENSION"
    )
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint=endpoint,
        model=os.getenv(
            "DEBATE_EMBEDDING_MODEL",
            os.getenv("CLOUD_EMBEDDING_MODEL", "text-embedding-v4"),
        ),
        api_key=api_key,
        dimensions=int(dimensions_value) if dimensions_value else 2048,
        timeout_seconds=float(os.getenv("DEBATE_EMBEDDING_TIMEOUT_SECONDS", "60")),
    )
    collection_name = os.getenv("DEBATE_RAG_SCORE_COLLECTION", "paper_review_score_cases")
    return ChromaHistoricalScoreRetriever.from_persistent_path(
        path=chroma_path,
        collection_name=collection_name,
        embedding_provider=provider,
    )


def _grade_for_score(score: float) -> str:
    if score >= 85.0:
        return "优秀"
    if score >= 75.0:
        return "良好"
    if score >= 60.0:
        return "一般"
    return "较差"


def _similarity(distance: float) -> float:
    return max(0.0, min(1.0, 1.0 / (1.0 + float(distance))))
