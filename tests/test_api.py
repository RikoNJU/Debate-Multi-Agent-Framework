"""Debate 框架可选 API 的任务生命周期测试。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from debate_agent_framework.main import create_app
from debate_agent_framework.persistence.models import ReviewRunRecord
from debate_agent_framework.schemas import MinerUParseResult

ROOT = Path(__file__).resolve().parents[1]


def load_example() -> dict:
    return json.loads(
        (ROOT / "examples" / "review_input.json").read_text(encoding="utf-8")
    )


def test_debate_health_and_run_lifecycle() -> None:
    with TestClient(create_app()) as client:
        health = client.get("/api/debate/health")
        assert health.status_code == 200
        assert health.json()["workflow"] == "debate"

        created = client.post("/api/debate/runs", json=load_example())
        assert created.status_code == 202
        task_id = created.json()["task_id"]

        result = client.get(f"/api/debate/runs/{task_id}")
        assert result.status_code == 200
        assert result.json()["status"] == "succeeded"
        assert result.json()["progress_percent"] == 100
        assert result.json()["current_stage_label"] == "评审已完成"
        assert any(
            event["stage"] == "independent_review"
            for event in result.json()["stage_events"]
        )
        assert result.json()["result"]["final_score"]["total_score"] > 0
        assert len(
            result.json()["result"]["final_score"]["legacy_level_scores"]
        ) == 18


def test_api_validates_input_and_returns_not_found() -> None:
    with TestClient(create_app()) as client:
        invalid = client.post("/api/debate/runs", json={"title": "缺少字段"})
        assert invalid.status_code == 422
        assert client.get("/api/debate/runs/not-found").status_code == 404


def test_mineru_parse_endpoint_requires_server_configuration(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DEBATE_MINERU_TOKEN", raising=False)

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/debate/papers/parse",
            files={"pdf": ("paper.pdf", b"%PDF-1.7\ntest", "application/pdf")},
        )

    assert response.status_code == 503
    assert "DEBATE_MINERU_TOKEN" in response.json()["detail"]


def test_pdf_review_endpoint_parses_and_creates_run(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import debate_agent_framework.routers.papers as papers_router

    class FakeMinerUClient:
        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            self.config = config

        async def parse_pdf(self, pdf_path, *, output_root):  # type: ignore[no-untyped-def]
            markdown_path = tmp_path / "full.md"
            markdown_path.write_text("# 测试论文\n\n## 第一章 绪论\n论文正文。", encoding="utf-8")
            content_list_path = tmp_path / "content_list.json"
            content_list_path.write_text(
                json.dumps(
                    [
                        {"type": "title", "text": "第一章 绪论", "text_level": 1, "page_idx": 1, "bbox": [10, 20, 300, 50]},
                        {"type": "text", "text": "论文正文。", "page_idx": 1, "bbox": [10, 60, 300, 100]},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return MinerUParseResult(
                batch_id="batch-api",
                markdown=markdown_path.read_text(encoding="utf-8"),
                output_dir=str(tmp_path),
                markdown_path=str(markdown_path),
                content_list_path=str(content_list_path),
            )

    monkeypatch.setenv("DEBATE_MINERU_TOKEN", "test-token")
    monkeypatch.setattr(papers_router, "MinerUClient", FakeMinerUClient)

    with TestClient(create_app()) as client:
        created = client.post(
            "/api/debate/papers/review",
            data={"paper_type": "方法创新"},
            files={"pdf": ("paper.pdf", b"%PDF-1.7\ntest", "application/pdf")},
        )
        assert created.status_code == 202
        payload = created.json()
        assert payload["title"] == "测试论文"
        assert payload["chapter_count"] == 1

        paper = client.get(f"/api/debate/papers/{payload['paper_id']}")
        assert paper.status_code == 200
        assert paper.json()["current_revision_id"]
        paper_runs = client.get(
            f"/api/debate/papers/{payload['paper_id']}/runs",
        )
        assert paper_runs.status_code == 200
        assert any(
            item["task_id"] == payload["task_id"] for item in paper_runs.json()
        )

        result = client.get(f"/api/debate/runs/{payload['task_id']}")
        assert result.status_code == 200
        assert result.json()["status"] == "succeeded"
        structured = result.json()["result"]["context"]["structured_document"]
        assert structured["page_count"] == 2
        assert structured["blocks"][1]["chapter_id"] == "C1"

        assert client.post(
            f"/api/debate/runs/{payload['task_id']}/retry",
        ).status_code == 409
        with client.app.state.database.session() as session:
            failed = session.get(ReviewRunRecord, payload["task_id"])
            assert failed is not None
            failed.status = "failed"
            failed.current_stage = "failed"
            failed.error = "transient model network failure"

        retried = client.post(
            f"/api/debate/runs/{payload['task_id']}/retry",
        )
        assert retried.status_code == 202
        retried_payload = retried.json()
        assert retried_payload["task_id"] == payload["task_id"]
        assert retried_payload["paper_id"] == payload["paper_id"]
        assert retried_payload["revision_id"] == result.json()["revision_id"]
        assert retried_payload["status"] == "running"
        retried_result = client.get(
            f"/api/debate/runs/{retried_payload['task_id']}",
        )
        assert retried_result.status_code == 200
        assert retried_result.json()["status"] == "succeeded"


def test_pdf_review_endpoint_auto_classifies_without_paper_type(
    monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    import debate_agent_framework.routers.papers as papers_router

    class FakeMinerUClient:
        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            self.config = config

        async def parse_pdf(self, pdf_path, *, output_root):  # type: ignore[no-untyped-def]
            markdown = (
                "# 新型优化算法\n\n"
                "## 第一章 绪论\n介绍研究背景与相关工作。\n\n"
                "## 第二章 算法与实验\n提出优化算法并进行实验验证与结果分析。"
            )
            markdown_path = tmp_path / "full-auto.md"
            markdown_path.write_text(markdown, encoding="utf-8")
            return MinerUParseResult(
                batch_id="batch-auto",
                markdown=markdown,
                output_dir=str(tmp_path),
                markdown_path=str(markdown_path),
            )

    monkeypatch.setenv("DEBATE_MINERU_TOKEN", "test-token")
    monkeypatch.setattr(papers_router, "MinerUClient", FakeMinerUClient)

    with TestClient(create_app()) as client:
        created = client.post(
            "/api/debate/papers/review",
            files={"pdf": ("paper.pdf", b"%PDF-1.7\ntest", "application/pdf")},
        )
        assert created.status_code == 202
        payload = created.json()
        result = client.get(
            f"/api/debate/runs/{payload['task_id']}",
        ).json()

    assert result["status"] == "succeeded"
    assert result["result"]["context"]["profile"]["paper_type"] == "方法创新"
    assert result["result"]["context"]["chapters"][0]["stage"] == (
        "引言/绪论（包含相关工作）"
    )


def test_identical_pdf_reuses_successful_review_before_mineru(
    monkeypatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    import debate_agent_framework.routers.papers as papers_router

    class CountingMinerUClient:
        calls = 0

        def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
            self.config = config

        async def parse_pdf(self, pdf_path, *, output_root):  # type: ignore[no-untyped-def]
            type(self).calls += 1
            markdown = "# 唯一复用测试论文\n\n## 第一章 绪论\n这是待评审的论文正文。"
            markdown_path = tmp_path / "reuse.md"
            markdown_path.write_text(markdown, encoding="utf-8")
            return MinerUParseResult(
                batch_id="batch-reuse",
                markdown=markdown,
                output_dir=str(tmp_path),
                markdown_path=str(markdown_path),
            )

    monkeypatch.setenv("DEBATE_MINERU_TOKEN", "test-token")
    monkeypatch.setattr(papers_router, "MinerUClient", CountingMinerUClient)
    upload = {
        "pdf": (
            "unique-reuse.pdf",
            b"%PDF-1.7\nunique exact reuse payload",
            "application/pdf",
        )
    }

    with TestClient(create_app()) as client:
        first = client.post(
            "/api/debate/papers/review",
            data={"paper_type": "方法创新"},
            files=upload,
        )
        second = client.post(
            "/api/debate/papers/review",
            data={"paper_type": "方法创新"},
            files=upload,
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["reused"] is True
    assert second.json()["task_id"] == first.json()["task_id"]
    assert second.json()["revision_id"] == first.json()["revision_id"]
    assert CountingMinerUClient.calls == 1
