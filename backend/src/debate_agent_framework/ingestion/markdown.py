"""把 MinerU Markdown 确定性转换为 Debate 工作流输入。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..schemas import ChapterInput, DebateReviewInput, PaperType

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CHAPTER = re.compile(r"^第\s*[一二三四五六七八九十百0-9]+\s*章")
_KEYWORDS = re.compile(r"^(?:关键词|关键字|keywords?)\s*[：:]\s*(.+)$", re.I)
_NON_REVIEWABLE = (
    "摘要",
    "abstract",
    "目录",
    "参考文献",
    "references",
    "致谢",
    "附录",
    "攻读",
    "声明",
)
_GENERIC_COVER_TITLES = {"南京大学", "本科毕业论文", "毕业论文", "学位论文"}


@dataclass(frozen=True)
class MarkdownSection:
    level: int
    title: str
    lines: list[str]

    @property
    def content(self) -> str:
        return "\n".join(self.lines).strip()


class MarkdownPaperParser:
    """面向中文学位论文常见标题结构的无模型解析器。"""

    def parse(
        self,
        markdown: str,
        *,
        paper_type: PaperType | None = None,
        paper_id: str | None = None,
        title: str | None = None,
        source_filename: str | None = None,
        mineru_batch_id: str | None = None,
    ) -> DebateReviewInput:
        text = markdown.strip()
        if not text:
            raise ValueError("MinerU Markdown 不能为空")

        sections = self._sections(text)
        resolved_title = (
            title
            or self._detect_explicit_title(text)
            or self._detect_title(sections, source_filename)
        )
        chapters = self._chapters(sections, text)
        abstract = self._clean_abstract(
            self._extract_named_section(sections, ("摘要", "abstract"))
        )
        keywords = self._extract_keywords(text)
        references_text = self._extract_named_section(sections, ("参考文献", "references"))
        references = [
            line.strip(" -*\t")
            for line in references_text.splitlines()
            if line.strip(" -*\t")
        ]
        resolved_id = paper_id or "paper-" + hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()[:16]
        metadata = {
            "ingestion": "mineru_markdown",
            "paper_type_source": "provided" if paper_type else "auto_pending",
            "chapter_stage_source": "markdown_heuristic",
        }
        if source_filename:
            metadata["source_filename"] = source_filename
        if mineru_batch_id:
            metadata["mineru_batch_id"] = mineru_batch_id

        return DebateReviewInput(
            paper_id=resolved_id,
            title=resolved_title,
            abstract=abstract[:5000],
            keywords=keywords,
            full_text=text,
            paper_type=paper_type,
            chapters=chapters,
            references=references,
            metadata=metadata,
        )

    @staticmethod
    def _sections(markdown: str) -> list[MarkdownSection]:
        sections: list[MarkdownSection] = []
        current_title = "正文"
        current_level = 1
        current_lines: list[str] = []
        for line in markdown.splitlines():
            match = _HEADING.match(line)
            if match:
                if current_lines or sections:
                    sections.append(
                        MarkdownSection(current_level, current_title, current_lines)
                    )
                current_level = len(match.group(1))
                current_title = match.group(2).strip()
                current_lines = []
            else:
                current_lines.append(line)
        sections.append(MarkdownSection(current_level, current_title, current_lines))
        return [section for section in sections if section.title or section.content]

    @staticmethod
    def _detect_title(
        sections: list[MarkdownSection], source_filename: str | None
    ) -> str:
        for section in sections:
            lowered = section.title.lower()
            if (
                section.title != "正文"
                and section.title.replace(" ", "") not in _GENERIC_COVER_TITLES
                and not _CHAPTER.match(section.title)
                and not any(
                marker in lowered for marker in ("摘要", "abstract", "目录")
                )
            ):
                return section.title
        if source_filename:
            return re.sub(r"(?i)\.pdf$", "", source_filename).strip() or "未命名论文"
        return "未命名论文"

    @staticmethod
    def _detect_explicit_title(markdown: str) -> str | None:
        for line in markdown.splitlines()[:80]:
            cleaned = line.strip().lstrip("#").strip()
            match = re.match(r"^题\s*目\s*[：:]\s*(.+)$", cleaned)
            if match and match.group(1).strip():
                return match.group(1).strip()[:120]
        return None

    def _chapters(
        self, sections: list[MarkdownSection], full_text: str
    ) -> list[ChapterInput]:
        chapter_starts = [
            index
            for index, section in enumerate(sections)
            if _CHAPTER.match(section.title)
            or section.title.lower() in {"摘要", "abstract", "参考文献", "references", "致谢"}
        ]
        if not chapter_starts:
            return [
                ChapterInput(
                    chapter_id="C1",
                    chapter_name="正文",
                    stage="正文",
                    content=full_text,
                )
            ]

        chapters: list[ChapterInput] = []
        for number, start in enumerate(chapter_starts, 1):
            end = chapter_starts[number] if number < len(chapter_starts) else len(sections)
            group = sections[start:end]
            lead = group[0]
            content = "\n\n".join(
                f"{'#' * item.level} {item.title}\n{item.content}".strip()
                for item in group
            )
            if not content.strip():
                continue
            chapters.append(
                ChapterInput(
                    chapter_id=f"C{len(chapters) + 1}",
                    chapter_name=lead.title,
                    stage=self._stage(lead.title),
                    content=content,
                    section_titles=[item.title for item in group[1:]],
                    reviewable=not any(
                        marker in lead.title.lower() for marker in _NON_REVIEWABLE
                    ),
                    metadata={"heading_level": str(lead.level)},
                )
            )
        if not any(chapter.reviewable for chapter in chapters):
            chapters[0].reviewable = True
        return chapters

    @staticmethod
    def _stage(title: str) -> str:
        lowered = title.lower()
        mappings = (
            (("摘要", "abstract"), "摘要"),
            (("绪论", "引言", "introduction"), "引言/绪论"),
            (("相关工作", "文献综述", "研究现状"), "相关工作"),
            (("方法", "模型", "算法", "设计"), "方法构建"),
            (("实验", "结果", "分析", "验证"), "实验验证"),
            (("结论", "总结", "展望"), "结论展望"),
            (("参考文献", "references"), "参考文献"),
            (("致谢",), "致谢"),
        )
        for keywords, stage in mappings:
            if any(keyword in lowered for keyword in keywords):
                return stage
        return "正文"

    @staticmethod
    def _extract_named_section(
        sections: list[MarkdownSection], names: tuple[str, ...]
    ) -> str:
        for section in sections:
            lowered = section.title.lower().strip()
            if any(name in lowered for name in names):
                return section.content
        return ""

    @staticmethod
    def _extract_keywords(markdown: str) -> list[str]:
        for line in markdown.splitlines():
            cleaned = line.strip().strip("*_")
            match = _KEYWORDS.match(cleaned)
            if match:
                return [
                    item.strip()
                    for item in re.split(r"[，,；;、]", match.group(1))
                    if item.strip()
                ]
        return []

    @staticmethod
    def _clean_abstract(value: str) -> str:
        lines = []
        for line in value.splitlines():
            if _KEYWORDS.match(line.strip().strip("*_")):
                break
            lines.append(line)
        return "\n".join(lines).strip()
