"""Debate 上下文构造 Agent：把原流程输入整理为全文上下文或语义内容包。"""

from __future__ import annotations

from backend.env import ModelClient
from ..schemas import (
    ContentPacket,
    DebateReviewInput,
    PaperProfile,
    ReviewContext,
)
from ..ports import ContextPlanner


class DebateContextPlannerAgent(ContextPlanner):
    """根据论文长度构造全文或相邻章节语义内容包。

    短论文使用全文上下文；长论文按相邻章节构造内容包，并保留章节之间的
    依赖关系，保证三个 Specialist 获得一致的全局论文档案。
    """

    def __init__(
        self,
        model_client: ModelClient | None = None,
        *,
        full_text_limit: int = 20_000,
    ) -> None:
        self.model_client = model_client
        self.full_text_limit = full_text_limit

    def build(self, review_input: DebateReviewInput) -> ReviewContext:
        profile = PaperProfile(
            title=review_input.title,
            paper_type=review_input.paper_type,
            research_problem=review_input.abstract or review_input.title,
            claimed_contributions=(
                [review_input.abstract] if review_input.abstract else []
            ),
            global_summary=review_input.abstract or review_input.title,
            chapter_relationships=[
                f"{left.chapter_name} → {right.chapter_name}"
                for left, right in zip(
                    review_input.chapters,
                    review_input.chapters[1:],
                    strict=False,
                )
            ],
        )
        if len(review_input.full_text) <= self.full_text_limit:
            return ReviewContext(
                paper_id=review_input.paper_id,
                profile=profile,
                full_text=review_input.full_text,
                content_packets=[],
                chapters=review_input.chapters,
                step3_advice=review_input.step3_advice,
                structured_document=review_input.structured_document,
                metadata=review_input.metadata,
            )

        packets: list[ContentPacket] = []
        for index in range(0, len(review_input.chapters), 2):
            group = review_input.chapters[index : index + 2]
            packets.append(
                ContentPacket(
                    packet_id=f"PACKET-{index // 2 + 1}",
                    chapter_ids=[chapter.chapter_id for chapter in group],
                    purpose="保留相邻章节之间的方法、实验或结论关系",
                    content="\n\n".join(chapter.content for chapter in group),
                    dependency_packet_ids=(
                        [f"PACKET-{index // 2}"] if index >= 2 else []
                    ),
                )
            )
        return ReviewContext(
            paper_id=review_input.paper_id,
            profile=profile,
            full_text=None,
            content_packets=packets,
            chapters=review_input.chapters,
            step3_advice=review_input.step3_advice,
            structured_document=review_input.structured_document,
            metadata=review_input.metadata,
        )
