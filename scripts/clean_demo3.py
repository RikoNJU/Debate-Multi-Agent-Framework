#!/usr/bin/env python3
"""清洗演示脚本 v3：读旧 Chroma 一条记录，输出前后对比 + JSONL。"""

import re, html, json, sys
import chromadb

OLD_CHROMA_PATH = "/nvme/home/rincug/qinhaozhe/backend/data/databases/user_result_cloud"
COLL_NAME = "user_result_content_collection_cloud"
RECORD_OFFSET = 5

FIELD_LABELS = ["论文标题:", "所属章节:", "问题位置:", "上下文:", "修改建议:", "分析过程:", "原文片段:"]


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
    """仅转换全角英文/数字为半角，保留中文标点全角状态。"""
    r = []
    for ch in t:
        c = ord(ch)
        if 0xFF21 <= c <= 0xFF3A:       # A-Z 全角 -> 半角
            r.append(chr(c - 0xFEE0))
        elif 0xFF41 <= c <= 0xFF5A:     # a-z 全角 -> 半角
            r.append(chr(c - 0xFEE0))
        elif 0xFF10 <= c <= 0xFF19:     # 0-9 全角 -> 半角
            r.append(chr(c - 0xFEE0))
        elif c == 0x3000:               # 全角空格 -> 半角空格
            r.append(" ")
        else:
            r.append(ch)
    return "".join(r)


def clean_latex_formula(t):
    """用 [公式] 占位符替换 LaTeX 公式块。"""
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


# ── 构建检索文本 ──

def build_dense_text(rec: dict) -> str:
    """历史问题表示，不含修改建议，用于 Dense Embedding。"""
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


def build_bm25_text(rec: dict) -> str:
    """BM25 关键词文本，字段拼接不带标签前缀。"""
    parts = []
    for k in ["issue_category", "chapter_stage", "problem_location",
              "analysis", "context", "original_excerpt"]:
        v = rec.get(k, "")
        if v:
            parts.append(v)
    return " ".join(parts)


def build_rerank_text(rec: dict) -> str:
    """Reranker 输入，在 Dense 基础上增加修改建议。"""
    text = build_dense_text(rec)
    if rec.get("advice"):
        text += f"\n历史修改建议：{rec['advice']}"
    return text


# ── 主入口 ──

def main():
    client = chromadb.PersistentClient(path=OLD_CHROMA_PATH)
    col = client.get_collection(COLL_NAME)
    results = col.get(limit=1, offset=RECORD_OFFSET,
                      include=["documents", "metadatas"])

    chunk_id = results["ids"][0]           # 例: content_201300074_2
    raw_doc  = results["documents"][0]
    meta     = results["metadatas"][0]

    old_fields = parse_seven_fields(raw_doc)

    paper_title  = clean_field("论文标题", old_fields.get("论文标题", ""))
    chapter      = clean_field("所属章节", old_fields.get("所属章节", ""))
    position     = clean_field("问题位置", old_fields.get("问题位置", ""))
    context      = clean_field("上下文",   old_fields.get("上下文", ""))
    advice       = clean_field("修改建议", old_fields.get("修改建议", ""))
    analysis     = clean_field("分析过程", old_fields.get("分析过程", ""))
    excerpt      = clean_field("原文片段", old_fields.get("原文片段", ""))

    issue_category = meta.get("type", "")
    chapter_stage  = chapter   # 旧数据无 chapter_stage，使用 chapter 替代

    record = {
        "chunk_id":         chunk_id,
        "paper_title":      paper_title,
        "chapter":          chapter,
        "issue_category":   issue_category,
        "chapter_stage":    chapter_stage,
        "problem_location": position,
        "context":          context,
        "advice":           advice,
        "analysis":         analysis,
        "original_excerpt": excerpt,
    }
    record["dense_text"]  = build_dense_text(record)
    record["bm25_text"]   = build_bm25_text(record)
    record["rerank_text"] = build_rerank_text(record)
    record["clean_version"] = "v2"

    # ── 清洗前完整内容 -> stderr ──
    print("=" * 70, file=sys.stderr)
    print(">>> 清洗前 - 原始 chunk 完整内容", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(raw_doc, file=sys.stderr)

    print(file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(">>> 清洗后 - 各字段完整内容", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    for key in ["paper_title", "chapter", "issue_category", "chapter_stage",
                "problem_location", "context", "advice", "analysis",
                "original_excerpt", "dense_text", "bm25_text", "rerank_text"]:
        print(f"\n--- {key} ---", file=sys.stderr)
        print(record.get(key, ""), file=sys.stderr)

    print(file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(">>> JSONL 输出 (stdout)", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    # ── JSONL 输出到 stdout ──
    json.dump(record, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
