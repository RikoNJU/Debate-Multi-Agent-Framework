#!/usr/bin/env python3
"""基于成对(人工分, AI分)拟合 Step 7 评分校准参数，写入 scoring_calibration.json。

用法：
  python scripts/calibrate_scores.py data.csv [--out backend/data/scoring_calibration.json] [--min-pairs 10]

CSV 需含表头，两列均可按需命名：
  ai_total, human_total       生成偏移前的 AI 原始总分、教师人工总分

拟合模型（最小二乘）：
  corrected = slope * raw + intercept     （默认 0-100 语义分同尺度，0.8<=slope<=1.2 时判定为偏移为主）
输出校验：
  MAE / 偏差均值 / Pearson / 校准后 |Δ|<=5 占比，并给出是否建议直接使用。
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def load_pairs(path: Path) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit("CSV 没有表头")
        ai_col = next(
            (
                c for c in reader.fieldnames
                if c.strip().lower() in {"ai_total", "ai_score", "ai", "system"}
            ),
            None,
        )
        human_col = next(
            (
                c for c in reader.fieldnames
                if c.strip().lower()
                in {"human_total", "human_score", "human", "teacher", "manual", "真实分"}
            ),
            None,
        )
        if ai_col is None or human_col is None:
            raise SystemExit(f"找不到 ai_total / human_total 列，实际表头: {reader.fieldnames}")
        for row in reader:
            try:
                ai, human = float(row[ai_col]), float(row[human_col])
            except (TypeError, ValueError):
                continue
            if ai > 0 and human > 0:
                pairs.append((ai, human))
    return pairs


def fit(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(pairs)
    mean_x = sum(x for x, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    den = sum((x - mean_x) ** 2 for x, y in pairs)
    slope = num / den if den else 1.0
    intercept = mean_y - slope * mean_x
    return slope, intercept


def pearson(pairs: list[tuple[float, float]]) -> float:
    n = len(pairs)
    mean_x = sum(x for x, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    den = (
        sum((x - mean_x) ** 2 for x, _ in pairs)
        * sum((y - mean_y) ** 2 for _, y in pairs)
    ) ** 0.5
    return num / den if den else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="配对分数 CSV 路径")
    parser.add_argument("--out", type=Path,
                        default=BASE / "backend" / "data" / "scoring_calibration.json")
    parser.add_argument("--min-pairs", type=int, default=10)
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"文件不存在: {args.csv}")
    pairs = load_pairs(args.csv)
    if len(pairs) < args.min_pairs:
        raise SystemExit(f"有效配对只有 {len(pairs)} 条，少于 --min-pairs={args.min_pairs}")

    raw_mae = sum(abs(a - h) for a, h in pairs) / len(pairs)
    raw_bias = sum(a - h for a, h in pairs) / len(pairs)
    slope, intercept = fit(pairs)
    corrected = [min(100, max(0, slope * a + intercept)) for a, _ in pairs]
    corr_mae = sum(abs(c - h) for (_, h), c in zip(pairs, corrected)) / len(pairs)
    within5 = sum(1 for (_, h), c in zip(pairs, corrected) if abs(c - h) <= 5) / len(pairs)
    r = pearson(pairs)

    print(f"配对样本 n = {len(pairs)}")
    print(f"校准前 |Δ|≤5 占比 = {100*sum(1 for a,h in pairs if abs(a-h)<=5)/len(pairs):.1f}%   MAE = {raw_mae:.2f}   偏差均值 = {raw_bias:+.2f}")
    print(f"Pearson r = {r:.3f}")
    print(f"拟合: corrected = {slope:.3f} × raw {intercept:+.2f}")
    print(f"校准后 |Δ|≤5 占比 = {100*within5:.1f}%   MAE = {corr_mae:.2f}")

    if not (0.8 <= slope <= 1.2):
        print("警告: slope 偏离 1 较多，说明尺度不一致；建议先检查语义分是否都能到 100 再校准。")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "slope": round(slope, 4),
        "intercept": round(intercept, 2),
        "clip_lo": 40.0,
        "clip_hi": 100.0,
        "fit_n": len(pairs),
        "fit_r_pearson": round(r, 4),
        "fit_rmse": round(corr_mae, 3),
        "temporary_default": False,
        "notes": (
            f"基于 {len(pairs)} 对真实人工分拟合；"
            f"corrected = {slope:.3f}×raw{intercept:+.2f}，校准后 MAE {corr_mae:.2f}"
        ),
    }
    args.out.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n已写入 {args.out}")


if __name__ == "__main__":
    main()