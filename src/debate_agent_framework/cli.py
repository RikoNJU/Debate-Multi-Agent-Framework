"""Debate 工作流的离线演示入口。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .demo import (
    DemoContextPlanner,
    DemoEvidenceRetriever,
    DemoHistoricalScoreRetriever,
    DemoOriginalPipelineAdapter,
    DemoReviewChair,
    DemoSpecialist,
)
from .schemas import DebateReviewInput, SpecialistRole
from .state import DebateWorkflowServices
from .workflow import DebateWorkflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行论文评审 Debate Multi-Agent 离线演示")
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 JSON 论文输入")
    parser.add_argument("--output", type=Path, help="可选的 UTF-8 JSON 输出路径")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    review_input = DebateReviewInput.model_validate_json(
        args.input.read_text(encoding="utf-8")
    )
    workflow = DebateWorkflow(
        DebateWorkflowServices(
            context_planner=DemoContextPlanner(),
            specialists={
                role: DemoSpecialist(role)
                for role in SpecialistRole
            },
            review_chair=DemoReviewChair(),
            evidence_retriever=DemoEvidenceRetriever(),
            historical_score_retriever=DemoHistoricalScoreRetriever(),
            original_pipeline=DemoOriginalPipelineAdapter(),
        )
    )
    result_json = workflow.run(review_input).model_dump_json(indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result_json + "\n", encoding="utf-8")
    else:
        print(result_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
