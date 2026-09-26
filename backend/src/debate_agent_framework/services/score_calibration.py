"""Step 7 评分的校准层。

用成对的「AI 原始总分 vs 教师人工总分」拟合一个仿射变换
    corrected = slope * raw + intercept
再作用到 12 项语义分上，重新走 legacy 18 项公式得到一致的总分和等级。

配置文件 JSON（backend/data/scoring_calibration.json）：
    {
        "slope": 1.0,
        "intercept": 10.0,
        "clip_lo": 40,
        "clip_hi": 100,
        "fit_n": 0,
        "fit_rmse": null,
        "temporary_default": true,
        "notes": "临时默认偏移 +10，用 scripts/calibrate_scores.py 基于真实配对分重拟合"
    }

不存在配置文件时保持原分不变（恒等映射）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG_PATH = os.environ.get(
    "SCORE_CALIBRATION_PATH",
    str(Path(__file__).resolve().parents[3] / "data" / "scoring_calibration.json"),
)


def load_score_calibration(path: str | os.PathLike | None = None) -> dict[str, Any] | None:
    config_path = Path(path or _CONFIG_PATH)
    if not config_path.exists():
        return None
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def apply_score_calibration(
    semantic_scores: dict[str, float],
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, float], str | None]:
    """将仿射校准作用到 12 项语义分。

    返回 (校准后的分数 dict, 校准说明 or None)。
    """
    if not config:
        return semantic_scores, None

    slope = float(config.get("slope", 1.0))
    intercept = float(config.get("intercept", 0.0))
    clip_lo = float(config.get("clip_lo", 0.0))
    clip_hi = float(config.get("clip_hi", 100.0))
    if slope == 1.0 and intercept == 0.0:
        return semantic_scores, None

    calibrated: dict[str, float] = {}
    for key, value in semantic_scores.items():
        adjusted = slope * float(value) + intercept
        adjusted = max(clip_lo, min(clip_hi, adjusted))
        calibrated[key] = round(adjusted, 1)

    note = (
        f"总分校准 applied：corrected = {slope:g} × raw {intercept:+g}（区间 "
        f"{clip_lo:g}~{clip_hi:g}，样本数 {config.get('fit_n', '?')}）"
    )
    return calibrated, note