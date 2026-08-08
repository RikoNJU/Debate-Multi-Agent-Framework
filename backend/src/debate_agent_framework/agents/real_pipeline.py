"""真实 Step 6/7 适配器：评分由 LLM 依据评审事实动态生成。"""

from __future__ import annotations

from collections.abc import Sequence

from backend.env import ModelClient

from ..schemas import (
    ComprehensiveScoreResult,
    DebateReviewInput,
    HistoricalScoreCase,
    ReviewSynthesis,
    SummaryAdviceResult,
)
from .json_client import complete_json


SCORE_DIMENSIONS = {
    "1": "选题契合度：符合本学科专业培养目标，达到科研和实践能力培养目的",
    "2": "选题工作量适宜度：满足培养方案要求，工作量适当",
    "3": "选题学术价值：符合学科发展，具有科技或应用参考价值",
    "4": "文献检索和分析能力：能够检索、分析、综合并应用中外文献",
    "5": "知识综合应用和研究深度：目标明确，内容具体并具有一定深度",
    "6": "专业方法工具运用：能够运用专业方法、手段和工具开展研究",
    "7": "专业技能和实践能力：掌握专业技能和研究方法并具备实践能力",
    "8": "技术应用和外语能力：软件、编程或建模能力及外文摘要和文献能力",
    "9": "创新性：问题、方法、见解或工程设计具有特色或新意",
    "10": "论证严谨性和科学性：数据可靠、论据充分、分析深入、结论正确",
    "11": "论文结构和语言表达：完整反映工作，结构严谨、语言通顺",
    "12": "成果价值：具有学术价值或可运行的实物、系统及复杂原型",
}


def grade_for(total: float) -> str:
    if total >= 90:
        return "优秀"
    if total >= 75:
        return "良好"
    if total >= 60:
        return "及格"
    return "不合格"


class RealOriginalPipelineAdapter:
    """用真实 LLM 生成 Step 7 语义评分，确定性计算总分与等级。"""

    def __init__(
        self,
        model_client: ModelClient | None = None,
        *,
        temperature: float = 0.2,
    ) -> None:
        self.model_client = model_client
        self.temperature = temperature

    def summarize_advice(
        self,
        review_input: DebateReviewInput,
        synthesis: ReviewSynthesis,
    ) -> SummaryAdviceResult:
        severity_order = {"fatal": 0, "major": 1, "moderate": 2, "minor": 3, "info": 4}
        findings = sorted(
            synthesis.global_review.resolved_findings,
            key=lambda item: (severity_order[item.severity.value], -item.confidence),
        )
        chapter_names = {
            chapter.chapter_id: chapter.chapter_name for chapter in review_input.chapters
        }
        selected = []
        for finding in findings:
            if finding.status.value in {"disputed", "insufficient"}:
                continue
            location = "、".join(
                chapter_names.get(chapter_id, chapter_id)
                for chapter_id in finding.affected_chapter_ids
            ) or "全文"
            selected.append(
                f"[{location}] 针对“{finding.claim}”修改正文并补充可核验证据"
            )
            if len(selected) == 5:
                break
        summary = "；".join(selected) or "未发现需要修改的问题。"
        return SummaryAdviceResult(summary=summary, advice_count=len(findings))

    def score(
        self,
        review_input: DebateReviewInput,
        synthesis: ReviewSynthesis,
        *,
        summary_advice: SummaryAdviceResult,
        historical_cases: Sequence[HistoricalScoreCase],
    ) -> ComprehensiveScoreResult:
        if self.model_client is None:
            raise NotImplementedError("RealOriginalPipelineAdapter 需要注入 ModelClient")

        payload = {
            "score_dimensions": SCORE_DIMENSIONS,
            "review_input": {
                "title": review_input.title,
                "paper_type": review_input.paper_type,
                "chapters": [
                    {
                        "chapter_name": chapter.chapter_name,
                        "stage": chapter.stage,
                        "reviewable": chapter.reviewable,
                    }
                    for chapter in review_input.chapters
                ],
            },
            "global_review": synthesis.global_review.model_dump(mode="json"),
            "chapter_evaluation": {
                key: envelope.chapter_data.model_dump(mode="json")
                for key, envelope in synthesis.chapter_evaluation.items()
            },
            "workload_evaluation": synthesis.workload_evaluation.model_dump(mode="json"),
            "summary_advice": summary_advice.model_dump(mode="json"),
            "historical_score_cases": [
                case.model_dump(mode="json") for case in historical_cases
            ],
        }
        data = complete_json(
            self.model_client,
            system_prompt=self._system_prompt(),
            user_prompt=self._scoring_prompt(),
            payload=payload,
            schema=ComprehensiveScoreResult.model_json_schema(),
            temperature=self.temperature,
        )
        score = ComprehensiveScoreResult.model_validate(data)
        total = round(sum(score.scores.values()) / len(score.scores), 1)
        score.total_score = total
        score.grade = grade_for(total)
        return score

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是论文评审系统的综合评分员（Step 7）。"
            "只能根据输入中的评审事实评分，不能把模板分数或历史案例当作论文事实。"
            "fatal/major 问题必须在相关维度显著扣分；证据不足的结论不得导致确定性重扣。"
            "输出必须严格符合 JSON Schema，scores 覆盖字符串键 '1' 到 '12'，每项 0-100。"
        )

    @staticmethod
    def _scoring_prompt() -> str:
        return (
            "请严格按照 score_dimensions 中给出的十二项定义逐项评分。"
            "评分依据优先级为：有原文证据的 resolved_findings、各维度评价、章节评价、"
            "工作量评价和修改建议。historical_score_cases 只用于尺度校准，不得照抄分数。"
            "分数必须体现维度差异，避免无依据地集中为相同分数。"
            "同时输出 overall_evaluation、calibration_notes 和 confidence。"
        )
