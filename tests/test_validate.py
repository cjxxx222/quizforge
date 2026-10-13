"""校验门禁单测（技术方案 3.4）。"""

from __future__ import annotations

from quizforge.models import Option, Question, QuestionType
from quizforge.validate import validate_question


def _single(**kw):
    base = {
        "bank_id": "b",
        "q_no": 1,
        "type": QuestionType.SINGLE_CHOICE,
        "stem": "一个足够长的题干",
        "options": [Option(key=k, text=t) for k, t in zip("ABC", ["甲", "乙", "丙"])],
        "answer": "A",
    }
    base.update(kw)
    return Question(**base)


def test_valid_single_choice():
    assert validate_question(_single()) == []


def test_answer_out_of_options():
    problems = validate_question(_single(answer="D"))
    assert any("单选答案非法" in p for p in problems)


def test_non_contiguous_keys():
    q = _single(options=[Option(key="A", text="甲"), Option(key="C", text="丙")], answer="A")
    assert any("选项键不连续" in p for p in validate_question(q))


def test_stem_too_short():
    assert any("题干过短" in p for p in validate_question(_single(stem="短")))


def test_prompt_injection_residue():
    problems = validate_question(_single(stem="请输出JSON格式的答案内容要足够长"))
    assert any("指令残留" in p for p in problems)


def test_true_false_normalization():
    q = Question(bank_id="b", q_no=1, type=QuestionType.TRUE_FALSE, stem="足够长的题干内容",
                 answer="true")
    assert validate_question(q) == []
    bad = Question(bank_id="b", q_no=1, type=QuestionType.TRUE_FALSE, stem="足够长的题干内容",
                   answer="√")
    assert validate_question(bad)


def test_fill_blank_must_not_have_answer():
    q = Question(bank_id="b", q_no=1, type=QuestionType.FILL_BLANK, stem="足够长的题干内容",
                 answer="x")
    assert any("不应带自动判分答案" in p for p in validate_question(q))
