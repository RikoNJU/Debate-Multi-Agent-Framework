#!/usr/bin/env python3
"""构建 Clean Dense V2 Chroma 向量库。"""

import json
import os, sys, time, hashlib, requests, shutil
import chromadb
from chromadb.config import Settings

# ── 配置 ──────────────────────────────────────────────────────
CORPUS_PATH = "/nvme/home/rincug/qhz/backend/data/cleaned_chunks_v2.jsonl"
CHROMA_DB_PATH = "/nvme/home/rincug/qhz/backend/data/chroma_dense_v2"
MANIFEST_PATH = "/nvme/home/rincug/qhz/backend/data/chroma_dense_v2/manifest.json"

EMBED_URL = "https://api.siliconflow.cn/v1/embeddings"
EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"
EMBED_API_KEY = os.getenv("DEBATE_V2_API_KEY", "")
EMBED_DIMS = 4096
EMBED_BATCH = 8

SCHEMA_VERSION = "historical_advice_v2"
DISTANCE_METRIC = "cosine"

CONTENT_COLL = "historical_advice_content_clean_v2"
FORMAT_COLL = "historical_advice_format_clean_v2"


# ── 工具 ──────────────────────────────────────────────────────

def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量调用硅基流动 embedding API，返回 4096 维向量列表。"""
    headers = {
        "Authorization": f"Bearer {EMBED_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": EMBED_MODEL, "input": texts}
    r = requests.post(EMBED_URL, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    items = sorted(data["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in items]


def make_advice_id(source_collection: str, chunk_id: str) -> str:
    raw = source_collection + chunk_id
    return "adv_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def make_checksum(rec: dict) -> str:
    raw = rec.get("dense_text", "") + rec.get("advice", "")
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── 主入口 ────────────────────────────────────────────────────

def main():
    t0 = time.time()

    # 读取语料
    records = []
    with open(CORPUS_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    total = len(records)
    print(f"读取语料: {total} 条", file=sys.stderr)

    # 生成 advice_id 和 checksum
    for rec in records:
        rec["advice_id"] = make_advice_id(
            rec["source_collection"], rec["chunk_id"]
        )
        rec["record_checksum"] = make_checksum(rec)
    print("advice_id 已生成", file=sys.stderr)

    # 拆分 content / format
    content_recs = [r for r in records if r["advice_type"] == "content"]
    format_recs  = [r for r in records if r["advice_type"] == "format"]
    print(f"content: {len(content_recs)}, format: {len(format_recs)}",
          file=sys.stderr)

    # 清空并重建 Chroma 目录
    if os.path.exists(CHROMA_DB_PATH):
        shutil.rmtree(CHROMA_DB_PATH)
    os.makedirs(CHROMA_DB_PATH, exist_ok=True)

    client = chromadb.PersistentClient(
        path=CHROMA_DB_PATH,
        settings=Settings(anonymized_telemetry=False),
    )

    content_col = client.create_collection(
        name=CONTENT_COLL,
        metadata={"hnsw:space": DISTANCE_METRIC},
    )
    format_col = client.create_collection(
        name=FORMAT_COLL,
        metadata={"hnsw:space": DISTANCE_METRIC},
    )

    # ── 批量 embed + 写入 ──
    def build_collection(recs, col, label):
        ids = [r["advice_id"] for r in recs]
        nums = list(range(len(recs)))
        total_embeddings = 0
        total_tokens = 0

        for start in range(0, len(recs), EMBED_BATCH):
            batch = recs[start : start + EMBED_BATCH]
            texts = [r["dense_text"] for r in batch]
            try:
                vecs = embed_batch(texts)
            except Exception as e:
                print(f"  [{label}] embed 失败 start={start}: {e}",
                      file=sys.stderr)
                raise

            metadatas = []
            for r in batch:
                metadatas.append({
                    "advice_id": r["advice_id"],
                    "chunk_id": r["chunk_id"],
                    "source_collection": r["source_collection"],
                    "advice_type": r["advice_type"],
                    "chapter_stage": r.get("chapter_stage", ""),
                    "issue_category": r.get("issue_category", ""),
                    "suggestion": r.get("advice", ""),
                    "schema_version": SCHEMA_VERSION,
                    "record_checksum": r["record_checksum"],
                })

            col.add(
                ids=[r["advice_id"] for r in batch],
                embeddings=vecs,
                documents=texts,
                metadatas=metadatas,
            )

            total_embeddings += len(batch)
            total_tokens += sum(len(t) for t in texts)

            progress = min(start + EMBED_BATCH, len(recs))
            elapsed = time.time() - t0
            print(f"  [{label}] {progress}/{len(recs)} 已写入 "
                  f"({elapsed:.1f}s)", file=sys.stderr)

        return total_embeddings, total_tokens

    print("构建 content collection ...", file=sys.stderr)
    c_emb, c_tok = build_collection(content_recs, content_col, "content")

    print("构建 format collection ...", file=sys.stderr)
    f_emb, f_tok = build_collection(format_recs, format_col, "format")

    elapsed = time.time() - t0

    # ── 校验 ──
    assert content_col.count() == len(content_recs), \
        f"content count mismatch: {content_col.count()} vs {len(content_recs)}"
    assert format_col.count() == len(format_recs), \
        f"format count mismatch: {format_col.count()} vs {len(format_recs)}"

    # ── 更新语料 JSONL（回写 advice_id）──
    os.rename(CORPUS_PATH, CORPUS_PATH + ".bak")
    with open(CORPUS_PATH, "w", encoding="utf-8") as f:
        for rec in records:
            json.dump(rec, f, ensure_ascii=False)
            f.write("\n")

    # ── Manifest ──
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "embedding_model": EMBED_MODEL,
        "embedding_dimensions": EMBED_DIMS,
        "distance_metric": DISTANCE_METRIC,
        "total_records": total,
        "content_count": len(content_recs),
        "format_count": len(format_recs),
        "total_embeddings": c_emb + f_emb,
        "build_time_seconds": round(elapsed, 1),
        "builder_version": "build_chroma_dense_v2_v1",
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, ensure_ascii=False, indent=2)
        mf.write("\n")

    # ── 输出 ──
    print(file=sys.stderr)
    print(f"=== 构建完成 ===", file=sys.stderr)
    for k, v in manifest.items():
        print(f"  {k}: {v}", file=sys.stderr)
    print(f"  DB 路径: {CHROMA_DB_PATH}", file=sys.stderr)
    print(f"  content collection: {CONTENT_COLL} ({content_col.count()} 条)",
          file=sys.stderr)
    print(f"  format collection:  {FORMAT_COLL} ({format_col.count()} 条)",
          file=sys.stderr)


if __name__ == "__main__":
    main()