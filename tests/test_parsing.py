"""解析管道单测：分节 / 切分 / 字段识别（规则路径）。"""

from __future__ import annotations

from quizforge.models import QuestionType
from quizforge.parsing import fields, sectioning, splitting


def _blocks_by_section(sample_lines):
    """按题型分节分组题块（题号按节重号，不能按 q_no 选块）。"""
    sections = sectioning.find_sections(sample_lines)
    blocks = splitting.split_blocks(sample_lines)
    groups: dict[int, list] = {}
    for b in blocks:
        idx = 0
        for i, s in enumerate(sections):
            if s.start_idx <= b.start_idx:
                idx = i
            else:
                break
        groups.setdefault(idx, []).append(b)
    return groups


def test_find_sections(sample_lines):
    sections = sectioning.find_sections(sample_lines)
    types = [s.qtype for s in sections]
    assert types == [QuestionType.FILL_BLANK, QuestionType.SINGLE_CHOICE, QuestionType.TRUE_FALSE]


def test_split_blocks_and_gaps(sample_lines):
    blocks = splitting.split_blocks(sample_lines)
    # 2 填空 + 3 单选（题号 1/3/4，缺 2）+ 3 判断
    assert len(blocks) == 8
    # 题号按节重号：填空与单选的第 1 题 q_no 都是 1
    assert blocks[0].q_no == 1 and blocks[2].q_no == 1
    # 题号前缀已剥离
    assert not blocks[0].text_without_number.startswith("1.")


def test_number_gaps_by_section(sample_lines):
    from quizforge.parsing.pipeline import _number_gaps_by_section

    sections = sectioning.find_sections(sample_lines)
    blocks = splitting.split_blocks(sample_lines)
    gaps = _number_gaps_by_section(blocks, sections)
    assert any("单选" in g and "2" in g for g in gaps), gaps


def test_type_for_range(sample_lines):
    sections = sectioning.find_sections(sample_lines)
    blocks = splitting.split_blocks(sample_lines)
    types = [sectioning.type_for_range(sections, b.start_idx, b.end_idx) for b in blocks]
    assert types[0] is QuestionType.FILL_BLANK
    assert types[3] is QuestionType.SINGLE_CHOICE
    assert types[6] is QuestionType.TRUE_FALSE


def test_single_choice_fields(sample_lines):
    groups = _blocks_by_section(sample_lines)
    single = groups[1]  # 单选节：1 / 3 / 4
    q1 = fields.parse_block(single[0].text_without_number, QuestionType.SINGLE_CHOICE, 1)
    assert q1.answer == "B"
    assert [o.key for o in q1.options] == ["A", "B", "C", "D"]
    assert "三原色" in q1.stem and "B" not in q1.stem

    q3 = fields.parse_block(single[1].text_without_number, QuestionType.SINGLE_CHOICE, 3)
    assert q3.answer == "C"
    assert q3.stem.startswith("关于沸腾的说法正确的是")

    q4 = fields.parse_block(single[2].text_without_number, QuestionType.SINGLE_CHOICE, 4)
    assert q4.answer == "A"
    assert q4.stem.startswith("下列物理量中属于守恒量的是")


def test_inline_options_and_analysis(sample_lines):
    """同行多选项（A./B.）+ 选项尾中文夹注解析。"""
    groups = _blocks_by_section(sample_lines)
    single = groups[1]
    block3 = single[1]  # 单选第 3 题（inline 选项）
    q = fields.parse_block(block3.text_without_number, QuestionType.SINGLE_CHOICE, 3)
    assert q.answer == "C"
    assert len(q.options) == 4
    assert q.analysis is not None and "误解" in q.analysis


def test_true_false_fields(sample_lines):
    groups = _blocks_by_section(sample_lines)
    tf = groups[2]  # 判断节 1/2/3
    q1 = fields.parse_block(tf[0].text_without_number, QuestionType.TRUE_FALSE, 1)
    assert q1.answer == "true"
    assert "最大的行星" in q1.stem

    q2 = fields.parse_block(tf[1].text_without_number, QuestionType.TRUE_FALSE, 2)
    assert q2.answer == "false"
    assert q2.analysis and "夹注" in q2.analysis
    # 答案符号与解析不应残留在题干
    assert "×" not in q2.stem


def test_fill_blank_fields(sample_lines):
    groups = _blocks_by_section(sample_lines)
    fb = groups[0][0]  # 填空第 1 题
    q = fields.parse_block(fb.text_without_number, QuestionType.FILL_BLANK, 1)
    assert q.type is QuestionType.FILL_BLANK
    assert q.answer is None
    assert "波粒二象性" in q.stem


def test_option_key_gap_raises():
    bad = "题干内容足够长\nA．选项一\nC．选项三"
    try:
        fields.parse_block(bad, QuestionType.SINGLE_CHOICE, 1)
    except fields.FieldParseError:
        return
    raise AssertionError("选项键不连续应触发 FieldParseError")


def test_answer_signal_guard():
    """源文无答案线索的块不进 LLM 兜底（防止猜答案污染题库）。"""
    from quizforge.parsing.pipeline import _has_answer_signal

    assert _has_answer_signal("下列正确的是(C)\nA.甲 B.乙") is True
    assert _has_answer_signal("下列正确的是: A\nA.甲 B.乙") is True
    assert _has_answer_signal("地球是最大的行星。√") is True
    assert _has_answer_signal("关于元曲,下列陈述中存在错误的一项是( )\nA.甲 B.乙") is False
    assert _has_answer_signal("对自然,自由美事物的追求") is False
