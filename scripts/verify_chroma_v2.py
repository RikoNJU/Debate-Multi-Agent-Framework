#!/usr/bin/env python3
"""验证 Chroma Dense V2 向量库。"""
import sys, chromadb

DB_PATH = "/nvme/home/rincug/qhz/backend/data/chroma_dense_v2"
COLLS = ["historical_advice_content_clean_v2", "historical_advice_format_clean_v2"]

client = chromadb.PersistentClient(path=DB_PATH)

for name in COLLS:
    col = client.get_collection(name)
    sample = col.get(limit=1, include=["embeddings", "metadatas", "documents"])
    emb = sample["embeddings"][0]
    meta = sample["metadatas"][0]
    doc = sample["documents"][0]

    print(f"\n=== {name} ===")
    print(f"  count: {col.count()}")
    print(f"  dim:   {len(emb)}")
    print(f"  id:    {meta['advice_id']}")
    print(f"  type:  {meta['advice_type']}")
    print(f"  cat:   {meta['issue_category']}")
    print(f"  doc:   {doc[:100]}...")

# 测试相似搜索
print(f"\n=== 相似搜索测试 ===")
col = client.get_collection("historical_advice_content_clean_v2")
sample = col.get(limit=1, include=["embeddings"])
query_vec = sample["embeddings"][0]

results = col.query(query_embeddings=[query_vec], n_results=3,
                    include=["metadatas", "distances"])
for i, (cid, dist, meta) in enumerate(zip(results["ids"][0],
                                           results["distances"][0],
                                           results["metadatas"][0])):
    print(f"  #{i+1} id={cid}, dist={dist:.4f}, cat={meta['issue_category']}")

print("\n验证通过。")
sys.exit(0)