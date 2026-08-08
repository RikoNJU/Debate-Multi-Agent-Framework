"""ReviewSynthesis 的确定性兼容装配。

Review Chair 只需要用真实模型产出判断部分 ``GlobalReview``，这里的装配函数
负责把 ``GlobalReview`` 映射为原 Step 4/5 兼容的 ``chapter_evaluation`` 和
``workload_evaluation``，保证章节键、章节名和评价结构始终符合原流程。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..schemas import (
    ChapterAdvice,
    CompatibleChapterData,
    CompatibleChapterEnvelope,
    CompatibleStructureEvaluation,
    CompatibleWorkloadEvaluation,
    FindingSeverity,
    GlobalReview,
    ResolvedFinding,
    ReviewContext,
    ReviewSynthesis,
    SectionStructure,
    WorkloadItem,
)


def assemble_review_synthesis(
    context: ReviewContext,
    global_review: GlobalReview,
) -> ReviewSynthesis:
    """把模型判断结果 GlobalReview 装配为原流程兼容的完整输出。"""

    resolved = global_review.resolved_findings
    reviewable = [chapter for chapter in context.chapters if chapter.reviewable]
    chapter_evaluation: dict[str, CompatibleChapterEnvelope] = {}
    for index, chapter in enumerate(reviewable, start=1):
        related = [
            finding
            for finding in resolved
            if chapter.chapter_id in finding.affected_chapter_ids
        ]
        weaknesses = [finding.claim for finding in related]
        chapter_evaluation[f"chapter_{index}"] = CompatibleChapterEnvelope(
            chapter_data=CompatibleChapterData(
                chapter_name=chapter.chapter_name,
                chapter_type=chapter_type(chapter.stage),
                chapter_summary=chapter.content[:240],
                chapter_remark=(
                    "；".join(weaknesses)
                    if weaknesses
                    else "本章内容与全文研究目标基本一致。"
                ),
                section_structure=[
                    SectionStructure(
                        section_title=title,
                        section_purpose="支撑本章核心论述",
                        key_points=[],
                        weaknesses=weaknesses,
                    )
                    for title in chapter.section_titles
                ],
                extracted_info={
                    "global_context": context.profile.global_summary
                },
                evaluation_items={
                    "evidence_grounded_review": (
                        "；".join(weaknesses)
                        if weaknesses
                        else "未发现高严重度问题[无问题]"
                    )
                },
                scoring_impact=scoring_impact(related),
                advice=[
                    ChapterAdvice(
                        position=chapter.chapter_name,
                        suggestion=suggestion(finding),
                    )
                    for finding in related
                ],
            )
        )

    workload = build_workload_evaluation(context, global_review)
    return ReviewSynthesis(
        global_review=global_review,
        chapter_evaluation=chapter_evaluation,
        workload_evaluation=workload,
    )


def build_workload_evaluation(
    context: ReviewContext, global_review: GlobalReview
) -> CompatibleWorkloadEvaluation:
    """根据实际章节结构和已确认问题生成 Step 5 兼容评价。"""

    titles = [chapter.chapter_name.lower() for chapter in context.chapters]
    stages = {chapter.stage for chapter in context.chapters if chapter.reviewable}
    reviewable = [chapter for chapter in context.chapters if chapter.reviewable]
    confirmed = [
        finding
        for finding in global_review.resolved_findings
        if finding.status.value in {"confirmed", "mostly_confirmed"}
        and any(
            marker in finding.dimension
            for marker in ("结构", "规范", "完整", "工作量", "写作", "表达")
        )
    ]
    penalty = min(
        35,
        sum(
            {
                FindingSeverity.FATAL: 20,
                FindingSeverity.MAJOR: 12,
                FindingSeverity.MODERATE: 6,
                FindingSeverity.MINOR: 2,
                FindingSeverity.INFO: 0,
            }[finding.severity]
            for finding in confirmed
        ),
    )
    has_method = any(
        "方法" in stage or "模型" in stage or "系统设计" in stage
        for stage in stages
    )
    has_validation = any(
        marker in stage for stage in stages for marker in ("实验", "结果", "评估", "实现")
    )
    if context.profile.paper_type.value == "理论研究":
        has_validation = has_validation or any("证明" in stage for stage in stages)
    structural_gaps = int(not has_method) + int(not has_validation)
    completeness_score = max(50, 95 - 10 * structural_gaps - penalty)
    section_ratio = (
        sum(bool(chapter.section_titles) for chapter in reviewable) / len(reviewable)
        if reviewable
        else 0
    )
    abstract_present = bool(context.profile.claimed_contributions) or any(
        "摘要" in title or "abstract" in title for title in titles
    )
    references_present = any(
        "参考文献" in title or "references" in title for title in titles
    )
    acknowledgement_present = any("致谢" in title for title in titles)

    structure = CompatibleStructureEvaluation(
        completeness=WorkloadItem(
            score=completeness_score,
            analysis=(
                f"检测到 {len(reviewable)} 个可评审章节；"
                f"方法章节={'有' if has_method else '缺失'}，"
                f"验证章节={'有' if has_validation else '缺失'}；"
                f"已确认问题造成 {penalty} 分结构性扣分。"
            ),
        ),
        abstract_and_keywords=WorkloadItem(
            score=90 if abstract_present else 65,
            analysis="检测到摘要章节。" if abstract_present else "输入中未识别到独立摘要章节。",
        ),
        catalog_standardization=WorkloadItem(
            score=round(70 + 25 * section_ratio),
            analysis=f"{round(section_ratio * 100)}% 的可评审章节包含小节标题。",
        ),
        chapter_standardization=WorkloadItem(
            score=max(50, round(92 - penalty / 2)),
            analysis="依据章节层级和已确认的结构性问题计算。",
        ),
        acknowledgement_standardization=WorkloadItem(
            score=90 if acknowledgement_present else 70,
            analysis="检测到致谢章节。" if acknowledgement_present else "输入中未识别到致谢章节，需人工确认。",
        ),
    )
    missing = []
    if not references_present:
        missing.append("参考文献")
    if not acknowledgement_present:
        missing.append("致谢")
    summary = f"论文包含 {len(reviewable)} 个可评审章节。"
    if missing:
        summary += f"未识别到：{'、'.join(missing)}。"
    workload_text = (
        f"正文约 {sum(len(chapter.content) for chapter in reviewable)} 字符；"
        f"方法和验证链路{'完整' if has_method and has_validation else '存在缺口'}。"
    )
    return CompatibleWorkloadEvaluation(
        structure_evaluation=structure,
        summary=summary,
        workload_evaluation=workload_text,
    )


def chapter_type(stage: str) -> str:
    """根据原 Step 2 的章节阶段映射为原 Step 4 章节类型。"""

    mapping = {
        "引言/绪论": "introduction",
        "引言/绪论（包含相关工作）": "introduction_related_work",
        "相关工作": "related_work",
        "背景知识": "background",
        "数据来源与处理": "data_processing",
        "数据来源与处理方式": "data_processing",
        "模型与证明": "methodology",
        "方法构建": "methodology",
        "系统设计": "methodology",
        "实验分析": "experiment",
        "实验验证": "experiment",
        "系统实现": "experiment",
        "性能评估": "result_analysis",
        "结果分析": "result_analysis",
        "系统评估": "result_analysis",
        "结论展望": "conclusion",
    }
    return mapping.get(stage, "general")


def scoring_impact(findings: Sequence[ResolvedFinding]) -> str:
    """把最严重的问题映射为评分影响说明。"""

    if not findings:
        return ""
    worst = min(
        findings,
        key=lambda item: list(FindingSeverity).index(item.severity),
    )
    return (
        f"{worst.claim}，可能影响相关评价维度[{worst.severity.value}]"
    )


def suggestion(finding: ResolvedFinding) -> str:
    """把具体问题转成可追溯的修改动作，避免通用模板建议。"""

    return f"针对“{finding.claim}”补充说明、修改正文，并提供可核验的支撑证据。"
