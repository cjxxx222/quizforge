"""Leitner 调度单测（技术方案 4.2）。"""

from __future__ import annotations

from datetime import date

from quizforge.strategy.leitner import (
    BOX_INTERVAL_DAYS,
    MAX_BOX,
    MIN_BOX,
    due_date_for,
    is_due,
    next_box,
)


def test_correct_promotes_wrong_resets():
    assert next_box(1, True) == 2
    assert next_box(5, True) == 6
    assert next_box(5, True) == MAX_BOX  # 封顶
    assert next_box(6, True) == MAX_BOX
    assert next_box(4, False) == MIN_BOX  # 答错当天重练


def test_due_dates_follow_ladder():
    today = date(2026, 10, 13)
    assert due_date_for(1, today) == today
    assert due_date_for(2, today) == date(2026, 10, 14)
    assert due_date_for(6, today) == date(2026, 11, 12)  # 30 天
    assert BOX_INTERVAL_DAYS[1] == 0


def test_is_due():
    today = date(2026, 10, 13)
    assert is_due(today, today) is True
    assert is_due(date(2026, 10, 12), today) is True
    assert is_due(date(2026, 10, 14), today) is False
