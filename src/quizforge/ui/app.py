"""M2 刷题界面（Streamlit）：今日 / 练习 / 错题本 / 学习统计 / 题库管理。

设计原则（与技术方案一致）：
- **无服务、无登录**：SQLite 直连，单机可用（ADR-6）；数据不出本机。
- **界面不含业务逻辑**：抽题、判分、调度全部复用 CLI 用的同一套引擎
  （`strategy.pools` / `quiz.engine`），保证"终端能跑"与"界面能跑"是同一份逻辑。
- **题库不写死**：任何题库 PDF 都能导入；界面上的"当前题库"只是数据库里的一行。
- **连接短开短关**：每次交互现开现关，避免 Streamlit 的 rerun 把未提交事务丢掉。
- **状态机放 session_state**：练习跨 rerun 保持；`goto` / `pending_bank` 用于跨页跳转与选中。

启动：`streamlit run app.py`
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path

import streamlit as st

from .. import metrics
from ..config import get_settings
from ..explain.pipeline import explain_question
from ..explain.search import BochaProvider
from ..importer import import_bank
from ..llm.client import LLMClient
from ..quiz.engine import PracticeSession
from ..storage import db
from ..strategy.pools import QuestionView

TYPE_LABEL = {
    "single_choice": "单选",
    "multi_choice": "多选",
    "true_false": "判断",
    "fill_blank": "填空",
    "self_rated": "自评",
}
POOL_LABEL = {"due": "到期复习", "new": "新题", "consolidation": "巩固"}
MODE_LABEL = {"normal": "随机练习", "due": "今日待复习", "wrong": "错题重练"}
MODE_OPTIONS = {v: k for k, v in MODE_LABEL.items()}
MAX_QUICK_N = 20  # 快捷入口单轮上限，避免"待复习 80 题"变成一次做不完的长会话

CSS = """
<style>
:root{
  --qf-ink:#161B26; --qf-muted:#6B7280; --qf-line:#E6E8F0;
  --qf-accent:#4F46E5; --qf-accent-d:#4338CA; --qf-soft:#EEF0FE;
  --qf-bg:#F6F7FB; --qf-ok:#0F6E56; --qf-ok-bg:#E7F6EF; --qf-ok-line:#9FE1CB;
  --qf-bad:#A32D2D; --qf-bad-bg:#FDECEC; --qf-bad-line:#F7C1C1;
}
.block-container{padding-top:2.2rem; padding-bottom:3.2rem; max-width:1080px;}
#MainMenu, footer{visibility:hidden;}
header[data-testid="stHeader"]{background:transparent;}
section[data-testid="stSidebar"]{background:#FBFBFE; border-right:1px solid var(--qf-line);}
section[data-testid="stSidebar"] .stRadio label{padding:5px 8px; border-radius:8px;}

.qf-logo{font-size:1.02rem; font-weight:600; color:#26215C; padding:0 0 2px;}
.qf-logo-sub{font-size:.74rem; color:var(--qf-muted); margin-bottom:12px;}

.qf-hero{background:linear-gradient(135deg,#EEF0FE 0%,#F3F0FF 52%,#E9F3FE 100%);
  border:1px solid #DFDCFA; border-radius:18px; padding:20px 24px; margin-bottom:20px;}
.qf-hero h1{font-size:1.42rem; font-weight:600; margin:0 0 5px; color:#26215C; letter-spacing:-.01em;}
.qf-hero p{margin:0; font-size:.87rem; color:#4E4C6B;}

.qf-card-icon{width:40px; height:40px; border-radius:12px; display:flex;
  align-items:center; justify-content:center; font-size:1.2rem; margin-bottom:10px;}
.qf-i-indigo{background:var(--qf-soft); color:var(--qf-accent);}
.qf-i-rose{background:#FDECEF; color:#C0396A;}
.qf-i-teal{background:#E1F5EE; color:var(--qf-ok);}
.qf-k{font-size:.78rem; color:var(--qf-muted);}
.qf-v{font-size:2.05rem; font-weight:600; line-height:1.12; color:var(--qf-ink); margin:1px 0 2px;}
.qf-s{font-size:.75rem; color:var(--qf-muted); margin-bottom:12px;}

.qf-stem{background:#fff; border:1px solid var(--qf-line); border-left:4px solid var(--qf-accent);
  border-radius:14px; padding:16px 20px; font-size:1.06rem; line-height:1.8;
  color:var(--qf-ink); margin:10px 0 16px;}
.qf-tag{display:inline-block; background:#fff; color:var(--qf-muted);
  border:1px solid var(--qf-line); border-radius:999px; padding:3px 11px;
  font-size:.75rem; margin:0 6px 6px 0;}
.qf-tag-hi{background:var(--qf-soft); border-color:#D9D5FA; color:var(--qf-accent);}
.qf-note{background:#FBFBFE; border:1px solid var(--qf-line); border-radius:12px;
  padding:13px 17px; font-size:.9rem; color:#374151; line-height:1.75;}

.qf-sheet{display:flex; flex-wrap:wrap; gap:7px; margin:10px 0 14px;}
.qf-as{width:29px; height:29px; border-radius:9px; display:flex; align-items:center;
  justify-content:center; font-size:.78rem; font-weight:500;
  border:1px solid var(--qf-line); background:#fff; color:#A2A8B4;}
.qf-as-ok{background:var(--qf-ok-bg); border-color:var(--qf-ok-line); color:var(--qf-ok);}
.qf-as-bad{background:var(--qf-bad-bg); border-color:var(--qf-bad-line); color:var(--qf-bad);}
.qf-as-self{background:#F1F2F7; border-color:#DFE2EB; color:#5B6472;}
.qf-as-cur{background:var(--qf-accent); border-color:var(--qf-accent); color:#fff;
  box-shadow:0 0 0 3px rgba(79,70,229,.16);}

.qf-fb{border-radius:14px; padding:14px 18px; font-size:1rem; font-weight:500; margin:12px 0 14px;}
.qf-fb-ok{background:var(--qf-ok-bg); color:var(--qf-ok); border:1px solid var(--qf-ok-line);}
.qf-fb-bad{background:var(--qf-bad-bg); color:var(--qf-bad); border:1px solid var(--qf-bad-line);}
.qf-fb-neutral{background:#F1F2F7; color:#4B5563; border:1px solid #DFE2EB;}

div[data-testid="stMetric"]{background:#fff; border:1px solid var(--qf-line);
  border-radius:15px; padding:14px 17px; box-shadow:0 1px 3px rgba(16,24,40,.045);}
div[data-testid="stMetricLabel"] p{font-size:.78rem !important; color:var(--qf-muted);}
div[data-testid="stMetricValue"]{font-size:1.5rem; font-weight:600;}

.stButton > button{border-radius:12px; border:1px solid var(--qf-line);
  font-weight:500; padding:.62rem 1.05rem; transition:all .15s ease; background:#fff;}
.stButton > button[kind="secondary"]{text-align:left;}
.stButton > button:hover{border-color:var(--qf-accent); color:var(--qf-accent);
  background:#F8F8FE; transform:translateY(-1px); box-shadow:0 4px 14px rgba(79,70,229,.12);}
.stButton > button[kind="primary"]{background:var(--qf-accent); border-color:var(--qf-accent);
  color:#fff; text-align:center;}
.stButton > button[kind="primary"]:hover{background:var(--qf-accent-d);
  border-color:var(--qf-accent-d); color:#fff; transform:translateY(-1px);
  box-shadow:0 6px 16px rgba(79,70,229,.26);}
.stButton > button:disabled{opacity:.42; transform:none; box-shadow:none;}

div[data-testid="stVerticalBlockBorderWrapper"]{border-radius:16px; border-color:var(--qf-line);
  transition:border-color .15s ease;}
div[data-testid="stExpander"]{border-radius:13px; border-color:var(--qf-line);}
div[data-testid="stProgress"] > div > div > div{background:var(--qf-accent);}
</style>
"""


# ---------------- 基础设施 ----------------

@contextmanager
def _open():
    """短生命周期连接：进入时确保建表，退出时提交并关闭。"""
    settings = get_settings()
    db.init_db(settings.db_path)
    with db.connect(settings.db_path) as conn:
        yield conn


def _llm() -> LLMClient:
    s = get_settings()
    return LLMClient(api_key=s.llm_api_key, base_url=s.llm_base_url, model=s.llm_model)


def _provider() -> BochaProvider:
    s = get_settings()
    return BochaProvider(api_key=s.bocha_api_key, base_url=s.bocha_base_url)


def _hero(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="qf-hero"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def _tag(text: str, hi: bool = False) -> str:
    cls = "qf-tag qf-tag-hi" if hi else "qf-tag"
    return f'<span class="{cls}">{text}</span>'


def _ring(pct: float, caption: str, color: str = "#4F46E5") -> str:
    """环形进度（刷题 App 结果页标配）。纯内联 SVG，不依赖图表库。"""
    r = 40
    circ = 2 * 3.14159265 * r
    ratio = max(0.0, min(1.0, pct))
    dash = ratio * circ
    return (
        '<div style="text-align:center">'
        f'<svg width="112" height="112" viewBox="0 0 100 100" role="img" aria-label="{caption}">'
        f'<circle cx="50" cy="50" r="{r}" fill="none" stroke="#EDEFF5" stroke-width="9"/>'
        f'<circle cx="50" cy="50" r="{r}" fill="none" stroke="{color}" stroke-width="9"'
        f' stroke-linecap="round" stroke-dasharray="{dash:.1f} {circ:.1f}"'
        ' transform="rotate(-90 50 50)"/>'
        '<text x="50" y="51" text-anchor="middle" dominant-baseline="central"'
        f' font-size="21" font-weight="600" fill="#161B26">{ratio * 100:.0f}%</text>'
        "</svg>"
        f'<div style="font-size:.78rem;color:#6B7280;margin-top:4px">{caption}</div>'
        "</div>"
    )


def _restrict_of(ps: dict) -> set[int] | None:
    return set(ps["restrict_ids"]) if ps["restrict_ids"] is not None else None


# ---------------- 练习会话：启动 / 结束 ----------------

def _start_practice(bank, mode: str = "normal", planned_n: int = 10,
                    explain_on: bool = True) -> None:
    """三种入口共用：随机练习 / 今日待复习（限定到期题）/ 错题重练（限定错题）。"""
    restrict: set[int] | None = None
    with _open() as conn:
        if mode == "due":
            ids = db.due_question_ids(conn, bank["id"])
            restrict = set(ids)
            planned_n = max(1, min(len(ids), MAX_QUICK_N))
        elif mode == "wrong":
            ids = db.wrongbook_question_ids(conn, bank["id"], status="open")
            if not ids:  # 待攻克的都清完了，就重练全部错题做巩固
                ids = db.wrongbook_question_ids(conn, bank["id"])
            restrict = set(ids)
            planned_n = max(1, min(len(ids), MAX_QUICK_N))
        sid = db.start_session(conn, bank["id"], planned_n, explain_on)
        view = PracticeSession(conn, bank["id"], restrict_ids=restrict).next_question()

    if view is None:
        st.warning(f"没有可练习的题目（{MODE_LABEL[mode]}）。")
        return

    st.session_state.pop("practice_summary", None)
    st.session_state.practice = {
        "session_id": sid,
        "bank_id": bank["id"],
        "bank_name": bank["name"],
        "mode": mode,
        "planned_n": planned_n,
        "explain_on": explain_on,
        "restrict_ids": sorted(restrict) if restrict is not None else None,
        "idx": 0,
        "answered_ids": [],
        "history": [],
        "answered": 0,
        "correct": 0,
        "current": asdict(view),
        "phase": "question",
        "result": None,
        "explanation": None,
    }
    st.session_state["goto"] = "练习"
    st.rerun()


def _finish(ps: dict, completed: bool) -> None:
    with _open() as conn:
        db.finish_session(conn, ps["session_id"], completed)
    st.session_state.practice_summary = {
        "bank_name": ps["bank_name"],
        "mode": ps["mode"],
        "planned_n": ps["planned_n"],
        "answered": ps["answered"],
        "correct": ps["correct"],
        "history": ps["history"],
        "completed": completed,
    }
    st.session_state.practice = None
    st.rerun()


# ---------------- 页面：今日 ----------------

def page_today(bank) -> None:
    _hero("今日", f"{date.today():%Y 年 %m 月 %d 日} · 先把到期的题过一遍，再攻错题")
    if bank is None:
        st.info("还没有题库 —— 在左侧「＋ 导入新题库」上传一份题库 PDF 就能开始。")
        return

    with _open() as conn:
        due_ids = db.due_question_ids(conn, bank["id"])
        new_n = db.count_new_questions(conn, bank["id"])
        wb = db.wrongbook_counts(conn, bank["id"])
        d = metrics.dashboard(conn, bank["id"])

    st.markdown(f"当前题库：**{bank['name']}** · 共 {bank['question_count']} 题")

    c1, c2, c3 = st.columns(3)
    with c1, st.container(border=True):
        st.markdown(
            '<div class="qf-card-icon qf-i-indigo">🔁</div>'
            f'<div class="qf-k">今日待复习</div><div class="qf-v">{len(due_ids)}</div>'
            '<div class="qf-s">按间隔重复到期，需巩固的题</div>',
            unsafe_allow_html=True,
        )
        if st.button("开始复习", type="primary", width="stretch", key="go_due",
                     disabled=not due_ids):
            _start_practice(bank, mode="due")
        if not due_ids:
            st.caption("暂时没有到期的题")
    with c2, st.container(border=True):
        st.markdown(
            '<div class="qf-card-icon qf-i-rose">📕</div>'
            f'<div class="qf-k">错题待攻克</div><div class="qf-v">{wb["open"]}</div>'
            f'<div class="qf-s">累计 {wb["total"]} 道 · 已攻克 {wb["mastered"]} 道</div>',
            unsafe_allow_html=True,
        )
        if st.button("一键重练", type="primary", width="stretch", key="go_wrong",
                     disabled=wb["total"] == 0):
            _start_practice(bank, mode="wrong")
        if not wb["total"]:
            st.caption("还没有错题，保持住")
    with c3, st.container(border=True):
        st.markdown(
            '<div class="qf-card-icon qf-i-teal">🎲</div>'
            f'<div class="qf-k">题库总题数</div><div class="qf-v">{bank["question_count"]}</div>'
            f'<div class="qf-s">尚未练习 {new_n} 道</div>',
            unsafe_allow_html=True,
        )
        if st.button("随机练习", type="primary", width="stretch", key="go_random"):
            _start_practice(bank, mode="normal", planned_n=10)

    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("今日已作答", d["daily"][-1]["attempts"], f"答对 {d['daily'][-1]['correct']}")
    m2.metric("连续学习", f"{d['streak']} 天", "今天没学不算断")
    m3.metric("练习完成率", f"{d['completion']['rate']:.0%}",
              f"{d['completion']['completed']}/{d['completion']['total']} 轮")
    m4.metric("错题重做率", f"{d['wrong']['redo_rate']:.0%}",
              f"{d['wrong']['redone']}/{d['wrong']['total']} 题")

    _render_daily_chart(d["daily"])


def _render_daily_chart(daily: list[dict]) -> None:
    st.markdown("#### 近 14 天练习量")
    try:
        import pandas as pd

        df = pd.DataFrame(daily).set_index("date")[["attempts", "correct"]]
        df.index = [i[5:] for i in df.index]
        df.columns = ["作答量", "答对"]
        st.bar_chart(df)
    except Exception:  # noqa: BLE001 - 图表失败不影响其余功能
        st.write(daily)


# ---------------- 页面：练习 ----------------

def page_practice(bank) -> None:
    if bank is None:
        st.info("请先在左侧选择一个题库（或导入一份新的）。")
        return

    ps = st.session_state.get("practice")
    if ps is None:
        _hero("练习", "选择模式 → 抽题 → 作答 → 判分 → 解析（答错可生成带引用的讲解）")
        _render_practice_setup(bank)
    else:
        _render_practice_running(ps)

    summary = st.session_state.get("practice_summary")
    if summary is not None:
        st.divider()
        _render_summary(summary)


def _render_practice_setup(bank) -> None:
    with _open() as conn:
        due_n = len(db.due_question_ids(conn, bank["id"]))
        wb = db.wrongbook_counts(conn, bank["id"])

    counts = {"随机练习": bank["question_count"], "今日待复习": due_n,
              "错题重练": wb["open"] or wb["total"]}
    labels = [f"{k}（{v}）" for k, v in counts.items()]
    picked = st.radio("练习模式", labels, horizontal=True, key="setup_mode")
    mode_label = picked.split("（")[0]
    mode = MODE_OPTIONS[mode_label]

    available = counts[mode_label]
    if mode == "normal":
        n = st.slider("本轮题数", min_value=5, max_value=50, value=10, step=5)
    else:
        n = max(1, min(available, MAX_QUICK_N))
        st.caption(f"本轮将复习 {n} 道题" + ("" if available else "（暂无可练习的题）"))

    explain_on = st.toggle(
        "答错后可生成带引用的讲解（M3）", value=True,
        help="调用博查检索 + DeepSeek 生成；未配置 key 时按钮不可用",
    )
    if st.button("开始练习", type="primary", disabled=available == 0, key="start_practice"):
        _start_practice(bank, mode=mode, planned_n=n, explain_on=explain_on)


def _answer_sheet(ps: dict) -> str:
    """答题卡：本轮每题的状态网格（刷题 App 的标志性组件）。"""
    cells = []
    for i in range(ps["planned_n"]):
        if i < len(ps["history"]):
            h = ps["history"][i]
            if h["self_rated"]:
                cls, mark = "qf-as-self", "自"
            elif h["correct"]:
                cls, mark = "qf-as-ok", "✓"
            else:
                cls, mark = "qf-as-bad", "✗"
        elif i == ps["idx"]:
            cls, mark = "qf-as-cur", str(i + 1)
        else:
            cls, mark = "qf-as", str(i + 1)
        cells.append(f'<span class="qf-as {cls}">{mark}</span>')
    return '<div class="qf-sheet">' + "".join(cells) + "</div>"


def _render_practice_running(ps: dict) -> None:
    view = ps["current"]
    st.markdown(
        _tag(MODE_LABEL.get(ps["mode"], ps["mode"]), hi=True)
        + _tag(f"第 {ps['idx'] + 1} / {ps['planned_n']} 题")
        + _tag(TYPE_LABEL.get(view["type"], view["type"]))
        + _tag(f"来自「{POOL_LABEL.get(view['pool'], view['pool'])}」池"),
        unsafe_allow_html=True,
    )
    st.progress(min((ps["idx"] + (1 if ps["phase"] == "feedback" else 0)) / ps["planned_n"], 1.0))
    st.markdown(_answer_sheet(ps), unsafe_allow_html=True)

    if ps["phase"] == "question":
        _render_question(ps)
    else:
        _render_feedback(ps)

    if st.button("结束本轮", key="abort"):
        _finish(ps, completed=False)


def _render_question(ps: dict) -> None:
    view = ps["current"]
    st.markdown(f'<div class="qf-stem">{view["stem"]}</div>', unsafe_allow_html=True)

    qtype = view["type"]
    options = view["options"]
    base = f"{ps['session_id']}_{ps['idx']}"

    if qtype == "fill_blank":
        ans = st.text_input("你的作答（填空）", key=f"fill_{base}")
        if st.button("提交", type="primary", key=f"sub_{base}"):
            if not (ans or "").strip():
                st.warning("先作答再提交；实在想不起来就直接提交，会记为「不会」。")
                return
            _submit(ps, ans, self_rating="known")
    elif qtype == "true_false":
        c1, c2 = st.columns(2)
        with c1:
            if st.button("正确", type="primary", width="stretch", key=f"tf_t_{base}"):
                _submit(ps, "正确")
        with c2:
            if st.button("错误", type="primary", width="stretch", key=f"tf_f_{base}"):
                _submit(ps, "错误")
    elif qtype == "multi_choice":
        picks = st.multiselect("选择答案（可多选）",
                               [o["key"] for o in options], key=f"ms_{base}")
        if st.button("提交", type="primary", key=f"sub_{base}"):
            if not picks:
                st.warning("先作答再提交。")
                return
            _submit(ps, "".join(sorted(picks)))
    else:
        cols = st.columns(2)
        for i, o in enumerate(options):
            with cols[i % 2]:
                if st.button(f"{o['key']}. {o['text']}", width="stretch",
                             key=f"opt_{base}_{o['key']}"):
                    _submit(ps, o["key"])


def _submit(ps: dict, user_answer: str, self_rating: str | None = None) -> None:
    view = QuestionView(**ps["current"])
    with _open() as conn:
        session = PracticeSession(conn, ps["bank_id"], restrict_ids=_restrict_of(ps))
        session._answered = set(ps["answered_ids"])
        result, question = session.submit(view, user_answer, self_rating=self_rating)
        # 自评题（填空）的对错由用户主观给出，不计入会话正确数，与 attempts.correct=NULL 口径一致
        db.record_session_answer(
            conn, ps["session_id"], None if result.needs_self_rating else bool(result.correct)
        )
    ps["result"] = {
        "question_id": view.question_id,
        "seq": question.seq,
        "q_no": question.q_no,
        "type": question.type.value,
        "stem": question.stem,
        "options": [o.model_dump() for o in question.options],
        "answer": question.answer,
        "analysis": question.analysis,
        "correct": result.correct,
        "expected": result.expected,
        "needs_self_rating": result.needs_self_rating,
    }
    ps["history"].append(
        {"correct": result.correct, "self_rated": result.needs_self_rating,
         "type": question.type.value}
    )
    ps["answered"] += 1
    if result.correct:
        ps["correct"] += 1
    ps["phase"] = "feedback"
    st.session_state.practice = ps
    st.rerun()


def _render_feedback(ps: dict) -> None:
    r = ps["result"]
    if r["needs_self_rating"]:
        box = '<div class="qf-fb qf-fb-neutral">已记录 · 填空题按自评计入</div>'
    elif r["correct"]:
        box = '<div class="qf-fb qf-fb-ok">✓ 答对了</div>'
    else:
        box = f'<div class="qf-fb qf-fb-bad">✗ 答错了 · 正确答案 <b>{r["expected"]}</b></div>'
    st.markdown(box, unsafe_allow_html=True)

    st.markdown(f'<div class="qf-stem">{r["stem"]}</div>', unsafe_allow_html=True)
    if r["options"]:
        st.markdown("".join(f"- {o['key']}. {o['text']}" for o in r["options"]))
    if r["analysis"]:
        st.markdown(
            f'<div class="qf-note"><b>题目自带解析</b><br>{r["analysis"]}</div>',
            unsafe_allow_html=True,
        )

    if r["correct"] is False and ps["explain_on"]:
        if ps["explanation"] is None:
            if st.button("生成带引用的讲解（M3）", key=f"exp_{ps['session_id']}_{ps['idx']}"):
                _generate_explanation(ps)
        else:
            _render_explanation(ps["explanation"])

    label = "完成本轮" if ps["idx"] + 1 >= ps["planned_n"] else "下一题 →"
    if st.button(label, type="primary", key=f"next_{ps['session_id']}_{ps['idx']}"):
        _next(ps)


def _generate_explanation(ps: dict) -> None:
    r = ps["result"]
    llm = _llm()
    if not llm.available:
        st.warning("未配置 DeepSeek API key，无法生成讲解。")
        return
    with st.spinner("检索 → 重排 → 生成中…"):
        try:
            with _open() as conn:
                row = conn.execute(
                    "SELECT * FROM questions WHERE id = ?", (r["question_id"],)
                ).fetchone()
                question = db.row_to_question(row)
                exp = explain_question(conn, r["question_id"], question, llm, _provider())
        except Exception as exc:  # noqa: BLE001 - 讲解失败不影响练习
            st.error(f"讲解生成失败：{type(exc).__name__}: {exc}")
            return
    ps["explanation"] = {
        "query": exp.query,
        "text": exp.text,
        "grounded": exp.grounded,
        "degraded_reason": exp.degraded_reason,
        "citations": [c.__dict__ for c in exp.citations],
    }
    st.session_state.practice = ps
    st.rerun()


def _render_explanation(exp: dict) -> None:
    if not exp["grounded"]:
        st.warning(f"⚠ 无检索支撑（{exp['degraded_reason']}）：以下为模型知识，非教材口径")
    with st.container(border=True):
        st.markdown("**讲解**")
        st.markdown(exp["text"])
        if exp["citations"]:
            st.caption(f"检索 query：{exp['query']}")
            with st.expander(f"引用来源（{len(exp['citations'])}）· 已落本地快照，不怕原页失效"):
                for c in exp["citations"]:
                    st.markdown(
                        f"**[{c['index']}]** [{c['title']}]({c['url']})  \n"
                        f"<sub>{c['domain']} · 快照 #{c['snapshot_id']}</sub>",
                        unsafe_allow_html=True,
                    )


def _next(ps: dict) -> None:
    ps["answered_ids"].append(ps["current"]["question_id"])
    ps["idx"] += 1
    ps["result"] = None
    ps["explanation"] = None

    if ps["idx"] >= ps["planned_n"]:
        _finish(ps, completed=True)
        return

    with _open() as conn:
        session = PracticeSession(conn, ps["bank_id"], restrict_ids=_restrict_of(ps))
        session._answered = set(ps["answered_ids"])
        view = session.next_question()
    if view is None:
        _finish(ps, completed=True)
        return
    ps["current"] = asdict(view)
    ps["phase"] = "question"
    st.session_state.practice = ps
    st.rerun()


def _render_summary(summary: dict) -> None:
    hist = summary["history"]
    objective = [h for h in hist if not h["self_rated"]]
    acc = sum(1 for h in objective if h["correct"]) / len(objective) if objective else 0.0

    with st.container(border=True):
        st.markdown(f"**本轮结果 · {MODE_LABEL.get(summary['mode'], summary['mode'])}**")
        left, right = st.columns([1, 2])
        with left:
            st.markdown(_ring(acc, "本轮正确率"), unsafe_allow_html=True)
        with right:
            c1, c2, c3 = st.columns(3)
            c1.metric("作答", f"{summary['answered']} / {summary['planned_n']}")
            c2.metric("答对", f"{summary['correct']}")
            c3.metric("状态", "已完成" if summary["completed"] else "提前结束")
            if summary["answered"] == 0:
                st.caption("本轮没有作答记录。")
        if st.button("关闭", key="close_summary"):
            st.session_state.pop("practice_summary", None)
            st.rerun()


# ---------------- 页面：错题本 ----------------

def page_wrongbook(bank) -> None:
    _hero("错题本", "未攻克优先排序；点「一键重练」只抽错题，连错题一起清干净")
    with _open() as conn:
        wb = db.wrongbook_counts(conn, bank["id"] if bank else None)
        rows = db.wrongbook_rows(conn, bank["id"] if bank else None)

    if not rows:
        st.info("还没有错题。去练习一轮吧。")
        return

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button(f"一键重练（{wb['open'] or wb['total']} 题）", type="primary",
                     width="stretch", key="wb_redo", disabled=bank is None):
            _start_practice(bank, mode="wrong")
    with c2:
        st.caption(f"共 {wb['total']} 道错题 · 待攻克 {wb['open']} · 已攻克 {wb['mastered']}")

    for r in rows:
        badge = "已攻克" if r["status"] == "mastered" else "待攻克"
        title = f"[{badge}] {r['stem'][:56]} · 错 {r['times_wrong']} 次 / 对 {r['times_right']} 次"
        with st.expander(title):
            st.markdown(f'<div class="qf-stem">{r["stem"]}</div>', unsafe_allow_html=True)
            for o in json.loads(r["options_json"]):
                st.markdown(f"- {o['key']}. {o['text']}")
            st.markdown(f"正确答案：**{r['answer']}**")
            if r["analysis"]:
                st.markdown(
                    f'<div class="qf-note"><b>解析</b><br>{r["analysis"]}</div>',
                    unsafe_allow_html=True,
                )


# ---------------- 页面：学习统计 ----------------

def page_stats(bank) -> None:
    _hero("学习统计", "模型指标衡量系统质量，这里衡量的是「产品有没有用」")
    with _open() as conn:
        d = metrics.dashboard(conn, bank["id"] if bank else None)

    comp, wrong, acc, cov = d["completion"], d["wrong"], d["accuracy"], d["coverage"]

    r1, r2, r3 = st.columns(3)
    with r1:
        st.markdown(_ring(comp["rate"], "练习完成率", "#4F46E5"), unsafe_allow_html=True)
    with r2:
        st.markdown(_ring(wrong["redo_rate"], "错题重做率", "#C0396A"), unsafe_allow_html=True)
    with r3:
        st.markdown(_ring(acc["rate"], "总体正确率", "#0F6E56"), unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("完成轮次", f"{comp['completed']} / {comp['total']}",
              f"放弃 {comp['abandoned']} · 中断 {comp['in_progress']}")
    c2.metric("错题攻克率", f"{wrong['mastery_rate']:.0%}", f"已攻克 {wrong['mastered']}")
    c3.metric("题库覆盖率", f"{cov['rate']:.0%}", f"{cov['seen']} / {cov['total']} 题")
    c4.metric("连续学习", f"{d['streak']} 天")

    st.divider()
    _render_daily_chart(d["daily"])

    with _open() as conn:
        rows = conn.execute(
            "SELECT type, COUNT(*) n FROM questions"
            + (" WHERE bank_id = ?" if bank else "")
            + " GROUP BY type",
            (bank["id"],) if bank else (),
        ).fetchall()
    if rows:
        st.markdown("#### 题库题型分布")
        import pandas as pd

        dist = pd.DataFrame(
            {
                "题型": [TYPE_LABEL.get(r["type"], r["type"]) for r in rows],
                "题数": [r["n"] for r in rows],
            }
        ).set_index("题型")
        st.bar_chart(dist)


# ---------------- 页面：题库管理 ----------------

def page_banks() -> None:
    _hero("题库管理", "导入题库 PDF → 自动抽取成结构化题库；隔离区保留源文件问题的题块")
    with _open() as conn:
        banks = db.list_banks(conn)

    if banks:
        st.dataframe(
            [
                {
                    "题库": b["name"],
                    "题目数": b["question_count"],
                    "状态": b["status"],
                    "创建时间": b["created_at"][:19].replace("T", " "),
                    "bank_id": b["id"],
                }
                for b in banks
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("还没有题库。用下面的「导入新题库」上传一份题库 PDF。")

    st.divider()
    st.markdown("#### 导入新题库")
    uploaded = st.file_uploader("上传题库 PDF", type=["pdf"])
    col1, col2 = st.columns([2, 1])
    with col1:
        name = st.text_input("题库名称（留空取文件名）", value="")
    with col2:
        use_llm = st.toggle("启用 LLM 兜底", value=True,
                            help="规则解析失败的块交给 DeepSeek 兜底抽取")

    if uploaded is not None and st.button("开始导入", type="primary", key="do_import"):
        _import_uploaded(uploaded, name, use_llm)

    with _open() as conn:
        rows = conn.execute("SELECT * FROM quarantine ORDER BY id DESC LIMIT 20").fetchall()
    if rows:
        with st.expander(f"隔离区（最近 {len(rows)} 条，均为源文件问题，可人工复核）"):
            st.dataframe(
                [
                    {"页码": r["page_ref"], "题号": r["q_no"],
                     "失败原因": r["fail_reason"], "阶段": r["stage"]}
                    for r in rows
                ],
                width="stretch",
                hide_index=True,
            )


def _import_uploaded(uploaded, name: str, use_llm: bool) -> None:
    """导入上传的 PDF；成功后把 flash 与目标题库写进 session_state，由 main() 消费。"""
    tmp = Path(get_settings().data_dir) / "_upload.pdf"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(uploaded.getbuffer())
    with st.spinner("解析 + 抽取 + 校验中…（首次可能较慢）"):
        report, _questions, _quarantine = import_bank(
            str(tmp), name=name or None, use_llm=use_llm
        )
    st.session_state["flash"] = (
        f"导入完成：{report.imported} 题入库，{report.quarantined} 题进隔离区 · "
        f"切分完整率 {report.split_completeness:.1%} · 规则直出率 {report.rule_direct_ratio:.1%}"
    )
    st.session_state["pending_bank"] = report.bank_id
    st.rerun()


# ---------------- 入口 ----------------

PAGES = ["今日", "练习", "错题本", "学习统计", "题库管理"]


def _sidebar_import() -> None:
    with st.expander("＋ 导入新题库 PDF"):
        st.caption("支持任意科目、任意来源的题库 PDF，不限某一门课程。")
        with st.form("sidebar_import", clear_on_submit=True):
            up = st.file_uploader("选择题库 PDF", type=["pdf"])
            nm = st.text_input("题库名称（留空取文件名）")
            if st.form_submit_button("导入", type="primary", width="stretch"):
                if up is None:
                    st.warning("先选择一个 PDF 文件。")
                else:
                    _import_uploaded(up, nm, True)


def main() -> None:
    st.set_page_config(page_title="QuizForge · 智能刷题", page_icon="📘", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    # 跨页跳转 / 选中新导入的题库：必须在对应 widget 实例化之前改 key
    if "goto" in st.session_state:
        st.session_state["nav"] = st.session_state.pop("goto")
    if "pending_bank" in st.session_state:
        st.session_state["bank_pick"] = st.session_state.pop("pending_bank")

    with _open() as conn:
        banks = db.list_banks(conn)

    names = [b["name"] for b in banks]
    ids = [b["id"] for b in banks]
    with st.sidebar:
        st.markdown(
            '<div class="qf-logo">📘 QuizForge</div>'
            '<div class="qf-logo-sub">题库 PDF 进 · 随机抽题出</div>',
            unsafe_allow_html=True,
        )
        page = st.radio("页面", PAGES, key="nav", label_visibility="collapsed")
        st.divider()

        if banks:
            default = st.session_state.get("bank_pick", ids[0])
            idx = ids.index(default) if default in ids else 0
            picked = st.selectbox("当前题库", names, index=idx, key="bank_pick")
            bank = banks[names.index(picked)]
        else:
            st.selectbox("当前题库", ["（还没有题库）"], index=0, key="bank_pick",
                         disabled=True)
            bank = None

        _sidebar_import()
        st.divider()
        s = get_settings()
        st.caption(
            f"LLM 兜底：{'✅ 已配置' if s.llm_api_key else '❌ 未配置'}  \n"
            f"检索讲解：{'✅ 已配置' if s.bocha_api_key else '❌ 未配置'}"
        )

    flash = st.session_state.pop("flash", None)
    if flash:
        st.success(flash)

    if page == "今日":
        page_today(bank)
    elif page == "练习":
        page_practice(bank)
    elif page == "错题本":
        page_wrongbook(bank)
    elif page == "学习统计":
        page_stats(bank)
    else:
        page_banks()
