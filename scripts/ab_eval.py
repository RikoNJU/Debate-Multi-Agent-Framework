#!/usr/bin/env python3
"""Evaluate legacy, Dense V2, BM25 V2, hybrid, and reranked retrieval."""

from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import time
from pathlib import Path
from statistics import mean
from typing import Any

import chromadb
import httpx

from debate_agent_framework.services.clean_advice import QUERY_INSTRUCTION
from debate_agent_framework.services.rag_v2_contract import (
    CONTENT_COLLECTION,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    FORMAT_COLLECTION,
    AdviceCorpusRecord,
    load_corpus,
    tokenize_bm25,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", required=True, help="Labeled JSONL query set")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--dense-db", required=True)
    parser.add_argument("--bm25-dir", required=True)
    parser.add_argument("--output", default="output/rag_v2_evaluation.json")
    parser.add_argument(
        "--embedding-endpoint",
        default=os.getenv(
            "DEBATE_V2_EMBEDDING_ENDPOINT",
            "https://api.siliconflow.cn/v1/embeddings",
        ),
    )
    parser.add_argument("--embedding-model", default=EMBEDDING_MODEL)
    parser.add_argument("--embedding-dimensions", type=int, default=4096)
    parser.add_argument(
        "--rerank-endpoint",
        default=os.getenv(
            "DEBATE_V2_RERANK_ENDPOINT",
            "https://api.siliconflow.cn/v1/rerank",
        ),
    )
    parser.add_argument(
        "--rerank-model",
        default=os.getenv(
            "DEBATE_V2_RERANK_MODEL", "Qwen/Qwen3-Reranker-0.6B"
        ),
    )
    parser.add_argument("--rerank-threshold", type=float, default=6.0)
    parser.add_argument("--legacy-db")
    parser.add_argument("--legacy-content-collection")
    parser.add_argument("--legacy-format-collection")
    parser.add_argument(
        "--legacy-embedding-endpoint",
        default=os.getenv("DEBATE_EMBEDDING_ENDPOINT"),
    )
    parser.add_argument(
        "--legacy-embedding-model",
        default=os.getenv("DEBATE_EMBEDDING_MODEL", "text-embedding-v4"),
    )
    parser.add_argument("--legacy-embedding-dimensions", type=int, default=2048)
    return parser.parse_args()


def read_queries(path: Path) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            required = {
                "query_id",
                "route",
                "dense_query",
                "bm25_query",
                "relevant_advice_ids",
            }
            missing = required - set(item)
            if missing:
                raise ValueError(f"query line {line_number} missing fields: {missing}")
            if item["route"] not in {"content", "format"}:
                raise ValueError(f"query line {line_number} has invalid route")
            queries.append(item)
    if not queries:
        raise ValueError("labeled query set is empty")
    return queries


def embed(
    client: httpx.Client,
    *,
    endpoint: str,
    model: str,
    dimensions: int,
    api_key: str,
    text: str,
) -> list[float]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    response = client.post(
        endpoint,
        headers=headers,
        json={"model": model, "input": [text], "dimensions": dimensions},
    )
    response.raise_for_status()
    vector = response.json()["data"][0]["embedding"]
    if len(vector) != dimensions:
        raise ValueError(f"{model} returned {len(vector)} dimensions, expected {dimensions}")
    return vector


def dense_search(collection: Any, vector: list[float], top_k: int = 5) -> list[str]:
    result = collection.query(
        query_embeddings=[vector],
        n_results=top_k,
        include=["distances"],
    )
    return result["ids"][0]


def bm25_search(index: Any, ids: list[str], query: str, top_k: int = 5) -> list[str]:
    scores = index.get_scores(tokenize_bm25(query))
    ranked = sorted(enumerate(scores), key=lambda pair: -pair[1])
    return [ids[position] for position, score in ranked[:top_k] if score > 0]


def rrf_fuse(dense_ids: list[str], bm25_ids: list[str], top_k: int = 3) -> list[str]:
    scores: dict[str, float] = {}
    for rank, advice_id in enumerate(dense_ids, 1):
        scores[advice_id] = scores.get(advice_id, 0.0) + 0.55 / (60 + rank)
    for rank, advice_id in enumerate(bm25_ids, 1):
        scores[advice_id] = scores.get(advice_id, 0.0) + 0.45 / (60 + rank)
    return [
        advice_id
        for advice_id, _ in sorted(scores.items(), key=lambda item: -item[1])[:top_k]
    ]


def rerank(
    client: httpx.Client,
    *,
    endpoint: str,
    model: str,
    api_key: str,
    query: str,
    candidate_ids: list[str],
    records: dict[str, AdviceCorpusRecord],
    threshold: float,
) -> list[str]:
    if not candidate_ids:
        return []
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    response = client.post(
        endpoint,
        headers=headers,
        json={
            "model": model,
            "query": query,
            "documents": [records[advice_id].rerank_text for advice_id in candidate_ids],
            "top_n": len(candidate_ids),
            "return_documents": False,
        },
    )
    response.raise_for_status()
    ranked: list[tuple[str, float]] = []
    for item in response.json().get("results", []):
        position = int(item["index"])
        score = float(item.get("relevance_score", item.get("score", 0.0)))
        normalized = score * 10 if 0 <= score <= 1 else score
        if 0 <= position < len(candidate_ids) and normalized >= threshold:
            ranked.append((candidate_ids[position], normalized))
    return [advice_id for advice_id, _ in sorted(ranked, key=lambda item: -item[1])[:2]]


def per_query_metrics(
    retrieved: list[str], relevant: set[str]
) -> dict[str, float | None]:
    if not relevant:
        return {
            "recall": None,
            "mrr": None,
            "ndcg": None,
            "precision_at_2": None,
            "empty_result_accuracy": 1.0 if not retrieved else 0.0,
        }
    hits = [1 if advice_id in relevant else 0 for advice_id in retrieved]
    recall = sum(hits) / len(relevant)
    reciprocal_rank = next((1 / rank for rank, hit in enumerate(hits, 1) if hit), 0.0)
    dcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(hits, 1))
    ideal_hits = min(len(relevant), len(retrieved))
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return {
        "recall": recall,
        "mrr": reciprocal_rank,
        "ndcg": dcg / ideal if ideal else 0.0,
        "precision_at_2": sum(hits[:2]) / 2,
        "empty_result_accuracy": None,
    }


def summarize(
    query_results: list[dict[str, Any]],
    query_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    metrics: list[dict[str, float | None]] = []
    empty_metrics: list[float] = []
    for result in query_results:
        relevant = set(query_lookup[result["query_id"]]["relevant_advice_ids"])
        values = per_query_metrics(result["retrieved"], relevant)
        if relevant:
            metrics.append(values)
        elif values["empty_result_accuracy"] is not None:
            empty_metrics.append(values["empty_result_accuracy"])
    if not metrics:
        raise ValueError("query set must contain at least one query with relevance labels")
    latencies = sorted(float(item["latency_ms"]) for item in query_results)

    def percentile(fraction: float) -> float:
        position = min(len(latencies) - 1, math.ceil(fraction * len(latencies)) - 1)
        return round(latencies[max(position, 0)], 2)

    return {
        "query_count": len(query_results),
        "labeled_relevance_query_count": len(metrics),
        "empty_relevance_query_count": len(empty_metrics),
        "recall": round(mean(float(item["recall"]) for item in metrics), 4),
        "mrr": round(mean(float(item["mrr"]) for item in metrics), 4),
        "ndcg": round(mean(float(item["ndcg"]) for item in metrics), 4),
        "precision_at_2": round(
            mean(float(item["precision_at_2"]) for item in metrics), 4
        ),
        "empty_result_accuracy": (
            round(mean(empty_metrics), 4) if empty_metrics else None
        ),
        "mean_latency_ms": round(
            mean(item["latency_ms"] for item in query_results), 2
        ),
        "p50_latency_ms": percentile(0.5),
        "p95_latency_ms": percentile(0.95),
        "results": query_results,
    }


def main() -> int:
    args = parse_args()
    if args.embedding_model != EMBEDDING_MODEL:
        raise ValueError(f"V2 evaluation requires {EMBEDDING_MODEL}")
    if args.embedding_dimensions != EMBEDDING_DIMENSIONS:
        raise ValueError(f"V2 evaluation requires {EMBEDDING_DIMENSIONS} dimensions")

    queries = read_queries(Path(args.queries))
    query_lookup = {item["query_id"]: item for item in queries}
    corpus = load_corpus(args.corpus)
    records = {record.advice_id: record for record in corpus}
    legacy_to_v2 = {record.legacy_chunk_id: record.advice_id for record in corpus}
    dense_client = chromadb.PersistentClient(path=args.dense_db)
    dense_collections = {
        "content": dense_client.get_collection(CONTENT_COLLECTION),
        "format": dense_client.get_collection(FORMAT_COLLECTION),
    }
    bm25_path = Path(args.bm25_dir)
    bm25: dict[str, Any] = {}
    bm25_ids: dict[str, list[str]] = {}
    for route in ("content", "format"):
        with (bm25_path / f"{route}_bm25.pkl").open("rb") as source:
            bm25[route] = pickle.load(source)
        bm25_ids[route] = json.loads(
            (bm25_path / f"{route}_bm25_id_map.json").read_text(encoding="utf-8")
        )

    api_key = os.getenv("DEBATE_V2_API_KEY", "")
    experiments: dict[str, list[dict[str, Any]]] = {
        "B_clean_qwen_dense": [],
        "C_bm25": [],
        "D_hybrid_rrf": [],
        "E_hybrid_reranker": [],
    }
    legacy_enabled = all(
        (
            args.legacy_db,
            args.legacy_content_collection,
            args.legacy_format_collection,
            args.legacy_embedding_endpoint,
        )
    )
    if legacy_enabled:
        experiments["A_legacy_dense"] = []
        legacy_client = chromadb.PersistentClient(path=args.legacy_db)
        legacy_collections = {
            "content": legacy_client.get_collection(args.legacy_content_collection),
            "format": legacy_client.get_collection(args.legacy_format_collection),
        }

    with httpx.Client(timeout=120.0) as http_client:
        for query in queries:
            route = query["route"]
            started = time.perf_counter()
            vector = embed(
                http_client,
                endpoint=args.embedding_endpoint,
                model=args.embedding_model,
                dimensions=args.embedding_dimensions,
                api_key=api_key,
                text=QUERY_INSTRUCTION + query["dense_query"],
            )
            dense_ids = dense_search(dense_collections[route], vector)
            dense_latency = (time.perf_counter() - started) * 1000
            experiments["B_clean_qwen_dense"].append(
                {
                    "query_id": query["query_id"],
                    "retrieved": dense_ids,
                    "latency_ms": dense_latency,
                }
            )

            started = time.perf_counter()
            sparse_ids = bm25_search(
                bm25[route], bm25_ids[route], query["bm25_query"]
            )
            sparse_latency = (time.perf_counter() - started) * 1000
            experiments["C_bm25"].append(
                {
                    "query_id": query["query_id"],
                    "retrieved": sparse_ids,
                    "latency_ms": sparse_latency,
                }
            )

            started = time.perf_counter()
            hybrid_ids = rrf_fuse(dense_ids, sparse_ids)
            hybrid_latency = dense_latency + sparse_latency + (
                time.perf_counter() - started
            ) * 1000
            experiments["D_hybrid_rrf"].append(
                {
                    "query_id": query["query_id"],
                    "retrieved": hybrid_ids,
                    "latency_ms": hybrid_latency,
                }
            )

            started = time.perf_counter()
            reranked_ids = rerank(
                http_client,
                endpoint=args.rerank_endpoint,
                model=args.rerank_model,
                api_key=api_key,
                query=query["dense_query"],
                candidate_ids=hybrid_ids,
                records=records,
                threshold=args.rerank_threshold,
            )
            experiments["E_hybrid_reranker"].append(
                {
                    "query_id": query["query_id"],
                    "retrieved": reranked_ids,
                    "latency_ms": hybrid_latency
                    + (time.perf_counter() - started) * 1000,
                }
            )

            if legacy_enabled:
                started = time.perf_counter()
                legacy_vector = embed(
                    http_client,
                    endpoint=args.legacy_embedding_endpoint,
                    model=args.legacy_embedding_model,
                    dimensions=args.legacy_embedding_dimensions,
                    api_key=os.getenv("DEBATE_EMBEDDING_API_KEY", api_key),
                    text=query["dense_query"],
                )
                legacy_ids = dense_search(legacy_collections[route], legacy_vector)
                mapped = [legacy_to_v2[item] for item in legacy_ids if item in legacy_to_v2]
                experiments["A_legacy_dense"].append(
                    {
                        "query_id": query["query_id"],
                        "retrieved": mapped,
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    }
                )

    report = {
        "evaluation_status": "measured_from_labeled_queries",
        "query_set": str(Path(args.queries).resolve()),
        "experiments": {
            name: summarize(results, query_lookup)
            for name, results in experiments.items()
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
