"""Normalize MinerU ``content_list.json`` and align blocks with Markdown chapters."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..schemas import (
    BoundingBox,
    DebateReviewInput,
    ParseQuality,
    ParseQualityStatus,
    StructuredBlock,
    StructuredPaperDocument,
)

_BLOCK_KEYS = {
    "type", "block_type", "category", "text", "content", "bbox", "poly",
    "img_path", "image_path", "latex", "table_body", "text_level",
}
_PAGE_KEYS = ("page_idx", "page_index", "page_number", "page_no", "page_id")
_TYPE_ALIASES = {
    "title": "heading", "header": "heading", "section_title": "heading",
    "interline_equation": "formula", "inline_equation": "formula",
    "equation": "formula", "image": "figure", "img": "figure",
    "table_caption": "table", "image_caption": "figure",
}


class MinerUContentListAdapter:
    """Tolerant adapter for flat and page-nested MinerU content-list variants."""

    def enrich(
        self, review_input: DebateReviewInput, content_list_path: str | Path
    ) -> DebateReviewInput:
        path = Path(content_list_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取 MinerU content_list.json：{exc}") from exc
        return self.enrich_data(review_input, payload)

    def enrich_data(
        self, review_input: DebateReviewInput, payload: Any
    ) -> DebateReviewInput:
        raw_blocks = list(self._walk(payload))
        normalized: list[StructuredBlock] = []
        digest_counts: dict[str, int] = {}
        current_chapter_id: str | None = None
        chapter_by_id = {item.chapter_id: item for item in review_input.chapters}
        chapter_markers = {
            item.chapter_id: _normalize(item.chapter_name)
            for item in review_input.chapters
        }

        for ordinal, (raw, inherited_page) in enumerate(raw_blocks):
            text = self._text(raw)
            block_type = self._block_type(raw)
            page_number = self._page_number(raw, inherited_page)
            bbox = self._bbox(raw.get("bbox") or raw.get("poly"))
            heading_level = self._heading_level(raw, block_type)

            if text and (block_type == "heading" or heading_level is not None):
                matched = self._match_heading(text, chapter_markers)
                if matched:
                    current_chapter_id = matched
            chapter_id = current_chapter_id
            if chapter_id is None and text:
                chapter_id = self._match_content(text, chapter_by_id)

            asset_path = self._first_string(
                raw, "img_path", "image_path", "asset_path"
            )
            latex = self._first_string(raw, "latex", "equation", "formula")
            locator = json.dumps(
                [page_number, bbox.model_dump() if bbox else None,
                 block_type, text, asset_path, latex],
                ensure_ascii=False,
                sort_keys=True,
            )
            identity_digest = hashlib.sha256(locator.encode("utf-8")).hexdigest()
            occurrence = digest_counts.get(identity_digest, 0)
            digest_counts[identity_digest] = occurrence + 1
            digest = hashlib.sha256(
                f"{identity_digest}:{occurrence}".encode("ascii")
            ).hexdigest()
            normalized.append(
                StructuredBlock(
                    block_id=f"B-{digest[:16]}",
                    chunk_id=f"CHUNK-{digest[:20]}",
                    block_type=block_type,
                    text=text,
                    page_number=page_number,
                    bbox=bbox,
                    asset_path=asset_path,
                    latex=latex,
                    heading_level=heading_level,
                    chapter_id=chapter_id,
                    metadata={"source_index": str(ordinal)},
                )
            )

        quality = self._quality(normalized)
        page_count = max(
            (block.page_number or 0 for block in normalized), default=0
        )
        document = StructuredPaperDocument(
            blocks=normalized,
            page_count=page_count,
            quality=quality,
        )
        chapters = []
        for chapter in review_input.chapters:
            related = [block for block in normalized if block.chapter_id == chapter.chapter_id]
            pages = [block.page_number for block in related if block.page_number]
            metadata = dict(chapter.metadata)
            if pages:
                metadata.update(
                    {"page_start": str(min(pages)), "page_end": str(max(pages))}
                )
            metadata["structured_block_count"] = str(len(related))
            chapters.append(
                chapter.model_copy(
                    update={
                        "block_ids": [block.block_id for block in related],
                        "metadata": metadata,
                    }
                )
            )
        metadata = dict(review_input.metadata)
        metadata.update(
            {
                "ingestion": "mineru_markdown+content_list",
                "content_list_rule_version": "mineru_content_list_v1",
                "parse_quality_status": quality.status.value,
                "parse_quality_score": f"{quality.score:.4f}",
            }
        )
        if quality.status is ParseQualityStatus.LOW:
            metadata["requires_parse_review"] = "true"
        return review_input.model_copy(
            update={
                "chapters": chapters,
                "structured_document": document,
                "metadata": metadata,
            }
        )

    def _walk(
        self, value: Any, inherited_page: int | None = None
    ) -> Iterable[tuple[dict[str, Any], int | None]]:
        if isinstance(value, list):
            for item in value:
                yield from self._walk(item, inherited_page)
            return
        if not isinstance(value, dict):
            return
        page = self._page_number(value, inherited_page)
        if self._is_block(value):
            yield value, page
            return
        for child in value.values():
            if isinstance(child, (list, dict)):
                yield from self._walk(child, page)

    @staticmethod
    def _is_block(value: dict[str, Any]) -> bool:
        if not (set(value) & _BLOCK_KEYS):
            return False
        if any(key in value for key in ("type", "block_type", "category", "bbox", "poly", "text_level")):
            return True
        if isinstance(value.get("text"), str):
            return True
        content = value.get("content")
        return isinstance(content, str) or (
            isinstance(content, list)
            and all(not isinstance(item, (dict, list)) for item in content)
        )

    @staticmethod
    def _text(raw: dict[str, Any]) -> str:
        for key in ("text", "content", "table_body"):
            value = raw.get(key)
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, list):
                joined = "\n".join(str(item) for item in value if item)
                if joined.strip():
                    return joined.strip()
        captions = raw.get("img_caption") or raw.get("image_caption")
        if isinstance(captions, list):
            return "\n".join(str(item) for item in captions if item).strip()
        return ""

    @staticmethod
    def _block_type(raw: dict[str, Any]) -> str:
        raw_type = str(
            raw.get("type") or raw.get("block_type") or raw.get("category") or "text"
        ).strip().lower()
        if raw.get("text_level") is not None:
            return "heading"
        return _TYPE_ALIASES.get(raw_type, raw_type or "text")

    @staticmethod
    def _page_number(raw: dict[str, Any], fallback: int | None) -> int | None:
        for key in _PAGE_KEYS:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                if key in {"page_idx", "page_index", "page_id"}:
                    return value + 1
                return max(1, value)
            if isinstance(value, str) and value.isdigit():
                number = int(value)
                return number + 1 if key in {"page_idx", "page_index", "page_id"} else max(1, number)
        return fallback

    @staticmethod
    def _bbox(value: Any) -> BoundingBox | None:
        if isinstance(value, dict):
            keys = ("x0", "y0", "x1", "y1")
            if all(isinstance(value.get(key), (int, float)) for key in keys):
                return BoundingBox(**{key: float(value[key]) for key in keys})
        if isinstance(value, list):
            flat: list[float] = []
            for item in value:
                if isinstance(item, (int, float)):
                    flat.append(float(item))
                elif isinstance(item, list):
                    flat.extend(float(number) for number in item if isinstance(number, (int, float)))
            if len(flat) >= 4:
                if len(flat) >= 8:
                    xs, ys = flat[0::2], flat[1::2]
                    return BoundingBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))
                return BoundingBox(
                    x0=min(flat[0], flat[2]),
                    y0=min(flat[1], flat[3]),
                    x1=max(flat[0], flat[2]),
                    y1=max(flat[1], flat[3]),
                )
        return None

    @staticmethod
    def _heading_level(raw: dict[str, Any], block_type: str) -> int | None:
        value = raw.get("text_level") or raw.get("level")
        if isinstance(value, int) and 1 <= value <= 6:
            return value
        return 1 if block_type == "heading" else None

    @staticmethod
    def _first_string(raw: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _match_heading(text: str, markers: dict[str, str]) -> str | None:
        normalized = _normalize(text)
        matches = [
            chapter_id for chapter_id, marker in markers.items()
            if marker and (marker in normalized or normalized in marker)
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _match_content(text: str, chapters: dict[str, Any]) -> str | None:
        needle = _normalize(text)
        if len(needle) < 12:
            return None
        matches = [
            chapter_id for chapter_id, chapter in chapters.items()
            if needle[:80] in _normalize(chapter.content)
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _quality(blocks: list[StructuredBlock]) -> ParseQuality:
        if not blocks:
            return ParseQuality(
                status=ParseQualityStatus.LOW,
                score=0.0,
                mapped_block_ratio=0.0,
                located_block_ratio=0.0,
                warnings=["content_list.json 中未识别到内容块"],
            )
        meaningful = [block for block in blocks if block.text or block.asset_path or block.latex]
        denominator = len(meaningful) or len(blocks)
        mapped = sum(block.chapter_id is not None for block in meaningful) / denominator
        located = sum(block.page_number is not None for block in meaningful) / denominator
        boxed = sum(block.bbox is not None for block in meaningful) / denominator
        score = round(0.55 * mapped + 0.3 * located + 0.15 * boxed, 4)
        status = (
            ParseQualityStatus.HIGH if score >= 0.8
            else ParseQualityStatus.MEDIUM if score >= 0.55
            else ParseQualityStatus.LOW
        )
        warnings = []
        if mapped < 0.8:
            warnings.append("部分 MinerU 内容块无法稳定映射到章节")
        if located < 0.8:
            warnings.append("部分 MinerU 内容块缺少页码")
        if boxed < 0.5:
            warnings.append("多数 MinerU 内容块缺少坐标，PDF 标注精度受限")
        return ParseQuality(
            status=status,
            score=score,
            mapped_block_ratio=round(mapped, 4),
            located_block_ratio=round(located, 4),
            warnings=warnings,
        )


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())
