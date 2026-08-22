#!/usr/bin/env python3
"""构建 BM25 V2 检索索引。"""

import json, sys, time, os, pickle
import jieba
from rank_bm25 import BM25Okapi

CORPUS_PATH = "/nvme/home/rincug/qhz/backend/data/cleaned_chunks_v2.jsonl"
BM25_DIR = "/nvme/home/rincug/qhz/backend/data/bm25_v2"


def tokenize(text: str) -> list[str]:
    """jieba 分词，过滤纯标点/空白 token。"""
    tokens = jieba.lcut(text)
    result = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        if len(t) == 1 and not t.isalnum() and '\u4e00' <= t <= '\u9fff':
            result.append(t)
        elif len(t) == 1 and not t.isalnum():
            continue
        else:
            result.append(t)
    return result


def main():
    t0 = time.time()

    with open(CORPUS_PATH, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    print(f"读取语料: {len(records)} 条", file=sys.stderr)

    content_recs = [r for r in records if r["advice_type"] == "content"]
    format_recs  = [r for r in records if r["advice_type"] == "format"]

    os.makedirs(BM25_DIR, exist_ok=True)

    def build_index(recs, label, filename):
        print(f"[{label}] 分词 {len(recs)} 条文档 ...", file=sys.stderr)
        tokenized = []
        ids = []
        for r in recs:
            text = r.get("bm25_text", "") or r.get("dense_text", "")
            tokens = tokenize(text)
            tokenized.append(tokens)
            ids.append(r["advice_id"])

        print(f"[{label}] 构建 BM25 索引 ...", file=sys.stderr)
        bm25 = BM25Okapi(tokenized)

        idx_path = os.path.join(BM25_DIR, f"{filename}.pkl")
        with open(idx_path, "wb") as f:
            pickle.dump(bm25, f)

        id_path = os.path.join(BM25_DIR, f"{filename}_id_map.json")
        with open(id_path, "w", encoding="utf-8") as f:
            json.dump(ids, f, ensure_ascii=False)

        tok_path = os.path.join(BM25_DIR, f"{filename}_tokenized.json")
        with open(tok_path, "w", encoding="utf-8") as f:
            json.dump(tokenized, f, ensure_ascii=False)

        print(f"[{label}] 已保存: {idx_path}", file=sys.stderr)
        return len(tokenized), sum(len(t) for t in tokenized)

    c_docs, c_tokens = build_index(content_recs, "content", "content_bm25")
    f_docs, f_tokens = build_index(format_recs, "format", "format_bm25")

    elapsed = time.time() - t0

    manifest = {
        "schema_version": "historical_advice_v2",
        "total_documents": c_docs + f_docs,
        "content_documents": c_docs,
        "format_documents": f_docs,
        "total_tokens": c_tokens + f_tokens,
        "tokenizer": "jieba",
        "bm25_library": "rank_bm25",
        "build_time_seconds": round(elapsed, 1),
    }
    with open(os.path.join(BM25_DIR, "manifest.json"), "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, ensure_ascii=False, indent=2)
        mf.write("\n")

    print(file=sys.stderr)
    print("=== BM25 V2 构建完成 ===", file=sys.stderr)
    for k, v in manifest.items():
        print(f"  {k}: {v}", file=sys.stderr)
    print(f"  路径: {BM25_DIR}", file=sys.stderr)


if __name__ == "__main__":
    main()
