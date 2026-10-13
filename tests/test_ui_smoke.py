"""界面冒烟测试（M2）：用 Streamlit AppTest 真跑一遍页面，确保没有运行时异常。

覆盖：应用启动 → 五个页面逐个渲染 → 今日页三个快捷入口 → 开始一轮练习 → 按钮式作答
→ 判分反馈 → 会话落库 → 错题一键重练 / 今日待复习。

全程离线：不点"生成讲解"，不依赖任何 API key。
抽题是随机的，所以夹具刻意让每道题"点哪个按钮都对/都错"可控，避免测试抖动。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from quizforge.models import Option, Question, QuestionType, SourceQuality
from quizforge.storage import db

APP = Path(__file__).resolve().parents[1] / "app.py"
BANK = "b_ui"
PAGES = ["今日", "练习", "错题本", "学习统计", "题库管理"]


def _choice(qno: int, stem: str, answer: str = "A") -> Question:
    return Question(
        bank_id=BANK, q_no=qno, seq=qno, type=QuestionType.SINGLE_CHOICE,
        stem=stem, answer=answer,
        options=[Option(key="A", text="甲"), Option(key="B", text="乙")],
        source_quality=SourceQuality.RULE,
    )


def _setup(tmp_path, monkeypatch, questions, bank_id=BANK, name="UI测试题库"):
    db_path = tmp_path / f"{bank_id}.db"
    monkeypatch.setenv("QUIZFORGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("QUIZFORGE_DB_PATH", str(db_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        db.insert_bank(conn, bank_id, name, "x.pdf", len(questions))
        db.insert_questions(conn, questions)
    return db_path


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """两道单选、选项与答案完全一致 —— 无论随机抽到哪道，点「A. 甲」都算答对。"""
    return _setup(tmp_path, monkeypatch, [_choice(1, "第一题：甲还是乙？"), _choice(2, "第二题：甲还是乙？")])


@pytest.fixture
def tf_env(tmp_path, monkeypatch):
    """只含判断题的题库，用于覆盖「正确 / 错误」按钮分支。"""
    return _setup(
        tmp_path, monkeypatch,
        [Question(bank_id=BANK, q_no=1, seq=1, type=QuestionType.TRUE_FALSE,
                  stem="地球是方的。", answer="false", source_quality=SourceQuality.RULE)],
    )


def _boot(app_env) -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception, at.exception
    return at


def _nav(at: AppTest, page: str) -> AppTest:
    for r in at.radio:
        if getattr(r, "key", None) == "nav":
            r.set_value(page)
            break
    else:  # pragma: no cover - 兜底
        at.radio[0].set_value(page)
    return at.run()


def _click(at: AppTest, label: str) -> bool:
    for b in at.button:
        if b.label == label:
            b.click()
            return True
    return False


def _markdown_blob(at: AppTest) -> str:
    return " ".join(str(m.value) for m in at.markdown)


# ---------- 基础渲染 ----------

def test_app_boots_without_exception(app_env):
    at = _boot(app_env)
    assert any("QuizForge" in str(m.value) for m in at.sidebar.markdown)


def test_all_pages_render(app_env):
    at = _boot(app_env)
    for page in PAGES:
        _nav(at, page)
        assert not at.exception, f"{page}: {at.exception}"


def test_today_page_has_quick_entries(app_env):
    at = _boot(app_env)
    labels = [b.label for b in at.button]
    assert "开始复习" in labels
    assert "一键重练" in labels
    assert "随机练习" in labels


def test_stats_page_shows_product_metrics(app_env):
    at = _boot(app_env)
    _nav(at, "学习统计")
    # 完成率 / 重做率现在是环形进度（内联 SVG），不是 st.metric
    blob = _markdown_blob(at)
    assert "练习完成率" in blob and "错题重做率" in blob
    assert "错题攻克率" in [m.label for m in at.metric]


def test_sidebar_has_import_entry(app_env):
    at = _boot(app_env)
    assert "＋ 导入新题库 PDF" in [e.label for e in at.expander]
    assert any("不限" in str(m.value) for m in at.sidebar.caption)


def test_answer_sheet_renders(app_env):
    at = _boot(app_env)
    _nav(at, "练习")
    assert _click(at, "开始练习")
    at.run()
    assert "qf-sheet" in _markdown_blob(at), "答题卡未渲染"


# ---------- 练习流程（按钮式作答） ----------

def test_practice_flow_button_answer(app_env):
    at = _boot(app_env)
    _nav(at, "练习")
    assert _click(at, "开始练习"), "找不到「开始练习」按钮"
    at.run()
    assert not at.exception, at.exception

    assert _click(at, "A. 甲"), f"找不到选项按钮：{[b.label for b in at.button]}"
    at.run()
    assert not at.exception, at.exception
    assert any(b.label.startswith(("下一题", "完成本轮")) for b in at.button)
    blob = _markdown_blob(at)
    assert ("答对了" in blob) or ("答错了" in blob), "判分反馈未渲染"

    with db.connect(app_env) as conn:
        row = conn.execute("SELECT * FROM practice_sessions").fetchone()
        assert row is not None
        assert row["answered_n"] == 1
        assert row["correct_n"] == 1  # 夹具保证「A. 甲」就是正确答案


def test_true_false_buttons_render(tf_env):
    at = _boot(tf_env)
    _nav(at, "练习")
    assert _click(at, "开始练习")
    at.run()
    assert not at.exception, at.exception
    labels = [b.label for b in at.button]
    assert "正确" in labels and "错误" in labels, labels


# ---------- 今日页快捷入口 ----------

def test_today_wrong_redo_entry(app_env):
    with db.connect(app_env) as conn:
        qid = conn.execute("SELECT id FROM questions ORDER BY seq").fetchone()["id"]
        conn.execute(
            "INSERT INTO wrongbook (question_id, times_wrong, added_at) VALUES (?, 1, ?)",
            (qid, db.now_iso()),
        )
    at = _boot(app_env)
    assert _click(at, "一键重练"), "找不到「一键重练」按钮"
    at.run()
    assert not at.exception, at.exception
    with db.connect(app_env) as conn:
        row = conn.execute("SELECT * FROM practice_sessions").fetchone()
        assert row is not None
        assert row["planned_n"] == 1  # 只重练那一道错题


def test_today_due_entry_disabled_without_due(app_env):
    at = _boot(app_env)
    due_btn = next(b for b in at.button if b.label == "开始复习")
    assert due_btn.disabled, "无到期题时「开始复习」应禁用"


def test_today_due_entry_starts_session(app_env):
    with db.connect(app_env) as conn:
        qid = conn.execute("SELECT id FROM questions ORDER BY seq").fetchone()["id"]
        conn.execute(
            "INSERT INTO schedule (question_id, box, due_date, last_result, updated_at)"
            " VALUES (?, 1, ?, 'wrong', ?)",
            (qid, db.today_str(), db.now_iso()),
        )
    at = _boot(app_env)
    assert _click(at, "开始复习"), "找不到「开始复习」按钮"
    at.run()
    assert not at.exception, at.exception
    with db.connect(app_env) as conn:
        assert conn.execute("SELECT COUNT(*) FROM practice_sessions").fetchone()[0] == 1
