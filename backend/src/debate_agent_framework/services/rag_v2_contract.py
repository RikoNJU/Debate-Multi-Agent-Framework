"""Shared data contract and text processing for historical-advice RAG V2."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

import jieba
from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "historical_advice_v2"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"
EMBEDDING_DIMENSIONS = 4096
DISTANCE_METRIC = "cosine"
CONTENT_COLLECTION = "historical_advice_content_clean_v2"
FORMAT_COLLECTION = "historical_advice_format_clean_v2"
TOKENIZER_VERSION = f"jieba-{getattr(jieba, '__version__', 'unknown')}-rag-v2"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_checksum(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_advice_id(
    source_collection: str,
    legacy_chunk_id: str,
    raw_checksum: str,
) -> str:
    identity = "\0".join((source_collection, legacy_chunk_id, raw_checksum))
    return "adv_" + sha256_text(identity)[:24]


def normalize_chapter_stage(chapter: str, position: str = "") -> str:
    text = f"{chapter} {position}".lower()
    rules = (
        ("abstract", ("摘要", "abstract")),
        ("introduction", ("绪论", "引言", "研究背景", "introduction")),
        ("related_work", ("相关工作", "文献综述", "国内外研究", "related work")),
        ("method", ("方法", "模型", "算法", "系统设计", "method")),
        ("experiment", ("实验", "结果", "消融", "性能评估", "experiment")),
        ("discussion", ("讨论", "局限", "discussion", "limitation")),
        ("conclusion", ("结论", "总结", "展望", "conclusion")),
        ("references", ("参考文献", "references")),
    )
    for stage, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return stage
    return "general"


def tokenize_bm25(text: str) -> list[str]:
    """Tokenize Chinese retrieval text with one versioned implementation."""

    tokens: list[str] = []
    for raw in jieba.lcut(text.lower()):
        token = raw.strip()
        if not token:
            continue
        if len(token) == 1 and not token.isalnum() and not "\u4e00" <= token <= "\u9fff":
            continue
        tokens.append(token)
    return tokens


class AdviceCorpusRecord(BaseModel):
    """Canonical record shared by Dense, BM25, reranking, and audit views."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    advice_id: str = Field(pattern=r"^adv_[0-9a-f]{24}$")
    legacy_chunk_id: str = Field(min_length=1)
    source_collection: str = Field(min_length=1)
    advice_type: Literal["content", "format"]
    paper_title: str = ""
    paper_type: str = ""
    chapter: str = ""
    chapter_stage: str = "general"
    problem_location: str = ""
    issue_category: str = ""
    context: str = ""
    advice: str = Field(min_length=1)
    analysis: str = ""
    evidence_excerpt: str = Field(default="", max_length=300)
    original_text: str = ""
    dense_text: str = Field(min_length=1)
    bm25_text: str = Field(min_length=1)
    rerank_text: str = Field(min_length=1)
    raw_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    clean_version: str = SCHEMA_VERSION

    @model_validator(mode="after")
    def validate_identity(self) -> "AdviceCorpusRecord":
        expected_id = stable_advice_id(
            self.source_collection,
            self.legacy_chunk_id,
            self.raw_checksum,
        )
        if self.advice_id != expected_id:
            raise ValueError("advice_id does not match canonical identity")
        return self


def record_checksum(record: dict[str, object]) -> str:
    fields = {
        key: record.get(key, "")
        for key in (
            "advice_id",
            "advice_type",
            "chapter_stage",
            "problem_location",
            "issue_category",
            "context",
            "advice",
            "analysis",
            "evidence_excerpt",
            "dense_text",
            "bm25_text",
            "rerank_text",
        )
    }
    return sha256_text(json.dumps(fields, ensure_ascii=False, sort_keys=True))


def load_corpus(path: str | Path) -> list[AdviceCorpusRecord]:
    records: list[AdviceCorpusRecord] = []
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            record = AdviceCorpusRecord.model_validate_json(line)
            if record.advice_id in seen:
                raise ValueError(
                    f"duplicate advice_id at line {line_number}: {record.advice_id}"
                )
            seen.add(record.advice_id)
            records.append(record)
    if not records:
        raise ValueError(f"canonical corpus is empty: {path}")
    return records


def id_set_checksum(ids: list[str] | set[str]) -> str:
    return sha256_text("\n".join(sorted(ids)))
