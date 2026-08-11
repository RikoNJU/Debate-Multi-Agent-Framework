"""OpenAlex 驱动的外部学术证据检索适配器（M6）。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Sequence
from typing import Any

from ..schemas import EvidenceKind, ReviewContext, ReviewEvidence

logger = logging.getLogger("debate.evidence")

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


class EvidenceRetrievalError(RuntimeError):
    pass


class OpenAlexEvidenceRetriever:
    """为 Debate 争议问题检索可追溯的外部学术证据。

    OpenAlex 是无需 API Key 的公开学术索引，返回的论文均带 DOI 或 URL，
    满足 ``ReviewEvidence`` 对外部证据必须可追溯的约束。检索失败按查询
    降级，不拖垮核心评审。
    """

    def __init__(
        self,
        *,
        mailto: str | None = None,
        per_query_results: int = 5,
        max_quote_chars: int = 500,
        timeout_seconds: float = 20.0,
        confidence: float = 0.7,
        http_client: Any | None = None,
    ) -> None:
        if per_query_results < 1:
            raise ValueError("per_query_results must be at least 1")
        if max_quote_chars < 50:
            raise ValueError("max_quote_chars must be at least 50")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.mailto = (mailto or "").strip() or None
        self.per_query_results = per_query_results
        self.max_quote_chars = max_quote_chars
        self.timeout_seconds = timeout_seconds
        self.confidence = confidence
        self.http_client = http_client

    async def retrieve(
        self,
        queries: Sequence[str],
        *,
        context: ReviewContext,
        limit: int,
    ) -> Sequence[ReviewEvidence]:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        collected: list[ReviewEvidence] = []
        seen: set[str] = set()
        failures = 0
        for query in queries:
            if len(collected) >= limit:
                break
            try:
                results = await self._search(query)
            except Exception as exc:  # 单次查询失败不拖垮其他查询
                failures += 1
                logger.warning("OpenAlex 检索失败（%r）：%s", query[:80], exc)
                continue
            for item in results:
                evidence = self._to_evidence(item)
                if evidence is None:
                    continue
                key = evidence.doi or evidence.url or evidence.evidence_id
                if key in seen:
                    continue
                seen.add(key)
                collected.append(evidence)
                if len(collected) >= limit:
                    break

        if not collected and failures == len(queries):
            raise EvidenceRetrievalError("全部外部证据查询均失败")
        return collected[:limit]

    async def _search(self, query: str) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "search": query[:500],
            "per-page": self.per_query_results,
            "select": (
                "id,title,doi,publication_year,primary_location,"
                "abstract_inverted_index,relevance_score"
            ),
        }
        if self.mailto:
            params["mailto"] = self.mailto

        async def do_search(client: Any) -> dict[str, Any]:
            response = await client.get(OPENALEX_WORKS_URL, params=params)
            if not 200 <= response.status_code < 300:
                raise EvidenceRetrievalError(
                    f"OpenAlex 请求失败 HTTP {response.status_code}"
                )
            try:
                return response.json()
            except Exception as exc:
                raise EvidenceRetrievalError("OpenAlex 返回了非法 JSON") from exc

        if self.http_client is not None:
            payload = await do_search(self.http_client)
        else:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - optional RAG dependency
                raise RuntimeError("httpx is required; install the 'rag' extra") from exc
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, follow_redirects=True
            ) as client:
                payload = await do_search(client)

        results = payload.get("results") or []
        return [item for item in results if isinstance(item, dict)]

    def _to_evidence(self, item: dict[str, Any]) -> ReviewEvidence | None:
        title = str(item.get("title") or item.get("display_name") or "").strip()
        if not title:
            return None
        doi = _clean_doi(item.get("doi"))
        url = _clean_url(item.get("url"))
        if not url and doi:
            url = f"https://doi.org/{doi}"
        if not (doi or url):
            return None

        abstract = _abstract_text(item.get("abstract_inverted_index"))
        quote = (abstract or title)[: self.max_quote_chars].strip()
        if not quote:
            return None

        source = _journal_name(item)
        year = item.get("publication_year")
        location = f"{source}（{year}）" if source and year else source or "OpenAlex"

        try:
            relevance = float(item.get("relevance_score") or 0.5)
        except (TypeError, ValueError):
            relevance = 0.5
        relevance = max(0.0, min(1.0, relevance))

        evidence_id = _stable_id(doi or url or title)
        return ReviewEvidence(
            evidence_id=f"openalex-{evidence_id}",
            kind=EvidenceKind.EXTERNAL,
            source_title=title,
            quote=quote,
            location=location,
            doi=doi,
            url=url,
            relevance=relevance,
            confidence=self.confidence,
        )


def build_evidence_retriever_from_env() -> OpenAlexEvidenceRetriever | None:
    """按环境变量构建外部证据检索器；未配置或显式关闭时返回 None。"""

    provider = os.getenv("DEBATE_EVIDENCE_PROVIDER", "").strip().lower()
    if not provider or provider in {"none", "off", "false"}:
        return None
    if provider != "openalex":
        raise RuntimeError(f"未知的外部证据检索源：{provider}")
    return OpenAlexEvidenceRetriever(
        mailto=os.getenv("DEBATE_EVIDENCE_MAILTO") or None,
        per_query_results=int(os.getenv("DEBATE_EVIDENCE_MAX_RESULTS", "5")),
        timeout_seconds=float(os.getenv("DEBATE_EVIDENCE_TIMEOUT_SECONDS", "20")),
    )


def _clean_doi(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("https://doi.org/"):
        text = text[len("https://doi.org/") :]
    return text if text else None


def _clean_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.startswith(("http://", "https://")):
        return None
    return text


def _journal_name(item: dict[str, Any]) -> str:
    location = item.get("primary_location") or item.get("host_venue") or {}
    if not isinstance(location, dict):
        return ""
    source = location.get("source") or {}
    if isinstance(source, dict):
        return str(source.get("display_name") or "").strip()
    display = location.get("display_name")
    return str(display or "").strip()


def _abstract_text(inverted: Any) -> str:
    if not isinstance(inverted, dict):
        return ""
    positions: dict[int, str] = {}
    for word, indexes in inverted.items():
        if not isinstance(indexes, (list, tuple)):
            continue
        for position in indexes:
            positions[int(position)] = str(word)
    return " ".join(positions[index] for index in sorted(positions) if index >= 0)


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
