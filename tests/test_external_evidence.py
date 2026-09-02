from __future__ import annotations

import asyncio

import httpx
import pytest
from debate_agent_framework.schemas import EvidenceKind
from debate_agent_framework.services import OpenAlexEvidenceRetriever
from debate_agent_framework.services import external_evidence
from debate_agent_framework.services.external_evidence import EvidenceRetrievalError


class FakeContext:
    paper_id = "paper-1"


def make_client(payload):  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    return httpx.AsyncClient(transport=transport)


def sample_payload() -> dict:
    return {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "title": "A survey of multi-agent review systems",
                "doi": "https://doi.org/10.1000/xyz123",
                "publication_year": 2024,
                "primary_location": {"source": {"display_name": "Journal of AI"}},
                "abstract_inverted_index": {"multi": [0], "agent": [1], "review": [2]},
                "relevance_score": 0.93,
            },
            {
                "id": "https://openalex.org/W2",
                "title": "Duplicate of the survey",
                "doi": "https://doi.org/10.1000/xyz123",
                "publication_year": 2024,
                "abstract_inverted_index": None,
                "relevance_score": 0.8,
            },
            {
                "id": "https://openalex.org/W3",
                "title": "Baselines in evaluation research",
                "doi": "https://doi.org/10.2000/abc456",
                "publication_year": 2023,
                "primary_location": {"source": {"display_name": "Review Metrics"}},
                "abstract_inverted_index": None,
                "relevance_score": 0.7,
            },
        ]
    }


def test_openalex_maps_dedupes_and_respects_limit() -> None:
    retriever = OpenAlexEvidenceRetriever(http_client=make_client(sample_payload()))
    evidence = asyncio.run(
        retriever.retrieve(["baseline survey"], context=FakeContext(), limit=2)
    )

    assert len(evidence) == 2
    first, second = evidence
    assert first.kind is EvidenceKind.EXTERNAL
    assert first.doi == "10.1000/xyz123"
    assert first.url == "https://doi.org/10.1000/xyz123"
    assert first.source_title == "A survey of multi-agent review systems"
    assert "multi agent review" in first.quote
    assert "Journal of AI" in first.location
    assert first.evidence_id.startswith("openalex-")
    assert second.doi == "10.2000/abc456"


def test_openalex_rejects_result_without_doi_or_url() -> None:
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W9",
                "title": "No locator work",
                "abstract_inverted_index": {"no": [0], "locator": [1]},
                "relevance_score": 0.5,
            }
        ]
    }
    retriever = OpenAlexEvidenceRetriever(http_client=make_client(payload))
    evidence = asyncio.run(
        retriever.retrieve(["query"], context=FakeContext(), limit=5)
    )
    assert evidence == []


def test_openalex_raises_when_all_queries_fail() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))
    retriever = OpenAlexEvidenceRetriever(http_client=httpx.AsyncClient(transport=transport))
    with pytest.raises(EvidenceRetrievalError):
        asyncio.run(retriever.retrieve(["a", "b"], context=FakeContext(), limit=5))


def test_openalex_returns_partial_results_on_single_failure() -> None:
    calls = {"count": 0}

    def handler(request):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=sample_payload())

    retriever = OpenAlexEvidenceRetriever(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    evidence = asyncio.run(
        retriever.retrieve(["failing", "ok"], context=FakeContext(), limit=5)
    )
    assert len(evidence) == 2


def test_env_builder_returns_none_when_disabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DEBATE_EVIDENCE_PROVIDER", raising=False)
    assert external_evidence.build_evidence_retriever_from_env() is None

    monkeypatch.setenv("DEBATE_EVIDENCE_PROVIDER", "none")
    assert external_evidence.build_evidence_retriever_from_env() is None


def test_env_builder_constructs_openalex(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DEBATE_EVIDENCE_PROVIDER", "openalex")
    monkeypatch.setenv("DEBATE_EVIDENCE_MAILTO", "me@example.org")
    monkeypatch.setenv("DEBATE_EVIDENCE_MAX_RESULTS", "3")

    retriever = external_evidence.build_evidence_retriever_from_env()

    assert isinstance(retriever, OpenAlexEvidenceRetriever)
    assert retriever.mailto == "me@example.org"
    assert retriever.per_query_results == 3


def test_env_builder_rejects_unknown_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DEBATE_EVIDENCE_PROVIDER", "crossref")
    with pytest.raises(RuntimeError, match="未知的外部证据检索源"):
        external_evidence.build_evidence_retriever_from_env()
