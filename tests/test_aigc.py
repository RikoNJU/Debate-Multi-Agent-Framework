from __future__ import annotations

import json

from fastapi.testclient import TestClient

from debate_agent_framework.aigc.aggregation import aggregate_results
from debate_agent_framework.aigc.preprocessing import AigcWindowBuilder, PREPROCESSING_VERSION
from debate_agent_framework.main import create_app
from debate_agent_framework.schemas import (
    BoundingBox,
    ChapterInput,
    DebateReviewInput,
    MinerUParseResult,
    ParseQuality,
    ParseQualityStatus,
    StructuredBlock,
    StructuredPaperDocument,
)


class CharacterDetector:
    dependencies_installed = True

    def encode(self, text: str) -> list[int]:
        return [ord(char) for char in text]

    def decode(self, token_ids) -> str:  # type: ignore[no-untyped-def]
        return "".join(chr(value) for value in token_ids)

    def predict_probabilities(self, texts):  # type: ignore[no-untyped-def]
        return [0.85 if "AI风险" in text else 0.15 for text in texts]


def review_input() -> DebateReviewInput:
    blocks = [
        StructuredBlock(
            block_id="B1",
            chunk_id="K1",
            block_type="text",
            text="人工写作" * 70,
            page_number=1,
            bbox=BoundingBox(x0=1, y0=2, x1=30, y1=40),
            chapter_id="C1",
        ),
        StructuredBlock(
            block_id="B2",
            chunk_id="K2",
            block_type="text",
            text="AI风险" * 180,
            page_number=2,
            chapter_id="C2",
        ),
        StructuredBlock(
            block_id="F1",
            chunk_id="KF1",
            block_type="formula",
            text="x = y + z",
            chapter_id="C2",
        ),
    ]
    return DebateReviewInput(
        paper_id="paper-aigc",
        title="AIGC测试",
        full_text="测试",
        chapters=[
            ChapterInput(
                chapter_id="C1",
                chapter_name="第一章",
                stage="正文",
                content=blocks[0].text,
            ),
            ChapterInput(
                chapter_id="C2",
                chapter_name="第二章",
                stage="正文",
                content=blocks[1].text,
            ),
        ],
        structured_document=StructuredPaperDocument(
            blocks=blocks,
            page_count=2,
            quality=ParseQuality(
                status=ParseQualityStatus.HIGH,
                score=1,
                mapped_block_ratio=1,
                located_block_ratio=1,
            ),
        ),
    )


def test_aigc_windows_are_bounded_and_keep_source_locators() -> None:
    segments = AigcWindowBuilder(target_tokens=120, min_tokens=20, max_tokens=150).build(
        review_input(), CharacterDetector()
    )

    assert segments
    repeated = AigcWindowBuilder(
        target_tokens=120, min_tokens=20, max_tokens=150
    ).build(review_input(), CharacterDetector())
    assert [item.segment_id for item in segments] == [item.segment_id for item in repeated]
    assert all(segment.token_count <= 150 for segment in segments)
    assert all(len({locator.block_id for locator in segment.locators}) >= 1 for segment in segments)
    assert not any(locator.block_id == "F1" for item in segments for locator in item.locators)
    assert all(len({item.chapter_id for item in segments if item.segment_id == segment.segment_id}) == 1 for segment in segments)


def test_aigc_aggregation_reports_weighted_risk_distribution() -> None:
    segments = AigcWindowBuilder(target_tokens=120, min_tokens=20, max_tokens=150).build(
        review_input(), CharacterDetector()
    )
    probabilities = CharacterDetector().predict_probabilities([item.text for item in segments])
    result = aggregate_results(
        segments,
        probabilities,
        model_id="test-detector",
        model_revision=None,
        preprocessing_version=PREPROCESSING_VERSION,
        medium_threshold=0.5,
        high_threshold=0.8,
    )

    assert result.segment_count == len(segments)
    assert result.high_risk_ratio > 0
    assert result.average_risk_score < 1
    assert result.calibrated is False
    assert "不能单独作为" in result.disclaimer


def test_standalone_aigc_api_requires_access_code(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import debate_agent_framework.aigc.service as service_module

    class FakeMinerUClient:
        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            pass

        async def parse_pdf(self, pdf_path, *, output_root):  # type: ignore[no-untyped-def]
            markdown = "# AIGC测试\n\n## 第一章\n" + ("人工写作" * 100)
            output_root.mkdir(parents=True, exist_ok=True)
            md = output_root / "full.md"
            md.write_text(markdown, encoding="utf-8")
            content = output_root / "content_list.json"
            content.write_text(
                json.dumps(
                    [
                        {
                            "type": "text",
                            "text": "人工写作" * 100,
                            "page_idx": 0,
                            "bbox": [10, 20, 300, 100],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return MinerUParseResult(
                batch_id="aigc-batch",
                markdown=markdown,
                output_dir=str(output_root),
                markdown_path=str(md),
                content_list_path=str(content),
            )

    monkeypatch.setenv("DEBATE_AIGC_ENABLED", "true")
    monkeypatch.setenv("DEBATE_MINERU_TOKEN", "test-token")
    monkeypatch.setattr(service_module, "MinerUClient", FakeMinerUClient)
    with TestClient(create_app()) as client:
        service = client.app.state.aigc_detection_service
        service.detector = CharacterDetector()
        service.mineru_client_factory = FakeMinerUClient
        response = client.post(
            "/api/debate/aigc/tasks",
            files={"pdf": ("paper.pdf", b"%PDF-1.7\ntest", "application/pdf")},
        )
        assert response.status_code == 202
        created = response.json()
        task_id = created["task_id"]
        assert client.get(f"/api/debate/aigc/tasks/{task_id}").status_code == 403
        result = client.get(
            f"/api/debate/aigc/tasks/{task_id}",
            headers={"X-AIGC-Access-Code": created["access_code"]},
        )

        deleted = client.delete(
            f"/api/debate/aigc/tasks/{task_id}",
            headers={"X-AIGC-Access-Code": created["access_code"]},
        )
        assert deleted.status_code == 204
        assert not service._task_dir(task_id).exists()

    assert result.status_code == 200
    assert result.json()["status"] == "succeeded"
    assert result.json()["result"]["segment_count"] > 0
    assert "final_score" not in result.json()
