"""原项目 Step 1/2 的 LangGraph 分类适配器。"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from backend.env import ModelClient

from ..ports import ChapterClassifier, PaperClassifier
from ..schemas import (
    ChapterClassificationResult,
    ChapterStageClassification,
    DebateReviewInput,
    PaperClassificationResult,
    PaperType,
)
from .json_client import complete_json

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "classification"
STEP1_RULE_VERSION = "legacy_step1_v1"
STEP2_RULE_VERSION = "legacy_step2_v1"

STEP2_LABELS: dict[PaperType, tuple[str, ...]] = {
    PaperType.THEORY: (
        "引言/绪论",
        "引言/绪论（包含相关工作）",
        "相关工作",
        "背景知识",
        "数据来源与处理方式",
        "模型与证明",
        "实验分析",
        "性能评估",
        "实验分析与性能评估",
        "结论展望",
    ),
    PaperType.METHOD: (
        "引言/绪论",
        "引言/绪论（包含相关工作）",
        "相关工作",
        "背景知识",
        "数据来源与处理方式",
        "方法构建",
        "实验验证",
        "结果分析",
        "实验验证与结果分析",
        "结论展望",
    ),
    PaperType.ENGINEERING: (
        "引言/绪论",
        "引言/绪论（包含相关工作）",
        "相关工作",
        "背景知识",
        "数据来源与处理方式",
        "系统设计",
        "系统实现",
        "系统评估",
        "系统实现与评估",
        "结论展望",
    ),
}


class LegacyStep12ClassificationAdapter(PaperClassifier, ChapterClassifier):
    """复用旧分类标准，并用严格 schema 修复旧流程的名称映射风险。"""

    def __init__(
        self,
        model_client: ModelClient | None = None,
        *,
        temperature: float = 0.0,
        chapter_preview_chars: int = 2_000,
    ) -> None:
        self.model_client = model_client
        self.temperature = temperature
        self.chapter_preview_chars = chapter_preview_chars

    def classify_paper(
        self, review_input: DebateReviewInput
    ) -> PaperClassificationResult:
        if self.model_client is None:
            return self._classify_paper_deterministically(review_input)

        data = complete_json(
            self.model_client,
            system_prompt=self._read_prompt("step1.md"),
            user_prompt=(
                "按照旧 Step 1 标准完成论文类型分类。paper_type 只能是理论研究、"
                "方法创新或工程实现；同时给出简短依据和置信度。"
            ),
            payload=self._paper_payload(review_input),
            schema=PaperClassificationResult.model_json_schema(),
            temperature=self.temperature,
        )
        try:
            return PaperClassificationResult.model_validate(data)
        except ValidationError as exc:
            raise ValueError("Step 1 输出不符合 PaperClassificationResult") from exc

    def classify_chapters(
        self, review_input: DebateReviewInput
    ) -> ChapterClassificationResult:
        paper_type = review_input.paper_type
        if paper_type is None:
            raise ValueError("Step 2 执行前必须先完成 Step 1 论文类型分类")
        reviewable = [chapter for chapter in review_input.chapters if chapter.reviewable]
        if not reviewable:
            raise ValueError("Step 2 至少需要一个可评审章节")

        if self.model_client is None:
            result = ChapterClassificationResult(
                chapters=[
                    ChapterStageClassification(
                        chapter_id=chapter.chapter_id,
                        chapter_name=chapter.chapter_name,
                        stage=self._deterministic_stage(review_input, chapter.chapter_id),
                    )
                    for chapter in reviewable
                ]
            )
        else:
            prompt_name = {
                PaperType.THEORY: "step2_theory.md",
                PaperType.METHOD: "step2_method.md",
                PaperType.ENGINEERING: "step2_engineering.md",
            }[paper_type]
            data = complete_json(
                self.model_client,
                system_prompt=self._read_prompt(prompt_name),
                user_prompt=(
                    "一次性分类输入中的全部可评审章节。必须原样返回每个 chapter_id 和 "
                    "chapter_name，stage 只能选用提示词规定的标签，不得遗漏或新增章节。"
                ),
                payload=self._chapter_payload(review_input),
                schema=ChapterClassificationResult.model_json_schema(),
                temperature=self.temperature,
            )
            try:
                result = ChapterClassificationResult.model_validate(data)
            except ValidationError as exc:
                raise ValueError("Step 2 输出不符合 ChapterClassificationResult") from exc

        self._validate_chapter_result(review_input, result)
        return result

    def _validate_chapter_result(
        self,
        review_input: DebateReviewInput,
        result: ChapterClassificationResult,
    ) -> None:
        expected = {
            chapter.chapter_id: chapter.chapter_name
            for chapter in review_input.chapters
            if chapter.reviewable
        }
        actual_ids = [chapter.chapter_id for chapter in result.chapters]
        if len(actual_ids) != len(set(actual_ids)):
            raise ValueError("Step 2 输出包含重复 chapter_id")
        if set(actual_ids) != set(expected):
            raise ValueError(
                f"Step 2 章节集合不一致，期望 {sorted(expected)}，实际 {sorted(actual_ids)}"
            )
        assert review_input.paper_type is not None
        allowed = set(STEP2_LABELS[review_input.paper_type])
        for item in result.chapters:
            if item.chapter_name != expected[item.chapter_id]:
                raise ValueError(f"Step 2 章节名与 {item.chapter_id} 不一致")
            if item.stage not in allowed:
                raise ValueError(
                    f"Step 2 标签 {item.stage!r} 不属于 {review_input.paper_type.value}"
                )

    def _paper_payload(self, review_input: DebateReviewInput) -> dict[str, object]:
        return {
            "title": review_input.title,
            "abstract": review_input.abstract[:2_000],
            "keywords": review_input.keywords,
            "structure": [
                {
                    "chapter_id": chapter.chapter_id,
                    "chapter_name": chapter.chapter_name,
                    "section_titles": chapter.section_titles,
                }
                for chapter in review_input.chapters
                if chapter.reviewable
            ],
        }

    def _chapter_payload(self, review_input: DebateReviewInput) -> dict[str, object]:
        assert review_input.paper_type is not None
        return {
            "title": review_input.title,
            "abstract": review_input.abstract[:2_000],
            "keywords": review_input.keywords,
            "paper_type": review_input.paper_type.value,
            "allowed_stages": STEP2_LABELS[review_input.paper_type],
            "chapter_count": sum(chapter.reviewable for chapter in review_input.chapters),
            "chapters": [
                {
                    "chapter_id": chapter.chapter_id,
                    "chapter_name": chapter.chapter_name,
                    "section_titles": chapter.section_titles,
                    "content_preview": chapter.content[: self.chapter_preview_chars],
                }
                for chapter in review_input.chapters
                if chapter.reviewable
            ],
        }

    @staticmethod
    def _read_prompt(filename: str) -> str:
        return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")

    @staticmethod
    def _classify_paper_deterministically(
        review_input: DebateReviewInput,
    ) -> PaperClassificationResult:
        text = "\n".join(
            [
                review_input.title,
                review_input.abstract,
                " ".join(review_input.keywords),
                *(chapter.chapter_name + " " + chapter.content[:1_000]
                  for chapter in review_input.chapters if chapter.reviewable),
            ]
        )
        signals = {
            PaperType.THEORY: ("定理", "证明", "复杂性", "收敛", "理论分析"),
            PaperType.METHOD: ("算法", "模型", "方法", "优化", "创新"),
            PaperType.ENGINEERING: ("系统", "平台", "架构", "模块", "部署", "实现"),
        }
        scores = {
            paper_type: sum(text.count(signal) for signal in words)
            for paper_type, words in signals.items()
        }
        paper_type = max(
            (PaperType.METHOD, PaperType.THEORY, PaperType.ENGINEERING),
            key=lambda item: scores[item],
        )
        total = sum(scores.values())
        confidence = 0.5 if total == 0 else min(0.95, 0.55 + scores[paper_type] / total * 0.4)
        return PaperClassificationResult(
            paper_type=paper_type,
            rationale="Demo 模式基于全文、摘要和章节结构中的类型信号分类",
            confidence=confidence,
        )

    def _deterministic_stage(
        self, review_input: DebateReviewInput, chapter_id: str
    ) -> str:
        chapter = next(item for item in review_input.chapters if item.chapter_id == chapter_id)
        text = " ".join(
            [chapter.chapter_name, *chapter.section_titles, chapter.content[:2_000]]
        ).casefold()
        has_related = any(word in text for word in ("相关工作", "研究现状", "文献综述"))
        if any(word in text for word in ("绪论", "引言", "introduction")):
            return "引言/绪论（包含相关工作）" if has_related else "引言/绪论"
        if has_related:
            return "相关工作"
        if any(word in text for word in ("背景知识", "预备知识", "基础知识")):
            return "背景知识"
        if any(word in text for word in ("数据集", "数据来源", "数据处理", "预处理")):
            return "数据来源与处理方式"
        if any(word in text for word in ("结论", "总结", "展望")):
            return "结论展望"

        assert review_input.paper_type is not None
        if review_input.paper_type is PaperType.THEORY:
            has_experiment = any(word in text for word in ("实验", "仿真", "验证"))
            has_evaluation = any(word in text for word in ("性能", "结果", "分析", "评估"))
            if has_experiment and has_evaluation:
                return "实验分析与性能评估"
            if has_experiment:
                return "实验分析"
            if has_evaluation:
                return "性能评估"
            return "模型与证明"
        if review_input.paper_type is PaperType.METHOD:
            has_experiment = any(word in text for word in ("实验", "验证", "测试"))
            has_analysis = any(word in text for word in ("结果", "分析", "性能", "评估"))
            if has_experiment and has_analysis:
                return "实验验证与结果分析"
            if has_experiment:
                return "实验验证"
            if has_analysis:
                return "结果分析"
            return "方法构建"

        has_implementation = any(word in text for word in ("实现", "开发", "模块", "部署"))
        has_evaluation = any(word in text for word in ("测试", "评估", "性能", "结果"))
        if has_implementation and has_evaluation:
            return "系统实现与评估"
        if has_evaluation:
            return "系统评估"
        if has_implementation:
            return "系统实现"
        return "系统设计"
