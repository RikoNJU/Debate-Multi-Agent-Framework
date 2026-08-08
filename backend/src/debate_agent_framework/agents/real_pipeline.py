"""真实 Step 6/7 适配器：评分由 LLM 依据评审事实动态生成。"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files

from backend.env import ModelClient

from ..schemas import (
    ComprehensiveScoreResult,
    DebateReviewInput,
    HistoricalScoreCase,
    ReviewSynthesis,
    SummaryAdviceResult,
)
from .json_client import complete_json
from .legacy_scoring import calculate_legacy_score
from .legacy_summary import build_summary_advice


SCORE_DIMENSIONS = {
    "1": "选题契合度：选题符合本学科专业培养目标，达到科学研究和实践能力培养的目的",
    "2": "选题工作量适宜度：选题满足专业培养方案中对素质、能力和知识结构的要求，工作量适当",
    "3": "选题学术价值：选题符合本学科专业的发展，具有一定的科技或应用参考价值",
    "4": "文献检索和分析能力：基本掌握检索中外文献资料的方法，能够分析、综合、归纳并适当应用",
    "5": "知识综合应用和研究深度：能够综合应用所学知识，研究目标明确、内容具体且具有一定深度",
    "6": "专业方法工具运用：较熟练运用本专业设计或研究的方法、手段和工具开展研究",
    "7": "专业技能和实践能力：基本掌握专业技能和研究方法，具有一定实践能力和水平",
    "8": "技术应用和外语能力：具备软件、编程或建模分析能力，外文摘要和外文文献使用规范",
    "9": "创新性：提出新问题、新见解，或解决问题的方法、手段、工程思路具有特色或新意",
    "10": "论证严谨性和科学性：概念清楚、内容正确、数据可靠、论据充分、结论基本正确",
    "11": "论文结构和语言表达：能够完整反映实际完成的工作，结构较严谨，语言通顺",
    "12": "成果价值：论文具有一定学术价值，或设计形成实物、可运行系统及复杂原型",
}


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
        if self.model_client is None:
            raise NotImplementedError("RealOriginalPipelineAdapter 需要注入 ModelClient")
        prompt = files("debate_agent_framework.prompts.summary").joinpath("step6.md").read_text(encoding="utf-8")
        data = complete_json(
            self.model_client,
            system_prompt="你是论文评审流程的 Step 6 建议汇总员。只能选择已给出的已裁决问题，不得伪造 finding_id 或 evidence_id。",
            user_prompt=prompt,
            payload={
                "title": review_input.title,
                "abstract": review_input.abstract,
                "keywords": review_input.keywords,
                "chapter_advices": {
                    key: [item.model_dump(mode="json") for item in envelope.chapter_data.advice]
                    for key, envelope in synthesis.chapter_evaluation.items()
                },
                "resolved_findings": [
                    item.model_dump(mode="json")
                    for item in synthesis.global_review.resolved_findings
                ],
            },
            schema=SummaryAdviceResult.model_json_schema(),
            temperature=self.temperature,
        )
        proposed = SummaryAdviceResult.model_validate(data)
        return build_summary_advice(review_input, synthesis, proposed.items)

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
        calculation = calculate_legacy_score(
            semantic_scores=score.scores,
            structure=synthesis.workload_evaluation.structure_evaluation,
            references=review_input.references,
        )
        score.total_score = calculation.total_score
        score.grade = calculation.grade
        score.legacy_raw_scores = calculation.raw_scores
        score.legacy_level_scores = calculation.level_scores
        score.scoring_rule = "legacy_step7_v1"
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
            "请严格按照 score_dimensions 中给出的旧项目十二项定义逐项评分。"
            "先参考 workload_evaluation：工作量不足时，与工作量和专业能力直接相关的"
            "评价项不得高于 80 分，但选题契合度、选题工作量适宜度、选题学术价值及"
            "文献检索和分析能力不受这一上限影响。"
            "重点依据有原文证据的 resolved_findings、章节评价中的不足及问题严重程度扣分。"
            "先判断各项属于优秀、良好、一般或较差，再在档位内给出具体分数："
            "优秀 90-100，良好 80-90，一般 60-80，较差低于 60。"
            "不得让大部分分数相同，并避免输出 75、80、85 这三个定级边界分数。"
            "historical_score_cases 只用于尺度校准，不得照抄分数。"
            "同时输出 overall_evaluation、calibration_notes 和 confidence。"
        )
