"""Review-table LaTeX generation and PDF export tests."""

from __future__ import annotations

from datetime import UTC, datetime

from debate_agent_framework.services.review_table_export import (
    build_review_table_data,
    chapter_advice_from_result,
    latex_escape,
    overall_grade,
    parse_student_meta,
    render_review_table,
)


def test_parse_student_meta() -> None:
    assert parse_student_meta("201300020-吴智超.pdf") == ("201300020", "吴智超")
    assert parse_student_meta("201300020_吴智超.pdf") == ("201300020", "吴智超")
    assert parse_student_meta("thesis.pdf") == ("", "")
    assert parse_student_meta(None) == ("", "")


def test_overall_grade_bands() -> None:
    assert overall_grade(100) == "优秀"
    assert overall_grade(90) == "优秀"
    assert overall_grade(89) == "良好"
    assert overall_grade(75) == "良好"
    assert overall_grade(74) == "合格"
    assert overall_grade(60) == "合格"
    assert overall_grade(59) == "不合格"


def test_chapter_advice_from_result() -> None:
    result = {
        "synthesis": {
            "chapter_evaluation": {
                "chapter_1": {
                    "chapter_data": {
                        "chapter_name": "第一章 绪论",
                        "chapter_remark": "补充问题定义。",
                    }
                },
                "chapter_3": {
                    "chapter_data": {
                        "chapter_name": "第三章 实验",
                        "chapter_remark": "增加消融实验。",
                    }
                },
            }
        }
    }
    assert chapter_advice_from_result(result) == [
        ("第一章 绪论", "补充问题定义。"),
        ("第三章 实验", "增加消融实验。"),
    ]
    assert chapter_advice_from_result({}) == []
    assert chapter_advice_from_result(None) == []


def test_latex_escape() -> None:
    assert latex_escape("100%_ac_cd<&>") == "100\\%\\_ac\\_cd<\\&>"
    assert latex_escape("纯中文&特殊") == "纯中文\\&特殊"


def test_build_review_table_data_maps_scores() -> None:
    data = build_review_table_data(
        paper_title="步态识别系统",
        source_filename="201300020-吴智超.pdf",
        paper_type="工程实现",
        reviewer_name="teacher01",
        section_scores=[3, 2, 1, 0] * 4 + [3, 2],
        total_score=91,
        advice_content="补充实验。",
        chapter_advice=[("第一章", "补充背景")],
        generated_at=datetime(2026, 5, 11, tzinfo=UTC),
    )
    assert data.student_no == "201300020"
    assert data.student_name == "吴智超"
    assert len(data.entries) == 18
    assert [entry.level for entry in data.entries][:4] == [3, 2, 1, 0]
    assert [entry.index for entry in data.entries] == list(range(1, 19))


def test_render_review_table_layout() -> None:
    data = build_review_table_data(
        paper_title="步态识别系统",
        source_filename="201300020-吴智超.pdf",
        paper_type="工程实现",
        reviewer_name="teacher01",
        section_scores=[3] * 18,
        total_score=100,
        advice_content="建议补充对比实验。",
        chapter_advice=[("第一章 绪论", "建议补充研究背景。")],
        generated_at=datetime(2026, 5, 11, tzinfo=UTC),
    )
    tex = render_review_table(data)
    assert "\\documentclass[UTF8]{article}" in tex
    assert "\\usepackage[UTF8]{ctex}" in tex
    assert "\\checkmark" in tex  # 勾选符号
    # 官方模板的四个评议分组
    group_rows = {"论文格式": 6, "论文选题": 3, "论文水平": 2, "论文质量": 3}
    for group, rows in group_rows.items():
        assert f"\\multirow{{{rows}}}{{*}}{{{group}}}" in tex
    assert "请参照评分标准，对论文打分" in tex
    assert "总体评价（给出百分制总评成绩" in tex
    assert "论文修改建议:" in tex
    # 水印页脚
    assert "睿文智评AI预审评估系统通过大语言模型生成" in tex
    # 学生信息与勾选占位符
    assert "\\newcommand{\\studentid}{201300020}" in tex
    assert "\\newcommand{\\studentname}{吴智超}" in tex
    assert "\\newcommand{\\papertitle}{步态识别系统}" in tex
    assert "\\newcommand{\\formatScoreOneA}{\\checkmark}" in tex
    assert "\\newcommand{\\qualityScoreThreeA}{\\checkmark}" in tex
    assert "\\newcommand{\\totalScore}{100}" in tex
    # 建议内容进入 suggestions 占位符
    assert "[第一章 绪论] 建议补充研究背景。" in tex
    assert "建议补充对比实验。" in tex


def test_render_review_table_marks_follow_levels() -> None:
    data = build_review_table_data(
        paper_title="步态识别系统",
        source_filename="201300020-吴智超.pdf",
        paper_type="工程实现",
        reviewer_name="teacher01",
        section_scores=[3, 2, 1, 0] * 4 + [3, 2],
        total_score=88,
        advice_content="",
        chapter_advice=[],
        generated_at=datetime(2026, 5, 11, tzinfo=UTC),
    )
    tex = render_review_table(data)
    assert "\\newcommand{\\formatScoreOneA}{\\checkmark}" in tex  # 3 -> 优秀
    assert "\\newcommand{\\formatScoreTwoB}{\\checkmark}" in tex  # 2 -> 良好
    assert "\\newcommand{\\formatScoreThreeC}{\\checkmark}" in tex  # 1 -> 一般
    assert "\\newcommand{\\formatScoreFourD}{\\checkmark}" in tex  # 0 -> 较差
    assert "\\newcommand{\\levelScoreSixC}{\\checkmark}" in tex
    assert "\\newcommand{\\qualityScoreOneD}{\\checkmark}" in tex
    assert "\\newcommand{\\qualityScoreThreeB}{\\checkmark}" in tex
    assert "\\newcommand{\\suggestions}{}" in tex