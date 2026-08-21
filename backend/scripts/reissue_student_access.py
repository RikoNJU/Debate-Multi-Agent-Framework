"""重新签发学生任务访问码的管理脚本。

用法：
    python scripts/reissue_student_access.py <task_id>

每个任务在数据库中只保存一条访问码哈希，重复签发会使旧访问码立即失效。
脚本会打印新访问码，请通过学生端“找回评审任务”页面使用。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from debate_agent_framework.config.settings import DebateWebSettings
from debate_agent_framework.persistence import Database, PortalRepository
from sqlalchemy import select

from debate_agent_framework.persistence.models import StudentTaskAccessRecord


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法：python scripts/reissue_student_access.py <task_id>", file=sys.stderr)
        return 2
    task_id = argv[1].strip()
    settings = DebateWebSettings.from_env()
    database = Database(settings.resolved_database_url())
    repository = PortalRepository(database, session_hours=settings.portal_session_hours)
    with database.session() as session:
        record = session.scalar(
            select(StudentTaskAccessRecord).where(
                StudentTaskAccessRecord.task_id == task_id
            )
        )
        paper_id = record.paper_id if record else None
    token = repository.issue_student_access(task_id=task_id, paper_id=paper_id)
    print(f"任务编号：{task_id}")
    print(f"新访问码：{token}")
    print("请在学生端“找回评审任务”页面输入任务编号和访问码恢复访问。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
