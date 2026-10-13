"""SQLite 存储层：六张表（技术方案 2.5）。零运维，单机直连。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date
from pathlib import Path

from ..models import Question, QuestionType, SourceQuality

SCHEMA = """
CREATE TABLE IF NOT EXISTS banks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_file TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    question_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_id TEXT NOT NULL,
    q_no INTEGER,
    seq INTEGER NOT NULL DEFAULT 0,
    type TEXT NOT NULL,
    stem TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '[]',
    answer TEXT,
    analysis TEXT,
    chapter TEXT,
    page_ref INTEGER,
    source_quality TEXT NOT NULL DEFAULT 'rule',
    content_hash TEXT NOT NULL,
    UNIQUE(bank_id, seq)
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    user_answer TEXT,
    correct INTEGER,               -- 0/1；自评题为 NULL
    self_rating TEXT,              -- 自评题的 'known'/'unknown'
    latency_ms INTEGER,
    mode TEXT NOT NULL DEFAULT 'practice',
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule (
    question_id INTEGER PRIMARY KEY,
    box INTEGER NOT NULL DEFAULT 1,
    due_date TEXT NOT NULL,
    last_result TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wrongbook (
    question_id INTEGER PRIMARY KEY,
    times_wrong INTEGER NOT NULL DEFAULT 1,
    times_right INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_id TEXT NOT NULL,
    q_no INTEGER,
    raw_block TEXT,
    fail_reason TEXT,
    stage TEXT,
    page_ref INTEGER
);
-- M2 产品度量：练习会话（完成率 / 放弃率的分母与分子）
CREATE TABLE IF NOT EXISTS practice_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_id TEXT NOT NULL,
    planned_n INTEGER NOT NULL DEFAULT 0,
    answered_n INTEGER NOT NULL DEFAULT 0,
    correct_n INTEGER NOT NULL DEFAULT 0,
    explain_on INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'in_progress',  -- in_progress / completed / abandoned
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_questions_bank ON questions(bank_id);
CREATE INDEX IF NOT EXISTS idx_schedule_due ON schedule(due_date);
CREATE INDEX IF NOT EXISTS idx_sessions_bank ON practice_sessions(bank_id);
CREATE INDEX IF NOT EXISTS idx_attempts_qid ON attempts(question_id);

-- M3 讲解增强：网页快照（防引用失效）+ 讲解记录
CREATE TABLE IF NOT EXISTS web_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    title TEXT,
    domain TEXT,
    tier INTEGER DEFAULT 3,
    content TEXT,                 -- 抓取/摘要正文（本地留档，用于引用可验证）
    content_hash TEXT,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS explanations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    query TEXT,
    text TEXT NOT NULL,
    citations_json TEXT NOT NULL DEFAULT '[]',
    grounded INTEGER NOT NULL DEFAULT 1,   -- 0 = 无检索支撑（LLM 直讲降级）
    degraded_reason TEXT,
    generated_at TEXT NOT NULL,
    UNIQUE(question_id)
);
"""


def init_db(db_path: str | Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(db_path: str | Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def insert_bank(conn, bank_id: str, name: str, source_file: str, question_count: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO banks (id, name, source_file, question_count, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (bank_id, name, source_file, question_count, now_iso()),
    )


def insert_questions(conn, questions: list[Question]) -> None:
    for q in questions:
        conn.execute(
            "INSERT OR REPLACE INTO questions"
            " (bank_id, q_no, seq, type, stem, options_json, answer, analysis, chapter,"
            "  page_ref, source_quality, content_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                q.bank_id,
                q.q_no,
                q.seq,
                q.type.value,
                q.stem,
                json.dumps([o.model_dump() for o in q.options], ensure_ascii=False),
                q.answer,
                q.analysis,
                q.chapter,
                q.page_ref,
                q.source_quality.value,
                q.content_hash,
            ),
        )


def row_to_question(row: sqlite3.Row) -> Question:
    options = json.loads(row["options_json"])
    return Question(
        bank_id=row["bank_id"],
        q_no=row["q_no"],
        seq=row["seq"],
        type=QuestionType(row["type"]),
        stem=row["stem"],
        options=options,
        answer=row["answer"],
        analysis=row["analysis"],
        chapter=row["chapter"],
        page_ref=row["page_ref"],
        source_quality=SourceQuality(row["source_quality"]),
        content_hash=row["content_hash"],
    )


def insert_quarantine(conn, items) -> None:
    for it in items:
        conn.execute(
            "INSERT INTO quarantine (bank_id, q_no, raw_block, fail_reason, stage, page_ref)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (it.bank_id, it.q_no, it.raw_block, it.fail_reason, it.stage, it.page_ref),
        )


def get_questions(conn, bank_id: str) -> list[tuple[int, Question]]:
    rows = conn.execute(
        "SELECT * FROM questions WHERE bank_id = ? ORDER BY q_no", (bank_id,)
    ).fetchall()
    return [(r["id"], row_to_question(r)) for r in rows]


def today_str() -> str:
    return date.today().isoformat()


# ---------- M3 讲解增强 ----------

def insert_snapshot(conn, url: str, title: str, domain: str, tier: int, content: str) -> int:
    """保存网页内容快照（本地留档，解决'网页会改会删导致引用失效'）。"""
    import hashlib

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    cur = conn.execute(
        "INSERT INTO web_snapshots (url, title, domain, tier, content, content_hash, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (url, title, domain, tier, content, content_hash, now_iso()),
    )
    return int(cur.lastrowid)


def upsert_explanation(conn, question_id: int, query: str, text: str,
                       citations_json: str, grounded: bool,
                       degraded_reason: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO explanations"
        " (question_id, query, text, citations_json, grounded, degraded_reason, generated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (question_id, query, text, citations_json, int(grounded), degraded_reason, now_iso()),
    )


def get_explanation(conn, question_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM explanations WHERE question_id = ?", (question_id,)
    ).fetchone()


# ---------- M2 练习会话（产品度量） ----------

def start_session(conn, bank_id: str, planned_n: int, explain_on: bool = False) -> int:
    """开启一轮练习。完成率的分母就是这里的行数。"""
    cur = conn.execute(
        "INSERT INTO practice_sessions (bank_id, planned_n, explain_on, status, started_at)"
        " VALUES (?, ?, ?, 'in_progress', ?)",
        (bank_id, planned_n, int(explain_on), now_iso()),
    )
    return int(cur.lastrowid)


def record_session_answer(conn, session_id: int, correct: bool | None) -> None:
    """每答一题累加一次；自评题（correct=None）不计入正确数，但仍算已作答。"""
    conn.execute(
        "UPDATE practice_sessions SET answered_n = answered_n + 1,"
        " correct_n = correct_n + ? WHERE id = ?",
        (1 if correct else 0, session_id),
    )


def finish_session(conn, session_id: int, completed: bool) -> None:
    """结束一轮练习：completed=正常答完；False=提前结束（计入放弃）。"""
    conn.execute(
        "UPDATE practice_sessions SET status = ?, finished_at = ? WHERE id = ?",
        ("completed" if completed else "abandoned", now_iso(), session_id),
    )


def get_session(conn, session_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM practice_sessions WHERE id = ?", (session_id,)
    ).fetchone()


def list_banks(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM banks ORDER BY created_at DESC").fetchall()


def get_bank(conn, key: str) -> sqlite3.Row | None:
    """按 bank_id 或名称解析题库（名称可能重复，取最新一个）。"""
    return conn.execute(
        "SELECT * FROM banks WHERE id = ? OR name = ? ORDER BY created_at DESC LIMIT 1",
        (key, key),
    ).fetchone()


def wrongbook_rows(conn, bank_id: str | None = None) -> list[sqlite3.Row]:
    """错题本明细（join 题目），可按题库过滤。"""
    sql = (
        "SELECT w.*, q.bank_id, q.q_no, q.seq, q.type, q.stem, q.options_json,"
        " q.answer, q.analysis, q.page_ref"
        " FROM wrongbook w JOIN questions q ON q.id = w.question_id"
    )
    params: tuple = ()
    if bank_id:
        sql += " WHERE q.bank_id = ?"
        params = (bank_id,)
    sql += " ORDER BY w.status = 'mastered', w.times_wrong DESC, w.added_at DESC"
    return conn.execute(sql, params).fetchall()


def recent_attempts(conn, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT a.*, q.stem, q.type, q.q_no FROM attempts a"
        " JOIN questions q ON q.id = a.question_id"
        " ORDER BY a.id DESC LIMIT ?",
        (limit,),
    ).fetchall()


# ---------- 快捷入口的题集查询（今日待复习 / 错题重练） ----------

def due_question_ids(conn, bank_id: str) -> list[int]:
    """今日到期需要复习的题（Leitner 调度里 due_date <= 今天）。"""
    rows = conn.execute(
        "SELECT s.question_id FROM schedule s JOIN questions q ON q.id = s.question_id"
        " WHERE q.bank_id = ? AND s.due_date <= ?",
        (bank_id, today_str()),
    ).fetchall()
    return [int(r[0]) for r in rows]


def count_new_questions(conn, bank_id: str) -> int:
    """从未作答过的题（schedule 里没有记录）。"""
    row = conn.execute(
        "SELECT COUNT(*) FROM questions q LEFT JOIN schedule s ON s.question_id = q.id"
        " WHERE q.bank_id = ? AND s.question_id IS NULL",
        (bank_id,),
    ).fetchone()
    return int(row[0] or 0)


def wrongbook_question_ids(conn, bank_id: str, status: str | None = None) -> list[int]:
    """错题本题集；status='open' 只取待攻克的，None 取全部。"""
    sql = (
        "SELECT w.question_id FROM wrongbook w JOIN questions q ON q.id = w.question_id"
        " WHERE q.bank_id = ?"
    )
    params: list = [bank_id]
    if status:
        sql += " AND w.status = ?"
        params.append(status)
    return [int(r[0]) for r in conn.execute(sql, params).fetchall()]


def wrongbook_counts(conn, bank_id: str | None = None) -> dict:
    """错题本按状态计数（错题本页头部用）。"""
    sql = "SELECT w.status, COUNT(*) n FROM wrongbook w JOIN questions q ON q.id = w.question_id"
    params: tuple = ()
    if bank_id:
        sql += " WHERE q.bank_id = ?"
        params = (bank_id,)
    sql += " GROUP BY w.status"
    rows = conn.execute(sql, params).fetchall()
    counts = {r["status"]: int(r["n"]) for r in rows}
    return {
        "open": counts.get("open", 0),
        "mastered": counts.get("mastered", 0),
        "total": sum(counts.values()),
    }
