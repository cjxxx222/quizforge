"""三池加权抽题（技术方案 4.1）：到期复习池 / 新题池 / 巩固池。

防剧透（4.3）：抽题只返回 stem + options + page_ref，
answer/analysis 只在判分后返回——由调用方（engine）保证。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .leitner import is_due

DEFAULT_WEIGHTS = {"due": 0.5, "new": 0.3, "consolidation": 0.2}


@dataclass
class QuestionView:
    """抽题视图： intentionally 不含 answer/analysis。"""

    question_id: int
    q_no: int | None
    type: str
    stem: str
    options: list
    page_ref: int | None
    pool: str


def choose_question(
    conn,
    bank_id: str,
    weights: dict | None = None,
    rng: random.Random | None = None,
    exclude: set[int] | None = None,
    restrict_ids: set[int] | None = None,
) -> QuestionView | None:
    """抽一题。

    - exclude     : 本次会话已答过的题，避免同一题连续重复出现；
    - restrict_ids: 只在给定题集内抽题（"今日待复习"限定到期题、"错题重练"限定错题）。
                    传 None 表示不限定；传空集合表示"无题可抽"，直接返回 None。
    """
    weights = weights or DEFAULT_WEIGHTS
    rng = rng or random.Random()
    exclude = exclude or set()

    rows = conn.execute(
        """
        SELECT q.id, q.q_no, q.type, q.stem, q.options_json, q.page_ref,
               s.box, s.due_date
        FROM questions q
        LEFT JOIN schedule s ON s.question_id = q.id
        WHERE q.bank_id = ?
        """,
        (bank_id,),
    ).fetchall()
    rows = [r for r in rows if r["id"] not in exclude]
    if restrict_ids is not None:
        rows = [r for r in rows if r["id"] in restrict_ids]
    if not rows:
        return None

    due_pool, new_pool, cons_pool = [], [], []
    for r in rows:
        if r["due_date"] is None:
            new_pool.append(r)
        elif is_due(_parse_date(r["due_date"])):
            due_pool.append(r)
        elif r["box"] is not None and r["box"] <= 2:
            cons_pool.append(r)

    pick = rng.choices(
        ["due", "new", "consolidation"],
        weights=[weights["due"], weights["new"], weights["consolidation"]],
    )[0]
    row = None
    for pool_name, pool in (("due", due_pool), ("new", new_pool), ("consolidation", cons_pool)):
        if pick == pool_name and pool:
            row = rng.choice(pool)
            break
    if row is None:  # 目标池空则按权重顺序兜底
        for pool in (due_pool, new_pool, cons_pool):
            if pool:
                row = rng.choice(pool)
                break
    if row is None:
        return None

    import json as _json

    return QuestionView(
        question_id=row["id"],
        q_no=row["q_no"],
        type=row["type"],
        stem=row["stem"],
        options=_json.loads(row["options_json"]),
        page_ref=row["page_ref"],
        pool=pick,
    )


def _parse_date(s: str):
    from datetime import date

    return date.fromisoformat(s)
