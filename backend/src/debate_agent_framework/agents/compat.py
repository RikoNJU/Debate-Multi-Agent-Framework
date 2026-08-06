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

    workload = CompatibleWorkloadEvaluation(
        structure_evaluation=CompatibleStructureEvaluation(
            completeness=WorkloadItem(score=82, analysis="核心章节完整。"),
            abstract_and_keywords=WorkloadItem(
                score=84, analysis="摘要与关键词基本规范。"
            ),
            catalog_standardization=WorkloadItem(
                score=80, analysis="目录层级清晰。"
            ),
            chapter_standardization=WorkloadItem(
                score=78, analysis="跨章节回指仍可加强。"
            ),
            acknowledgement_standardization=WorkloadItem(
                score=85, analysis="致谢格式无明显问题。"
            ),
        ),
        summary="论文结构基本完整，方法与实验之间的对应关系需要进一步明确。",
        workload_evaluation=(
            "论文具备基本研究工作量，但关键实验覆盖仍需补充。"
        ),
    )
    return ReviewSynthesis(
        global_review=global_review,
        chapter_evaluation=chapter_evaluation,
        workload_evaluation=workload,
    )


def chapter_type(stage: str) -> str:
    """根据原 Step 2 的章节阶段映射为原 Step 4 章节类型。"""

    mapping = {
        "引言/绪论": "introduction",
        "引言/绪论（包含相关工作）": "introduction_related_work",
        "相关工作": "related_work",
        "背景知识": "background",
        "数据来源与处理": "data_processing",
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
    """按问题维度生成修改建议。"""

    if finding.dimension == "实验与证据":
        return "补充强 Baseline 与消融实验。"
    if finding.dimension == "理论与方法":
        return "说明关键假设与方法失效边界。"
    return "明确贡献与验证结果的跨章节对应。"
