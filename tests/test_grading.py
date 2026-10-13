"""判分单测（ADR-3：判分零 LLM、结果确定）。"""

from __future__ import annotations

from quizforge.models import Option, Question, QuestionType
from quizforge.quiz.grading import grade


def _multi():
    return Question(
        bank_id="b", q_no=9, type=QuestionType.MULTI_CHOICE, stem="足够长的题干",
        options=[Option(key=k, text=t) for k, t in zip("ABC", ["甲", "乙", "丙"])],
        answer="AB",
    )


def test_single_choice_grade(single_choice_question):
    assert grade(single_choice_question, "B").correct is True
    assert grade(single_choice_question, "b").correct is True
    assert grade(single_choice_question, "C").correct is False


def test_true_false_grade_variants(tf_question):
    assert grade(tf_question, "对").correct is True
    assert grade(tf_question, "√").correct is True
    assert grade(tf_question, "错").correct is False


def test_multi_choice_set_equality():
    q = _multi()
    assert grade(q, "AB").correct is True
    assert grade(q, "BA").correct is True  # 顺序无关
    assert grade(q, "A").correct is False  # 不全选即错
    assert grade(q, "ABC").correct is False


def test_fill_blank_requires_self_rating():
    q = Question(bank_id="b", q_no=1, type=QuestionType.FILL_BLANK, stem="足够长的题干")
    result = grade(q, "随便")
    assert result.correct is None
    assert result.needs_self_rating is True
