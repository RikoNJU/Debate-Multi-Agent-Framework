from __future__ import annotations

from debate_agent_framework.ingestion import MarkdownPaperParser, MinerUContentListAdapter
from debate_agent_framework.schemas import ParseQualityStatus, PaperType
from debate_agent_framework.schemas import EvidenceKind, ReviewEvidence
from debate_agent_framework.agents.context_planner import DebateContextPlannerAgent
from debate_agent_framework.workflows import DebateWorkflow


MARKDOWN = """# 测试论文

## 第一章 绪论
研究背景与研究问题。

## 第二章 方法设计
提出一个新的评审方法，并给出公式。
"""


def test_content_list_maps_pages_coordinates_and_stable_ids() -> None:
    review_input = MarkdownPaperParser().parse(MARKDOWN, paper_type=PaperType.METHOD)
    payload = [
        {"type": "title", "text": "第一章 绪论", "text_level": 1, "page_idx": 2, "bbox": [10, 20, 300, 50]},
        {"type": "text", "text": "研究背景与研究问题。", "page_idx": 2, "bbox": [10, 60, 300, 100]},
        {"type": "title", "text": "第二章 方法设计", "text_level": 1, "page_idx": 4, "bbox": [10, 20, 300, 50]},
        {"type": "interline_equation", "latex": "x=y+1", "page_idx": 4, "bbox": [10, 60, 300, 100]},
        {"type": "image", "img_path": "images/figure-1.jpg", "page_idx": 5, "bbox": [20, 30, 400, 500]},
    ]

    first = MinerUContentListAdapter().enrich_data(review_input, payload)
    second = MinerUContentListAdapter().enrich_data(review_input, payload)

    assert first.structured_document is not None
    assert first.structured_document.page_count == 6
    assert first.structured_document.blocks[3].latex == "x=y+1"
    assert first.structured_document.blocks[4].asset_path == "images/figure-1.jpg"
    assert first.structured_document.blocks[0].bbox.x0 == 10
    assert first.chapters[0].metadata["page_start"] == "3"
    assert first.chapters[1].metadata["page_end"] == "6"
    assert [item.block_id for item in first.structured_document.blocks] == [
        item.block_id for item in second.structured_document.blocks
    ]
    assert first.metadata["content_list_rule_version"] == "mineru_content_list_v1"


def test_content_list_accepts_nested_pages_and_marks_low_quality() -> None:
    review_input = MarkdownPaperParser().parse(MARKDOWN)
    payload = {"pages": [{"page_idx": 0, "blocks": [{"type": "text", "text": "无法映射的短句"}]}]}

    result = MinerUContentListAdapter().enrich_data(review_input, payload)

    assert result.structured_document is not None
    assert result.structured_document.quality.status is ParseQualityStatus.LOW
    assert result.metadata["requires_parse_review"] == "true"
    assert result.structured_document.blocks[0].page_number == 1


def test_content_list_accepts_content_wrapped_blocks() -> None:
    review_input = MarkdownPaperParser().parse(MARKDOWN)
    payload = {
        "content": [
            {"type": "title", "text": "第一章 绪论", "page_number": 1},
            {"type": "text", "text": "研究背景与研究问题。", "page_number": 1},
        ]
    }

    result = MinerUContentListAdapter().enrich_data(review_input, payload)

    assert len(result.structured_document.blocks) == 2
    assert result.structured_document.blocks[1].chapter_id == "C1"


def test_paper_evidence_is_enriched_with_exact_mineru_locator() -> None:
    review_input = MarkdownPaperParser().parse(MARKDOWN, paper_type=PaperType.METHOD)
    payload = [
        {"type": "title", "text": "第二章 方法设计", "text_level": 1, "page_idx": 4, "bbox": [10, 20, 300, 50]},
        {"type": "text", "text": "提出一个新的评审方法，并给出公式。", "page_idx": 4, "bbox": [10, 60, 300, 100]},
    ]
    enriched = MinerUContentListAdapter().enrich_data(review_input, payload)
    evidence = ReviewEvidence(
        evidence_id="E-MINERU", kind=EvidenceKind.PAPER, source_title="第二章 方法设计",
        quote="提出一个新的评审方法", location="第二章", chapter_id="C2",
    )

    DebateWorkflow._validate_paper_evidence(
        [evidence], DebateContextPlannerAgent().build(enriched)
    )

    assert evidence.block_id == enriched.structured_document.blocks[1].block_id
    assert evidence.chunk_id == enriched.structured_document.blocks[1].chunk_id
    assert evidence.page_number == 5
    assert evidence.bbox.x1 == 300
