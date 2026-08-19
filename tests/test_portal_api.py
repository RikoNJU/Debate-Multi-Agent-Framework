"""Authentication, RBAC, assignment, and human-review API tests."""

from __future__ import annotations

from datetime import UTC, datetime

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
    pdf_path = tmp_path / "paper.pdf"
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
                    pdf_path=str(pdf_path),
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
        student_token = client.app.state.portal_repository.issue_student_access(
            task_id="run-portal", paper_id="paper-portal"
        )
        assert client.get("/api/debate/runs/run-portal").status_code == 401
        assert client.get(
            "/api/debate/runs/run-portal",
            headers={"X-Submission-Token": "wrong-access-code"},
        ).status_code == 403
        initial_student_result = client.get(
            "/api/debate/runs/run-portal",
            headers={"X-Submission-Token": student_token},
        )
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
        student_result = client.get(
            "/api/debate/runs/run-portal",
            headers={"X-Submission-Token": student_token},
        ).json()
        assert student_result["published_review"]["total_score"] == 100
        assert "teacher_comments" not in student_result["published_review"]
        assert client.get(
            "/api/debate/student/tasks/run-portal/pdf",
            headers={"X-Submission-Token": student_token},
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
