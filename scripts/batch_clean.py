#!/usr/bin/env python3
"""Export legacy Chroma advice into the canonical historical-advice V2 corpus."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import chromadb

from debate_agent_framework.services.rag_v2_contract import (
    AdviceCorpusRecord,
    DISTANCE_METRIC,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    SCHEMA_VERSION,
    id_set_checksum,
    normalize_chapter_stage,
    record_checksum,
    sha256_text,
    stable_advice_id,
)


FIELD_LABELS = (
    "论文标题:",
    "所属章节:",
    "问题位置:",
    "上下文:",
    "修改建议:",
    "分析过程:",
    "原文片段:",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-db",
        default=os.getenv("LEGACY_CHROMA_PATH"),
        required=not bool(os.getenv("LEGACY_CHROMA_PATH")),
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("RAG_V2_CORPUS_DIR", "backend/data/rag_v2/corpus"),
    )
    parser.add_argument(
        "--content-collection",
        default=os.getenv(
            "LEGACY_CONTENT_COLLECTION",
            "user_result_content_collection_cloud_4b",
        ),
    )
    parser.add_argument(
        "--format-collection",
        default=os.getenv(
            "LEGACY_FORMAT_COLLECTION",
            "user_result_format_collection_cloud_4b",
        ),
    )
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, help="Limit each collection for a smoke build")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail the build when any source record cannot be cleaned",
    )
    return parser.parse_args()


def parse_seven_fields(document: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    pattern = "(" + "|".join(re.escape(label) for label in FIELD_LABELS) + ")"
    parts = re.split(pattern, document)
    current: str | None = None
    for part in parts:
        if part in FIELD_LABELS:
            current = part.rstrip(":")
        elif current and part.strip() and current not in fields:
            fields[current] = part.strip()
    return fields


def clean_whitespace(text: str) -> str:
    text = re.sub(r"[ \t\v\f]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"(?<!\n)\n(?!\n)", " ", text).strip()


def clean_personal_data(text: str) -> str:
    text = re.sub(r"\b2[01]\d{6}\b", "[学号已脱敏]", text)
    text = re.sub(r"\b1\d{6,8}\b", "[学号已脱敏]", text)
    return re.sub(
        r"(?:[A-Za-z]:)?[/\\][\w\-./\\ ]*\.(?:pdf|tex|md|txt|docx?)\b",
        "[路径已脱敏]",
        text,
        flags=re.IGNORECASE,
    )


def clean_field(field_name: str, value: str) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"\\([*_#\[\]])", r"\1", text)
    text = clean_personal_data(text)
    text = re.sub(r"##\s*块\s*\d+\s*/\s*\d+\s*\[.*?\]", "", text)
    text = re.sub(r"块\s*\d+\s*/\s*\d+", "", text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "[图片]", text)
    text = re.sub(r"\$\$.*?\$\$", "[公式]", text, flags=re.DOTALL)
    text = re.sub(r"\$[^$]+\$", "[公式]", text)
    text = re.sub(r"<!--\s*formula\s*-->", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    if field_name == "分析过程":
        text = re.sub(r"从\s*原[文始]?\s*块?\s*\d*\s*可以\s*看[出到]", "", text)
        text = re.sub(r"检查原文块\d+发现", "", text)
    return clean_whitespace(text)


def build_dense_text(record: dict[str, str]) -> str:
    labels = (
        ("问题类型", "issue_category"),
        ("章节阶段", "chapter_stage"),
        ("问题位置", "problem_location"),
        ("历史问题诊断", "analysis"),
        ("历史上下文", "context"),
        ("历史证据摘要", "evidence_excerpt"),
    )
    return "\n".join(
        f"{label}：{record[key]}" for label, key in labels if record.get(key)
    )


def build_bm25_text(record: dict[str, str]) -> str:
    return " ".join(
        record[key]
        for key in (
            "issue_category",
            "chapter_stage",
            "problem_location",
            "analysis",
            "context",
            "evidence_excerpt",
        )
        if record.get(key)
    )


def clean_one(
    legacy_chunk_id: str,
    raw_document: str,
    metadata: dict[str, object],
    collection_name: str,
) -> AdviceCorpusRecord:
    fields = parse_seven_fields(raw_document)
    chapter = clean_field("所属章节", fields.get("所属章节", ""))
    position = clean_field("问题位置", fields.get("问题位置", ""))
    original_text = clean_field("原文片段", fields.get("原文片段", ""))
    advice_type = str(metadata.get("advice_type", "")).lower()
    if advice_type not in {"content", "format"}:
        advice_type = "format" if "format" in collection_name.lower() else "content"

    raw_checksum = sha256_text(raw_document)
    base: dict[str, object] = {
        "advice_id": stable_advice_id(collection_name, legacy_chunk_id, raw_checksum),
        "legacy_chunk_id": legacy_chunk_id,
        "source_collection": collection_name,
        "advice_type": advice_type,
        "paper_title": clean_field("论文标题", fields.get("论文标题", "")),
        "paper_type": str(metadata.get("paper_type", "")),
        "chapter": chapter,
        "chapter_stage": normalize_chapter_stage(chapter, position),
        "problem_location": position,
        "issue_category": str(metadata.get("type", metadata.get("issue_category", ""))),
        "context": clean_field("上下文", fields.get("上下文", "")),
        "advice": clean_field("修改建议", fields.get("修改建议", "")),
        "analysis": clean_field("分析过程", fields.get("分析过程", "")),
        "evidence_excerpt": original_text[:300],
        "original_text": original_text,
        "raw_checksum": raw_checksum,
        "clean_version": SCHEMA_VERSION,
    }
    text_view = {key: str(value) for key, value in base.items()}
    base["dense_text"] = build_dense_text(text_view)
    base["bm25_text"] = build_bm25_text(text_view)
    base["rerank_text"] = (
        f"{base['dense_text']}\n历史修改建议：{base['advice']}"
    )
    base["record_checksum"] = record_checksum(base)
    return AdviceCorpusRecord.model_validate(base)


def main() -> int:
    args = parse_args()
    source_db = Path(args.source_db).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not source_db.exists():
        raise FileNotFoundError(f"legacy Chroma path does not exist: {source_db}")
    if args.batch_size < 1:
        raise ValueError("--batch-size must be at least 1")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "historical_advice_v2.jsonl"
    error_path = output_dir / "historical_advice_v2_errors.jsonl"
    manifest_path = output_dir / "manifest_v2.json"
    collections = (args.content_collection, args.format_collection)
    client = chromadb.PersistentClient(path=str(source_db))
    available = {item.name for item in client.list_collections()}
    missing = [name for name in collections if name not in available]
    if missing:
        raise ValueError(
            f"legacy collections not found: {missing}; available={sorted(available)}"
        )

    started = time.time()
    counts = {"content": 0, "format": 0}
    errors = 0
    advice_ids: set[str] = set()
    source_fingerprints: list[str] = []
    with output_path.open("w", encoding="utf-8") as output, error_path.open(
        "w", encoding="utf-8"
    ) as error_output:
        for collection_name in collections:
            collection = client.get_collection(collection_name)
            total = collection.count()
            if args.limit is not None:
                total = min(total, args.limit)
            for offset in range(0, total, args.batch_size):
                batch = collection.get(
                    limit=min(args.batch_size, total - offset),
                    offset=offset,
                    include=["documents", "metadatas"],
                )
                for legacy_id, document, metadata in zip(
                    batch["ids"], batch["documents"], batch["metadatas"], strict=True
                ):
                    try:
                        record = clean_one(
                            legacy_id,
                            document or "",
                            metadata or {},
                            collection_name,
                        )
                        if record.advice_id in advice_ids:
                            raise ValueError(f"duplicate advice_id: {record.advice_id}")
                        advice_ids.add(record.advice_id)
                        source_fingerprints.append(
                            f"{collection_name}\0{legacy_id}\0{record.raw_checksum}"
                        )
                        counts[record.advice_type] += 1
                        output.write(record.model_dump_json() + "\n")
                    except Exception as exc:
                        errors += 1
                        error_output.write(
                            json.dumps(
                                {
                                    "legacy_chunk_id": legacy_id,
                                    "source_collection": collection_name,
                                    "error": str(exc),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                print(
                    f"[{collection_name}] {min(offset + args.batch_size, total)}/{total}",
                    file=sys.stderr,
                )

    if args.strict and errors:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"strict clean failed with {errors} invalid records")
    if not advice_ids:
        raise RuntimeError("cleaning produced no valid records")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "builder_version": "batch_clean_v2",
        "source_database_path": str(source_db),
        "source_collections": list(collections),
        "source_fingerprint": sha256_text("\n".join(sorted(source_fingerprints))),
        "record_count": len(advice_ids),
        "content_count": counts["content"],
        "format_count": counts["format"],
        "error_count": errors,
        "advice_id_checksum": id_set_checksum(advice_ids),
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "distance_metric": DISTANCE_METRIC,
        "build_time": datetime.now(timezone.utc).isoformat(),
        "build_elapsed_seconds": round(time.time() - started, 3),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
