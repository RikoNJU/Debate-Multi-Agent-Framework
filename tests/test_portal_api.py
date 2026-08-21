"""Authentication, RBAC, assignment, and human-review API tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from debate_agent_framework.config import DebateWebSettings
from debate_agent_framework.main import create_app
from debate_agent_framework.persistence.models import (
    PaperRecord,
    PaperRevisionRecord,
    ReviewRunRecord,
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_teacher_admin_portal_core_workflow(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = DebateWebSettings(
        data_dir=str(tmp_path / "data"),
        database_url=f"sqlite:///{(tmp_path / 'portal.db').as_posix()}",
        bootstrap_admin_username="root-admin",
        bootstrap_admin_password="strong-admin-password",
        bootstrap_admin_display_name="测试管理员",
    )
    data_dir = tmp_path / "data"
    pdf_path = (
        data_dir / "papers" / "paper-portal" / "revision-portal" / "source.pdf"
    )
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\nportal test")
    now = datetime.now(UTC)

    with TestClient(create_app(settings)) as client:
        database = client.app.state.database
        with database.session() as session:
            session.add(
                PaperRecord(
                    id="paper-portal",
                    title="门户评审测试论文",
                    paper_type="方法创新",
                    source_filename="paper.pdf",
                    sha256="a" * 64,
                    current_revision_id="revision-portal",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                PaperRevisionRecord(
                    id="revision-portal",
                    paper_id="paper-portal",
                    sha256="a" * 64,
                    pdf_path=pdf_path.relative_to(data_dir).as_posix(),
                    structured_input_path=str(tmp_path / "structured.json"),
                    mineru_batch_id="batch-portal",
                    parse_status="succeeded",
                    created_at=now,
                )
            )
            session.add(
                ReviewRunRecord(
                    task_id="run-portal",
                    paper_id="paper-portal",
                    revision_id="revision-portal",
                    status="succeeded",
                    current_stage="completed",
                    result_json={
                        "final_score": {
                            "total_score": 86,
                            "legacy_level_scores": [2] * 18,
                        },
                        "synthesis": {
                            "global_review": {"overall_summary": "AI 初评摘要"}
                        },
                    },
                    created_at=now,
                    updated_at=now,
                )
            )
        initial_student_result = client.get("/api/debate/runs/run-portal")
        assert initial_student_result.status_code == 200
        assert initial_student_result.json()["published_review"] is None

        login = client.post(
            "/api/debate/portal/auth/login",
            json={"username": "root-admin", "password": "strong-admin-password"},
        )
        assert login.status_code == 200
        admin_token = login.json()["access_token"]
        admin_id = login.json()["user"]["id"]

        admin_assignment = client.post(
            "/api/debate/portal/admin/assignments",
            headers=auth(admin_token),
            json={"paper_id": "paper-portal", "reviewer_id": admin_id},
        )
        assert admin_assignment.status_code == 201
        assert admin_assignment.json()["ai_section_scores"] == [2] * 18
        assert client.get(
            "/api/debate/portal/teacher/assignments", headers=auth(admin_token)
        ).status_code == 200
        assert client.get(
            f"/api/debate/portal/teacher/assignments/{admin_assignment.json()['assignment_id']}/pdf",
            headers=auth(admin_token),
        ).status_code == 200

        created_teacher = client.post(
            "/api/debate/portal/admin/users",
            headers=auth(admin_token),
            json={
                "username": "reviewer.one",
                "display_name": "评审教师一",
                "role": "teacher",
                "password": "teacher-password",
            },
        )
        assert created_teacher.status_code == 201
        teacher_id = created_teacher.json()["id"]

        assignment = client.post(
            "/api/debate/portal/admin/assignments",
            headers=auth(admin_token),
            json={"paper_id": "paper-portal", "reviewer_id": teacher_id},
        )
        assert assignment.status_code == 201
        assignment_id = assignment.json()["assignment_id"]
        assert assignment.json()["ai_score"] == 86

        teacher_login = client.post(
            "/api/debate/portal/auth/login",
            json={"username": "reviewer.one", "password": "teacher-password"},
        )
        teacher_token = teacher_login.json()["access_token"]
        assert client.get(
            "/api/debate/portal/admin/statistics", headers=auth(teacher_token)
        ).status_code == 403

        criteria = client.get(
            "/api/debate/portal/teacher/criteria", headers=auth(teacher_token)
        )
        assert criteria.status_code == 200
        assert len(criteria.json()) == 18
        assert client.get(
            f"/api/debate/portal/teacher/assignments/{assignment_id}/pdf",
            headers=auth(teacher_token),
        ).status_code == 200

        review_payload = {
            "section_scores": [3] * 18,
            "advice_content": "建议补充对比实验。",
            "teacher_comments": "已核对关键证据。",
        }
        draft = client.put(
            f"/api/debate/portal/teacher/assignments/{assignment_id}/review",
            headers=auth(teacher_token),
            json=review_payload,
        )
        assert draft.status_code == 200
        assert draft.json()["status"] == "draft"
        assert draft.json()["total_score"] == 100

        submitted = client.post(
            f"/api/debate/portal/teacher/assignments/{assignment_id}/review/submit",
            headers=auth(teacher_token),
            json=review_payload,
        )
        assert submitted.status_code == 200
        assert submitted.json()["status"] == "submitted"
        review_id = submitted.json()["review_id"]
        assert client.put(
            f"/api/debate/portal/teacher/assignments/{assignment_id}/review",
            headers=auth(teacher_token),
            json=review_payload,
        ).status_code == 409

        statistics = client.get(
            "/api/debate/portal/admin/statistics", headers=auth(admin_token)
        )
        assert statistics.json()["submitted_reviews"] == 1
        assert statistics.json()["average_human_score"] == 100
        published = client.post(
            f"/api/debate/portal/admin/reviews/{review_id}/publish",
            headers=auth(admin_token),
        )
        assert published.status_code == 200
        assert published.json()["published_at"]
        student_result = client.get("/api/debate/runs/run-portal").json()
        assert student_result["published_review"]["total_score"] == 100
        assert "teacher_comments" not in student_result["published_review"]
        assert client.get(
            "/api/debate/student/tasks/run-portal/pdf",
        ).status_code == 200
        exported = client.get(
            "/api/debate/portal/admin/exports/reviews.csv",
            headers=auth(admin_token),
        )
        assert exported.status_code == 200
        assert "评审教师一" in exported.content.decode("utf-8-sig")

        logs = client.get(
            "/api/debate/portal/admin/audit-logs", headers=auth(admin_token)
        ).json()
        assert {item["action"] for item in logs} >= {
            "paper.assigned",
            "review.draft_saved",
            "review.submitted",
            "review.published",
        }

        assert client.post(
            "/api/debate/portal/auth/logout", headers=auth(teacher_token)
        ).status_code == 204
        assert client.get(
            "/api/debate/portal/teacher/assignments", headers=auth(teacher_token)
        ).status_code == 401

def _fake_compile(*, data, output_dir, stem, tectonic_path=None, cache_dir=None):
    """A stub that mimics compile_review_table_pdf without a real compiler."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    pdf_path = output / f"{stem}.pdf"
    pdf_path.write_bytes(b"%PDF-2.0\nfake review table\n")
    tex_path = output / f"{stem}.tex"
    tex_path.write_text(
        "\n".join(
            [
                "\\documentclass[12pt]{ctexart}",
                "\\begin{document}",
                "人工智能学院本科毕设论文院内预审表",
                f".的题目：{data.paper_title}",
                "\\end{document}",
            ]
        ),
        encoding="utf-8",
    )
    return pdf_path


def test_review_table_export_endpoints(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = DebateWebSettings(
        data_dir=str(tmp_path / "data"),
        database_url=f"sqlite:///{(tmp_path / 'export.db').as_posix()}",
        bootstrap_admin_username="root-admin",
        bootstrap_admin_password="strong-admin-password",
        bootstrap_admin_display_name="测试管理员",
    )
    data_dir = tmp_path / "data"
    pdf_path = (
        data_dir / "papers" / "paper-export" / "revision-export" / "source.pdf"
    )
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\nexport test")
    now = datetime.now(UTC)

    with TestClient(create_app(settings)) as client:
        database = client.app.state.database
        with database.session() as session:
            session.add(
                PaperRecord(
                    id="paper-export",
                    title="评审表导出测试论文",
                    paper_type="工程实现",
                    source_filename="201300088-测试学生.pdf",
                    sha256="b" * 64,
                    current_revision_id="revision-export",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                PaperRevisionRecord(
                    id="revision-export",
                    paper_id="paper-export",
                    sha256="b" * 64,
                    pdf_path=pdf_path.relative_to(data_dir).as_posix(),
                    structured_input_path=str(tmp_path / "structured.json"),
                    mineru_batch_id="batch-export",
                    parse_status="succeeded",
                    created_at=now,
                )
            )
            session.add(
                ReviewRunRecord(
                    task_id="run-export",
                    paper_id="paper-export",
                    revision_id="revision-export",
                    status="succeeded",
                    current_stage="completed",
                    result_json={
                        "final_score": {
                            "total_score": 81,
                            "legacy_level_scores": [2] * 18,
                        },
                        "synthesis": {
                            "chapter_evaluation": {
                                "chapter_1": {
                                    "chapter_data": {
                                        "chapter_name": "第一章 绪论",
                                        "chapter_remark": "建议补充选题背景。",
                                    }
                                }
                            }
                        },
                    },
                    created_at=now,
                    updated_at=now,
                )
            )

        login = client.post(
            "/api/debate/portal/auth/login",
            json={"username": "root-admin", "password": "strong-admin-password"},
        )
        admin_token = login.json()["access_token"]
        admin_id = login.json()["user"]["id"]
        assignment = client.post(
            "/api/debate/portal/admin/assignments",
            headers=auth(admin_token),
            json={"paper_id": "paper-export", "reviewer_id": admin_id},
        ).json()
        assignment_id = assignment["assignment_id"]

        # 未评分时导出应被拒绝
        response = client.get(
            f"/api/debate/portal/teacher/assignments/{assignment_id}/review-table",
            headers=auth(admin_token),
        )
        assert response.status_code == 409

        client.put(
            f"/api/debate/portal/teacher/assignments/{assignment_id}/review",
            headers=auth(admin_token),
            json={
                "section_scores": [3] * 18,
                "advice_content": "建议完善对比实验。",
                "teacher_comments": "已核对。",
            },
        )

        client.post(
            f"/api/debate/portal/teacher/assignments/{assignment_id}/review/submit",
            headers=auth(admin_token),
            json={
                "section_scores": [3] * 18,
                "advice_content": "建议完善对比实验。",
                "teacher_comments": "已核对。",
            },
        )

        with patch(
            "debate_agent_framework.routers.teacher.compile_review_table_pdf",
            side_effect=_fake_compile,
        ):
            exported = client.get(
                f"/api/debate/portal/teacher/assignments/{assignment_id}/review-table",
                headers=auth(admin_token),
            )
        assert exported.status_code == 200
        assert exported.headers["content-type"] == "application/pdf"
        assert "18%E7%BB%B4" in exported.headers["content-disposition"] or "18维评审表" in exported.headers["content-disposition"]

        # LaTeX 与 PDF 均已持久化到论文版本目录
        import hashlib
        paper_key = hashlib.sha256(b"paper-export").hexdigest()[:24]
        persisted_dir = data_dir / "papers" / paper_key / "revision-export"
        tex_files = list(persisted_dir.glob("review_table_*.tex"))
        pdf_files = list(persisted_dir.glob("review_table_*.pdf"))
        assert len(tex_files) == 1
        assert len(pdf_files) == 1
        assert "人工智能学院本科毕设论文院内预审表" in tex_files[
            0
        ].read_text(encoding="utf-8")

        # 学生端在人工终审发布前可导出 AI 预审版评审表
        with patch(
            "debate_agent_framework.routers.student.compile_review_table_pdf",
            side_effect=_fake_compile,
        ):
            before_publish = client.get(
                "/api/debate/student/tasks/run-export/review-table",
            )
        assert before_publish.status_code == 200
        assert before_publish.headers["content-type"] == "application/pdf"
        paper_key = hashlib.sha256(b"paper-export").hexdigest()[:24]
        ai_tex = list(
            (data_dir / "papers" / paper_key / "revision-export").glob(
                "review_table_ai_*.tex"
            )
        )
        assert len(ai_tex) == 1
        assert "人工智能学院本科毕设论文院内预审表" in ai_tex[0].read_text(encoding="utf-8")

        # 通过 assignment detail 拿到 review_id 并发布终审
        detail = client.get(
            f"/api/debate/portal/teacher/assignments/{assignment_id}",
            headers=auth(admin_token),
        ).json()
        review_id = detail["human_review"]["review_id"]
        published = client.post(
            f"/api/debate/portal/admin/reviews/{review_id}/publish",
            headers=auth(admin_token),
        )
        assert published.status_code == 200

        with patch(
            "debate_agent_framework.routers.student.compile_review_table_pdf",
            side_effect=_fake_compile,
        ):
            student_export = client.get(
                "/api/debate/student/tasks/run-export/review-table",
            )
        assert student_export.status_code == 200
        assert student_export.headers["content-type"] == "application/pdf"
        assert "18%E7%BB%B4" in student_export.headers["content-disposition"] or "18维评审表" in student_export.headers["content-disposition"]

        # 发布后导出以教师评分为准（生成 review_table_{review_id} 版本）
        persisted_tex = list(
            (data_dir / "papers" / paper_key / "revision-export").glob(
                f"review_table_{review_id}.tex"
            )
        )
        assert len(persisted_tex) == 1
