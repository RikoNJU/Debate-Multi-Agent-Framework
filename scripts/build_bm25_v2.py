#!/usr/bin/env python3
"""Build and atomically publish BM25 V2 from the canonical advice corpus."""

from __future__ import annotations

import argparse
import json
import os
import pickle
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from rank_bm25 import BM25Okapi

from debate_agent_framework.services.rag_v2_contract import (
    SCHEMA_VERSION,
    TOKENIZER_VERSION,
    file_checksum,
    id_set_checksum,
    load_corpus,
    tokenize_bm25,
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
        default=os.getenv("DEBATE_V2_BM25_PATH", "backend/data/rag_v2/bm25"),
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def publish(staging: Path, target: Path, *, force: bool) -> None:
    staging = staging.resolve()
    target = target.resolve()
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
    if not corpus_path.is_file():
        raise FileNotFoundError(f"canonical corpus not found: {corpus_path}")
    records = load_corpus(corpus_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.building-{uuid4().hex}")
    staging.mkdir()
    started = time.time()

    try:
        counts: dict[str, int] = {}
        token_count = 0
        for advice_type in ("content", "format"):
            subset = [record for record in records if record.advice_type == advice_type]
            ids = [record.advice_id for record in subset]
            tokenized = [tokenize_bm25(record.bm25_text) for record in subset]
            if not tokenized or any(not tokens for tokens in tokenized):
                raise ValueError(f"{advice_type} BM25 corpus contains an empty document")
            index = BM25Okapi(tokenized)
            with (staging / f"{advice_type}_bm25.pkl").open("wb") as output:
                pickle.dump(index, output, protocol=pickle.HIGHEST_PROTOCOL)
            (staging / f"{advice_type}_bm25_id_map.json").write_text(
                json.dumps(ids, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            counts[advice_type] = len(ids)
            token_count += sum(len(tokens) for tokens in tokenized)

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "builder_version": "build_bm25_v2_v2",
            "tokenizer": TOKENIZER_VERSION,
            "bm25_library": "rank_bm25",
            "corpus_checksum": file_checksum(corpus_path),
            "advice_id_checksum": id_set_checksum(
                {record.advice_id for record in records}
            ),
            "total_documents": len(records),
            "content_documents": counts["content"],
            "format_documents": counts["format"],
            "total_tokens": token_count,
            "build_time": datetime.now(timezone.utc).isoformat(),
            "build_elapsed_seconds": round(time.time() - started, 3),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        publish(staging, target, force=args.force)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
