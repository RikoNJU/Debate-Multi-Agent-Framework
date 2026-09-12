"""工作人员端（教师/教务）账号注册脚本。

在本地 SQLite 数据库中创建或重置门户账号，密码使用与后端
``debate_agent_framework.services.security`` 完全一致的 PBKDF2-SHA256 格式。

用法示例::

    python scripts/create_portal_user.py --username admin \
        --display-name 系统管理员 --role admin --password '你的密码'

    # 已存在账号时重置密码
    python scripts/create_portal_user.py --username admin --role admin \
        --display-name 系统管理员 --password '新密码' --reset-password
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = REPO_ROOT / "backend" / "data" / "debate.db"
SECURITY_MODULE_PATH = (
    REPO_ROOT
    / "backend"
    / "src"
    / "debate_agent_framework"
    / "services"
    / "security.py"
)
ROLES = ("teacher", "admin")


def load_security_module() -> ModuleType:
    """按文件路径加载 security.py，避免引入后端依赖。"""

    spec = importlib.util.spec_from_file_location(
        "debate_portal_security", SECURITY_MODULE_PATH
    )
    if spec is None or spec.loader is None:  # pragma: no cover - 路径固定
        raise RuntimeError(f"无法加载密码模块：{SECURITY_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="注册工作人员端账号")
    parser.add_argument("--username", required=True, help="登录用户名")
    parser.add_argument("--role", required=True, choices=ROLES, help="账号角色")
    parser.add_argument("--display-name", default=None, help="显示名称，默认与用户名相同")
    parser.add_argument("--password", required=True, help="登录密码，至少 8 位")
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"SQLite 数据库路径，默认 {DEFAULT_DATABASE}",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="用户名已存在时重置其密码（默认报错退出）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    database_path = Path(args.database).resolve()
    if not database_path.exists():
        print(f"数据库不存在：{database_path}", file=sys.stderr)
        return 1
    if len(args.password) < 8:
        print("密码至少需要 8 个字符", file=sys.stderr)
        return 1

    security = load_security_module()
    display_name = args.display_name or args.username

    connection = sqlite3.connect(database_path)
    try:
        existing = connection.execute(
            "select id from users where username = ?", (args.username,)
        ).fetchone()
        if existing is not None and not args.reset_password:
            print(f"用户名已存在：{args.username}（{existing[0]}）", file=sys.stderr)
            return 1

        password_hash = security.hash_password(args.password)
        if existing is None:
            user_id = uuid.uuid4().hex
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            connection.execute(
                "insert into users "
                "(id, username, display_name, role, password_hash, is_active, created_at) "
                "values (?, ?, ?, ?, ?, 1, ?)",
                (
                    user_id,
                    args.username,
                    display_name,
                    args.role,
                    password_hash,
                    created_at,
                ),
            )
            action = "已创建"
        else:
            user_id = existing[0]
            connection.execute(
                "update users set display_name = ?, role = ?, password_hash = ?, "
                "is_active = 1 where id = ?",
                (display_name, args.role, password_hash, user_id),
            )
            action = "已重置密码"
        connection.commit()
    finally:
        connection.close()

    print(f"{action}工作人员端账号 {args.username}（角色 {args.role}，ID {user_id}）")
    print("现在可以在 /login 使用该账号登录工作人员端。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
