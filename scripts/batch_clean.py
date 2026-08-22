#!/usr/bin/env python3
"""
批量清洗脚本：从旧 Chroma 两个 collection 导出并清洗，产出 JSONL 规范化语料 + Manifest。
失败记录跳过不中断，错误明细记入 errors.jsonl。
"""

import re, html, json, sys, hashlib, os, time
from datetime import datetime, timezone
import chromadb

# ── 配置 ──────────────────────────────────────────────────────
OLD_CHROMA_PATH = "/nvme/home/rincug/qinhaozhe/backend/data/databases/user_result_cloud"
OUTPUT_DIR      = "/nvme/home/rincug/qhz/backend/data/corpus"
COLLECTIONS     = [
    "user_result_content_collection_cloud",
    "user_result_format_collection_cloud",
]
SCHEMA_VERSION = "historical_advice_v2"
EMBEDDING_MODEL = "text-embedding-v4"
EMBEDDING_DIMS  = 2048
DISTANCE_METRIC = "cosine"
BATCH_SIZE      = 200

FIELD_LABELS = ["论文标题:", "所属章节:", "问题位置:", "上下文:", "修改建议:", "分析过程:", "原文片段:"]

# ── 解析 ──────────────────────────────────────────────────────

def parse_seven_fields(doc):
    fields = {}
    pattern = "(" + "|".join(re.escape(l) for l in FIELD_LABELS) + ")"
    parts = re.split(pattern, doc)
    cur = None
    for p in parts:
        if p in FIELD_LABELS:
            cur = p.rstrip(":")
        elif cur and p.strip():
            if cur not in fields:
                fields[cur] = p.strip()
    return fields

# ── 清洗函数 ──────────────────────────────────────────────────

def clean_html_entities(t):
    return html.unescape(t)

def clean_ws(t):
    t = re.sub(r"[ \t\v\f]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"(?<!\n)\n(?!\n)", " ", t)
    return t.strip()

def clean_md_escapes(t):
    return re.sub(r"\\([*_#\[\]])", r"\1", t)

def clean_parser_markers(t):
    t = re.sub(r"##\s*块\s*\d+\s*/\s*\d+\s*\[.*?\]", "", t)
    t = re.sub(r"块\s*\d+\s*/\s*\d+", "", t)
    return t

def clean_field_label_dup(t):
    for l in FIELD_LABELS:
        t = t.replace(l, "", 1)
    return t

def clean_html_tags(t):
    return re.sub(r"<[^>]+>", " ", t)

def clean_low_value(t):
    t = re.sub(r"从\s*原[文始]?\s*块?\s*\d*\s*可以\s*看[出到]", "", t)
    t = re.sub(r"根据评审建议第\d+条", "", t)
    t = re.sub(r"检查原文块\d+发现", "", t)
    return t

def clean_personal(t):
    t = re.sub(r"\b2[01]\d{6}\b", "[学号已脱敏]", t)
    t = re.sub(r"\b1\d{6,8}\b", "[学号已脱敏]", t)
    t = re.sub(r"/[\w/.-]*\.(?:pdf|tex|md|txt|docx?)\b", "[路径已脱敏]", t)
    return t

def normalize_hw(t):
    r = []
    for ch in t:
        c = ord(ch)
        if 0xFF21 <= c <= 0xFF3A:
            r.append(chr(c - 0xFEE0))
        elif 0xFF41 <= c <= 0xFF5A:
            r.append(chr(c - 0xFEE0))
        elif 0xFF10 <= c <= 0xFF19:
            r.append(chr(c - 0xFEE0))
        elif c == 0x3000:
            r.append(" ")
        else:
            r.append(ch)
    return "".join(r)

def clean_latex_formula(t):
    t = re.sub(r"\$\$.*?\$\$", "[公式]", t, flags=re.DOTALL)
    t = re.sub(r"\$[^$]+\$", "[公式]", t)
    t = re.sub(r"\$", "", t)
    t = re.sub(r"\\[a-zA-Z]+(?:\{[^}]*\})*", "", t)
    t = re.sub(r"\[公式\](?:\s*\[公式\])+", "[公式]", t)
    t = re.sub(r"\s+\[公式\]", "[公式]", t)
    t = re.sub(r"\[公式\]\s+", "[公式]", t)
    t = re.sub(r"[，,]\s*\[公式\]\s*[。.]", "[公式]。", t)
    t = re.sub(r"[，,]\s*\[公式\]\s*[，,]", "[公式]，", t)
    return t

def clean_image_markdown(t):
    t = re.sub(r"!\[.*?\]\(.*?\)", "[图片]", t)
    t = re.sub(r"\[图片\](?:\s*\[图片\])+", "[图片]", t)
    return t

def clean_field(fname, text):
    if not text:
        return text
    text = clean_html_entities(text)
    text = clean_md_escapes(text)
    text = normalize_hw(text)
    text = clean_personal(text)
    if fname == "原文片段":
        text = clean_html_tags(text)
        text = clean_image_markdown(text)
        text = clean_latex_formula(text)
        text = clean_parser_markers(text)
        text = clean_ws(text)
    else:
        text = clean_parser_markers(text)
        text = clean_field_label_dup(text)
        text = clean_image_markdown(text)
        text = clean_latex_formula(text)
        text = clean_html_tags(text)
        text = clean_ws(text)
        if fname == "分析过程":
            text = clean_low_value(text)
    return text.strip()


# ── 构建检索文本 ──────────────────────────────────────────────

def build_dense_text(rec):
    lines = []
    if rec.get("issue_category"):
        lines.append(f"问题类型：{rec['issue_category']}")
    if rec.get("chapter_stage"):
        lines.append(f"章节阶段：{rec['chapter_stage']}")
    if rec.get("problem_location"):
        lines.append(f"问题位置：{rec['problem_location']}")
    if rec.get("analysis"):
        lines.append(f"历史问题诊断：{rec['analysis']}")
    if rec.get("context"):
        lines.append(f"历史上下文：{rec['context']}")
    if rec.get("original_excerpt"):
        lines.append(f"历史证据摘要：{rec['original_excerpt']}")
    return "\n".join(lines)

def build_bm25_text(rec):
    parts = []
    for k in ["issue_category", "chapter_stage", "problem_location",
              "analysis", "context", "original_excerpt"]:
        v = rec.get(k, "")
        if v:
            parts.append(v)
    return " ".join(parts)

def build_rerank_text(rec):
    text = build_dense_text(rec)
    if rec.get("advice"):
        text += f"\n历史修改建议：{rec['advice']}"
    return text


# ── 清洗一条记录 ──────────────────────────────────────────────

def clean_one(chunk_id, raw_doc, meta, coll_name):
    old_fields = parse_seven_fields(raw_doc)

    paper_title  = clean_field("论文标题", old_fields.get("论文标题", ""))
    chapter      = clean_field("所属章节", old_fields.get("所属章节", ""))
    position     = clean_field("问题位置", old_fields.get("问题位置", ""))
    context      = clean_field("上下文",   old_fields.get("上下文", ""))
    advice       = clean_field("修改建议", old_fields.get("修改建议", ""))
    analysis     = clean_field("分析过程", old_fields.get("分析过程", ""))
    excerpt      = clean_field("原文片段", old_fields.get("原文片段", ""))

    record = {
        "chunk_id":         chunk_id,
        "source_collection": coll_name,
        "advice_type":      meta.get("advice_type", ""),
        "paper_title":      paper_title,
        "chapter":          chapter,
        "issue_category":   meta.get("type", ""),
        "chapter_stage":    chapter,
        "problem_location": position,
        "context":          context,
        "advice":           advice,
        "analysis":         analysis,
        "original_excerpt": excerpt,
    }
    record["dense_text"]    = build_dense_text(record)
    record["bm25_text"]     = build_bm25_text(record)
    record["rerank_text"]   = build_rerank_text(record)
    record["clean_version"] = SCHEMA_VERSION
    record["source_database"] = OLD_CHROMA_PATH
    return record


# ── 主入口 ────────────────────────────────────────────────────

def main():
    t0 = time.time()
    client = chromadb.PersistentClient(path=OLD_CHROMA_PATH)

    output_path = os.path.join(OUTPUT_DIR, f"historical_advice_v2.jsonl")
    error_path  = os.path.join(OUTPUT_DIR, f"historical_advice_v2_errors.jsonl")

    total_cleaned = 0
    total_errors  = 0
    content_count = 0
    format_count  = 0

    with open(output_path, "w", encoding="utf-8") as out_f, \
         open(error_path,  "w", encoding="utf-8") as err_f:

        for coll_name in COLLECTIONS:
            col = client.get_collection(coll_name)
            total = col.count()
            print(f"[{coll_name}] 共 {total} 条记录", file=sys.stderr)

            for start in range(0, total, BATCH_SIZE):
                end = min(start + BATCH_SIZE, total)
                results = col.get(limit=end-start, offset=start,
                                  include=["documents", "metadatas"])

                for i in range(len(results["ids"])):
                    chunk_id = results["ids"][i]
                    raw_doc  = results["documents"][i]
                    meta     = results["metadatas"][i]

                    try:
                        record = clean_one(chunk_id, raw_doc, meta, coll_name)
                        json.dump(record, out_f, ensure_ascii=False)
                        out_f.write("\n")

                        if record["advice_type"] == "content":
                            content_count += 1
                        else:
                            format_count += 1
                        total_cleaned += 1

                    except Exception as e:
                        err_entry = {
                            "chunk_id": chunk_id,
                            "collection": coll_name,
                            "error": str(e),
                        }
                        json.dump(err_entry, err_f, ensure_ascii=False)
                        err_f.write("\n")
                        total_errors += 1

                print(f"  [{coll_name}] {min(end, total)}/{total} 已处理, "
                      f"成功 {total_cleaned}, 失败 {total_errors}",
                      file=sys.stderr)

    elapsed = time.time() - t0

    # ── 输出统计到 stderr ──
    print(file=sys.stderr)
    print(f"总成功记录:  {total_cleaned}", file=sys.stderr)
    print(f"  - content:  {content_count}", file=sys.stderr)
    print(f"  - format:   {format_count}", file=sys.stderr)
    print(f"总失败记录:  {total_errors}", file=sys.stderr)
    print(f"耗时:        {elapsed:.1f}s", file=sys.stderr)
    print(f"输出文件:    {output_path}", file=sys.stderr)
    if total_errors:
        print(f"错误文件:    {error_path}", file=sys.stderr)

    # ── 写入 Manifest ──
    manifest_path = os.path.join(OUTPUT_DIR, "manifest_v2.json")
    with open(output_path, "rb") as f:
        sha = hashlib.sha256()
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            sha.update(chunk)
        corpus_checksum = sha.hexdigest()

    manifest = {
        "schema_version":         SCHEMA_VERSION,
        "output_corpus_checksum": corpus_checksum,
        "source_database_path":   OLD_CHROMA_PATH,
        "record_count":           total_cleaned,
        "content_count":          content_count,
        "format_count":           format_count,
        "error_count":            total_errors,
        "embedding_model":        EMBEDDING_MODEL,
        "embedding_dimensions":   EMBEDDING_DIMS,
        "distance_metric":        DISTANCE_METRIC,
        "build_time":             datetime.now(timezone.utc).isoformat(),
        "build_elapsed_seconds":  round(elapsed, 1),
        "builder_version":        "batch_clean_v1",
    }

    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, ensure_ascii=False, indent=2)
        mf.write("\n")

    print(f"Manifest:    {manifest_path}", file=sys.stderr)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
