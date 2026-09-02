#!/usr/bin/env python3
"""验证 BM25 V2 检索。"""
import json, pickle, sys, os
import jieba
from rank_bm25 import BM25Okapi

BM25_DIR = "/nvme/home/rincug/qhz/backend/data/bm25_v2"

def load_bm25(filename):
    with open(os.path.join(BM25_DIR, f"{filename}.pkl"), "rb") as f:
        bm25 = pickle.load(f)
    with open(os.path.join(BM25_DIR, f"{filename}_id_map.json"), encoding="utf-8") as f:
        id_map = json.load(f)
    return bm25, id_map

def tokenize(text):
    return [t.strip() for t in jieba.lcut(text) if t.strip()]

bm25_content, ids_content = load_bm25("content_bm25")
bm25_format, ids_format = load_bm25("format_bm25")

print(f"content: {len(ids_content)} docs")
print(f"format:  {len(ids_format)} docs")
print()

# 测试查询
queries = [
    "统计显著性 验证实验",
    "表格 超出 页面 宽度 格式",
    "引用 文献 缺失",
]

print("=== 检索测试 ===")
for q in queries:
    tok = tokenize(q)
    print(f"\nQuery: {q}")
    print(f"Tokens: {tok}")

    scores_c = bm25_content.get_scores(tok)
    top_c = sorted(enumerate(scores_c), key=lambda x: -x[1])[:3]
    print("  [content] top 3:")
    for idx, score in top_c:
        if score > 0:
            print(f"    {ids_content[idx]} score={score:.2f}")

    scores_f = bm25_format.get_scores(tok)
    top_f = sorted(enumerate(scores_f), key=lambda x: -x[1])[:3]
    print("  [format] top 3:")
    for idx, score in top_f:
        if score > 0:
            print(f"    {ids_format[idx]} score={score:.2f}")

print("\nBM25 V2 验证通过。")