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


def grade_for(total: float) -> str:
    """把总分映射为等级。

    demo 版本只区分“良好/一般”，这里提供更细粒度的四档，同时保持
    81.3 → 良好 的历史行为一致。
    """

    if total >= 90:
        return "优秀"
    if total >= 75:
        return "良好"
    if total >= 60:
        return "及格"
    return "不合格"


class RealOriginalPipelineAdapter:
    """用真实 LLM 复用原 Step 6/7 的适配器。

    ``summarize_advice`` 仍是确定性聚合（把各章节建议拼接为一段汇总），因为它是纯
    汇总操作，不需要模型参与；``score`` 则把评审事实（GlobalReview、章节评价、工作量
    评价、修改建议、历史评分案例）交给模型，要求模型逐项给出 1-12 的十二项语义评分，
    再由适配器确定性地由十二项均值得出总分与等级，保证分数内部自洽（total 恒等于
    十二项均值，grade 由统一规则映射）。
    """

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
        advice = [
            item
            for envelope in synthesis.chapter_evaluation.values()
            for item in envelope.chapter_data.advice
        ]
        summary = "；".join(item.suggestion for item in advice) or "未发现需要修改的问题。"
        return SummaryAdviceResult(summary=summary, advice_count=len(advice))

    def score(
        self,
        review_input: DebateReviewInput,
        synthesis: ReviewSynthesis,
        *,
        summary_advice: SummaryAdviceResult,
        historical_cases: Sequence[HistoricalScoreCase],
    ) -> ComprehensiveScoreResult:
        if self.model_client is None:
            raise NotImplementedError(
                "RealOriginalPipelineAdapter 需要注入 ModelClient"
            )

        payload = {
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
            "workload_evaluation": synthesis.workload_evaluation.model_dump(
                mode="json"
            ),
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
            "你是论文评审系统的综合评分员（原 Step 7）。"
            "你根据已经形成的评审事实（全文评价、各维度评价、章节评价、工作量评价、"
            "修改建议汇总）对论文打分，而不是凭空给出固定数值。"
            "评分必须与评审中列出的问题严重程度保持一致：fatal/major 问题应在对应维度"
            "明显扣分，strengths 多的维度可以适当加分。"
            "输出必须严格符合调用方要求的 JSON Schema，scores 必须覆盖字符串键 "
            "'1' 到 '12' 共十二项，每项位于 0-100。"
        )

    @staticmethod
    def _scoring_prompt() -> str:
        return (
            "请依据输入中的评审事实，对论文在 1 到 12 号十二个评价维度逐项给出 0-100 的"
            "评分（scores 的键必须是字符串 '1' 到 '12'）。评分依据如下：\n"
            "1. global_review.resolved_findings 的严重程度：fatal/major 扣分最重，"
            "moderate 次之，minor/info 轻微扣分；\n"
            "2. global_review.dimensions 每个维度的 strengths 加分、weaknesses 扣分；\n"
            "3. chapter_evaluation 与 workload_evaluation 反映结构完整性与工作量；\n"
            "4. historical_score_cases 只用于尺度校准，不要直接照抄其分数。\n"
            "同时给出 overall_evaluation（基于 global_review.overall_summary 与 "
            "weaknesses 概括论文整体表现）、calibration_notes（说明是否参考了历史案例）"
            "和 confidence。"
        )
