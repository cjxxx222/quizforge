"""M3 讲解增强单测（全离线：假搜索源 + 假 LLM，不依赖网络）。"""

from __future__ import annotations

import json

from quizforge.explain import whitelist
from quizforge.explain.models import Explanation, SearchResult
from quizforge.explain.pipeline import explain_question
from quizforge.explain.query import build_query
from quizforge.models import Option, Question, QuestionType
from quizforge.storage import db


class FakeProvider:
    name = "fake"

    def __init__(self, results=None, available=True, raise_on_search=False):
        self._results = results or []
        self._available = available
        self._raise = raise_on_search

    @property
    def available(self) -> bool:
        return self._available

    def search(self, query: str, count: int = 8) -> list[SearchResult]:
        if self._raise:
            raise RuntimeError("模拟检索失败")
        return self._results


class FakeLLM:
    available = True

    def __init__(self, reply: str = "考点：xxx[1]\n讲解：yyy[2]"):
        self.reply = reply
        self.last_system = ""

    def chat(self, system: str, user: str, json_mode: bool = False, max_tokens: int = 800) -> str:
        self.last_system = system
        return self.reply


def _question() -> Question:
    return Question(
        bank_id="b", q_no=4, type=QuestionType.SINGLE_CHOICE,
        stem="关于《示例文言篇目》的文体说法不正确的是",
        options=[Option(key=k, text=t) for k, t in zip("AB", ["甲", "乙"])],
        answer="D",
    )


# ---------- 查询构造 ----------

def test_build_query_strips_boilerplate():
    q = _question()
    query = build_query(q)
    assert "不正确的是" not in query
    assert "《示例文言篇目》" in query  # 书名号实体被保留


def test_build_query_handles_plain_stem():
    q = Question(bank_id="b", q_no=1, type=QuestionType.FILL_BLANK,
                 stem="____的教育思想是：有教无类")
    assert "有教无类" in build_query(q)


# ---------- 来源白名单 ----------

def test_classify_tiers():
    assert whitelist.classify("www.zxxk.com") == 2          # 学科网 → 教学资源
    assert whitelist.classify("www.pku.edu.cn") == 1        # 教育机构 → 权威
    assert whitelist.classify("baijiahao.baidu.com") == 3   # 内容农场 → 其他
    assert whitelist.classify("someblog.com") == 3


def test_rank_prefers_authoritative_and_caps_per_domain():
    results = [
        SearchResult(title="博客", url="https://blog.com/a", summary="x" * 100),
        SearchResult(title="学案", url="https://www.zxxk.com/a", summary="y" * 100),
        SearchResult(title="学案2", url="https://www.zxxk.com/b", summary="y" * 100),
        SearchResult(title="学案3", url="https://www.zxxk.com/c", summary="y" * 100),
        SearchResult(title="百科", url="https://baike.baidu.com/item/x", summary="z" * 100),
    ]
    ranked = whitelist.rank(results, query="示例篇目", top_k=5)
    assert ranked[0].tier == 1                      # 百科优先
    domains = [r.domain for r in ranked]
    assert domains.count("www.zxxk.com") <= 2       # 同域名最多 2 条


# ---------- 讲解管道 ----------

def _seeded_conn(tmp_path):
    import sqlite3

    db_path = tmp_path / "t.db"
    db.init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    db.insert_bank(conn, "b", "测试题库", "synthetic", 1)
    db.insert_questions(conn, [_question()])
    conn.commit()
    qid = conn.execute("SELECT id FROM questions").fetchone()["id"]
    return conn, qid


def test_explain_grounded_creates_citations_and_snapshots(tmp_path):
    conn, qid = _seeded_conn(tmp_path)
    provider = FakeProvider([
        SearchResult(title="学案", url="https://www.zxxk.com/a", summary="逸事状是行状的变体" * 5),
        SearchResult(title="百科", url="https://baike.baidu.com/item/逸事状", summary="状的一种" * 10),
    ])
    llm = FakeLLM("考点：文体辨析[1]\n讲解：逸事状只记逸事[2]")
    exp = explain_question(conn, qid, _question(), llm, provider)

    assert exp.grounded is True
    assert len(exp.citations) == 2
    assert all(c.snapshot_id for c in exp.citations)          # 快照已落库
    assert "只能依据" in llm.last_system or "只允许基于给定资料" in llm.last_system
    snapshots = conn.execute("SELECT COUNT(*) c FROM web_snapshots").fetchone()["c"]
    assert snapshots == 2
    row = db.get_explanation(conn, qid)
    assert row is not None and json.loads(row["citations_json"])


def test_explain_degrades_without_provider(tmp_path):
    conn, qid = _seeded_conn(tmp_path)
    llm = FakeLLM("以下内容来自模型知识，非教材原文，请以教材为准\n考点：xxx")
    exp = explain_question(conn, qid, _question(), llm, FakeProvider(available=False))
    assert exp.grounded is False
    assert exp.citations == []
    assert exp.degraded_reason
    assert "没有可用的检索资料" in llm.last_system


def test_explain_degrades_on_search_error(tmp_path):
    conn, qid = _seeded_conn(tmp_path)
    llm = FakeLLM("以下内容来自模型知识…")
    exp = explain_question(conn, qid, _question(), llm, FakeProvider(raise_on_search=True))
    assert exp.grounded is False
    assert "检索失败" in (exp.degraded_reason or "")


def test_explain_flags_missing_citation_markers(tmp_path):
    conn, qid = _seeded_conn(tmp_path)
    provider = FakeProvider([
        SearchResult(title="学案", url="https://www.zxxk.com/a", summary="内容" * 30),
    ])
    llm = FakeLLM("讲解：完全没标注引用编号的一段话")
    exp = explain_question(conn, qid, _question(), llm, provider)
    assert exp.degraded_reason == "模型未标注引用编号"


def test_explanation_model_defaults():
    exp = Explanation(question_id=1, query="q", text="t")
    assert exp.grounded is True and exp.citations == []
