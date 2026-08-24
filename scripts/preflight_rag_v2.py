#!/usr/bin/env python3
"""Validate canonical corpus, Dense V2, and BM25 V2 before enabling retrieval."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import chromadb

from debate_agent_framework.services.rag_v2_contract import (
    CONTENT_COLLECTION,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    FORMAT_COLLECTION,
    SCHEMA_VERSION,
    TOKENIZER_VERSION,
    file_checksum,
    id_set_checksum,
    load_corpus,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--dense-db", required=True)
    parser.add_argument("--bm25-dir", required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, object] | list[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    corpus_path = Path(args.corpus).resolve()
    dense_path = Path(args.dense_db).resolve()
    bm25_path = Path(args.bm25_dir).resolve()
    records = load_corpus(corpus_path)
    expected_ids = {record.advice_id for record in records}
    expected_checksum = id_set_checksum(expected_ids)
    expected_corpus_checksum = file_checksum(corpus_path)

    dense_manifest = read_json(dense_path / "manifest.json")
    bm25_manifest = read_json(bm25_path / "manifest.json")
    assert isinstance(dense_manifest, dict)
    assert isinstance(bm25_manifest, dict)
    checks = {
        "dense_schema": dense_manifest.get("schema_version") == SCHEMA_VERSION,
        "dense_model": dense_manifest.get("embedding_model") == EMBEDDING_MODEL,
        "dense_dimensions": dense_manifest.get("embedding_dimensions")
        == EMBEDDING_DIMENSIONS,
        "dense_corpus": dense_manifest.get("corpus_checksum")
        == expected_corpus_checksum,
        "dense_ids": dense_manifest.get("advice_id_checksum") == expected_checksum,
        "bm25_schema": bm25_manifest.get("schema_version") == SCHEMA_VERSION,
        "bm25_tokenizer": bm25_manifest.get("tokenizer") == TOKENIZER_VERSION,
        "bm25_corpus": bm25_manifest.get("corpus_checksum")
        == expected_corpus_checksum,
        "bm25_ids": bm25_manifest.get("advice_id_checksum") == expected_checksum,
    }

    client = chromadb.PersistentClient(path=str(dense_path))
    content = client.get_collection(CONTENT_COLLECTION)
    format_collection = client.get_collection(FORMAT_COLLECTION)
    checks["dense_content_count"] = content.count() == sum(
        record.advice_type == "content" for record in records
    )
    checks["dense_format_count"] = format_collection.count() == sum(
        record.advice_type == "format" for record in records
    )

    content_ids = read_json(bm25_path / "content_bm25_id_map.json")
    format_ids = read_json(bm25_path / "format_bm25_id_map.json")
    if not isinstance(content_ids, list) or not isinstance(format_ids, list):
        raise ValueError("BM25 ID maps must be JSON arrays")
    checks["bm25_id_maps"] = set(content_ids) | set(format_ids) == expected_ids
    failed = sorted(name for name, passed in checks.items() if not passed)
    report = {
        "status": "passed" if not failed else "failed",
        "record_count": len(records),
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "tokenizer": TOKENIZER_VERSION,
        "checks": checks,
        "failed_checks": failed,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
