"""产品度量（M2）：把「用户行为指标」从「模型指标」里独立出来算清楚。

技术方案里的指标（切分完整率 / 规则直出率 / grounded_rate / whitelist_hit_rate）
衡量的是**系统质量**；本模块衡量的是**产品是否有用**：
- 练习完成率：开了几轮、答完几轮 —— 判断练习流程是否劝退；
- 错题重做率 / 攻克率：错题本是否真的被用起来 —— 判断学习闭环是否成立；
- 覆盖率 / 连续学习天数：题库被消化到什么程度、用户是否形成习惯。

这些数字不依赖任何模型调用，纯 SQL 聚合，可离线复现。
"""

from __future__ import annotations

from datetime import date, timedelta


def _scalar(conn, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    value = row[0] if row else 0
    return int(value or 0)


# ---------- 练习完成率 ----------

def practice_completion(conn) -> dict:
    """完成率 = 答完的轮次 / 开始的轮次。

    - completed   : 答满计划题数；
    - abandoned   : 用户主动结束（计入未完成）；
    - in_progress : 中途离开（关掉页面），同样计入未完成 —— 这本身就是完成率要暴露的问题。
    """
    total = _scalar(conn, "SELECT COUNT(*) FROM practice_sessions")
    completed = _scalar(conn, "SELECT COUNT(*) FROM practice_sessions WHERE status = 'completed'")
    abandoned = _scalar(conn, "SELECT COUNT(*) FROM practice_sessions WHERE status = 'abandoned'")
    in_progress = _scalar(conn, "SELECT COUNT(*) FROM practice_sessions WHERE status = 'in_progress'")
    return {
        "total": total,
        "completed": completed,
        "abandoned": abandoned,
        "in_progress": in_progress,
        "rate": completed / total if total else 0.0,
    }


# ---------- 错题重做率 / 攻克率 ----------

def wrong_redo(conn) -> dict:
    """重做率 = 错题本里被再次作答过的题数 / 错题总数（衡量「回头处理」的行为）。

    攻克率 = 已标记 mastered 的题数 / 错题总数（衡量「真的学会了」的结果）。
    """
    total = _scalar(conn, "SELECT COUNT(*) FROM wrongbook")
    redone = _scalar(
        conn,
        "SELECT COUNT(*) FROM wrongbook w"
        " WHERE (SELECT COUNT(*) FROM attempts a WHERE a.question_id = w.question_id) >= 2",
    )
    mastered = _scalar(conn, "SELECT COUNT(*) FROM wrongbook WHERE status = 'mastered'")
    open_n = _scalar(conn, "SELECT COUNT(*) FROM wrongbook WHERE status = 'open'")
    return {
        "total": total,
        "redone": redone,
        "mastered": mastered,
        "open": open_n,
        "redo_rate": redone / total if total else 0.0,
        "mastery_rate": mastered / total if total else 0.0,
    }


# ---------- 正确率 ----------

def accuracy(conn, bank_id: str | None = None) -> dict:
    """总体正确率（自评填空不计入，避免污染分母）。"""
    if bank_id:
        row = conn.execute(
            "SELECT COUNT(*) n, SUM(a.correct) c FROM attempts a"
            " JOIN questions q ON q.id = a.question_id"
            " WHERE a.correct IS NOT NULL AND q.bank_id = ?",
            (bank_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) n, SUM(a.correct) c FROM attempts a WHERE a.correct IS NOT NULL"
        ).fetchone()
    n = int(row["n"] or 0)
    c = int(row["c"] or 0)
    return {"answered": n, "correct": c, "rate": c / n if n else 0.0}


# ---------- 题库覆盖率 ----------

def coverage(conn, bank_id: str | None = None) -> dict:
    """已作答过的题占题库的比例 —— 衡量题库被消化到什么程度。"""
    if bank_id:
        total = _scalar(conn, "SELECT COUNT(*) FROM questions WHERE bank_id = ?", (bank_id,))
        seen = _scalar(
            conn,
            "SELECT COUNT(DISTINCT a.question_id) FROM attempts a"
            " JOIN questions q ON q.id = a.question_id WHERE q.bank_id = ?",
            (bank_id,),
        )
    else:
        total = _scalar(conn, "SELECT COUNT(*) FROM questions")
        seen = _scalar(conn, "SELECT COUNT(DISTINCT question_id) FROM attempts")
    return {"total": total, "seen": seen, "rate": seen / total if total else 0.0}


# ---------- 连续学习天数 ----------

def study_streak(conn) -> int:
    """连续学习天数。今天还没学不算断（从昨天起算），符合学习类产品惯例。"""
    rows = conn.execute(
        "SELECT DISTINCT substr(ts, 1, 10) AS d FROM attempts ORDER BY d DESC"
    ).fetchall()
    days = {r["d"] for r in rows if r["d"]}
    if not days:
        return 0
    today = date.today()
    cursor = today if today.isoformat() in days else today - timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


# ---------- 近 N 天练习曲线 ----------

def daily_activity(conn, days: int = 14) -> list[dict]:
    """近 N 天每日作答量（含 0 值补全，便于直接画图）。"""
    today = date.today()
    start = today - timedelta(days=days - 1)
    rows = conn.execute(
        "SELECT substr(ts, 1, 10) AS d, COUNT(*) AS n,"
        " SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) AS c"
        " FROM attempts WHERE substr(ts, 1, 10) >= ? GROUP BY d",
        (start.isoformat(),),
    ).fetchall()
    bucket = {r["d"]: (int(r["n"] or 0), int(r["c"] or 0)) for r in rows}
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        n, c = bucket.get(d, (0, 0))
        out.append({"date": d, "attempts": n, "correct": c})
    return out


# ---------- 汇总看板 ----------

def dashboard(conn, bank_id: str | None = None) -> dict:
    return {
        "completion": practice_completion(conn),
        "wrong": wrong_redo(conn),
        "accuracy": accuracy(conn, bank_id),
        "coverage": coverage(conn, bank_id),
        "streak": study_streak(conn),
        "daily": daily_activity(conn),
    }
