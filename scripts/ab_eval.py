#!/usr/bin/env python3
"""A-E 五方案离线评测对比。"""
import json
import os, sys, time, pickle, re
import jieba, requests, chromadb

LEGACY_DB = "/nvme/home/rincug/qinhaozhe/backend/data/databases/user_result_cloud"
LEGACY_CC = "user_result_content_collection_cloud"
LEGACY_FC = "user_result_format_collection_cloud"
CLEAN_DB  = "/nvme/home/rincug/qhz/backend/data/chroma_dense_v2"
CLEAN_CC  = "historical_advice_content_clean_v2"
CLEAN_FC  = "historical_advice_format_clean_v2"
BM25_DIR  = "/nvme/home/rincug/qhz/backend/data/bm25_v2"

EMBED_URL   = "https://api.siliconflow.cn/v1/embeddings"
EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"
CHAT_URL    = "https://api.siliconflow.cn/v1/chat/completions"
CHAT_MODEL  = "deepseek-ai/DeepSeek-V3.2"
API_KEY     = os.getenv("DEBATE_V2_API_KEY", "")
TOP_K = 5
RRF_K = 60

# -- 测试 Query --
TEST_QUERIES = [
    {"id": "Q1", "route": "content",
     "chapter": "第一章 绪论 PGD 对抗性训练 研究问题",
     "dense": "问题类型：专业术语解释不充分\n章节阶段：第一章 绪论\n问题位置：1.2 研究问题\n\n确认问题：论文提到PGD对抗性训练但未详细解释。\n证据原文：基于PGD的对抗性训练是最有效的选择之一。\n局部上下文：本章介绍了对抗性训练方法，提到了PGD但缺乏解释。",
     "bm25": "PGD 对抗性训练 术语解释 专业概念"},
    {"id": "Q2", "route": "content",
     "chapter": "第四章 实验 MSE RMSE 统计显著性 数据划分",
     "dense": "问题类型：实验统计验证不足\n章节阶段：实验结果分析\n问题位置：主实验结果\n\n确认问题：仅根据MSE和RMSE数值比较认定模型优于基线，未提供统计显著性检验。\n证据原文：所提模型的MSE低于基线模型，因此所提模型拥有最好的性能。\n局部上下文：使用MSE和RMSE比较所提模型和基线模型，没有说明多次实验和置信区间。",
     "bm25": "实验结果 数值比较 MSE RMSE 统计显著性 重复实验 数据划分 置信区间"},
    {"id": "Q3", "route": "format",
     "chapter": "第四章 实验 表格 格式 页宽 排版",
     "dense": "问题类型：表格格式问题\n章节阶段：实验研究\n问题位置：第四章 实验研究\n\n确认问题：表4-1超出页宽。\n证据原文：表4-1包含多列数据。\n局部上下文：展示了在CIFAR10数据集上的改进效果对比表。",
     "bm25": "表格 超出 页宽 格式 排版"},
]

# -- API --
def embed(texts, dim=None):
    payload = {"model": EMBED_MODEL, "input": texts}
    if dim:
        payload["dimensions"] = dim
    r = requests.post(EMBED_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload, timeout=120)
    r.raise_for_status()
    items = sorted(r.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in items]

def llm_chat(prompt):
    r = requests.post(CHAT_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": CHAT_MODEL, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.1, "max_tokens": 300}, timeout=90)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

# -- 检索 --
def dense_search(coll, query, k, dim=None):
    vec = embed([query], dim=dim)[0]
    res = coll.query(query_embeddings=[vec], n_results=k, include=["metadatas", "distances"])
    return [{"advice_id": res["ids"][0][i], "rank": i+1,
             "dist": round(res["distances"][0][i], 6),
             "metadata": res["metadatas"][0][i]}
            for i in range(len(res["ids"][0]))]

def bm25_search(bm25, ids, query, k):
    tokens = [t.strip() for t in jieba.lcut(query) if t.strip()]
    scores = bm25.get_scores(tokens)
    indexed = sorted(enumerate(scores), key=lambda x: -x[1])
    return [{"advice_id": ids[idx], "rank": i+1, "score": round(score, 4)}
            for i, (idx, score) in enumerate(indexed[:k]) if score > 0]

def rrf_fuse(dense, bm25):
    fused = {}
    for h in dense:
        fused[h["advice_id"]] = {"advice_id": h["advice_id"], "dense_rank": h["rank"],
            "bm25_rank": None, "metadata": h.get("metadata"),
            "rrf_score": 0.55/(RRF_K+h["rank"])}
    for h in bm25:
        nid = h["advice_id"]
        if nid in fused:
            f = fused[nid]; f["bm25_rank"] = h["rank"]
            f["rrf_score"] = 0.55/(RRF_K+f["dense_rank"]) + 0.45/(RRF_K+h["rank"])
        else:
            fused[nid] = {"advice_id": nid, "dense_rank": None,
                "bm25_rank": h["rank"], "metadata": None,
                "rrf_score": 0.45/(RRF_K+h["rank"])}
    return sorted(fused.values(), key=lambda x: -x["rrf_score"])

LEGACY_DIM = 2048
CLEAN_DIM  = 4096

# -- 加载 --
lc = chromadb.PersistentClient(path=LEGACY_DB)
lcc, lfc = lc.get_collection(LEGACY_CC), lc.get_collection(LEGACY_FC)
nc = chromadb.PersistentClient(path=CLEAN_DB)
ncc, nfc = nc.get_collection(CLEAN_CC), nc.get_collection(CLEAN_FC)
with open(os.path.join(BM25_DIR, "content_bm25.pkl"), "rb") as f: bc = pickle.load(f)
with open(os.path.join(BM25_DIR, "content_bm25_id_map.json")) as f: ic = json.load(f)
with open(os.path.join(BM25_DIR, "format_bm25.pkl"), "rb") as f: bf = pickle.load(f)
with open(os.path.join(BM25_DIR, "format_bm25_id_map.json")) as f: fi = json.load(f)

# -- 方案 --
def run_A(q):
    c = lcc if q["route"]=="content" else lfc
    return dense_search(c, q["chapter"], TOP_K, dim=LEGACY_DIM)

def run_B(q):
    c = lcc if q["route"]=="content" else lfc
    return dense_search(c, q["dense"], TOP_K, dim=LEGACY_DIM)

def run_C(q):
    c = lcc if q["route"]=="content" else lfc
    bm = bc if q["route"]=="content" else bf
    ids = ic if q["route"]=="content" else fi
    return rrf_fuse(dense_search(c, q["dense"], TOP_K, dim=LEGACY_DIM),
                    bm25_search(bm, ids, q["bm25"], TOP_K))[:TOP_K]

def run_D(q):
    c = ncc if q["route"]=="content" else nfc
    return dense_search(c, q["dense"], TOP_K, dim=CLEAN_DIM)

def run_E(q):
    c = ncc if q["route"]=="content" else nfc
    bm = bc if q["route"]=="content" else bf
    ids = ic if q["route"]=="content" else fi
    rrf = rrf_fuse(dense_search(c, q["dense"], TOP_K, dim=CLEAN_DIM),
                   bm25_search(bm, ids, q["bm25"], TOP_K))[:3]
    for x in rrf:
        m = x.get("metadata") or {}
        prompt = f"评估历史修改建议对当前问题的适用性。\n## 当前问题\n{q['dense']}\n## 历史建议\n问题类型：{m.get('issue_category','')}\n建议：{m.get('suggestion','')}\n请输出JSON：{{\"relevance\": <0-10>, \"applicability\": <0-10>, \"reason\": \"<理由>\"}}"
        try:
            resp = llm_chat(prompt); rr = json.loads(resp)
        except:
            mm = re.search(r'\{[^}]+\}', resp); rr = json.loads(mm.group()) if mm else {"relevance":0,"applicability":0}
        rr["rerank_score"] = round((rr["relevance"]+rr["applicability"])/2, 1)
        x["rerank"] = rr
    return sorted(rrf, key=lambda x: -x["rerank"]["rerank_score"])

# -- 主 --
schemes = {"A": run_A, "B": run_B, "C": run_C, "D": run_D, "E": run_E}
t0 = time.time()

for q in TEST_QUERIES:
    print(f"\n{'='*80}")
    print(f"  {q['id']} [{q['route']}] {q['dense'].split(chr(10))[0]}")
    print(f"{'='*80}")

    results = {}
    for name, fn in schemes.items():
        print(f"  [{name}] ...", file=sys.stderr, end=" ")
        st = time.time(); results[name] = fn(q)
        print(f"{time.time()-st:.1f}s", file=sys.stderr)

    print(f"\n  {'方案':5s} {'#1':22s} {'#2':22s} {'#3':22s}")
    print(f"  {'-'*70}")
    for name in ["A","B","C","D","E"]:
        ids = "  ".join(r["advice_id"][:20] for r in results[name][:3])
        print(f"  {name:5s} {ids}")

    for name in ["A","B","C","D","E"]:
        print(f"\n  [{name}] Top3:")
        for i, r in enumerate(results[name][:3], 1):
            m = r.get("metadata") or {}
            if name == "E":
                rr = r["rerank"]
                print(f"    #{i} {r['advice_id']} r={rr['rerank_score']:.0f} cat={m.get('issue_category','?')[:30]}")
            elif "rrf_score" in r:
                print(f"    #{i} {r['advice_id']} rrf={r['rrf_score']:.5f} D_r={r.get('dense_rank','-')} B_r={r.get('bm25_rank','-')} cat={m.get('issue_category','?')[:30]}")
            else:
                print(f"    #{i} {r['advice_id']} rank={r['rank']} dist={r.get('dist',0):.4f} cat={m.get('issue_category','?')[:30]}")

print(f"\n总耗时: {time.time()-t0:.1f}s")