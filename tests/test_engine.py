"""练习引擎端到端单测：落库 → 抽题 → 判分 → 调度/错题本更新。"""

from __future__ import annotations

import random

from quizforge.models import Option, Question, QuestionType
from quizforge.quiz.engine import PracticeSession
from quizforge.storage import db
from quizforge.strategy import pools


def _seed(conn, bank_id="b_test"):
    db.insert_bank(conn, bank_id, "测试题库", "synthetic", 2)
    qs = [
        Question(bank_id=bank_id, q_no=1, seq=1, type=QuestionType.SINGLE_CHOICE,
                 stem="一个足够长的单选题题干",
                 options=[Option(key=k, text=t) for k, t in zip("AB", ["甲", "乙"])],
                 answer="A"),
        Question(bank_id=bank_id, q_no=2, seq=2, type=QuestionType.TRUE_FALSE,
                 stem="一个足够长的判断题题干", answer="true"),
    ]
    db.insert_questions(conn, qs)
    return [r["id"] for r in conn.execute("SELECT id FROM questions").fetchall()]


def test_practice_loop(tmp_path):
    random.seed(0)  # 固定抽题随机序列，保证两步抽到不同题
    db_path = tmp_path / "t.db"
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        _seed(conn)
        # 首轮：新题（无 schedule 记录）
        view = pools.choose_question(conn, "b_test")
        assert view is not None and view.pool in ("new", "due", "consolidation")

        session = PracticeSession(conn, "b_test")
        v1 = session.next_question()
        assert v1.stem and not hasattr(v1, "answer")  # 防剧透契约

        # 答错 → 应进错题本 + box 归 1 + due=今天
        result, _ = session.submit(v1, "B" if v1.type == "single_choice" else "错")
        assert result.correct is False
        row = conn.execute("SELECT * FROM schedule WHERE question_id=?",
                           (v1.question_id,)).fetchone()
        assert row["box"] == 1 and row["due_date"] == db.today_str()
        assert conn.execute("SELECT times_wrong FROM wrongbook WHERE question_id=?",
                            (v1.question_id,)).fetchone()["times_wrong"] == 1

        # 另取一题（确定性构造视图，避开随机抽到同一题）
        other = conn.execute(
            "SELECT id FROM questions WHERE id != ? ORDER BY id", (v1.question_id,)
        ).fetchone()
        import json as _json

        row = conn.execute("SELECT * FROM questions WHERE id = ?", (other["id"],)).fetchone()
        v2 = pools.QuestionView(
            question_id=row["id"], q_no=row["q_no"], type=row["type"], stem=row["stem"],
            options=_json.loads(row["options_json"]), page_ref=row["page_ref"], pool="new",
        )
        result2, _ = session.submit(v2, "A" if v2.type == "single_choice" else "对")
        assert result2.correct is True
        row2 = conn.execute("SELECT box FROM schedule WHERE question_id=?",
                            (v2.question_id,)).fetchone()
        assert row2["box"] == 2

        stats = session.stats()
        assert stats["total"] == 2 and stats["today_attempts"] == 2 and stats["wrong_open"] == 1


def test_restrict_ids_limits_draw(tmp_path):
    """限定题集抽题：只在给定题集内抽（「今日待复习」与「错题重练」的底层能力）。"""
    db_path = tmp_path / "t.db"
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        qids = _seed(conn)
        session = PracticeSession(conn, "b_test", restrict_ids={qids[0]})
        for _ in range(5):  # 反复抽也只可能抽到限定题
            view = session.next_question()
            assert view is not None and view.question_id == qids[0]


def test_restrict_ids_empty_means_no_question(tmp_path):
    """空题集必须返回 None，而不是"没有限定"——否则"没有到期题"会变成抽全库。"""
    db_path = tmp_path / "t.db"
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        _seed(conn)
        session = PracticeSession(conn, "b_test", restrict_ids=set())
        assert session.next_question() is None
