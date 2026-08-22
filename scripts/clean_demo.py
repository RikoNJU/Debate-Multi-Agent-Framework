#!/usr/bin/env python3
"""清洗演示脚本：从旧 Chroma 向量库中选取一条记录，展示清洗前后变化。"""

import re
import html
import hashlib
import chromadb

# ── 配置 ──────────────────────────────────────────────────────
OLD_CHROMA_PATH = "/nvme/home/rincug/qinhaozhe/backend/data/databases/user_result_cloud"
COLLECTION_NAME = "user_result_content_collection_cloud"

# ── 字段标签列表 ──────────────────────────────────────────────
FIELD_LABELS = [
    "论文标题:", "所属章节:", "问题位置:", "上下文:",
    "修改建议:", "分析过程:", "原文片段:"
]


def parse_seven_fields(document: str) -> dict[str, str]:
    """将七段式文档解析为字段字典。"""
    fields: dict[str, str] = {}
    # 用字段标签做切分
    pattern = "(" + "|".join(re.escape(l) for l in FIELD_LABELS) + ")"
    parts = re.split(pattern, document)
    # parts 为 [空白, 标签1, 内容1, 标签2, 内容2, ...]
    current_label = None
    for part in parts:
        if part in FIELD_LABELS:
            current_label = part.rstrip(":")
        elif current_label and part.strip():
            # 找到对应记录项（同名可能有多个，取第一个未赋值的）
            key = current_label
            if key not in fields:
                fields[key] = part.strip()
    return fields


# ── 文本规范化 ────────────────────────────────────────────────

def clean_html_entities(text: str) -> str:
    """解码 &#x20; 等 HTML Entity。"""
    return html.unescape(text)


def clean_whitespace(text: str) -> str:
    """合并多余空白和 PDF 换行断句。"""
    # 多个空白合并为一个空格
    text = re.sub(r"[ \t\v\f]+", " ", text)
    # 保留真正的段落分隔（连续空行），合并单行
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 把单换行当空格（PDF 断句常见）
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    return text.strip()


def clean_markdown_escapes(text: str) -> str:
    """清理 Markdown 转义字符。"""
    text = re.sub(r"\\([*_#\[\](){}\\])", r"\1", text)
    return text


def clean_parser_markers(text: str) -> str:
    """删除无意义的 '块 N/M' 等解析标记。"""
    text = re.sub(r"##\s*块\s*\d+\s*/\s*\d+\s*\[.*?\]", "", text)
    text = re.sub(r"块\s*\d+\s*/\s*\d+", "", text)
    return text


def clean_field_label_duplicates(text: str) -> str:
    """删除重复出现的字段标签和模板前缀。"""
    for label in FIELD_LABELS:
        # 删除非首位出现的标签
        text = text.replace(label, "", 1)
    return text


# ── 公式处理 ──────────────────────────────────────────────────

def clean_latex(text: str) -> str:
    """删除完整 LaTeX 表达式，保留指标名。"""
    # 删除行间公式 $$...$$
    text = re.sub(r"\$\$(.+?)\$\$", " ", text, flags=re.DOTALL)
    # 删除行内公式 $...$
    text = re.sub(r"\$(.+?)\$", " ", text)
    # 删除 LaTeX 命令 \command{...} 或 \command
    text = re.sub(r"\\[a-zA-Z]+(?:\{[^}]*\})*", " ", text)
    return text


# ── 表格处理 ──────────────────────────────────────────────────

def clean_html_tables(text: str) -> str:
    """删除 HTML 标签。"""
    text = re.sub(r"<[^>]+>", " ", text)
    return text


# ── 分析过程处理 ──────────────────────────────────────────────

def clean_low_value_templates(text: str) -> str:
    """删除 '从原文块 N 可以看出' 等低价值模板。"""
    text = re.sub(r"从\s*原[文始]?\s*块?\s*\d*\s*可以\s*看[出到]", "", text)
    text = re.sub(r"根据评审建议第\d+条", "", text)
    text = re.sub(r"检查原文块\d+发现", "", text)
    return text


def clean_personal_info(text: str) -> str:
    """删除姓名、学号、文件路径。"""
    text = re.sub(r"\b2[01]\d{6}\b", "[学号已脱敏]", text)
    text = re.sub(r"\b1\d{6,8}\b", "[学号已脱敏]", text)
    text = re.sub(r"/[\w/.-]*\.(?:pdf|tex|md|txt|docx?)\b", "[路径已脱敏]", text)
    return text


# ── 原文片段处理 ──────────────────────────────────────────────

def shorten_evidence(text: str, max_chars: int = 300) -> str:
    """截取不超过约 300 字的原文证据。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3] + "..."


def clean_evidence_excerpt(text: str) -> str:
    """清洗原文片段，移除解析标记和格式杂质。"""
    text = clean_html_tables(text)
    text = clean_latex(text)
    text = clean_parser_markers(text)
    text = clean_markdown_escapes(text)
    text = clean_whitespace(text)
    return text


# ── 全半角/大小写统一 ────────────────────────────────────────

def normalize_halfwidth(text: str) -> str:
    """统一全角为半角，英文统一小写（专有名词保留）。"""
    result = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            result.append(" ")
        else:
            result.append(ch)
    return "".join(result)


# ── 主清洗流水线 ──────────────────────────────────────────────

def clean_field(field_name: str, text: str) -> str:
    """对单个字段执行清洗。"""
    if not text:
        return text

    # 所有字段都做基础文本规范化
    text = clean_html_entities(text)
    text = clean_markdown_escapes(text)
    text = normalize_halfwidth(text)
    text = clean_personal_info(text)

    if field_name == "原文片段":
        text = clean_evidence_excerpt(text)
        text = shorten_evidence(text)
    else:
        text = clean_parser_markers(text)
        text = clean_field_label_duplicates(text)
        text = clean_latex(text)
        text = clean_html_tables(text)
        text = clean_whitespace(text)

        if field_name == "分析过程":
            text = clean_low_value_templates(text)

    return text.strip()


def clean_document(document: str) -> dict[str, str]:
    """解析并清洗整个文档，返回清洗后字段。"""
    fields = parse_seven_fields(document)
    cleaned: dict[str, str] = {}
    for name, val in fields.items():
        cleaned[name] = clean_field(name, val)
    return cleaned


def generate_advice_id(source_collection: str, legacy_id: str) -> str:
    """生成匿名 advice_id。"""
    raw = source_collection + legacy_id
    return "adv_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── 主入口 ────────────────────────────────────────────────────

def main():
    client = chromadb.PersistentClient(path=OLD_CHROMA_PATH)
    col = client.get_collection(COLLECTION_NAME)
    results = col.get(limit=1, include=["documents", "metadatas"])

    raw_doc = results["documents"][0]
    metadata = results["metadatas"][0]

    print("=" * 70)
    print("清洗前后对比")
    print("=" * 70)

    # 解析旧字段
    old_fields = parse_seven_fields(raw_doc)
    # 清洗
    new_fields = clean_document(raw_doc)

    for name in FIELD_LABELS:
        key = name.rstrip(":")
        old_val = old_fields.get(key, "(缺失)")
        new_val = new_fields.get(key, "(缺失)")

        print(f"\n{'─' * 60}")
        print(f"【{key}】")
        print(f"{'─' * 60}")
        print(f"  清洗前: {old_val[:200]}{'...' if len(old_val) > 200 else ''}")
        print(f"  清洗后: {new_val[:200]}{'...' if len(new_val) > 200 else ''}")

    # 生成匿名 ID
    legacy_id = metadata.get("student_id", "") + "_" + metadata.get("title", "")
    advice_id = generate_advice_id(COLLECTION_NAME, legacy_id)
    print(f"\n{'─' * 60}")
    print("【匿名 ID】")
    print(f"{'─' * 60}")
    print(f"  旧 student_id: {metadata.get('student_id', 'N/A')}")
    print(f"  新 advice_id:  {advice_id}")


if __name__ == "__main__":
    main()