import re, html, hashlib
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
        # 全角字母 A-Z a-z → 半角
        if 0xFF21 <= c <= 0xFF3A:
            r.append(chr(c - 0xFEE0))
        elif 0xFF41 <= c <= 0xFF5A:
            r.append(chr(c - 0xFEE0))
        # 全角数字 0-9 → 半角
        elif 0xFF10 <= c <= 0xFF19:
            r.append(chr(c - 0xFEE0))
        # 全角空格 → 半角空格
        elif c == 0x3000:
            r.append(" ")
        else:
            r.append(ch)
    return "".join(r)


def clean_latex_formula(t):
    """用 [公式] 占位符替换 LaTeX 公式块，避免删除后语义断裂。
    根据文档：保留指标名如 MSE、RMSE、F1、AUC 在上下文中的出现。"""
    # 行间公式 $$...$$ → [公式]（跨行匹配）
    t = re.sub(r"\$\$.*?\$\$", "[公式]", t, flags=re.DOTALL)
    # 行内公式 $...$ → [公式]
    t = re.sub(r"\$[^$]+\$", "[公式]", t)
    # 清理孤立残留的 $ 符号
    t = re.sub(r"\$", "", t)
    # 清理 LaTeX 命令（不在 $ 内的，如 \in \times 等已被公式替换处理，这里是兜底）
    t = re.sub(r"\\[a-zA-Z]+(?:\{[^}]*\})*", "", t)
    # 合并连续的 [公式] 占位符
    t = re.sub(r"\[公式\](?:\s*\[公式\])+", "[公式]", t)
    # 清理 [公式] 前后的多余空格和标点粘连
    # 「 ，[公式] 」→「 ，[公式]」
    t = re.sub(r"\s+\[公式\]", "[公式]", t)
    t = re.sub(r"\[公式\]\s+", "[公式]", t)
    # 「，[公式]。」→「[公式]。」（前面紧邻标点时合并）
    t = re.sub(r"[，,]\s*\[公式\]\s*[。.]", "[公式]。", t)
    t = re.sub(r"[，,]\s*\[公式\]\s*[，,]", "[公式]，", t)
    return t


def clean_image_markdown(t):
    """移除 Markdown 图片引用 ![...](...)。"""
    t = re.sub(r"!\[.*?\]\(.*?\)", "[图片]", t)
    # 合并连续的图片占位符
    t = re.sub(r"\[图片\](?:\s*\[图片\])+", "[图片]", t)
    return t


def shorten(text, max_chars=300):
    return text if len(text) <= max_chars else text[:max_chars-3] + "..."

NO_TRUNCATE = True  # 演示模式：输出完整内容便于对比


def _maybe_shorten(text, max_chars=300):
    if NO_TRUNCATE:
        return text
    return shorten(text, max_chars)


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
        text = _maybe_shorten(text)
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


def main():
    client = chromadb.PersistentClient(path=OLD_CHROMA_PATH)
    col = client.get_collection(COLL_NAME)
    results = col.get(limit=1, offset=RECORD_OFFSET, include=["documents", "metadatas"])
    raw = results["documents"][0]
    meta = results["metadatas"][0]
    old = parse_seven_fields(raw)

    print("=" * 70)
    print(">>> 清洗前 - 原始 chunk 完整内容")
    print("=" * 70)
    print(raw)

    print()
    print("=" * 70)
    print(">>> 清洗后 - 各字段完整内容")
    print("=" * 70)
    for label in FIELD_LABELS:
        key = label.rstrip(":")
        original = old.get(key, "")
        cleaned = clean_field(key, original)
        print(f"\n--- {key} ---")
        print(cleaned)

    print()
    print("=" * 70)
    print(">>> 匿名 ID")
    print("=" * 70)
    legacy = meta.get("student_id","") + "_" + meta.get("title","")
    print("old student_id:", meta.get("student_id"))
    print("new advice_id:", "adv_" + hashlib.sha256((COLL_NAME + legacy).encode()).hexdigest()[:16])
    print()
    print(">>> metadata:")
    print(meta)


if __name__ == "__main__":
    main()