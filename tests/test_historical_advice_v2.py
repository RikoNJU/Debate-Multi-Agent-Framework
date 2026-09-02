"""Focused tests for the canonical historical-advice V2 retrieval path."""

from __future__ import annotations

from debate_agent_framework.schemas import (
    ChapterInput,
    DebateReviewInput,
    EvidenceKind,
    FindingAdviceItem,
    FindingSeverity,
    PaperType,
    ResolutionStatus,
    ResolvedFinding,
    ReviewEvidence,
    SummaryAdviceItem,
    SummaryAdviceResult,
)
from debate_agent_framework.services.clean_advice import CleanAdviceRetriever
from debate_agent_framework.services.rag_v2_contract import (
    AdviceCorpusRecord,
    record_checksum,
    sha256_text,
    stable_advice_id,
)
from debate_agent_framework.skills.models import RetrievalOverlay
from debate_agent_framework.workflows.debate import DebateWorkflow
from scripts.batch_clean import clean_one


def make_record(
    seed: str, *, advice_type: str = "content", paper_type: str = "method"
) -> AdviceCorpusRecord:
    raw_checksum = sha256_text(f"raw-{seed}")
    advice_id = stable_advice_id("legacy-content", seed, raw_checksum)
    data: dict[str, object] = {
        "advice_id": advice_id,
        "legacy_chunk_id": seed,
        "source_collection": "legacy-content",
        "advice_type": advice_type,
        "paper_title": "历史论文",
        "paper_type": paper_type,
        "chapter": "第四章 实验",
        "chapter_stage": "experiment",
        "problem_location": "4.2 实验结果",
        "issue_category": "统计显著性",
        "context": "仅报告了单次实验数值。",
        "advice": f"建议 {seed} 补充显著性检验。",
        "analysis": "数值差异缺少统计检验支持。",
        "evidence_excerpt": "本文方法的 MSE 更低。",
        "original_text": "本文方法的 MSE 更低。",
        "dense_text": "问题类型：统计显著性\n历史问题诊断：缺少显著性检验。",
        "bm25_text": "统计显著性 实验结果 MSE",
        "rerank_text": f"缺少显著性检验。建议 {seed} 补充检验。",
        "raw_checksum": raw_checksum,
        "clean_version": "historical_advice_v2",
    }
    data["record_checksum"] = record_checksum(data)
    return AdviceCorpusRecord.model_validate(data)


def make_retriever() -> CleanAdviceRetriever:
    return CleanAdviceRetriever(
        chroma_db_path="unused",
        bm25_dir="unused",
        corpus_path="unused",
        embed_endpoint="http://unused/embeddings",
        rerank_endpoint="",
    )


def test_cleaner_generates_stable_id_and_separate_text_views() -> None:
    original = "实验结果" * 100
    document = (
        "论文标题: 测试论文\n"
        "所属章节: 第四章 实验\n"
        "问题位置: 4.2 实验结果\n"
        "上下文: 报告模型对比结果。\n"
        "修改建议: 请补充显著性检验。\n"
        "分析过程: 当前结论缺少统计支持。\n"
        f"原文片段: {original}"
    )
    first = clean_one("content_1", document, {"advice_type": "content"}, "legacy")
    second = clean_one("content_1", document, {"advice_type": "content"}, "legacy")

    assert first.advice_id == second.advice_id
    assert first.chapter_stage == "experiment"
    assert len(first.evidence_excerpt) == 300
    assert "请补充显著性检验" not in first.dense_text
    assert "请补充显著性检验" not in first.bm25_text
    assert "请补充显著性检验" in first.rerank_text


def test_rrf_hydrates_bm25_only_candidate_from_canonical_records() -> None:
    dense_record = make_record("dense")
    bm25_record = make_record("bm25")
    retriever = make_retriever()
    retriever._records = {
        dense_record.advice_id: dense_record,
        bm25_record.advice_id: bm25_record,
    }

    fused = retriever._rrf_fuse(
        [{"advice_id": dense_record.advice_id, "rank": 1}],
        [{"advice_id": bm25_record.advice_id, "rank": 1, "score": 4.0}],
    )

    bm25_only = next(item for item in fused if item["advice_id"] == bm25_record.advice_id)
    assert bm25_only["dense_rank"] is None
    assert bm25_only["record"].advice == bm25_record.advice


def test_type_overlay_boosts_matching_candidate_without_changing_physical_corpus() -> None:
    method_record = make_record("method", paper_type="method")
    theory_record = make_record("theory", paper_type="theory")
    retriever = make_retriever()
    retriever._records = {
        method_record.advice_id: method_record,
        theory_record.advice_id: theory_record,
    }
    overlay = RetrievalOverlay(
        paper_type_values=["method"],
        filter_mode="prefer",
        preferred_boost=2.0,
        rerank_instruction_file="unused.md",
    )

    fused = retriever._rrf_fuse(
        [
            {"advice_id": theory_record.advice_id, "rank": 1},
            {"advice_id": method_record.advice_id, "rank": 2},
        ],
        [],
        overlay,
    )

    assert fused[0]["advice_id"] == method_record.advice_id


def test_finding_query_uses_actual_paper_evidence_and_chapter_context() -> None:
    review_input = DebateReviewInput(
        paper_id="paper-1",
        title="跨域推荐研究",
        full_text="完整正文",
        paper_type=PaperType.METHOD,
        chapters=[
            ChapterInput(
                chapter_id="C4",
                chapter_name="第四章 实验",
                stage="实验验证",
                content="当前论文只报告了一次运行的 MSE 结果。",
            )
        ],
    )
    finding = ResolvedFinding(
        finding_id="F1",
        dimension="论证严谨性和科学性",
        claim="实验结果缺少统计显著性检验",
        severity=FindingSeverity.MAJOR,
        status=ResolutionStatus.CONFIRMED,
        rationale="仅比较数值便得出性能更优结论。",
        evidence=[
            ReviewEvidence(
                evidence_id="E1",
                kind=EvidenceKind.PAPER,
                source_title="第四章 实验",
                quote="我们的模型拥有最好的性能。",
                location="4.2 实验结果",
                chapter_id="C4",
                relevance=0.95,
                confidence=0.95,
            )
        ],
        affected_chapter_ids=["C4"],
        confidence=0.9,
    )

    dense_query, bm25_query = make_retriever()._build_finding_query(
        finding, review_input
    )

    assert "我们的模型拥有最好的性能" in dense_query
    assert "只报告了一次运行的 MSE" in dense_query
    assert "统计显著性" in bm25_query


def test_summary_provenance_keeps_two_sources_without_overwrite() -> None:
    result = SummaryAdviceResult(
        summary="补充实验。",
        items=[
            SummaryAdviceItem(
                position="第四章",
                suggestion="补充显著性检验。",
                severity=FindingSeverity.MAJOR,
                finding_ids=["F1"],
            )
        ],
    )
    sources = [
        FindingAdviceItem(
            finding_id="F1",
            advice_id=f"adv_{index:024x}",
            suggestion=f"历史建议 {index}",
            rerank_score=9.0 - index,
        )
        for index in (1, 2)
    ]

    enriched = DebateWorkflow._enrich_with_finding_advice(result, sources)

    assert [source.advice_id for source in enriched.items[0].historical_sources] == [
        sources[0].advice_id,
        sources[1].advice_id,
    ]
    assert enriched.items[0].advice_id == sources[0].advice_id
