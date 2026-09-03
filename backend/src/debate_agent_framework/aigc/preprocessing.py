from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Sequence
from typing import Protocol

from ..schemas import DebateReviewInput, StructuredBlock
from .schemas import AigcLocator, AigcSegment

PREPROCESSING_VERSION = "aigc_windows_v1"
_TEXT_TYPES = frozenset({"text", "paragraph", "plain_text"})
_NOISE = re.compile(r"<!--.*?-->|<[^>]+>|!\[[^]]*]\([^)]*\)", re.S)
_SPACE = re.compile(r"[ \t\r\f\v]+")


class TokenCodec(Protocol):
    def encode(self, text: str) -> list[int]: ...

    def decode(self, token_ids: Sequence[int]) -> str: ...


class AigcWindowBuilder:
    def __init__(
        self,
        *,
        target_tokens: int = 384,
        min_tokens: int = 80,
        max_tokens: int = 510,
    ) -> None:
        if not 1 <= min_tokens <= target_tokens <= max_tokens:
            raise ValueError("AIGC window token limits are invalid")
        self.target_tokens = target_tokens
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens

    def build(
        self, review_input: DebateReviewInput, tokenizer: TokenCodec
    ) -> list[AigcSegment]:
        chapter_by_id = {item.chapter_id: item for item in review_input.chapters}
        blocks = self._source_blocks(review_input)
        pieces: list[tuple[str | None, str, str, list[int], AigcLocator]] = []
        for block in blocks:
            chapter = chapter_by_id.get(block.chapter_id or "")
            if chapter is not None and not chapter.reviewable:
                continue
            cleaned = self.clean_text(block.text)
            if not cleaned:
                continue
            token_ids = tokenizer.encode(cleaned)
            for offset in range(0, len(token_ids), self.target_tokens):
                part = token_ids[offset : offset + self.target_tokens]
                if not part:
                    continue
                pieces.append(
                    (
                        block.chapter_id,
                        chapter.chapter_name if chapter else "未知章节",
                        block.block_id,
                        part,
                        AigcLocator(
                            block_id=block.block_id,
                            page_number=block.page_number,
                            bbox=(block.bbox.model_dump() if block.bbox else None),
                        ),
                    )
                )

        groups: list[list[tuple[str | None, str, str, list[int], AigcLocator]]] = []
        current: list[tuple[str | None, str, str, list[int], AigcLocator]] = []
        current_tokens = 0
        for piece in pieces:
            same_chapter = not current or current[0][0] == piece[0]
            if current and (not same_chapter or current_tokens + len(piece[3]) > self.max_tokens):
                groups.append(current)
                current, current_tokens = [], 0
            current.append(piece)
            current_tokens += len(piece[3])
            if current_tokens >= self.target_tokens:
                groups.append(current)
                current, current_tokens = [], 0
        if current:
            if groups and sum(len(item[3]) for item in current) < self.min_tokens and groups[-1][0][0] == current[0][0]:
                previous_size = sum(len(item[3]) for item in groups[-1])
                if previous_size + current_tokens <= self.max_tokens:
                    groups[-1].extend(current)
                else:
                    groups.append(current)
            else:
                groups.append(current)

        segments = []
        for group in groups:
            token_ids = [token for item in group for token in item[3]][: self.max_tokens]
            text = tokenizer.decode(token_ids).strip()
            if not text:
                continue
            block_ids = list(dict.fromkeys(item[2] for item in group))
            digest_source = (
                f"{PREPROCESSING_VERSION}:{group[0][0] or 'unassigned'}:"
                f"{','.join(block_ids)}:{text}"
            )
            digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
            locators = list({item[4].block_id: item[4] for item in group}.values())
            segments.append(
                AigcSegment(
                    segment_id=f"AS-{digest[:20]}",
                    chapter_id=group[0][0],
                    chapter_name=group[0][1],
                    text=text,
                    text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    token_count=len(token_ids),
                    locators=locators,
                )
            )
        return segments

    @staticmethod
    def clean_text(value: str) -> str:
        value = html.unescape(value)
        value = _NOISE.sub(" ", value)
        value = _SPACE.sub(" ", value)
        return "\n".join(line.strip() for line in value.splitlines() if line.strip())

    @staticmethod
    def _source_blocks(review_input: DebateReviewInput) -> list[StructuredBlock]:
        if review_input.structured_document is not None:
            return [
                block
                for block in review_input.structured_document.blocks
                if block.block_type.lower() in _TEXT_TYPES and block.text.strip()
            ]
        blocks = []
        for chapter in review_input.chapters:
            if not chapter.reviewable:
                continue
            digest = hashlib.sha256(chapter.content.encode("utf-8")).hexdigest()
            blocks.append(
                StructuredBlock(
                    block_id=f"AIGC-FALLBACK-{digest[:16]}",
                    chunk_id=f"AIGC-FALLBACK-{digest[:20]}",
                    block_type="text",
                    text=chapter.content,
                    chapter_id=chapter.chapter_id,
                )
            )
        return blocks
