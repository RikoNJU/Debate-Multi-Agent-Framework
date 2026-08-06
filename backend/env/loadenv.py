"""轻量 .env 加载器，避免额外引入 python-dotenv 依赖。"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> None:
    """把 ``KEY=VALUE`` 形式的配置读入环境变量。

    已存在的环境变量优先，避免覆盖用户显式导出的配置。
    """

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")
