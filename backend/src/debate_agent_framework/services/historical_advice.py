"""Adapters for reusing the legacy Step 3 Chroma advice collections."""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from ..schemas import ChapterInput, DebateReviewInput, RetrievedAdvice


class QueryEmbeddingProvider(Protocol):
    """Compatible with the legacy CloudEmbeddings.embed_query method."""

    def embed_query(self, text: str) -> list[float]:
        ...


class OpenAICompatibleEmbeddingProvider:
    """Embedding client compatible with the endpoint used by the legacy backend."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str,
        dimensions: int | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds

    async def embed_query(self, text: str) -> list[float]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional RAG dependency
            raise RuntimeError("httpx is required; install the project with 'rag'") from exc

        payload: dict[str, Any] = {
            "model": self.model,
            "input": [text],
            "encoding_format": "float",
        }
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"embedding request failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            return [float(item) for item in response.json()["data"][0]["embedding"]]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("embedding response has an invalid shape") from exc


class ChromaCollection(Protocol):
    def query(self, **kwargs: Any) -> Mapping[str, Any]:
        ...


class LegacyChromaHistoricalAdviceRetriever:
    """Read legacy Chroma collections and produce the new Step 3 contract.

    The adapter intentionally depends on Chroma's collection surface instead of
    LangChain, so an existing persistent database and embedding implementation can
    be reused without coupling the LangGraph workflow to the old chain runtime.
    """

    def __init__(
        self,
        *,
        collections: Sequence[ChromaCollection],
        embedding_provider: QueryEmbeddingProvider,
        max_query_chars: int = 6_000,
    ) -> None:
        if not collections:
            raise ValueError("collections must contain at least one Chroma collection")
        if max_query_chars < 500:
            raise ValueError("max_query_chars must be at least 500")
        self.collections = tuple(collections)
        self.embedding_provider = embedding_provider
        self.max_query_chars = max_query_chars

    @classmethod
    def from_persistent_path(
        cls,
        *,
        path: str | Path,
        collection_names: Sequence[str],
        embedding_provider: QueryEmbeddingProvider,
        max_query_chars: int = 6_000,
    ) -> "LegacyChromaHistoricalAdviceRetriever":
        """Open legacy collections while keeping chromadb an optional dependency."""

        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "chromadb is required; install the project with the 'rag' extra"
            ) from exc

        client = chromadb.PersistentClient(path=str(path))
        collections = [client.get_collection(name=name) for name in collection_names]
        return cls(
            collections=collections,
            embedding_provider=embedding_provider,
            max_query_chars=max_query_chars,
        )

    async def retrieve(
        self,
        review_input: DebateReviewInput,
        *,
        limit_per_chapter: int,
    ) -> Sequence[RetrievedAdvice]:
        if limit_per_chapter < 1:
            raise ValueError("limit_per_chapter must be at least 1")

        tasks = [
            self._retrieve_chapter(review_input, chapter, limit_per_chapter)
            for chapter in review_input.chapters
            if chapter.reviewable
        ]
        return [advice for advice in await asyncio.gather(*tasks) if advice.suggestions]

    async def _retrieve_chapter(
        self,
        review_input: DebateReviewInput,
        chapter: ChapterInput,
        limit: int,
    ) -> RetrievedAdvice:
        query = self._build_query(review_input, chapter)
        embedding = await self._embed(query)
        raw_results = await asyncio.gather(
            *(
                asyncio.to_thread(
                    collection.query,
                    query_embeddings=[embedding],
                    n_results=limit,
                    include=["documents", "metadatas", "distances"],
                )
                for collection in self.collections
            )
        )

        ranked: list[tuple[float, str]] = []
        for result in raw_results:
            documents = self._first_row(result.get("documents"))
            metadatas = self._first_row(result.get("metadatas"))
            distances = self._first_row(result.get("distances"))
            row_count = max(len(documents), len(metadatas), len(distances))
            for index in range(row_count):
                metadata = metadatas[index] if index < len(metadatas) else {}
                document = documents[index] if index < len(documents) else ""
                if not self._matches_paper_type(metadata, review_input.paper_type.value):
                    continue
                suggestion = self._extract_suggestion(metadata, document)
                if not suggestion:
                    continue
                distance = distances[index] if index < len(distances) else float("inf")
                ranked.append((float(distance), suggestion))

        suggestions: list[str] = []
        for _, suggestion in sorted(ranked, key=lambda item: item[0]):
            if suggestion not in suggestions:
                suggestions.append(suggestion)
            if len(suggestions) == limit:
                break
        return RetrievedAdvice(
            chapter_id=chapter.chapter_id,
            stage=chapter.stage,
            suggestions=suggestions,
        )

    async def _embed(self, query: str) -> list[float]:
        embed = self.embedding_provider.embed_query
        if inspect.iscoroutinefunction(embed):
            value = await embed(query)  # type: ignore[misc]
        else:
            value = await asyncio.to_thread(embed, query)
        return [float(item) for item in value]

    def _build_query(
        self, review_input: DebateReviewInput, chapter: ChapterInput
    ) -> str:
        parts = [
            f"论文类型：{review_input.paper_type.value}",
            f"论文标题：{review_input.title}",
            f"章节阶段：{chapter.stage}",
            f"章节名称：{chapter.chapter_name}",
            f"章节内容：{chapter.content}",
        ]
        return "\n".join(parts)[: self.max_query_chars]

    @staticmethod
    def _first_row(value: Any) -> list[Any]:
        if not value:
            return []
        first = value[0]
        return list(first) if isinstance(first, (list, tuple)) else list(value)

    @staticmethod
    def _matches_paper_type(metadata: Any, expected: str) -> bool:
        if not isinstance(metadata, Mapping):
            return True
        actual = metadata.get("paper_type")
        return actual in (None, "", expected)

    @staticmethod
    def _extract_suggestion(metadata: Any, document: Any) -> str:
        if isinstance(metadata, Mapping):
            for key in ("suggestion", "advice", "content"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return document.strip() if isinstance(document, str) else ""


def build_historical_advice_retriever_from_env(
) -> LegacyChromaHistoricalAdviceRetriever | None:
    """Build the legacy RAG adapter when a Chroma path is configured."""

    chroma_path = os.getenv("DEBATE_RAG_CHROMA_PATH")
    if not chroma_path:
        return None
    api_key = os.getenv("DEBATE_EMBEDDING_API_KEY") or os.getenv("DEBATE_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "DEBATE_EMBEDDING_API_KEY or DEBATE_API_KEY is required when RAG is enabled"
        )
    base_url = os.getenv("DEBATE_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    endpoint = os.getenv("DEBATE_EMBEDDING_ENDPOINT", f"{base_url}/embeddings")
    dimensions_value = os.getenv("DEBATE_EMBEDDING_DIMENSIONS")
    collections = [
        item.strip()
        for item in os.getenv(
            "DEBATE_RAG_COLLECTIONS", "advice_collection_cloud_4b"
        ).split(",")
        if item.strip()
    ]
    provider = OpenAICompatibleEmbeddingProvider(
        endpoint=endpoint,
        model=os.getenv("DEBATE_EMBEDDING_MODEL", "text-embedding-3-small"),
        api_key=api_key,
        dimensions=int(dimensions_value) if dimensions_value else None,
        timeout_seconds=float(os.getenv("DEBATE_EMBEDDING_TIMEOUT_SECONDS", "60")),
    )
    return LegacyChromaHistoricalAdviceRetriever.from_persistent_path(
        path=chroma_path,
        collection_names=collections,
        embedding_provider=provider,
    )
