"""Legacy Step 5 workload standards adapted to structured MinerU facts."""

from __future__ import annotations

import re
from importlib.resources import files

from backend.env import ModelClient

from ..schemas import (
    CompatibleStructureEvaluation,
    CompatibleWorkloadEvaluation,
    DebateReviewInput,
    PaperType,
    ReviewSynthesis,
    WorkloadItem,
)
from .json_client import complete_json

STEP5_RULE_VERSION = "legacy_step5_v2"
_PROMPTS = {
    PaperType.THEORY: "step5_theory.md",
    PaperType.METHOD: "step5_method.md",
    PaperType.ENGINEERING: "step5_engineering.md",
}
_TYPE_REQUIREMENTS = {
    PaperType.THEORY: (("模型与证明", "理论", "证明"), ("实验分析", "性能评估"), 12000, 14000),
    PaperType.METHOD: (("方法构建",), ("实验验证", "结果分析"), 14000, 16000),
    PaperType.ENGINEERING: (("系统设计", "系统实现"), ("系统评估",), 12000, 15000),
}


class DeterministicLegacyWorkloadEvaluator:
    """Computes the objective portion of the old Step 5 rubric."""

    def evaluate_workload(
        self, review_input: DebateReviewInput, synthesis: ReviewSynthesis
    ) -> CompatibleWorkloadEvaluation:
        facts = workload_facts(review_input)
        missing = facts["missing_modules"]
        completeness_score = 100 if not missing else 80 if len(missing) == 1 else 60
        completeness_analysis = (
            " " if not missing else "\n".join(f"[{item}]缺失或未识别到{item}" for item in missing)
        )

        abstract_issues = []
        abstract_chars = int(facts["abstract_chars"])
        if not 300 <= abstract_chars <= 600:
            abstract_issues.append("[摘要]摘要字数应以300-600字为宜")
        keyword_count = int(facts["keyword_count"])
        if not 3 <= keyword_count <= 5:
            abstract_issues.append("[关键词]关键词数量应为3-5个")
        if not facts["keyword_semicolon"] and keyword_count:
            abstract_issues.append('[关键词]关键词之间应使用"；"分开')
        abstract_score = max(40, 100 - 20 * len(abstract_issues))

        catalog_ok = bool(facts["has_catalog"]) and bool(facts["heading_order_stable"])
        catalog_analysis = " " if catalog_ok else "[目录]目录缺失或章节层次需人工核对"

        chapter_issues = []
        if not facts["has_introduction"] or not facts["has_conclusion"]:
            chapter_issues.append("[章节规范性]引言/绪论和结论应独立成章")
        body_chars = int(facts["body_chars"])
        minimum = int(facts["minimum_chars"])
        if body_chars < minimum:
            chapter_issues.append(
                f"[章节规范性]正文约{body_chars}字，低于该类型最低参考值{minimum}字"
            )
        chapter_score = 60 if body_chars < minimum else 80 if chapter_issues else 100

        acknowledgement_chars = int(facts["acknowledgement_chars"])
        if acknowledgement_chars > 1000:
            acknowledgement_score, acknowledgement_analysis = 80, "[致谢]致谢字数过多"
        elif acknowledgement_chars < 50:
            acknowledgement_score, acknowledgement_analysis = 60, "[致谢]致谢过短或未识别到致谢"
        else:
            acknowledgement_score, acknowledgement_analysis = 100, " "

        analyses = [
            completeness_analysis, *abstract_issues, catalog_analysis,
            *chapter_issues, acknowledgement_analysis,
        ]
        summary = "\n".join(item for item in analyses if item.strip())
        workload = self._workload_comment(review_input, facts)
        return CompatibleWorkloadEvaluation(
            structure_evaluation=CompatibleStructureEvaluation(
                completeness=WorkloadItem(score=completeness_score, analysis=completeness_analysis),
                abstract_and_keywords=WorkloadItem(
                    score=abstract_score, analysis="\n".join(abstract_issues) or " "
                ),
                catalog_standardization=WorkloadItem(
                    score=100 if catalog_ok else 80, analysis=catalog_analysis
                ),
                chapter_standardization=WorkloadItem(
                    score=chapter_score, analysis="\n".join(chapter_issues) or " "
                ),
                acknowledgement_standardization=WorkloadItem(
                    score=acknowledgement_score, analysis=acknowledgement_analysis
                ),
            ),
            summary=summary or " ",
            workload_evaluation=workload,
        )

    @staticmethod
    def _workload_comment(review_input: DebateReviewInput, facts: dict[str, object]) -> str:
        present = set(str(item) for item in facts["stages"])
        core_groups = facts["required_stage_groups"]
        missing_groups = [
            "或".join(group) for group in core_groups
            if not any(any(marker in stage for marker in group) for stage in present)
        ]
        text = (
            f"正文约{facts['body_chars']}字；{len([c for c in review_input.chapters if c.reviewable])}个可评审章节。"
            f"该{review_input.paper_type.value}论文建议达到{facts['preferred_chars']}字以上。"
        )
        if missing_groups:
            text += f"结构递进中未稳定识别到：{'、'.join(missing_groups)}，建议核对并补充相应内容。"
        else:
            text += "核心研究与验证阶段已识别，仍需结合各 Agent 的证据判断内容是否充实。"
        return text


class RealLegacyWorkloadEvaluator(DeterministicLegacyWorkloadEvaluator):
    """Uses the three old paper-type prompts while grounding format facts deterministically."""

    def __init__(self, model_client: ModelClient, *, temperature: float = 0.1) -> None:
        self.model_client = model_client
        self.temperature = temperature

    def evaluate_workload(
        self, review_input: DebateReviewInput, synthesis: ReviewSynthesis
    ) -> CompatibleWorkloadEvaluation:
        baseline = super().evaluate_workload(review_input, synthesis)
        prompt_name = _PROMPTS[review_input.paper_type]
        prompt = files("debate_agent_framework.prompts.workload").joinpath(prompt_name).read_text(encoding="utf-8")
        payload = {
            "paper_type": review_input.paper_type,
            "title": review_input.title,
            "facts": workload_facts(review_input),
            "deterministic_format_baseline": baseline.model_dump(mode="json"),
            "chapters": [
                {
                    "chapter_id": chapter.chapter_id,
                    "chapter_name": chapter.chapter_name,
                    "stage": chapter.stage,
                    "content_chars": len(_plain_text(chapter.content)),
                    "section_titles": chapter.section_titles,
                    "pages": {
                        "start": chapter.metadata.get("page_start"),
                        "end": chapter.metadata.get("page_end"),
                    },
                }
                for chapter in review_input.chapters
            ],
            "agent_review": synthesis.global_review.model_dump(mode="json"),
        }
        data = complete_json(
            self.model_client,
            system_prompt="你是论文评审流程的 Step 5 工作量与结构评估员。客观格式事实必须服从 deterministic_format_baseline，解析质量低只能要求人工核对，不能作为论文扣分依据。",
            user_prompt=prompt,
            payload=payload,
            schema=CompatibleWorkloadEvaluation.model_json_schema(),
            temperature=self.temperature,
        )
        result = CompatibleWorkloadEvaluation.model_validate(data)
        # Objective format checks are deterministic; the model owns only the holistic workload prose.
        result.structure_evaluation = baseline.structure_evaluation
        result.summary = baseline.summary
        return result


def workload_facts(review_input: DebateReviewInput) -> dict[str, object]:
    titles = [chapter.chapter_name for chapter in review_input.chapters]
    lowered = "\n".join(titles).casefold()
    full_text = review_input.full_text
    stages = [chapter.stage for chapter in review_input.chapters if chapter.reviewable]
    has_chinese_abstract = bool(review_input.abstract) or "摘要" in lowered
    has_english_abstract = bool(re.search(r"(?im)^#{0,6}\s*abstract\b", full_text))
    has_catalog = "目录" in lowered or bool(re.search(r"(?m)^#{0,6}\s*目录\s*$", full_text))
    has_references = bool(review_input.references) or "参考文献" in lowered or "references" in lowered
    missing = []
    if not has_chinese_abstract or not has_english_abstract:
        missing.append("摘要")
    if not has_catalog:
        missing.append("目录")
    if not any(chapter.reviewable for chapter in review_input.chapters):
        missing.append("正文")
    if not has_references:
        missing.append("参考文献")
    acknowledgement = next(
        (chapter for chapter in review_input.chapters if "致谢" in chapter.chapter_name), None
    )
    minimum, preferred = _TYPE_REQUIREMENTS[review_input.paper_type][2:]
    keyword_line = re.search(r"(?im)^(?:\*{0,2})?(?:关键词|关键字)\s*[：:]\s*(.+)$", full_text)
    return {
        "missing_modules": missing,
        "has_catalog": has_catalog,
        "has_english_abstract": has_english_abstract,
        "heading_order_stable": len({chapter.chapter_id for chapter in review_input.chapters}) == len(review_input.chapters),
        "abstract_chars": len(_plain_text(review_input.abstract)),
        "keyword_count": len(review_input.keywords),
        "keyword_semicolon": bool(keyword_line and "；" in keyword_line.group(1)),
        "has_introduction": any("引言" in stage or "绪论" in stage for stage in stages),
        "has_conclusion": any("结论" in stage for stage in stages),
        "acknowledgement_chars": len(_plain_text(acknowledgement.content)) if acknowledgement else 0,
        "body_chars": sum(len(_plain_text(chapter.content)) for chapter in review_input.chapters if chapter.reviewable),
        "minimum_chars": minimum,
        "preferred_chars": preferred,
        "stages": stages,
        "required_stage_groups": _TYPE_REQUIREMENTS[review_input.paper_type][:2],
        "parse_quality": review_input.metadata.get("parse_quality_status", "markdown_only"),
    }


def _plain_text(value: str) -> str:
    return re.sub(r"\s+|[#*_`>|]", "", value)
