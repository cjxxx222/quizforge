"""产品度量单测（M2）：练习完成率 / 错题重做率 / 攻克率 / 连续学习天数。

这些指标是"产品是否有用"的判据，口径必须锁死——所以每条都写测试。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from quizforge import metrics
from quizforge.models import Option, Question, QuestionType, SourceQuality
from quizforge.storage import db

BANK = "b_test"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    with db.connect(path) as c:
        db.insert_bank(c, BANK, "测试题库", "x.pdf", 3)
        yield c


def _seed_questions(conn, n=3):
    qs = [
        Question(
            bank_id=BANK, q_no=i + 1, seq=i + 1,
            type=QuestionType.SINGLE_CHOICE, stem=f"题目{i + 1}",
            options=[Option(key="A", text="甲"), Option(key="B", text="乙")],
            answer="A", source_quality=SourceQuality.RULE,
        )
        for i in range(n)
    ]
    db.insert_questions(conn, qs)
    return [r["id"] for r in conn.execute("SELECT id FROM questions ORDER BY seq")]


def _attempt(conn, qid, correct, ts=None):
    conn.execute(
        "INSERT INTO attempts (question_id, user_answer, correct, mode, ts)"
        " VALUES (?, 'A', ?, 'practice', ?)",
        (qid, None if correct is None else int(correct), ts or db.now_iso()),
    )


def _wrong(conn, qid):
    conn.execute(
        "INSERT INTO wrongbook (question_id, times_wrong, added_at) VALUES (?, 1, ?)",
        (qid, db.now_iso()),
    )


# ---------- 练习完成率 ----------

def test_completion_rate_empty(conn):
    assert metrics.practice_completion(conn)["rate"] == 0.0


def test_completion_rate_counts_abandoned_as_incomplete(conn):
    s1 = db.start_session(conn, BANK, 10)
    s2 = db.start_session(conn, BANK, 10)
    s3 = db.start_session(conn, BANK, 10)
    db.finish_session(conn, s1, completed=True)
    db.finish_session(conn, s2, completed=True)
    db.finish_session(conn, s3, completed=False)  # 主动结束

    m = metrics.practice_completion(conn)
    assert m["total"] == 3
    assert m["completed"] == 2
    assert m["abandoned"] == 1
    assert m["rate"] == pytest.approx(2 / 3)


def test_session_answer_counter(conn):
    sid = db.start_session(conn, BANK, 5)
    db.record_session_answer(conn, sid, True)
    db.record_session_answer(conn, sid, False)
    db.record_session_answer(conn, sid, None)  # 自评题
    row = db.get_session(conn, sid)
    assert row["answered_n"] == 3
    assert row["correct_n"] == 1
    assert row["status"] == "in_progress"


# ---------- 错题重做率 / 攻克率 ----------

def test_wrong_redo_rate(conn):
    qids = _seed_questions(conn, 3)
    for qid in qids:
        _wrong(conn, qid)
        _attempt(conn, qid, False)
    # 只有第一题被重做过（第 2 次作答）
    _attempt(conn, qids[0], True)

    m = metrics.wrong_redo(conn)
    assert m["total"] == 3
    assert m["redone"] == 1
    assert m["redo_rate"] == pytest.approx(1 / 3)


def test_wrong_mastery_rate(conn):
    qids = _seed_questions(conn, 2)
    for qid in qids:
        _wrong(conn, qid)
    conn.execute("UPDATE wrongbook SET status = 'mastered' WHERE question_id = ?", (qids[0],))

    m = metrics.wrong_redo(conn)
    assert m["mastered"] == 1
    assert m["open"] == 1
    assert m["mastery_rate"] == pytest.approx(0.5)


# ---------- 正确率（自评题不计入） ----------

def test_accuracy_ignores_self_rated(conn):
    qids = _seed_questions(conn, 3)
    _attempt(conn, qids[0], True)
    _attempt(conn, qids[1], False)
    _attempt(conn, qids[2], None)  # 填空自评：correct=NULL，不应污染分母

    m = metrics.accuracy(conn)
    assert m["answered"] == 2
    assert m["correct"] == 1
    assert m["rate"] == pytest.approx(0.5)


def test_accuracy_scoped_by_bank(conn):
    qids = _seed_questions(conn, 1)
    _attempt(conn, qids[0], True)
    assert metrics.accuracy(conn, "b_other")["answered"] == 0
    assert metrics.accuracy(conn, BANK)["answered"] == 1


# ---------- 连续学习天数 ----------

def test_study_streak_counts_consecutive_days(conn):
    qids = _seed_questions(conn, 1)
    today = date.today()
    _attempt(conn, qids[0], True, ts=today.isoformat() + "T10:00:00+08:00")
    _attempt(conn, qids[0], True, ts=(today - timedelta(days=1)).isoformat() + "T10:00:00+08:00")
    _attempt(conn, qids[0], True, ts=(today - timedelta(days=3)).isoformat() + "T10:00:00+08:00")

    assert metrics.study_streak(conn) == 2  # 第 3 天断档，不算


def test_study_streak_not_broken_if_today_idle(conn):
    qids = _seed_questions(conn, 1)
    yesterday = date.today() - timedelta(days=1)
    _attempt(conn, qids[0], True, ts=yesterday.isoformat() + "T10:00:00+08:00")
    assert metrics.study_streak(conn) == 1  # 今天还没学，不算断


def test_study_streak_empty(conn):
    assert metrics.study_streak(conn) == 0


# ---------- 覆盖率与曲线 ----------

def test_coverage(conn):
    qids = _seed_questions(conn, 4)
    _attempt(conn, qids[0], True)
    _attempt(conn, qids[0], True)  # 重复作答只算一道
    _attempt(conn, qids[1], False)

    m = metrics.coverage(conn, BANK)
    assert m["seen"] == 2
    assert m["total"] == 4
    assert m["rate"] == pytest.approx(0.5)


def test_daily_activity_zero_filled(conn):
    qids = _seed_questions(conn, 1)
    _attempt(conn, qids[0], True)
    daily = metrics.daily_activity(conn, days=7)
    assert len(daily) == 7
    assert daily[-1]["attempts"] == 1  # 今天
    assert daily[0]["attempts"] == 0  # 补 0


# ---------- 快捷入口的题集查询 ----------

def test_count_new_questions(conn):
    qids = _seed_questions(conn, 3)
    assert db.count_new_questions(conn, BANK) == 3
    conn.execute(
        "INSERT INTO schedule (question_id, box, due_date, updated_at) VALUES (?, 1, ?, ?)",
        (qids[0], db.today_str(), db.now_iso()),
    )
    assert db.count_new_questions(conn, BANK) == 2


def test_due_question_ids_only_future_excluded(conn):
    qids = _seed_questions(conn, 3)
    today = date.today()
    conn.execute(
        "INSERT INTO schedule (question_id, box, due_date, updated_at) VALUES (?, 1, ?, ?)",
        (qids[0], today.isoformat(), db.now_iso()),
    )
    conn.execute(
        "INSERT INTO schedule (question_id, box, due_date, updated_at) VALUES (?, 3, ?, ?)",
        (qids[1], (today + timedelta(days=3)).isoformat(), db.now_iso()),
    )
    # 只有到期的那道；未来到期的和没有 schedule 的都不算
    assert db.due_question_ids(conn, BANK) == [qids[0]]


def test_wrongbook_question_ids_and_counts(conn):
    qids = _seed_questions(conn, 3)
    for qid in qids:
        _wrong(conn, qid)
    conn.execute("UPDATE wrongbook SET status = 'mastered' WHERE question_id = ?", (qids[0],))

    assert sorted(db.wrongbook_question_ids(conn, BANK, status="open")) == sorted(qids[1:])
    assert sorted(db.wrongbook_question_ids(conn, BANK)) == sorted(qids)
    assert db.wrongbook_counts(conn, BANK) == {"open": 2, "mastered": 1, "total": 3}
