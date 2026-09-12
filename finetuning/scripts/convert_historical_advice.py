import json
import re
from pathlib import Path


CATEGORY_MAP = {}

def _init_mappings():
    global CATEGORY_MAP, SEVERITY_RULES

    CATEGORY_MAP = {
        "academic_expression": [
            "标点符号使用不规范", "公式格式不规范", "公式无标号", "公式符号缺失",
            "公式标号格式不规范", "表格格式不规范", "表格标题位置不规范",
            "表格标题位置错误", "表格标题位置不符合规范", "表格排版不规范",
            "图表引用格式不规范", "图片引用不规范", "图片标题格式不规范",
            "文献引用格式不规范", "参考文献格式不规范", "参考文献格式不符合规范要求",
            "参考文献引用格式不规范", "引文格式不规范", "引文格式不统一",
            "文献引用格式不统一", "排版格式问题", "非原创图片过多",
            "语言表达不符合中文表述逻辑", "语言表达不符合中文逻辑规范",
            "语言表达口语化，不够正式", "内容表述不准确",
            "伪代码语言不符合规范", "代码实现描述过于详细",
        ],
        "ambiguity": [
            "研究创新点和贡献表述不够明确", "创新点和贡献表述不够明确突出",
            "创新点表述不够突出，贡献阐述不清晰", "理论基础和研究方法描述不够清晰准确",
            "内容表述不准确", "章节内容概括不准确，与正文内容存在偏差",
            "实验内容描述不够具体，缺乏结果展示", "实验内容描述过于简略，缺乏具体案例展示",
            "实验设置描述不够充分", "实验设计描述不够详细，缺乏数据集选择依据",
            "未来发展方向分析不够深入具体", "研究背景表述过于宽泛",
            "模型算法原理说明不充分",
        ],
        "redundancy": [
            "内容重复和表述冗余", "内容重复和格式错误", "内容重复表述",
            "章节内容重复表述", "总结内容重复性高，缺乏深度提炼",
            "章节结构冗余",
        ],
        "logical_jump": [
            "论证过程不够严谨，缺乏详细的数学推导", "实验结果表述不够严谨",
            "理论证明缺乏实验验证和实际应用支撑",
            "消融实验结果分析缺乏统计显著性验证",
        ],
        "section_role": [
            "章节结构逻辑关系不清晰", "章节内容组织不当", "章节内容深度不足",
            "小节内容过于简略", "引言结构组织不当", "章节内容概括不准确，与正文内容存在偏差",
            "缺少本文结构介绍", "研究局限性表述位置不当",
            "相关研究综述不够系统", "相关研究覆盖不全面，缺乏与主流平台的对比分析",
            "文献综述不充分，参考文献数量不足", "文献综述深度不足，缺乏对现有方法缺陷的系统分析",
            "背景知识描述过于基础，缺乏与论文主题的深度关联",
            "大语言模型介绍过于简略，缺乏系统性分析",
            "内容篇幅不足", "研究内容不够充实，实验部分缺乏结果展示",
            "研究内容深度不足，缺乏系统性分析",
            "相关工作内容不完整，缺乏对物体识别算法研究现状的介绍",
            "相关工作引用不充分", "参考文献不足，研究调研不充分",
        ],
        "abstract_completeness": [
            "总结内容不够全面，缺乏对研究不足的分析",
            "结论章节内容不完整，缺乏研究总结和成果概括",
            "总结内容不够深入，缺乏系统性反思", "结论内容过于简略，缺乏深度总结和展望",
            "总结内容不够深入全面，展望部分缺乏具体性",
            "总结内容不够充实，创新点和贡献表述不够明确",
            "研究局限性分析不够深入，未来展望缺乏具体性",
            "讨论深度不足，未来工作展望不够具体",
            "缺失诚信承诺书", "承诺书签名缺失", "诚信承诺书信息不完整",
        ],
        "terminology_consistency": [
            "专业术语解释不充分", "术语使用不一致", "英文术语大小写不一致",
            "英文名词大小写不统一", "引用缺失", "文献引用缺失",
        ],
        "evidence_conclusion_mismatch": [
            "实验结果分析深度不足", "实验分析深度不足",
            "实验结果分析不够深入，缺乏具体数据支撑", "实验结果分析不足",
            "实验结果展示不够充分", "量化分析结果展示不充分，缺乏具体数据支撑",
            "性能分析深度不足", "实验分析不充分，缺乏对性能差异原因的深入探讨",
            "实验验证不足，缺乏多数据集测试", "实验平台局限性分析不足",
            "算法复杂度分析不够深入", "方法适用性分析不足",
            "平台扩展性分析不足",
        ],
    }

    severity_map = {}
    for kw in ["标点", "格式", "排版", "位置", "引文", "引用格式", "参考文献格式", "承诺书", "签名", "诚信", "缺少"]:
        severity_map[kw] = "trivial"
    for kw in ["不一致", "不统一", "不规范", "口语化", "逻辑", "术语", "不够明确", "不清晰", "不准确", "缺失", "过大", "过少", "过宽泛"]:
        severity_map[kw] = "minor"
    for kw in ["不足", "不够深入", "不够严谨", "不够充分", "不够全面", "深度不足", "论证", "篇幅不足", "不够充实", "结构", "组织不当", "不完整"]:
        severity_map[kw] = "major"
    SEVERITY_RULES = severity_map


def map_issue_category(original: str) -> str:
    for std_cat, keywords in CATEGORY_MAP.items():
        for kw in keywords:
            if kw in original:
                return std_cat
    return "academic_expression"


def map_severity(original: str) -> str:
    max_prio = {"trivial": 0, "minor": 1, "major": 2, "critical": 3}
    best = "minor"
    for kw, sev in SEVERITY_RULES.items():
        if kw in original:
            if max_prio[sev] > max_prio[best]:
                best = sev
    return best


def convert_record(record: dict) -> dict:
    std_cat = map_issue_category(record["issue_category"])
    severity = map_severity(record["issue_category"])

    evidence = record.get("evidence_excerpt", "") or record.get("original_text", "")
    if len(evidence) > 500:
        evidence = evidence[:500]

    assistant_output = {
        "status": "valid",
        "findings": [
            {
                "category": std_cat,
                "evidence_quote": evidence.strip()[:200],
                "severity": severity,
                "rationale": f"{record['analysis']}"[:300],
                "suggestion": record["advice"][:300],
            }
        ],
    }

    paper_title = record.get("paper_title", "unknown")
    chapter = record.get("chapter", record.get("problem_location", "unknown"))
    problem_location = record.get("problem_location", chapter)
    context_summary = record.get("context", "")

    original_text = record.get("original_text", "")
    evidence_excerpt = record.get("evidence_excerpt", "")

    if len(original_text) > 2000:
        original_text = original_text[:2000]
    if not original_text and evidence_excerpt:
        original_text = evidence_excerpt
        if len(original_text) > 2000:
            original_text = original_text[:2000]

    paper_text = (
        f"论文标题：{paper_title}\n"
        f"评审章节：{chapter}\n"
        f"问题位置：{problem_location}\n"
    )
    if context_summary:
        paper_text += f"章节背景：{context_summary[:500]}\n"
    paper_text += f"\n论文原文：\n{original_text}"

    return {
        "paper_id": record["advice_id"],
        "section": chapter,
        "text": paper_text,
        "assistant_output": assistant_output,
    }


def main():
    _init_mappings()

    src = Path("backend/data/historical_advice_v2.jsonl")
    dst = Path("finetuning/data/raw/historical_advice_sft.jsonl")
    dst.parent.mkdir(parents=True, exist_ok=True)

    records = []
    with open(src) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    print(f"Total source records: {len(records)}")

    converted = []
    category_stats = {}
    for r in records:
        sample = convert_record(r)
        converted.append(sample)
        cat = sample["assistant_output"]["findings"][0]["category"]
        category_stats[cat] = category_stats.get(cat, 0) + 1

    shuffled = list(enumerate(converted))
    shuffled.sort(key=lambda x: hash(str(x[1]["paper_id"])) % 1000)

    with open(dst, "w") as f:
        for _, sample in shuffled:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")

    print(f"Written {len(converted)} samples to {dst}")
    print("\nCategory distribution:")
    for cat, count in sorted(category_stats.items(), key=lambda x: -x[1]):
        pct = 100 * count / len(converted)
        print(f"  {cat:35s}: {count:3d} ({pct:4.1f}%)")


if __name__ == "__main__":
    main()