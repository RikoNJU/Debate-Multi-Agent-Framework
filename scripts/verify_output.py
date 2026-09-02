import json

path = "/nvme/home/rincug/qhz/backend/data/corpus/historical_advice_v2.jsonl"
required = ["chunk_id", "paper_title", "chapter", "issue_category", "chapter_stage",
            "problem_location", "context", "advice", "analysis", "original_excerpt",
            "dense_text", "bm25_text", "rerank_text", "clean_version",
            "source_collection", "advice_type"]

lines = 0
missing = {}
empty_counts = {f: 0 for f in required}
content_n = 0
format_n = 0

with open(path) as f:
    for lno, line in enumerate(f, 1):
        lines += 1
        rec = json.loads(line)
        if rec.get("advice_type") == "content":
            content_n += 1
        else:
            format_n += 1
        for k in required:
            if k not in rec:
                missing.setdefault(k, []).append(lno)
            if not rec.get(k):
                empty_counts[k] += 1

print(f"总行数: {lines}")
print(f"content: {content_n}, format: {format_n}")
print(f"缺失字段: {dict(missing) if missing else '无'}")
print()
print("=== 各字段为空的行数 ===")
for k, c in empty_counts.items():
    if c > 0:
        print(f"  {k}: {c}")

print()
print("=== 第1条记录 ===")
with open(path) as f:
    rec = json.loads(f.readline())
    print(json.dumps(rec, ensure_ascii=False, indent=2)[:2000])