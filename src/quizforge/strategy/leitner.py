"""Leitner 间隔重复调度（技术方案 4.2）。

box 1..6，间隔阶梯 当天/1天/3天/7天/14天/30天；
答对升 box（封顶 6），答错降回 box 1（当天重练）。
选 Leitner 而非 SM-2：参数少、行为可解释、个人规模够用（ADR-5）。
"""

from __future__ import annotations

from datetime import date, timedelta

BOX_INTERVAL_DAYS: dict[int, int] = {1: 0, 2: 1, 3: 3, 4: 7, 5: 14, 6: 30}
MIN_BOX, MAX_BOX = 1, 6


def next_box(box: int, correct: bool) -> int:
    if correct:
        return min(box + 1, MAX_BOX)
    return MIN_BOX  # 答错当天重练


def due_date_for(box: int, today: date | None = None) -> date:
    today = today or date.today()
    return today + timedelta(days=BOX_INTERVAL_DAYS.get(box, 30))


def is_due(due: date, today: date | None = None) -> bool:
    return due <= (today or date.today())
