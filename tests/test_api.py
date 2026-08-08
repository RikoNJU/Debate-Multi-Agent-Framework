"""Debate 框架可选 API 的任务生命周期测试。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from debate_agent_framework.main import create_app
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
        assert result.json()["result"]["final_score"]["total_score"] > 0


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
            return MinerUParseResult(
                batch_id="batch-api",
                markdown=markdown_path.read_text(encoding="utf-8"),
                output_dir=str(tmp_path),
                markdown_path=str(markdown_path),
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

        result = client.get(f"/api/debate/runs/{payload['task_id']}")
        assert result.status_code == 200
        assert result.json()["status"] == "succeeded"
