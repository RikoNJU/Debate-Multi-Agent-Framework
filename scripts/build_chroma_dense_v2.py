#!/usr/bin/env python3
"""Build and atomically publish the Qwen3 4096-dimension Dense V2 index."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import chromadb
import httpx
from chromadb.config import Settings

from debate_agent_framework.services.rag_v2_contract import (
    CONTENT_COLLECTION,
    DISTANCE_METRIC,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    FORMAT_COLLECTION,
    SCHEMA_VERSION,
    file_checksum,
    id_set_checksum,
    load_corpus,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        default=os.getenv(
            "RAG_V2_CORPUS_PATH",
            "backend/data/rag_v2/corpus/historical_advice_v2.jsonl",
        ),
    )
    parser.add_argument(
        "--output",
        default=os.getenv("DEBATE_V2_CHROMA_PATH", "backend/data/rag_v2/chroma"),
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv(
            "DEBATE_V2_EMBEDDING_ENDPOINT",
            "https://api.siliconflow.cn/v1/embeddings",
        ),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("DEBATE_V2_EMBEDDING_MODEL", EMBEDDING_MODEL),
    )
    parser.add_argument(
        "--dimensions",
        type=int,
        default=int(os.getenv("DEBATE_V2_EMBEDDING_DIMENSIONS", "4096")),
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def embed_batch(
    client: httpx.Client,
    *,
    endpoint: str,
    api_key: str,
    model: str,
    dimensions: int,
    texts: list[str],
    attempts: int,
) -> list[list[float]]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "input": texts, "dimensions": dimensions}
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            items = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in items]
            if len(vectors) != len(texts):
                raise ValueError(
                    f"embedding count mismatch: {len(vectors)} != {len(texts)}"
                )
            invalid = [len(vector) for vector in vectors if len(vector) != dimensions]
            if invalid:
                raise ValueError(
                    f"embedding dimension mismatch: expected {dimensions}, got {invalid[:3]}"
                )
            return vectors
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise RuntimeError(f"embedding request failed after {attempts} attempts: {last_error}")


def safe_publish(staging: Path, target: Path, *, force: bool) -> None:
    target = target.resolve()
    staging = staging.resolve()
    if staging.parent != target.parent or staging == target:
        raise ValueError("staging and target directories are not safe siblings")
    if target.exists() and not force:
        raise FileExistsError(f"target already exists; pass --force to replace it: {target}")
    backup: Path | None = None
    if target.exists():
        backup = target.with_name(f".{target.name}.backup-{uuid4().hex}")
        target.rename(backup)
    try:
        staging.rename(target)
    except Exception:
        if backup is not None and not target.exists():
            backup.rename(target)
        raise
    if backup is not None:
        shutil.rmtree(backup)


def main() -> int:
    args = parse_args()
    corpus_path = Path(args.corpus).expanduser().resolve()
    target = Path(args.output).expanduser().resolve()
    if args.model != EMBEDDING_MODEL:
        raise ValueError(
            f"Dense V2 is fixed to {EMBEDDING_MODEL}; received {args.model}"
        )
    if args.dimensions != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Dense V2 is fixed to {EMBEDDING_DIMENSIONS} dimensions; "
            f"received {args.dimensions}"
        )
    if args.batch_size < 1 or args.attempts < 1:
        raise ValueError("batch size and attempts must be at least 1")
    if not corpus_path.is_file():
        raise FileNotFoundError(f"canonical corpus not found: {corpus_path}")

    records = load_corpus(corpus_path)
    content_records = [record for record in records if record.advice_type == "content"]
    format_records = [record for record in records if record.advice_type == "format"]
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.building-{uuid4().hex}")
    staging.mkdir()

    started = time.time()
    api_key = (
        os.getenv("DEBATE_V2_API_KEY")
        or os.getenv("DEBATE_EMBEDDING_API_KEY")
        or os.getenv("CLOUD_API_KEY")
        or ""
    )
    try:
        client = chromadb.PersistentClient(
            path=str(staging),
            settings=Settings(anonymized_telemetry=False),
        )
        collections = {
            "content": client.create_collection(
                CONTENT_COLLECTION,
                metadata={"hnsw:space": DISTANCE_METRIC},
            ),
            "format": client.create_collection(
                FORMAT_COLLECTION,
                metadata={"hnsw:space": DISTANCE_METRIC},
            ),
        }
        with httpx.Client(timeout=args.timeout) as http_client:
            for advice_type, subset in (
                ("content", content_records),
                ("format", format_records),
            ):
                collection = collections[advice_type]
                for start in range(0, len(subset), args.batch_size):
                    batch = subset[start : start + args.batch_size]
                    texts = [record.dense_text for record in batch]
                    vectors = embed_batch(
                        http_client,
                        endpoint=args.endpoint,
                        api_key=api_key,
                        model=args.model,
                        dimensions=args.dimensions,
                        texts=texts,
                        attempts=args.attempts,
                    )
                    collection.add(
                        ids=[record.advice_id for record in batch],
                        embeddings=vectors,
                        documents=texts,
                        metadatas=[
                            {
                                "advice_id": record.advice_id,
                                "source_collection": record.source_collection,
                                "advice_type": record.advice_type,
                                "paper_type": record.paper_type,
                                "chapter_stage": record.chapter_stage,
                                "issue_category": record.issue_category,
                                "record_checksum": record.record_checksum,
                                "schema_version": SCHEMA_VERSION,
                            }
                            for record in batch
                        ],
                    )
                    print(
                        f"[{advice_type}] {min(start + args.batch_size, len(subset))}/"
                        f"{len(subset)}",
                        file=sys.stderr,
                    )

        if collections["content"].count() != len(content_records):
            raise RuntimeError("content collection count mismatch")
        if collections["format"].count() != len(format_records):
            raise RuntimeError("format collection count mismatch")

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "builder_version": "build_chroma_dense_v2_v2",
            "embedding_model": args.model,
            "embedding_dimensions": args.dimensions,
            "distance_metric": DISTANCE_METRIC,
            "query_instruction_version": "thesis-finding-zh-v1",
            "corpus_checksum": file_checksum(corpus_path),
            "advice_id_checksum": id_set_checksum(
                {record.advice_id for record in records}
            ),
            "total_records": len(records),
            "content_count": len(content_records),
            "format_count": len(format_records),
            "collections": {
                "content": CONTENT_COLLECTION,
                "format": FORMAT_COLLECTION,
            },
            "build_time": datetime.now(timezone.utc).isoformat(),
            "build_elapsed_seconds": round(time.time() - started, 3),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        system = getattr(client, "_system", None)
        if system is not None and hasattr(system, "stop"):
            system.stop()
        del collections, client
        safe_publish(staging, target, force=args.force)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
