"""18 维评审表 LaTeX 生成与 PDF 编译服务。

复刻 docs/18维评审表模版.pdf 的版式：包含学号/姓名/论文题目信息行、
18 个评议项目（四档勾选）、总体评价百分制与等级、按章节修改建议，
底部落款为“睿文智评 AI 预审评估系统生成”。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..services.review_criteria import REVIEW_CRITERIA

LEVELS_IN_ORDER = ["优秀", "良好", "一般", "较差"]
GRADE_BANDS = [
    (90, "优秀"),
    (75, "良好"),
    (60, "合格"),
]


def overall_grade(total_score: int) -> str:
    for threshold, label in GRADE_BANDS:
        if total_score >= threshold:
            return label
    return "不合格"


def find_tectonic() -> str | None:
    """定位 tectonic 可执行文件。"""
    candidates: list[str] = []
    env = os.getenv("DEBATE_TECTONIC_PATH")
    if env:
        candidates.append(env)
    which = shutil.which("tectonic")
    if which:
        candidates.append(which)
    candidates.extend(
        [
            str(Path.home() / ".local/bin/tectonic"),
            "/usr/local/bin/tectonic",
            "/opt/tectonic/tectonic",
        ]
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def find_xelatex() -> str | None:
    """回退定位 xelatex（TeX Live 环境通常已安装 tectonic 缺失时可用）。"""
    candidates: list[str] = []
    env = os.getenv("DEBATE_LATEX_PATH")
    if env:
        candidates.append(env)
    which = shutil.which("xelatex")
    if which:
        candidates.append(which)
    candidates.extend(
        [
            r"D:\texlive\2026\bin\windows\xelatex.exe",
            "/usr/bin/xelatex",
            "/usr/local/bin/xelatex",
        ]
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


_STUDENT_META_PATTERN = re.compile(
    r"^([0-9A-Za-z_-]+)[-_](.+?)(?:\.[A-Za-z0-9]+)?$"
)


def parse_student_meta(source_filename: str | None) -> tuple[str, str]:
    """从论文文件名解析 学号-姓名，例如 201300020-吴智超.pdf。"""
    if not source_filename:
        return "", ""
    match = _STUDENT_META_PATTERN.match(source_filename.strip())
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def chapter_advice_from_result(result: dict | None) -> list[tuple[str, str]]:
    """从 AI 评审结果中提取按章节的修改建议。"""
    if not result:
        return []
    synthesis = result.get("synthesis") or {}
    evaluation = synthesis.get("chapter_evaluation") or {}
    advice: list[tuple[str, str]] = []
    for envelope in evaluation.values():
        chapter_data = (envelope or {}).get("chapter_data") or {}
        name = chapter_data.get("chapter_name") or ""
        remark = chapter_data.get("chapter_remark") or ""
        if name and remark:
            advice.append((name, remark))
    return advice


_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(text: str) -> str:
    return "".join(_LATEX_SPECIALS.get(char, char) for char in str(text))


@dataclass(frozen=True)
class ReviewTableEntry:
    index: int
    name: str
    description: str
    level: int


@dataclass(frozen=True)
class ReviewTableData:
    paper_title: str
    student_no: str
    student_name: str
    paper_type: str
    reviewer_name: str
    entries: list[ReviewTableEntry]
    total_score: int
    advice_content: str
    chapter_advice: list[tuple[str, str]]
    generated_at: datetime


_SCORE_PREFIXES = [
    # 与官方模板占位符命名保持一致，按条目顺序排列。
    "formatScoreOne",
    "formatScoreTwo",
    "formatScoreThree",
    "formatScoreFour",
    "formatScoreFive",
    "formatScoreSix",
    "topicScoreOne",
    "topicScoreTwo",
    "topicScoreThree",
    "levelScoreOne",
    "levelScoreTwo",
    "levelScoreThree",
    "levelScoreFour",
    "levelScoreFive",
    "levelScoreSix",
    "qualityScoreOne",
    "qualityScoreTwo",
    "qualityScoreThree",
]

_LEVEL_SLOTS = {3: "A", 2: "B", 1: "C", 0: "D"}

_DOCUMENT_TEMPLATE = r"""\documentclass[UTF8]{article}
\usepackage[UTF8]{ctex}
\usepackage{amssymb}
\usepackage{geometry}
\usepackage{array}
\usepackage{multirow}
\usepackage{setspace}
\usepackage{lastpage}
\usepackage{fancyhdr}
\usepackage{graphicx}
\usepackage{ragged2e}
\usepackage{xcolor}
\usepackage{datetime}
\usepackage{advdate}

% ===== 统一命名的占位符 =====
\newcommand{\studentid}{@STUDENT_ID@}  % 学号
\newcommand{\studentname}{@STUDENT_NAME@}  % 姓名
\newcommand{\username}{@STUDENT_NAME@}  % 用户名
\newcommand{\papertitle}{@PAPER_TITLE@}  % 论文标题

@SCORE_COMMANDS@

\newcommand{\totalScore}{@TOTAL_SCORE@}  % 总评分
\newcommand{\suggestions}{@SUGGESTIONS@}  % 修改建议

% 日期
\newcommand{\autoyear}{\the\year}
\newcommand{\automonth}{\ifcase\month\or 01\or 02\or 03\or 04\or 05\or 06\or 07\or 08\or 09\or 10\or 11\or 12\fi}
\newcommand{\autoday}{\ifnum\day<10 0\fi\number\day}
% =========================

% 设置水印样式
\definecolor{watermark}{gray}{0.75}
\newcommand{\watermarktext}{
    \raisebox{1.5\baselineskip}{
        \parbox{\textwidth}{
            \centering
            \textcolor{watermark}{%
                \fontsize{12}{12}\selectfont%
                本报告由睿文智评AI预审评估系统通过大语言模型生成，需人工复核后使用，不能直接作为最终评价\\%
                生成时间为 \today\ \currenttime，用户名为 \username %
            }%
        }%
    }%
}

% 配置页脚
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\fancyfoot[C]{\watermarktext}
\fancyfoot[L]{}
\fancyfoot[R]{}

\newcolumntype{C}[1]{>{\centering\arraybackslash}p{#1}}
\geometry{a4paper, left=0.5cm, right=0.5cm, top=1.5cm, bottom=1.5cm}

\begin{document}

\setlength{\parindent}{0pt}
\setlength{\arrayrulewidth}{0.3pt}
\renewcommand{\arraystretch}{1.2}

% 标题
\begin{center}
    \Large\textbf{人工智能学院本科毕设论文院内预审表}
\end{center}

\vspace*{1cm}

\centering
\setlength{\tabcolsep}{3pt}
\renewcommand{\arraystretch}{1.5}
\begin{tabular}{|@{}>{}p{2.23cm}@{}|p{5.6cm}|p{2.72cm}|p{2.96cm}|@{}*{4}{>{\centering\arraybackslash}p{0.89cm}@{}|}}
    \hline
    学号 & \studentid & 姓名 & \multicolumn{5}{c|}{\studentname} \\
    \hline
    论文题目 & \multicolumn{7}{c|}{\papertitle} \\
    \hline
    \multicolumn{8}{|c|}{请参照评分标准，对论文打分}  \\
    \hline
    评议项目 & \multicolumn{3}{|c|}{评分标准} & 优秀 & 良好 & 一般 & 较差 \\
    \hline
    \multirow{6}{*}{论文格式}
    & \multicolumn{3}{p{11.28cm}|}{顺序：封面-承诺书-摘要（中、英文）-目录-正文-参考文献-致谢-附录完备，承诺书签名} &
    \formatScoreOneA & \formatScoreOneB & \formatScoreOneC & \formatScoreOneD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{摘要内容以300—600字为宜，摘要与关键词应在同一页；

    关键词一般 3—5 个，摘要关键词用"；"分开；

    英文摘要和关键词内容与中文摘要和关键词相一致，其中英文摘要以约 300 个实词为宜} &
    \formatScoreTwoA & \formatScoreTwoB & \formatScoreTwoC & \formatScoreTwoD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{论文目录要求标题层次清晰，目录中的标题要与正文中的标题一致} &
    \formatScoreThreeA & \formatScoreThreeB & \formatScoreThreeC & \formatScoreThreeD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{主体部分一般从引言（绪论）开始，以结论或讨论结束，其中引言（绪论）应独立成章；

    主体部分每一章应另起页，一般不少于 15000 字或相当信息量（包括图表）；

    图表、公式注意格式和编号，表的题目在表的上方，图的题目在图的下方，格式"表/图 1-3"，公式（1-3），表和图按各自顺序分开编号} &
    \formatScoreFourA & \formatScoreFourB & \formatScoreFourC & \formatScoreFourD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{参考文献表应置于正文后并另起页；

    所有被引用文献均要列入参考文献表中；

    引文采用著作-出版年制标注时，参考文献表应按著者字顺和出版年排序；

    文中上标，页面脚注，参考文献格式符合学术标准，左顶格，[1]格式标记} &
    \formatScoreFiveA & \formatScoreFiveB & \formatScoreFiveC & \formatScoreFiveD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{谢辞应以简短的文字表示自己的谢意，内容限一页。} &
    \formatScoreSixA & \formatScoreSixB & \formatScoreSixC & \formatScoreSixD \\
    \hline

    \multirow{3}{*}{论文选题}
    & \multicolumn{3}{p{11.28cm}|}{符合本学科专业培养目标，达到科学研究和实践能力培养的目的} &
    \topicScoreOneA & \topicScoreOneB & \topicScoreOneC & \topicScoreOneD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{满足专业培养方案中对素质、能力和知识结构的要求，工作量适当} &
    \topicScoreTwoA & \topicScoreTwoB & \topicScoreTwoC & \topicScoreTwoD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{选题符合本学科专业的发展，具有一定的科技或应用的参考价值} &
    \topicScoreThreeA & \topicScoreThreeB & \topicScoreThreeC & \topicScoreThreeD \\
    \hline

    \multirow{2}{*}{论文水平}
    & \multicolumn{3}{p{11.28cm}|}{基本掌握检索中外文献资料的方法，对资料进行初步分析、综合、归纳等整理，并能适当应用。} &
    \levelScoreOneA & \levelScoreOneB & \levelScoreOneC & \levelScoreOneD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{能够综合应用所学知识，对课题所研究问题进行分析，研究目标明确，内容具体，且具有一定的深度。} &
    \levelScoreTwoA & \levelScoreTwoB & \levelScoreTwoC & \levelScoreTwoD \\
    \hline
\end{tabular}


\centering
\setlength{\tabcolsep}{3pt}
\renewcommand{\arraystretch}{1.5}
\begin{tabular}{|@{}>{}p{2.23cm}@{}|p{5.6cm}|p{2.72cm}|p{2.96cm}|@{}*{4}{>{\centering\arraybackslash}p{0.89cm}@{}|}}
    \hline
    \multirow{4}{*}{}
    & \multicolumn{3}{p{11.28cm}|}{较熟练运用本专业设计或研究的方法、手段和工具开展课题的设计与研究工作} &
    \levelScoreThreeA & \levelScoreThreeB & \levelScoreThreeC & \levelScoreThreeD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{已基本掌握了专业技能和研究方法，有一定的实践能力和水平} &
    \levelScoreFourA & \levelScoreFourB & \levelScoreFourC & \levelScoreFourD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{(1)熟练使用软件完成论文的录入、排版，质量较高

    (2)能选用专业软件或相应软件进行编程或建模、分析等工作；编程或软件使用水平较高

    (3)外文摘要能概括论文的主要内容，用词较准确，语法较规范；能查阅并引用本专业外文文献
    } &
    \levelScoreFiveA & \levelScoreFiveB & \levelScoreFiveC & \levelScoreFiveD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{(1)论文：基于选题的研究现状，进行科学的分析与综合，提出新问题，探索解决问题的方法、手段有一定的特色或新意；或有新见解
    (2)设计：将专业知识、技能应用于实际问题的解决，方法或思路有一定的特色或创新} &
    \levelScoreSixA & \levelScoreSixB & \levelScoreSixC & \levelScoreSixD \\
    \hline

    \multirow{3}{*}{论文质量}
    & \multicolumn{3}{p{11.28cm}|}{概念清楚，内容正确，数据可靠，论据较充分，论证较严密，结论基本正确} &
    \qualityScoreOneA & \qualityScoreOneB & \qualityScoreOneC & \qualityScoreOneD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{能够完整地反映实际完成的工作，结构较严谨，语言通顺} &
    \qualityScoreTwoA & \qualityScoreTwoB & \qualityScoreTwoC & \qualityScoreTwoD \\
    \cline{2-8}
    & \multicolumn{3}{p{11.28cm}|}{(1)论文：有一定的学术价值

    (2)设计：有实物作品、实际运行的系统或具有一定复杂度的原型系统} &
    \qualityScoreThreeA & \qualityScoreThreeB & \qualityScoreThreeC & \qualityScoreThreeD \\
    \hline

    \hline
    \multicolumn{4}{|p{13.51cm}|}{总体评价（给出百分制总评成绩：100-90为优秀；89-75为良好；74-60为合格；60分以下为不合格）} & \multicolumn{4}{c|}{\totalScore} \\
    \hline
    \multicolumn{8}{|p{17.05cm}|}{
        \begin{minipage}[t][11.24cm][t]{\linewidth}
            论文修改建议:
            \par
            \vspace*{0pt}


            \suggestions
            \vfill
            ~
        \end{minipage}
    } \\
    \hline
    \multicolumn{8}{|r|}{
        \autoyear \hspace{0.5em} 年\hspace{1em}
        \automonth \hspace{0.5em} 月\hspace{1em}
        \autoday \hspace{0.5em} 日\hspace{1em}
    } \\
    \hline
\end{tabular}
\end{document}
"""


def _score_commands(entries: list[ReviewTableEntry]) -> str:
    """按官方模板生成 18 组勾选占位符命令。"""
    ordered = sorted(entries, key=lambda entry: entry.index)
    if len(ordered) != len(_SCORE_PREFIXES):
        raise ValueError(
            f"评审表需要 {len(_SCORE_PREFIXES)} 个评分项，实际收到 {len(ordered)} 个"
        )
    lines: list[str] = []
    for position, entry in enumerate(ordered):
        prefix = _SCORE_PREFIXES[position]
        selected = _LEVEL_SLOTS.get(entry.level)
        for slot in "ABCD":
            value = r"\checkmark" if slot == selected else ""
            lines.append(rf"\newcommand{{\{prefix}{slot}}}{{{value}}}")
    return "\n".join(lines)


def _suggestions_block(data: ReviewTableData) -> str:
    """将按章节建议与综合意见排版为模板 suggestions 内容。"""
    blocks = [
        rf"[{latex_escape(name)}] {latex_escape(remark)}"
        for name, remark in data.chapter_advice
    ]
    if data.advice_content:
        blocks.append(latex_escape(data.advice_content))
    return "".join(r"\par " + block for block in blocks)


def render_review_table(data: ReviewTableData) -> str:
    body = _DOCUMENT_TEMPLATE
    body = body.replace("@STUDENT_ID@", latex_escape(data.student_no))
    body = body.replace("@STUDENT_NAME@", latex_escape(data.student_name))
    body = body.replace("@PAPER_TITLE@", latex_escape(data.paper_title))
    body = body.replace("@SCORE_COMMANDS@", _score_commands(list(data.entries)))
    body = body.replace("@TOTAL_SCORE@", str(int(data.total_score)))
    body = body.replace("@SUGGESTIONS@", _suggestions_block(data))
    return body


def build_review_table_data(
    *,
    paper_title: str,
    source_filename: str | None,
    paper_type: str | None,
    reviewer_name: str,
    section_scores: list[int],
    total_score: int,
    advice_content: str,
    chapter_advice: list[tuple[str, str]],
    generated_at: datetime | None = None,
) -> ReviewTableData:
    student_no, student_name = parse_student_meta(source_filename)
    entries = [
        ReviewTableEntry(
            index=int(item["id"]),
            name=item["name"],
            description=item["description"],
            level=int(section_scores[index]),
        )
        for index, item in enumerate(REVIEW_CRITERIA)
    ]
    return ReviewTableData(
        paper_title=paper_title,
        student_no=student_no,
        student_name=student_name,
        paper_type=paper_type or "",
        reviewer_name=reviewer_name,
        entries=entries,
        total_score=int(total_score),
        advice_content=advice_content,
        chapter_advice=chapter_advice,
        generated_at=generated_at or datetime.now(),
    )


def compile_review_table_pdf(
    *,
    data: ReviewTableData,
    output_dir: str | Path,
    stem: str,
    tectonic_path: str | None = None,
    cache_dir: str | Path | None = None,
) -> Path:
    """将评审表渲染为 LaTeX 并用 tectonic 编译成 PDF。

    返回编译产生的 PDF 路径；同时把生成的 .tex 源文件写入 output_dir。
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    tex_source = render_review_table(data)
    tex_path = output / f"{stem}.tex"
    tex_path.write_text(tex_source, encoding="utf-8")

    executable = tectonic_path or find_tectonic() or find_xelatex()
    if executable is None:
        raise FileNotFoundError(
            "未找到 tectonic/xelatex 编译器，请安装 tectonic 或设置 DEBATE_TECTONIC_PATH"
        )

    env = dict(os.environ)

    pdf_path = output / f"{stem}.pdf"
    if "tectonic" in Path(executable).name.lower():
        if cache_dir:
            env["TECTONIC_CACHE_DIR"] = str(cache_dir)
        env["TECTONIC_KEEP_LOGS"] = "1"
        command = [executable, "-X", "compile", "--outdir", str(output), str(tex_path)]
    else:
        command = [
            executable,
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-output-directory={output}",
            str(tex_path),
        ]
    completed = subprocess.run(
        command,
        cwd=str(output),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if not pdf_path.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        raise RuntimeError(f"LaTeX 编译失败，无法生成评审表 PDF：{detail}")
    return pdf_path
