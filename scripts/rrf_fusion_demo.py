#!/usr/bin/env python3
"""RRF 融合召回：Dense V2 (Chroma ANN Top5) + BM25 V2 (Top5) -> RRF Top3"""

import json
import os, sys, time, pickle
import jieba
import requests
import chromadb
from rank_bm25 import BM25Okapi

# ── 配置 ──────────────────────────────────────────────────────
CHROMA_DB_PATH = "/nvme/home/rincug/qhz/backend/data/chroma_dense_v2"
BM25_DIR       = "/nvme/home/rincug/qhz/backend/data/bm25_v2"

CONTENT_COLL = "historical_advice_content_clean_v2"
FORMAT_COLL  = "historical_advice_format_clean_v2"

EMBED_URL    = "https://api.siliconflow.cn/v1/embeddings"
EMBED_MODEL  = "Qwen/Qwen3-Embedding-8B"
EMBED_API_KEY = os.getenv("DEBATE_V2_API_KEY", "")

DENSE_TOP_K = 5
BM25_TOP_K  = 5
RRF_TOP_K   = 3
RRF_K       = 60            # RRF 平滑常数
DENSE_WEIGHT = 0.55
BM25_WEIGHT  = 0.45

# ── 测试 Query（按文档设计）───────────────────────────────────

TEST_DENSE_QUERY = """问题类型：实验统计验证不足
章节阶段：实验结果分析
问题位置：主实验结果

确认问题：
论文仅根据MSE和RMSE数值比较认定模型优于基线，
未提供统计显著性检验、实验重复次数和数据划分方式。

证据原文：
所提模型的MSE低于基线模型，因此所提模型拥有最好的性能。

局部上下文：
本节使用MSE和RMSE比较所提模型和两个基线模型，
但没有说明多次实验、方差、置信区间和数据集划分过程。"""

TEST_BM25_QUERY = "实验结果 数值比较 MSE RMSE 统计显著性 重复实验 数据划分 置信区间 基线模型"

ROUTE = "content"            # "content" | "format" | "auto"


# ── 加载索引 ──────────────────────────────────────────────────

def load_indexes():
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    coll_content = client.get_collection(CONTENT_COLL) if ROUTE in ("content", "auto") else None
    coll_format  = client.get_collection(FORMAT_COLL)  if ROUTE in ("format",  "auto") else None

    bm25_content = bm25_format = ids_content = ids_format = None

    def load_bm25(label):
        with open(os.path.join(BM25_DIR, f"{label}_bm25.pkl"), "rb") as f:
            bm25 = pickle.load(f)
        with open(os.path.join(BM25_DIR, f"{label}_bm25_id_map.json"), encoding="utf-8") as f:
            ids = json.load(f)
        return bm25, ids

    if ROUTE in ("content", "auto"):
        bm25_content, ids_content = load_bm25("content")
    if ROUTE in ("format", "auto"):
        bm25_format, ids_format = load_bm25("format")

    return {
        "content": (coll_content, bm25_content, ids_content),
        "format":  (coll_format,  bm25_format,  ids_format),
    }


# ── Dense 检索 ────────────────────────────────────────────────

def dense_query(chroma_coll, query_text: str, top_k: int) -> list[dict]:
    """Embed query -> Chroma ANN search -> return [{advice_id, rank, distance, metadata}]"""
    headers = {
        "Authorization": f"Bearer {EMBED_API_KEY}",
        "Content-Type": "application/json",
    }
    r = requests.post(EMBED_URL, headers=headers,
                      json={"model": EMBED_MODEL, "input": [query_text]},
                      timeout=60)
    r.raise_for_status()
    vec = r.json()["data"][0]["embedding"]

    results = chroma_coll.query(
        query_embeddings=[vec],
        n_results=top_k,
        include=["metadatas", "distances"],
    )

    hits = []
    for rank, (cid, dist, meta) in enumerate(zip(
        results["ids"][0], results["distances"][0], results["metadatas"][0]
    ), 1):
        hits.append({
            "advice_id": cid,
            "dense_rank": rank,
            "dense_dist": round(dist, 6),
            "dense_score": round(1.0 - dist, 6),  # cosine sim = 1 - distance
            "metadata": meta,
        })
    return hits


# ── BM25 检索 ────────────────────────────────────────────────

def bm25_query(bm25, ids: list[str], query_text: str, top_k: int) -> list[dict]:
    """Tokenize query -> BM25 scoring -> return [{advice_id, rank, score}]"""
    tokens = [t.strip() for t in jieba.lcut(query_text) if t.strip()]
    scores = bm25.get_scores(tokens)

    indexed = sorted(enumerate(scores), key=lambda x: -x[1])
    top = indexed[:top_k]

    hits = []
    for rank, (idx, score) in enumerate(top, 1):
        if score <= 0:
            continue
        hits.append({
            "advice_id": ids[idx],
            "bm25_rank": rank,
            "bm25_score": round(score, 4),
        })
    return hits


# ── RRF 融合 ─────────────────────────────────────────────────

def rrf_fuse(dense_hits: list[dict], bm25_hits: list[dict],
             k: int = RRF_K, dw: float = DENSE_WEIGHT, bw: float = BM25_WEIGHT
             ) -> list[dict]:
    """按 advice_id 合并 Dense + BM25 结果，计算 RRF 分数并排序。"""
    fused: dict[str, dict] = {}

    for h in dense_hits:
        fused[h["advice_id"]] = {
            "advice_id": h["advice_id"],
            "dense_rank": h["dense_rank"],
            "dense_dist": h["dense_dist"],
            "bm25_rank": None,
            "bm25_score": None,
            "metadata": h["metadata"],
            "rrf_score": dw / (k + h["dense_rank"]),
        }

    for h in bm25_hits:
        if h["advice_id"] in fused:
            fused[h["advice_id"]]["bm25_rank"] = h["bm25_rank"]
            fused[h["advice_id"]]["bm25_score"] = h["bm25_score"]
            fused[h["advice_id"]]["rrf_score"] = (
                dw / (k + fused[h["advice_id"]]["dense_rank"]) +
                bw / (k + h["bm25_rank"])
            )
        else:
            fused[h["advice_id"]] = {
                "advice_id": h["advice_id"],
                "dense_rank": None,
                "dense_dist": None,
                "bm25_rank": h["bm25_rank"],
                "bm25_score": h["bm25_score"],
                "metadata": None,
                "rrf_score": bw / (k + h["bm25_rank"]),
            }

    result = sorted(fused.values(), key=lambda x: -x["rrf_score"])
    for i, r in enumerate(result):
        r["rrf_rank"] = i + 1
    return result


# ── 主入口 ────────────────────────────────────────────────────

def main():
    t0 = time.time()
    indexes = load_indexes()

    for label in (["content"] if ROUTE in ("content", "auto") else []) + \
                 (["format"]  if ROUTE in ("format",  "auto") else []):
        coll, bm25, ids = indexes[label]
        if coll is None:
            continue

        print(f"\n{'='*60}")
        print(f"  路由: {label}")
        print(f"{'='*60}")

        # Dense
        print("\n[Dense 检索] embedding + ANN top 5 ...", file=sys.stderr, end=" ")
        dt0 = time.time()
        dense_hits = dense_query(coll, TEST_DENSE_QUERY, DENSE_TOP_K)
        print(f"{time.time()-dt0:.1f}s", file=sys.stderr)

        # BM25
        print("[BM25 检索] tokenize + scoring top 5 ...", file=sys.stderr, end=" ")
        bt0 = time.time()
        bm25_hits = bm25_query(bm25, ids, TEST_BM25_QUERY, BM25_TOP_K)
        print(f"{time.time()-bt0:.1f}s", file=sys.stderr)

        # RRF
        print("[RRF 融合] ...", file=sys.stderr)
        fused = rrf_fuse(dense_hits, bm25_hits)
        top = fused[:RRF_TOP_K]

        # ── 输出 ──
        print(f"\n{'─'*60}")
        print(f"  测试 Query（Dense）")
        print(f"{'─'*60}")
        for line in TEST_DENSE_QUERY.strip().split("\n"):
            print(f"  {line}")

        print(f"\n{'─'*60}")
        print(f"  测试 Query（BM25）")
        print(f"{'─'*60}")
        print(f"  {TEST_BM25_QUERY}")

        print(f"\n{'─'*60}")
        print(f"  融合前候选")
        print(f"{'─'*60}")

        print("\n  [Dense Top 5]")
        for h in dense_hits:
            meta = h["metadata"]
            print(f"    #{h['dense_rank']} {h['advice_id']} "
                  f"dist={h['dense_dist']:.4f} "
                  f"cat={meta.get('issue_category','?')}")

        print("\n  [BM25 Top 5]")
        for h in bm25_hits:
            print(f"    #{h['bm25_rank']} {h['advice_id']} "
                  f"score={h['bm25_score']:.2f}")

        print(f"\n{'─'*60}")
        print(f"  RRF 融合 Top {RRF_TOP_K}")
        print(f"{'─'*60}")
        print(f"  RRF 公式: {DENSE_WEIGHT}/({RRF_K}+dense_rank) + "
              f"{BM25_WEIGHT}/({RRF_K}+bm25_rank)")
        print()

        header = f"{'#':3s} {'advice_id':20s} {'D_rank':>7s} {'B_rank':>7s} {'RRF':>9s} {'category':20s} {'suggestion'}"
        print(header)
        print("-" * len(header))
        for h in top:
            meta = h.get("metadata") or {}
            dr = str(h["dense_rank"]) if h["dense_rank"] else "-"
            br = str(h["bm25_rank"]) if h["bm25_rank"] else "-"
            sug = (meta.get("suggestion", "") or "")[:60]
            print(f"{h['rrf_rank']:3d} "
                  f"{h['advice_id']:20s} "
                  f"{dr:>7s} "
                  f"{br:>7s} "
                  f"{h['rrf_score']:9.5f} "
                  f"{meta.get('issue_category','?'):20.20s} "
                  f"{sug}")

        # 完整 JSON 细节
        print(f"\n{'─'*60}")
        print(f"  Top {RRF_TOP_K} 完整 JSON")
        print(f"{'─'*60}")
        for h in top:
            print(json.dumps({
                "advice_id": h["advice_id"],
                "dense_rank": h["dense_rank"],
                "bm25_rank": h["bm25_rank"],
                "rrf_score": round(h["rrf_score"], 5),
                "metadata": h.get("metadata"),
            }, ensure_ascii=False, indent=2))
            print()

    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    main()