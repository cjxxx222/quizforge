"""练习引擎：抽题 → 作答 → 判分 → 反馈 → 更新调度/错题本/流水。"""

from __future__ import annotations

from datetime import date

from ..models import Question
from ..storage import db
from ..strategy import pools
from ..strategy.leitner import due_date_for, next_box
from .grading import GradeResult, grade


class PracticeSession:
    def __init__(self, conn, bank_id: str, weights: dict | None = None,
                 restrict_ids: set[int] | None = None):
        self.conn = conn
        self.bank_id = bank_id
        self.weights = weights
        self.restrict_ids = restrict_ids  # 限定题集（今日待复习 / 错题重练）
        self._answered: set[int] = set()  # 本次会话已答过的题，避免连续重复

    def next_question(self) -> pools.QuestionView | None:
        """抽一题（不含答案与解析——防剧透契约）。

        本次会话已答过的题会被排除；若题库已被答完则回退为允许重复
        （避免"练到一半无题可抽"）。
        """
        view = pools.choose_question(
            self.conn, self.bank_id, self.weights,
            exclude=self._answered, restrict_ids=self.restrict_ids,
        )
        if view is None and self._answered:
            view = pools.choose_question(
                self.conn, self.bank_id, self.weights, restrict_ids=self.restrict_ids
            )
        return view

    def submit(self, view: pools.QuestionView, user_answer: str,
               self_rating: str | None = None) -> tuple[GradeResult, Question]:
        """判分并落库：attempts / schedule / wrongbook。返回判分结果与完整题目。"""
        self._answered.add(view.question_id)
        row = self.conn.execute(
            "SELECT * FROM questions WHERE id = ?", (view.question_id,)
        ).fetchone()
        question = db.row_to_question(row)

        if self_rating in ("known", "unknown"):
            correct = self_rating == "known"
            self.conn.execute(
                "INSERT INTO attempts (question_id, user_answer, correct, self_rating, mode, ts)"
                " VALUES (?, ?, NULL, ?, 'practice', ?)",
                (view.question_id, user_answer, self_rating, db.now_iso()),
            )
        else:
            result = grade(question, user_answer)
            correct = bool(result.correct)
            self.conn.execute(
                "INSERT INTO attempts (question_id, user_answer, correct, mode, ts)"
                " VALUES (?, ?, ?, 'practice', ?)",
                (view.question_id, user_answer, int(correct), db.now_iso()),
            )

        self._update_schedule(view.question_id, correct)
        self._update_wrongbook(view.question_id, correct)

        if self_rating in ("known", "unknown"):
            return GradeResult(correct=correct, needs_self_rating=True), question
        return grade(question, user_answer), question

    def _update_schedule(self, question_id: int, correct: bool) -> None:
        row = self.conn.execute(
            "SELECT box FROM schedule WHERE question_id = ?", (question_id,)
        ).fetchone()
        box = row["box"] if row else 1
        new_box = next_box(box, correct)
        due = due_date_for(new_box).isoformat()
        self.conn.execute(
            "INSERT INTO schedule (question_id, box, due_date, last_result, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(question_id) DO UPDATE SET"
            " box=excluded.box, due_date=excluded.due_date,"
            " last_result=excluded.last_result, updated_at=excluded.updated_at",
            (question_id, new_box, due, "correct" if correct else "wrong", db.now_iso()),
        )

    def _update_wrongbook(self, question_id: int, correct: bool) -> None:
        row = self.conn.execute(
            "SELECT * FROM wrongbook WHERE question_id = ?", (question_id,)
        ).fetchone()
        if row is None:
            if not correct:
                self.conn.execute(
                    "INSERT INTO wrongbook (question_id, times_wrong, added_at)"
                    " VALUES (?, 1, ?)",
                    (question_id, db.now_iso()),
                )
            return
        if correct:
            times_right = row["times_right"] + 1
            status = "mastered" if times_right >= 2 else "open"
            self.conn.execute(
                "UPDATE wrongbook SET times_right = ?, status = ? WHERE question_id = ?",
                (times_right, status, question_id),
            )
        else:
            self.conn.execute(
                "UPDATE wrongbook SET times_wrong = times_wrong + 1, status = 'open'"
                " WHERE question_id = ?",
                (question_id,),
            )

    def stats(self) -> dict:
        today = date.today().isoformat()
        today_attempts = self.conn.execute(
            "SELECT COUNT(*) c FROM attempts WHERE ts LIKE ?", (today + "%",)
        ).fetchone()["c"]
        wrong_open = self.conn.execute(
            "SELECT COUNT(*) c FROM wrongbook WHERE status = 'open'"
        ).fetchone()["c"]
        due = self.conn.execute(
            "SELECT COUNT(*) c FROM schedule WHERE due_date <= ?", (today,)
        ).fetchone()["c"]
        total = self.conn.execute(
            "SELECT COUNT(*) c FROM questions WHERE bank_id = ?", (self.bank_id,)
        ).fetchone()["c"]
        return {"today_attempts": today_attempts, "wrong_open": wrong_open,
                "due": due, "total": total}
