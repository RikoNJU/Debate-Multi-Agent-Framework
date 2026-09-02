#!/usr/bin/env python3
"""RRF 融合 + LLM Reranker 精排全链路演示。"""

import json
import os, sys, time, pickle, re
import jieba, requests, chromadb

# ── 配置 ──
CHROMA_DB_PATH = "/nvme/home/rincug/qhz/backend/data/chroma_dense_v2"
BM25_DIR       = "/nvme/home/rincug/qhz/backend/data/bm25_v2"

CONTENT_COLL = "historical_advice_content_clean_v2"
FORMAT_COLL  = "historical_advice_format_clean_v2"

EMBED_URL     = "https://api.siliconflow.cn/v1/embeddings"
EMBED_MODEL   = "Qwen/Qwen3-Embedding-8B"
CHAT_URL      = "https://api.siliconflow.cn/v1/chat/completions"
CHAT_MODEL    = "deepseek-ai/DeepSeek-V3.2"
API_KEY       = os.getenv("DEBATE_V2_API_KEY", "")

DENSE_TOP_K = 5
BM25_TOP_K  = 5
RRF_TOP_K   = 3
RRF_K       = 60
DENSE_W     = 0.55
BM25_W      = 0.45

TEST_DENSE_Q = """问题类型：实验统计验证不足
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

TEST_BM25_Q = "实验结果 数值比较 MSE RMSE 统计显著性 重复实验 数据划分 置信区间 基线模型"

ROUTE = "content"

# ── API 封装 ──

def embed(texts: list[str]) -> list[list[float]]:
    r = requests.post(EMBED_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": EMBED_MODEL, "input": texts}, timeout=60)
    r.raise_for_status()
    data = r.json()
    items = sorted(data["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in items]

def llm_chat(prompt: str) -> str:
    r = requests.post(CHAT_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": CHAT_MODEL, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.1, "max_tokens": 300}, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

# ── 加载索引 ──

def load_indexes():
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    coll = client.get_collection(CONTENT_COLL if ROUTE == "content" else FORMAT_COLL)

    prefix = "content" if ROUTE == "content" else "format"
    with open(os.path.join(BM25_DIR, f"{prefix}_bm25.pkl"), "rb") as f:
        bm25 = pickle.load(f)
    with open(os.path.join(BM25_DIR, f"{prefix}_bm25_id_map.json"), encoding="utf-8") as f:
        ids = json.load(f)
    return coll, bm25, ids

# ── 检索 ──

def dense_query(coll, query: str, k: int) -> list[dict]:
    vec = embed([query])[0]
    results = coll.query(query_embeddings=[vec], n_results=k,
                         include=["metadatas", "distances"])
    hits = []
    for rank, (cid, dist, meta) in enumerate(zip(
        results["ids"][0], results["distances"][0], results["metadatas"][0]), 1):
        hits.append({"advice_id": cid, "dense_rank": rank,
                     "dense_dist": round(dist, 6), "metadata": meta})
    return hits

def bm25_query(bm25, ids: list[str], query: str, k: int) -> list[dict]:
    tokens = [t.strip() for t in jieba.lcut(query) if t.strip()]
    scores = bm25.get_scores(tokens)
    indexed = sorted(enumerate(scores), key=lambda x: -x[1])
    hits = []
    for rank, (idx, score) in enumerate(indexed[:k], 1):
        if score <= 0: continue
        hits.append({"advice_id": ids[idx], "bm25_rank": rank,
                     "bm25_score": round(score, 4)})
    return hits

def rrf_fuse(dense_hits, bm25_hits) -> list[dict]:
    fused = {}
    for h in dense_hits:
        fused[h["advice_id"]] = {"advice_id": h["advice_id"],
            "dense_rank": h["dense_rank"], "dense_dist": h["dense_dist"],
            "bm25_rank": None, "bm25_score": None, "metadata": h["metadata"],
            "rrf_score": DENSE_W / (RRF_K + h["dense_rank"])}
    for h in bm25_hits:
        if h["advice_id"] in fused:
            f = fused[h["advice_id"]]
            f["bm25_rank"] = h["bm25_rank"]
            f["bm25_score"] = h["bm25_score"]
            f["rrf_score"] = DENSE_W/(RRF_K+f["dense_rank"]) + BM25_W/(RRF_K+h["bm25_rank"])
        else:
            fused[h["advice_id"]] = {"advice_id": h["advice_id"],
                "dense_rank": None, "dense_dist": None,
                "bm25_rank": h["bm25_rank"], "bm25_score": h["bm25_score"],
                "metadata": None, "rrf_score": BM25_W/(RRF_K+h["bm25_rank"])}
    result = sorted(fused.values(), key=lambda x: -x["rrf_score"])
    for i, r in enumerate(result): r["rrf_rank"] = i + 1
    return result

# ── Reranker ──

def rerank_one(query_context: str, candidate: dict) -> dict:
    """LLM 判断历史建议对当前问题的适用性。"""
    meta = candidate.get("metadata") or {}
    prompt = f"""评估历史修改建议对当前问题的适用性。

## 当前问题
{query_context}

## 历史建议
- 问题类型：{meta.get('issue_category', '')}
- 问题诊断：{meta.get('record_checksum', '')}  # 实际应读 analysis
- 修改建议：{meta.get('suggestion', '')}

请严格输出 JSON：
{{"relevance": <0-10整数, 历史问题与当前问题语义相似度>,
 "applicability": <0-10整数, 历史建议是否可在当前论文中执行>,
 "reason": "<一句话理由>"}}

只输出 JSON，不要解释。"""
    response = llm_chat(prompt)
    try:
        result = json.loads(response)
    except json.JSONDecodeError:
        m = re.search(r'\{[^}]+\}', response)
        result = json.loads(m.group()) if m else {"relevance": 0, "applicability": 0, "reason": "parse error"}
    result["rerank_score"] = round((result["relevance"] + result["applicability"]) / 2, 1)
    result["raw_response"] = response
    return result

def rerank_all(query_context: str, rrf_top: list[dict]) -> list[dict]:
    for c in rrf_top:
        rr = rerank_one(query_context, c)
        c["rerank"] = rr
    return sorted(rrf_top, key=lambda c: -c["rerank"]["rerank_score"])

# ── 主入口 ──

def main():
    t0 = time.time()
    coll, bm25, bm25_ids = load_indexes()

    print(f"\n{'='*70}")
    print(f"  路由: {ROUTE}  |  RRF Top{3} -> Reranker")
    print(f"{'='*70}")

    # Step 1: 检索
    print("\n── 检索阶段 ──", file=sys.stderr)
    dense_hits = dense_query(coll, TEST_DENSE_Q, DENSE_TOP_K)
    bm25_hits  = bm25_query(bm25, bm25_ids, TEST_BM25_Q, BM25_TOP_K)
    rrf_top    = rrf_fuse(dense_hits, bm25_hits)[:RRF_TOP_K]
    print(f"  Dense={len(dense_hits)}  BM25={len(bm25_hits)}  RRF={len(rrf_top)}", file=sys.stderr)

    # Step 2: Reranker
    print("\n── Reranker 精排阶段 ──", file=sys.stderr)
    query_ctx = f"{TEST_DENSE_Q}\n\n关键词：{TEST_BM25_Q}"
    reranked = rerank_all(query_ctx, rrf_top)
    print(f"  LLM model: {CHAT_MODEL}", file=sys.stderr)
    print(f"  耗时: {time.time()-t0:.1f}s", file=sys.stderr)

    # ── 输出 ──
    print(f"\n{'─'*70}")
    print(f"  当前问题")
    print(f"{'─'*70}")
    for line in TEST_DENSE_Q.strip().split("\n"):
        print(f"  {line}")

    print(f"\n{'─'*70}")
    print(f"  RRF 候选 (Top {RRF_TOP_K})")
    print(f"{'─'*70}")
    print(f"  {'#':3s} {'advice_id':20s} {'D_r':>4s} {'B_r':>4s} {'RRF':>8s} {'category':20s}")
    print(f"  {'-'*65}")
    for c in rrf_top:
        meta = c.get("metadata") or {}
        dr = str(c["dense_rank"]) if c["dense_rank"] else "-"
        br = str(c["bm25_rank"]) if c["bm25_rank"] else "-"
        print(f"  {c['rrf_rank']:3d} {c['advice_id']:20s} {dr:>4s} {br:>4s} "
              f"{c['rrf_score']:8.5f} {meta.get('issue_category','?'):20s}")

    print(f"\n{'─'*70}")
    print(f"  Reranker 精排结果 (relevance + applicability → 综合分, 阈值≥6)")
    print(f"{'─'*70}")
    print(f"  {'#':3s} {'advice_id':20s} {'rel':>4s} {'app':>4s} {'score':>6s} {'reason'}")
    print(f"  {'-'*80}")
    passed = 0
    for i, c in enumerate(reranked, 1):
        rr = c["rerank"]
        meta = c.get("metadata") or {}
        status = "✓" if rr["rerank_score"] >= 6 else "✗"
        print(f"  {i:3d} {c['advice_id']:20s} "
              f"{rr['relevance']:>4d} {rr['applicability']:>4d} "
              f"{rr['rerank_score']:>6.1f} [{status}] {rr['reason']}")
        if rr["rerank_score"] >= 6:
            passed += 1

    print(f"\n  通过阈值: {passed}/{len(reranked)} 条")

    print(f"\n{'─'*70}")
    print(f"  最终采纳建议 (通过阈值)")
    print(f"{'─'*70}")
    adopted = [c for c in reranked if c["rerank"]["rerank_score"] >= 6]
    if adopted:
        for i, c in enumerate(adopted, 1):
            meta = c.get("metadata") or {}
            print(f"\n  [{i}] {c['advice_id']}")
            print(f"  类别: {meta.get('issue_category','?')}")
            print(f"  建议: {meta.get('suggestion','')}")
    else:
        print("  0 条建议通过阈值。")

    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.1f}s (检索 + RRF + Reranker)")


if __name__ == "__main__":
    main()
