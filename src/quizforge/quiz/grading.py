"""判分（技术方案 2.4）：纯程序、零 LLM、结果确定（ADR-3）。

- 单选：字母精确匹配；
- 多选：集合相等（全对才对，v1 不支持半对）；
- 判断：true/false 匹配；
- 填空：不自动判分 → 自评（known/unknown）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Question, QuestionType


@dataclass(frozen=True)
class GradeResult:
    correct: bool | None  # None = 需自评
    needs_self_rating: bool
    expected: str | None = None


def grade(question: Question, user_answer: str) -> GradeResult:
    qtype = question.type
    if qtype is QuestionType.FILL_BLANK:
        return GradeResult(correct=None, needs_self_rating=True)
    if qtype is QuestionType.TRUE_FALSE:
        ua = (user_answer or "").strip().lower()
        expected = (question.answer or "").lower()
        normalized = {"对": "true", "错": "false", "正确": "true", "错误": "false",
                      "t": "true", "f": "false", "y": "true", "n": "false",
                      "√": "true", "×": "false"}.get(ua, ua)
        return GradeResult(correct=normalized == expected, needs_self_rating=False,
                           expected=question.answer)
    if qtype is QuestionType.MULTI_CHOICE:
        ua = {x.strip().upper() for x in (user_answer or "") if x.strip()}
        expected = set(question.answer or "")
        return GradeResult(correct=ua == expected, needs_self_rating=False,
                           expected=question.answer)
    # 单选（及兜底按单选处理的选择题）
    ua = (user_answer or "").strip().upper()
    return GradeResult(correct=ua == (question.answer or "").strip().upper(),
                       needs_self_rating=False, expected=question.answer)
