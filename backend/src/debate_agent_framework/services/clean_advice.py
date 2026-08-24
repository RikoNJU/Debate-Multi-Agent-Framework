"""Finding-level hybrid retrieval over the canonical historical-advice V2 corpus."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import re
from pathlib import Path
from typing import Any

import chromadb
import httpx

from ..schemas.domain import (
    DebateReviewInput,
    FindingAdviceItem,
    ResolvedFinding,
    ReviewSynthesis,
)
from .rag_v2_contract import (
    CONTENT_COLLECTION,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    FORMAT_COLLECTION,
    SCHEMA_VERSION,
    TOKENIZER_VERSION,
    AdviceCorpusRecord,
    file_checksum,
    id_set_checksum,
    load_corpus,
    tokenize_bm25,
)


logger = logging.getLogger("debate.rag.historical_advice_v2")
QUERY_INSTRUCTION = (
    "Instruct: 检索与当前毕业论文已确认问题语义相关、可用于生成"
    "针对性修改建议的历史评审案例\nQuery: "
)


class CleanAdviceRetriever:
    """Dense Top5 + BM25 Top5 -> weighted RRF Top3 -> dedicated reranker."""

    def __init__(
        self,
        *,
        chroma_db_path: str,
        bm25_dir: str,
        corpus_path: str,
        embed_endpoint: str,
        embed_model: str = EMBEDDING_MODEL,
        embedding_dimensions: int = EMBEDDING_DIMENSIONS,
        rerank_endpoint: str = "",
        rerank_model: str = "Qwen/Qwen3-Reranker-0.6B",
        api_key: str = "",
        dense_top_k: int = 5,
        bm25_top_k: int = 5,
        rrf_top_k: int = 3,
        rrf_k: int = 60,
        rerank_threshold: float = 6.0,
        max_advice_per_finding: int = 2,
        timeout_seconds: float = 120.0,
        max_concurrency: int = 2,
        mode: str = "v2",
    ) -> None:
        if embed_model != EMBEDDING_MODEL:
            raise ValueError(f"Dense V2 requires embedding model {EMBEDDING_MODEL}")
        if embedding_dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Dense V2 requires {EMBEDDING_DIMENSIONS} dimensions"
            )
        if mode not in {"shadow_v2", "v2"}:
            raise ValueError("CleanAdviceRetriever mode must be shadow_v2 or v2")
        self.chroma_db_path = Path(chroma_db_path)
        self.bm25_dir = Path(bm25_dir)
        self.corpus_path = Path(corpus_path)
        self.embed_endpoint = embed_endpoint
        self.embed_model = embed_model
        self.embedding_dimensions = embedding_dimensions
        self.rerank_endpoint = rerank_endpoint
        self.rerank_model = rerank_model
        self.api_key = api_key
        self.dense_top_k = dense_top_k
        self.bm25_top_k = bm25_top_k
        self.rrf_top_k = rrf_top_k
        self.rrf_k = rrf_k
        self.rerank_threshold = rerank_threshold
        self.max_advice_per_finding = max_advice_per_finding
        self.timeout_seconds = timeout_seconds
        self.mode = mode
        self._semaphore = asyncio.Semaphore(max_concurrency)

        self._client: chromadb.PersistentClient | None = None
        self._content_col: Any = None
        self._format_col: Any = None
        self._bm25_content: Any = None
        self._bm25_format: Any = None
        self._ids_content: list[str] = []
        self._ids_format: list[str] = []
        self._records: dict[str, AdviceCorpusRecord] = {}

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(f"RAG V2 manifest not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _ensure_loaded(self) -> None:
        if self._client is not None:
            return
        records = load_corpus(self.corpus_path)
        self._records = {record.advice_id: record for record in records}
        expected_ids = set(self._records)
        expected_id_checksum = id_set_checksum(expected_ids)
        corpus_checksum = file_checksum(self.corpus_path)

        dense_manifest = self._read_manifest(self.chroma_db_path / "manifest.json")
        bm25_manifest = self._read_manifest(self.bm25_dir / "manifest.json")
        if dense_manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Dense V2 schema version is incompatible")
        if dense_manifest.get("embedding_model") != self.embed_model:
            raise ValueError("Dense V2 embedding model does not match runtime")
        if dense_manifest.get("embedding_dimensions") != self.embedding_dimensions:
            raise ValueError("Dense V2 dimensions do not match runtime")
        if bm25_manifest.get("tokenizer") != TOKENIZER_VERSION:
            raise ValueError("BM25 tokenizer version does not match runtime")
        for label, manifest in (("Dense", dense_manifest), ("BM25", bm25_manifest)):
            if manifest.get("corpus_checksum") != corpus_checksum:
                raise ValueError(f"{label} V2 was built from a different corpus")
            if manifest.get("advice_id_checksum") != expected_id_checksum:
                raise ValueError(f"{label} V2 advice IDs do not match the corpus")

        self._client = chromadb.PersistentClient(path=str(self.chroma_db_path))
        self._content_col = self._client.get_collection(CONTENT_COLLECTION)
        self._format_col = self._client.get_collection(FORMAT_COLLECTION)
        if self._content_col.count() != dense_manifest.get("content_count"):
            raise ValueError("Dense V2 content collection count mismatch")
        if self._format_col.count() != dense_manifest.get("format_count"):
            raise ValueError("Dense V2 format collection count mismatch")

        with (self.bm25_dir / "content_bm25.pkl").open("rb") as source:
            self._bm25_content = pickle.load(source)
        with (self.bm25_dir / "format_bm25.pkl").open("rb") as source:
            self._bm25_format = pickle.load(source)
        self._ids_content = json.loads(
            (self.bm25_dir / "content_bm25_id_map.json").read_text(encoding="utf-8")
        )
        self._ids_format = json.loads(
            (self.bm25_dir / "format_bm25_id_map.json").read_text(encoding="utf-8")
        )
        bm25_ids = set(self._ids_content) | set(self._ids_format)
        if bm25_ids != expected_ids:
            raise ValueError("BM25 ID maps do not match the canonical corpus")

    async def retrieve_from_findings(
        self,
        synthesis: ReviewSynthesis,
        review_input: DebateReviewInput,
    ) -> list[FindingAdviceItem]:
        """Retrieve history only after the Chair confirms a paper-grounded issue."""

        self._ensure_loaded()
        confirmed = [
            finding
            for finding in synthesis.global_review.resolved_findings
            if finding.status.value == "confirmed"
            and any(evidence.kind.value == "paper" for evidence in finding.evidence)
        ]
        if not confirmed:
            return []
        results = await asyncio.gather(
            *(self._retrieve_finding(finding, review_input) for finding in confirmed)
        )
        return [item for group in results for item in group]

    async def _retrieve_finding(
        self,
        finding: ResolvedFinding,
        review_input: DebateReviewInput,
    ) -> list[FindingAdviceItem]:
        async with self._semaphore:
            dense_query, bm25_query = self._build_finding_query(finding, review_input)
            route = self._route_finding(finding)
            collection = self._format_col if route == "format" else self._content_col
            bm25 = self._bm25_format if route == "format" else self._bm25_content
            ids = self._ids_format if route == "format" else self._ids_content

            dense_hits: list[dict[str, Any]] = []
            bm25_hits: list[dict[str, Any]] = []
            try:
                dense_hits = await self._dense_search(collection, dense_query)
            except Exception as exc:
                logger.warning("Dense V2 failed for %s: %s", finding.finding_id, exc)
            try:
                bm25_hits = await self._bm25_search(bm25, ids, bm25_query)
            except Exception as exc:
                logger.warning("BM25 V2 failed for %s: %s", finding.finding_id, exc)
            if not dense_hits and not bm25_hits:
                return []

            fused = self._rrf_fuse(dense_hits, bm25_hits)[: self.rrf_top_k]
            rerank_results: dict[str, tuple[float, str]] = {}
            try:
                rerank_results = await self._rerank(dense_query, fused)
            except Exception as exc:
                logger.warning("Reranker failed for %s: %s", finding.finding_id, exc)

            candidates: list[FindingAdviceItem] = []
            for candidate in fused:
                record = candidate["record"]
                rerank_score: float | None = None
                reason = ""
                if rerank_results:
                    rerank_score, reason = rerank_results.get(
                        record.advice_id, (0.0, "reranker did not return this candidate")
                    )
                    if rerank_score < self.rerank_threshold:
                        continue
                candidates.append(
                    FindingAdviceItem(
                        finding_id=finding.finding_id,
                        advice_id=record.advice_id,
                        suggestion=record.advice,
                        issue_category=record.issue_category,
                        dense_rank=candidate.get("dense_rank"),
                        bm25_rank=candidate.get("bm25_rank"),
                        rrf_score=candidate["rrf_score"],
                        rerank_score=rerank_score,
                        relevance=(round(rerank_score) if rerank_score is not None else None),
                        applicability=(round(rerank_score) if rerank_score is not None else None),
                        source_collection=record.source_collection,
                        index_version=SCHEMA_VERSION,
                        rerank_reason=reason,
                    )
                )
            if rerank_results:
                candidates.sort(
                    key=lambda item: item.rerank_score or 0.0,
                    reverse=True,
                )
            return self._deduplicate(candidates)[: self.max_advice_per_finding]

    async def _dense_search(self, collection: Any, query: str) -> list[dict[str, Any]]:
        embedding = await self._embed(QUERY_INSTRUCTION + query)
        raw = await asyncio.to_thread(
            collection.query,
            query_embeddings=[embedding],
            n_results=self.dense_top_k,
            include=["distances"],
        )
        return [
            {
                "advice_id": advice_id,
                "rank": rank,
                "distance": distance,
            }
            for rank, (advice_id, distance) in enumerate(
                zip(raw["ids"][0], raw["distances"][0], strict=True), 1
            )
        ]

    async def _bm25_search(
        self,
        index: Any,
        ids: list[str],
        query: str,
    ) -> list[dict[str, Any]]:
        scores = await asyncio.to_thread(index.get_scores, tokenize_bm25(query))
        ranked = sorted(enumerate(scores), key=lambda pair: -pair[1])
        hits: list[dict[str, Any]] = []
        for rank, (position, score) in enumerate(ranked[: self.bm25_top_k], 1):
            if score <= 0:
                continue
            hits.append(
                {"advice_id": ids[position], "rank": rank, "score": float(score)}
            )
        return hits

    def _build_finding_query(
        self,
        finding: ResolvedFinding,
        review_input: DebateReviewInput,
    ) -> tuple[str, str]:
        evidence = " ".join(
            item.quote for item in finding.evidence if item.kind.value == "paper"
        )[:800]
        affected = set(finding.affected_chapter_ids)
        chapters = [
            chapter for chapter in review_input.chapters if chapter.chapter_id in affected
        ]
        chapter_names = "、".join(chapter.chapter_name for chapter in chapters)
        stages = "、".join(dict.fromkeys(chapter.stage for chapter in chapters))
        local_context = " ".join(chapter.content for chapter in chapters)[:1000]
        dense_query = (
            f"论文题：{review_input.title}\n"
            f"论文类型：{getattr(review_input.paper_type, 'value', review_input.paper_type) or '未知'}\n"
            f"评审维度：{finding.dimension}\n"
            f"问题类型：{finding.claim}\n"
            f"严重程度：{finding.severity.value}\n"
            f"章节阶段：{stages or '未知'}\n"
            f"问题位置：{chapter_names or '未知'}\n"
            f"确认理由：{finding.rationale}\n"
            f"当前论文证据：{evidence}\n"
            f"当前局部上下文：{local_context}"
        )[:4000]
        bm25_query = " ".join(
            part
            for part in (
                finding.dimension,
                finding.claim,
                finding.rationale,
                stages,
                chapter_names,
                evidence[:300],
            )
            if part
        )[:1000]
        return dense_query, bm25_query

    @staticmethod
    def _route_finding(finding: ResolvedFinding) -> str:
        text = f"{finding.dimension} {finding.claim}".lower()
        format_terms = (
            "格式",
            "排版",
            "页宽",
            "字体",
            "目录",
            "图表编号",
            "参考文献格式",
            "承诺书",
            "format",
            "layout",
        )
        return "format" if any(term in text for term in format_terms) else "content"

    def _rrf_fuse(
        self,
        dense_hits: list[dict[str, Any]],
        bm25_hits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        fused: dict[str, dict[str, Any]] = {}
        for hit in dense_hits:
            advice_id = hit["advice_id"]
            record = self._records.get(advice_id)
            if record is None:
                logger.warning("Dense returned unknown advice_id: %s", advice_id)
                continue
            fused[advice_id] = {
                "advice_id": advice_id,
                "dense_rank": hit["rank"],
                "bm25_rank": None,
                "rrf_score": 0.55 / (self.rrf_k + hit["rank"]),
                "record": record,
            }
        for hit in bm25_hits:
            advice_id = hit["advice_id"]
            record = self._records.get(advice_id)
            if record is None:
                logger.warning("BM25 returned unknown advice_id: %s", advice_id)
                continue
            if advice_id in fused:
                candidate = fused[advice_id]
                candidate["bm25_rank"] = hit["rank"]
                candidate["rrf_score"] += 0.45 / (self.rrf_k + hit["rank"])
            else:
                fused[advice_id] = {
                    "advice_id": advice_id,
                    "dense_rank": None,
                    "bm25_rank": hit["rank"],
                    "rrf_score": 0.45 / (self.rrf_k + hit["rank"]),
                    "record": record,
                }
        return sorted(fused.values(), key=lambda item: -item["rrf_score"])

    async def _embed(self, text: str) -> list[float]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.embed_endpoint,
                headers=headers,
                json={
                    "model": self.embed_model,
                    "input": [text],
                    "dimensions": self.embedding_dimensions,
                },
            )
        response.raise_for_status()
        vector = response.json()["data"][0]["embedding"]
        if len(vector) != self.embedding_dimensions:
            raise ValueError(
                f"query embedding dimension mismatch: {len(vector)} != "
                f"{self.embedding_dimensions}"
            )
        return vector

    async def _rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, tuple[float, str]]:
        if not self.rerank_endpoint or not candidates:
            return {}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.rerank_endpoint,
                headers=headers,
                json={
                    "model": self.rerank_model,
                    "query": query,
                    "documents": [item["record"].rerank_text for item in candidates],
                    "top_n": len(candidates),
                    "return_documents": False,
                },
            )
        response.raise_for_status()
        ranked: dict[str, tuple[float, str]] = {}
        for item in response.json().get("results", []):
            index = int(item["index"])
            if not 0 <= index < len(candidates):
                continue
            score = float(item.get("relevance_score", item.get("score", 0.0)))
            normalized = score * 10 if 0.0 <= score <= 1.0 else score
            advice_id = candidates[index]["record"].advice_id
            ranked[advice_id] = (
                round(max(0.0, min(10.0, normalized)), 2),
                "Qwen3 dedicated reranker relevance score",
            )
        return ranked

    @staticmethod
    def _deduplicate(items: list[FindingAdviceItem]) -> list[FindingAdviceItem]:
        kept: list[FindingAdviceItem] = []
        token_sets: list[set[str]] = []
        for item in items:
            normalized = re.sub(r"\W+", "", item.suggestion.lower())
            tokens = set(tokenize_bm25(item.suggestion))
            duplicate = False
            for previous, previous_tokens in zip(kept, token_sets, strict=True):
                previous_normalized = re.sub(r"\W+", "", previous.suggestion.lower())
                union = tokens | previous_tokens
                overlap = len(tokens & previous_tokens) / len(union) if union else 0.0
                if normalized == previous_normalized or overlap >= 0.85:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(item)
                token_sets.append(tokens)
        return kept


def build_clean_advice_retriever_from_env() -> CleanAdviceRetriever | None:
    mode = os.getenv("DEBATE_V2_MODE", "off").lower()
    if mode == "off":
        return None
    if mode not in {"shadow_v2", "v2"}:
        raise ValueError("DEBATE_V2_MODE must be off, shadow_v2, or v2")
    chroma_path = os.getenv("DEBATE_V2_CHROMA_PATH")
    bm25_path = os.getenv("DEBATE_V2_BM25_PATH")
    corpus_path = os.getenv("RAG_V2_CORPUS_PATH")
    if not chroma_path or not bm25_path or not corpus_path:
        return None
    return CleanAdviceRetriever(
        chroma_db_path=chroma_path,
        bm25_dir=bm25_path,
        corpus_path=corpus_path,
        embed_endpoint=os.getenv(
            "DEBATE_V2_EMBEDDING_ENDPOINT",
            "https://api.siliconflow.cn/v1/embeddings",
        ),
        embed_model=os.getenv("DEBATE_V2_EMBEDDING_MODEL", EMBEDDING_MODEL),
        embedding_dimensions=int(
            os.getenv("DEBATE_V2_EMBEDDING_DIMENSIONS", str(EMBEDDING_DIMENSIONS))
        ),
        rerank_endpoint=os.getenv(
            "DEBATE_V2_RERANK_ENDPOINT",
            "https://api.siliconflow.cn/v1/rerank",
        ),
        rerank_model=os.getenv(
            "DEBATE_V2_RERANK_MODEL",
            "Qwen/Qwen3-Reranker-0.6B",
        ),
        api_key=(
            os.getenv("DEBATE_V2_API_KEY")
            or os.getenv("DEBATE_EMBEDDING_API_KEY")
            or os.getenv("CLOUD_API_KEY")
            or ""
        ),
        rerank_threshold=float(os.getenv("DEBATE_V2_RERANK_THRESHOLD", "6")),
        timeout_seconds=float(os.getenv("DEBATE_V2_TIMEOUT_SECONDS", "120")),
        max_concurrency=int(os.getenv("DEBATE_V2_RETRIEVAL_CONCURRENCY", "2")),
        mode=mode,
    )
