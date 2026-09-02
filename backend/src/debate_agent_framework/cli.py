"""Debate 工作流的离线演示入口。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Sequence

from backend.env import ModelClientError
from backend.env.loadenv import load_env_file
from debate_agent_framework.core.errors import WorkflowExecutionError
from .schemas import DebateReviewInput
from .workflows import build_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行论文评审 Debate Multi-Agent 离线演示")
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 JSON 论文输入")
    parser.add_argument("--output", type=Path, help="可选的 UTF-8 JSON 输出路径")
    parser.add_argument(
        "--runtime",
        choices=["demo", "real"],
        default=os.getenv("DEBATE_RUNTIME", "demo"),
        help="demo 使用确定性 Agent；real 使用真实模型 Agent（默认 demo）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    load_env_file(Path(__file__).resolve().parent.parent.parent / ".env")
    args = build_parser().parse_args(argv)
    review_input = DebateReviewInput.model_validate_json(
        args.input.read_text(encoding="utf-8")
    )
    workflow = build_workflow(args.runtime)
    try:
        result_json = workflow.run(review_input).model_dump_json(indent=2)
    except WorkflowExecutionError as exc:
        print(f"评审失败：{exc}", file=sys.stderr)
        return 1
    except ModelClientError as exc:
        print(f"模型调用失败：{exc}", file=sys.stderr)
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result_json + "\n", encoding="utf-8")
        print(f"评审结果已写入 {args.output}", file=sys.stderr)
    else:
        print(result_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
