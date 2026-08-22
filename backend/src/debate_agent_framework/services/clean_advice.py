"""V2 清洗后向量库的 RAG 检索器：finding 级查询、Dense V2 + BM25 V2 + RRF + Reranker。"""

from __future__ import annotations

import asyncio
import json
import os
import pickle
import re
from pathlib import Path
from typing import Any

import jieba
import httpx
import chromadb

from ..schemas.domain import FindingAdviceItem, ResolvedFinding, ReviewSynthesis


class CleanAdviceRetriever:
    """使用 Clean Dense V2 + BM25 V2 按 finding 检索，RRF 融合，LLM Reranker 精排。"""

    def __init__(
        self,
        *,
        chroma_db_path: str,
        bm25_dir: str,
        embed_endpoint: str,
        embed_model: str,
        chat_endpoint: str,
        chat_model: str,
        api_key: str,
        dense_top_k: int = 5,
        bm25_top_k: int = 5,
        rrf_top_k: int = 3,
        rrf_k: int = 60,
        rerank_threshold: float = 6.0,
        max_advice_per_finding: int = 2,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.chroma_db_path = chroma_db_path
        self.bm25_dir = bm25_dir
        self.embed_endpoint = embed_endpoint
        self.embed_model = embed_model
        self.chat_endpoint = chat_endpoint
        self.chat_model = chat_model
        self.api_key = api_key
        self.dense_top_k = dense_top_k
        self.bm25_top_k = bm25_top_k
        self.rrf_top_k = rrf_top_k
        self.rrf_k = rrf_k
        self.rerank_threshold = rerank_threshold
        self.max_advice_per_finding = max_advice_per_finding
        self.timeout_seconds = timeout_seconds

        self._client: chromadb.PersistentClient | None = None
        self._bm25_content: Any = None
        self._bm25_format: Any = None
        self._ids_content: list[str] = []
        self._ids_format: list[str] = []

    def _ensure_loaded(self) -> None:
        if self._client is not None:
            return
        self._client = chromadb.PersistentClient(path=self.chroma_db_path)
        self._content_col = self._client.get_collection(
            "historical_advice_content_clean_v2"
        )
        self._format_col = self._client.get_collection(
            "historical_advice_format_clean_v2"
        )

        with open(os.path.join(self.bm25_dir, "content_bm25.pkl"), "rb") as f:
            self._bm25_content = pickle.load(f)
        with open(os.path.join(self.bm25_dir, "content_bm25_id_map.json"), encoding="utf-8") as f:
            self._ids_content = json.load(f)
        with open(os.path.join(self.bm25_dir, "format_bm25.pkl"), "rb") as f:
            self._bm25_format = pickle.load(f)
        with open(os.path.join(self.bm25_dir, "format_bm25_id_map.json"), encoding="utf-8") as f:
            self._ids_format = json.load(f)

    async def retrieve_from_findings(
        self,
        synthesis: ReviewSynthesis,
        review_title: str = "",
    ) -> list[FindingAdviceItem]:
        """对 Chair 已确认的 findings 逐一检索历史建议。"""
        self._ensure_loaded()

        confirmed = [
            f for f in synthesis.global_review.resolved_findings
            if f.status.value == "confirmed"
        ]
        if not confirmed:
            return []

        tasks = [self._retrieve_finding(f, review_title) for f in confirmed]
        results = await asyncio.gather(*tasks)
        items: list[FindingAdviceItem] = []
        for r in results:
            items.extend(r)
        return items

    async def _retrieve_finding(
        self, finding: ResolvedFinding, title: str
    ) -> list[FindingAdviceItem]:
        """对一个 confirmed finding 构造 Query，检索，RRF 融合，Reranker 精排。"""
        dense_q, bm25_q = self._build_finding_query(finding)

        # 路由决定
        is_format = any(
            kw in finding.claim.lower()
            for kw in ["格式", "排版", "页宽", "字体", "表格", "图", "引用格式", "承诺书", "目录"]
        )
        route = "format" if is_format else "content"
        col = self._format_col if is_format else self._content_col
        bm25 = self._bm25_format if is_format else self._bm25_content
        ids = self._ids_format if is_format else self._ids_content

        # Dense
        emb = await self._embed(dense_q)
        dense_raw = await asyncio.to_thread(
            col.query,
            query_embeddings=[emb],
            n_results=self.dense_top_k,
            include=["metadatas", "distances"],
        )
        dense_hits = []
        for rank, (cid, dist, meta) in enumerate(
            zip(dense_raw["ids"][0], dense_raw["distances"][0], dense_raw["metadatas"][0]), 1
        ):
            dense_hits.append({"advice_id": cid, "rank": rank, "dist": dist, "metadata": meta})

        # BM25
        tokens = [t.strip() for t in jieba.lcut(bm25_q) if t.strip()]
        bm25_scores = await asyncio.to_thread(bm25.get_scores, tokens)
        indexed = sorted(enumerate(bm25_scores), key=lambda x: -x[1])
        bm25_hits = []
        for rank, (idx, score) in enumerate(indexed[:self.bm25_top_k], 1):
            if score <= 0:
                continue
            bm25_hits.append({"advice_id": ids[idx], "rank": rank, "score": score})

        # RRF fuse
        fused = self._rrf_fuse(dense_hits, bm25_hits)[:self.rrf_top_k]

        # Reranker
        items: list[FindingAdviceItem] = []
        for c in fused:
            meta = c.get("metadata") or {}
            result = await self._rerank(dense_q, meta)
            if result["rerank_score"] >= self.rerank_threshold:
                items.append(
                    FindingAdviceItem(
                        finding_id=finding.finding_id,
                        advice_id=c["advice_id"],
                        suggestion=meta.get("suggestion", ""),
                        issue_category=meta.get("issue_category", ""),
                        dense_rank=c.get("dense_rank"),
                        bm25_rank=c.get("bm25_rank"),
                        rrf_score=c.get("rrf_score"),
                        rerank_score=result["rerank_score"],
                        relevance=result.get("relevance"),
                        applicability=result.get("applicability"),
                        source_collection=meta.get("source_collection", ""),
                        index_version="historical_advice_v2",
                        rerank_reason=result.get("reason", ""),
                    )
                )

        return items[:self.max_advice_per_finding]

    def _build_finding_query(self, finding: ResolvedFinding) -> tuple[str, str]:
        """从 Finding 构造 Dense Query 和 BM25 Query。"""
        severity = finding.severity.value
        claim = finding.claim
        rationale = finding.rationale
        evidence_text = " ".join(
            e.quote for e in finding.evidence if e.kind.value == "paper"
        )[:600]
        location = " ".join(e.location for e in finding.evidence)[:300]
        chapters = ", ".join(finding.affected_chapter_ids)

        dense = (
            f"问题类型：{claim}\n"
            f"严重程度：{severity}\n"
            f"受影响章节：{chapters}\n"
            f"确认问题：{rationale}\n"
            f"证据原文：{evidence_text or '无'}\n"
            f"局部上下文：{location or '无'}"
        )[:3000]

        # BM25 关键词提取（简单启发式：从 rationale 提取关键术语）
        bm25 = " ".join(
            [claim, severity, chapters,
             " ".join(finding.rationale.split("，")[:3])]
        )[:500]

        return dense, bm25

    def _rrf_fuse(
        self, dense_hits: list[dict], bm25_hits: list[dict]
    ) -> list[dict]:
        fused: dict[str, dict] = {}
        k = self.rrf_k
        for h in dense_hits:
            fused[h["advice_id"]] = {
                "advice_id": h["advice_id"],
                "dense_rank": h["rank"],
                "bm25_rank": None,
                "metadata": h["metadata"],
                "rrf_score": 0.55 / (k + h["rank"]),
            }
        for h in bm25_hits:
            nid = h["advice_id"]
            if nid in fused:
                f = fused[nid]
                f["bm25_rank"] = h["rank"]
                f["rrf_score"] = 0.55 / (k + f["dense_rank"]) + 0.45 / (k + h["rank"])
            else:
                fused[nid] = {
                    "advice_id": nid,
                    "dense_rank": None,
                    "bm25_rank": h["rank"],
                    "metadata": None,
                    "rrf_score": 0.45 / (k + h["rank"]),
                }
        return sorted(fused.values(), key=lambda x: -x["rrf_score"])

    async def _embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            r = await client.post(
                self.embed_endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.embed_model, "input": [text], "dimensions": 4096},
            )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]

    async def _rerank(self, query: str, meta: dict) -> dict:
        prompt = f"""评估历史修改建议对当前问题的适用性。

## 当前问题
{query}

## 历史建议
- 问题类型：{meta.get('issue_category', '')}
- 修改建议：{meta.get('suggestion', '')}

请严格输出 JSON：{{"relevance": <0-10 整数>, "applicability": <0-10 整数>, "reason": "<一句话>"}}"""
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            r = await client.post(
                self.chat_endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.chat_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 300,
                },
            )
        r.raise_for_status()
        resp = r.json()["choices"][0]["message"]["content"].strip()
        try:
            result = json.loads(resp)
        except json.JSONDecodeError:
            m = re.search(r"\{[^}]+\}", resp)
            result = json.loads(m.group()) if m else {"relevance": 0, "applicability": 0}
        result["rerank_score"] = round((result["relevance"] + result["applicability"]) / 2, 1)
        result["reason"] = result.get("reason", "")
        return result


def build_clean_advice_retriever_from_env() -> CleanAdviceRetriever | None:
    """从环境变量构建 CleanAdviceRetriever，未配置时返回 None。"""
    chroma_path = os.getenv("DEBATE_V2_CHROMA_PATH")
    bm25_path = os.getenv("DEBATE_V2_BM25_PATH")
    if not chroma_path or not bm25_path:
        return None

    api_key = (
        os.getenv("DEBATE_V2_API_KEY")
        or os.getenv("DEBATE_EMBEDDING_API_KEY")
        or os.getenv("CLOUD_API_KEY")
        or os.getenv("DEBATE_API_KEY", "")
    )
    if not api_key:
        return None

    return CleanAdviceRetriever(
        chroma_db_path=chroma_path,
        bm25_dir=bm25_path,
        embed_endpoint=os.getenv(
            "DEBATE_V2_EMBEDDING_ENDPOINT",
            "https://api.siliconflow.cn/v1/embeddings",
        ),
        embed_model=os.getenv(
            "DEBATE_V2_EMBEDDING_MODEL",
            "Qwen/Qwen3-Embedding-8B",
        ),
        chat_endpoint=os.getenv(
            "DEBATE_V2_CHAT_ENDPOINT",
            "https://api.siliconflow.cn/v1/chat/completions",
        ),
        chat_model=os.getenv(
            "DEBATE_V2_CHAT_MODEL",
            "deepseek-ai/DeepSeek-V3.2",
        ),
        api_key=api_key,
        timeout_seconds=float(os.getenv("DEBATE_V2_TIMEOUT_SECONDS", "120")),
    )