from __future__ import annotations

from debate_agent_framework.ingestion import MarkdownPaperParser
from debate_agent_framework.schemas import PaperType


MARKDOWN = """# 多智能体论文评审研究

## 摘要
本文研究证据驱动的多智能体论文评审。

关键词：多智能体，论文评审；LangGraph

## 第一章 绪论
### 1.1 研究背景
本章介绍研究背景。

## 第二章 方法设计
### 2.1 总体架构
本文提出三个专家和一位 Chair。

## 第三章 实验验证
实验结果表明方法有效。

## 参考文献
[1] Example, 2026.

## 致谢
感谢指导教师。
"""


def test_markdown_parser_builds_review_input() -> None:
    result = MarkdownPaperParser().parse(
        MARKDOWN,
        paper_type=PaperType.METHOD,
        source_filename="fallback.pdf",
        mineru_batch_id="batch-1",
    )

    assert result.title == "多智能体论文评审研究"
    assert result.abstract.startswith("本文研究")
    assert result.keywords == ["多智能体", "论文评审", "LangGraph"]
    assert [chapter.chapter_name for chapter in result.chapters] == [
        "摘要",
        "第一章 绪论",
        "第二章 方法设计",
        "第三章 实验验证",
        "参考文献",
        "致谢",
    ]
    assert result.chapters[-1].reviewable is False
    assert result.chapters[0].reviewable is False
    assert result.references == ["[1] Example, 2026."]
    assert result.metadata["mineru_batch_id"] == "batch-1"


def test_markdown_parser_falls_back_to_single_chapter() -> None:
    result = MarkdownPaperParser().parse(
        "没有 Markdown 标题的正文",
        paper_type=PaperType.ENGINEERING,
        title="工程论文",
    )

    assert result.title == "工程论文"
    assert len(result.chapters) == 1
    assert result.chapters[0].content == "没有 Markdown 标题的正文"


def test_markdown_parser_prefers_cover_title_field() -> None:
    result = MarkdownPaperParser().parse(
        "# 南京大学\n\n题目：真正的论文标题\n\n# 第一章 绪论\n正文",
        paper_type=PaperType.THEORY,
    )

    assert result.title == "真正的论文标题"
