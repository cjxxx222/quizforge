"""CLI：import（导入题库）/ practice（终端练习）/ stats（统计）/ banks（题库列表）。"""

from __future__ import annotations

import argparse
import sys

from .config import get_settings
from .explain.pipeline import explain_question
from .explain.search import BochaProvider
from .importer import import_bank
from .llm.client import LLMClient
from .quiz.engine import PracticeSession
from .storage import db


def _build_llm() -> LLMClient:
    s = get_settings()
    return LLMClient(api_key=s.llm_api_key, base_url=s.llm_base_url, model=s.llm_model)


def _build_provider():
    s = get_settings()
    return BochaProvider(api_key=s.bocha_api_key, base_url=s.bocha_base_url)


def cmd_import(args: argparse.Namespace) -> int:
    report, _, _ = import_bank(
        args.pdf, name=args.name, use_llm=not args.no_llm
    )
    print(f"\n===== 导入报告 bank_id={report.bank_id} =====")
    print(f"来源文件      : {report.source_file}")
    print(f"题号块总数    : {report.total_numbered_blocks}")
    print(f"入池/隔离     : {report.imported} / {report.quarantined}")
    print(f"切分完整率    : {report.split_completeness:.1%}")
    print(f"规则直出率    : {report.rule_direct_ratio:.1%}（LLM 兜底救回 {report.llm_rescued} 题）")
    if report.number_gaps:
        print(f"题号缺口      : {report.number_gaps}")
    if report.empty_pages:
        print(f"疑似扫描页    : {report.empty_pages}")
    if report.quarantine_reasons:
        print("隔离原因 TOP  :")
        for reason, cnt in sorted(report.quarantine_reasons.items(), key=lambda x: -x[1])[:8]:
            print(f"  [{cnt:>3}] {reason}")
    if report.rule_direct_ratio < 0.8 and report.imported:
        print("⚠ 规则直出率低于 80%，建议检查解析规则库（技术方案 3.5）")
    return 0


def cmd_banks(args: argparse.Namespace) -> int:
    settings = get_settings()
    db.init_db(settings.db_path)
    with db.connect(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM banks ORDER BY created_at DESC").fetchall()
    if not rows:
        print("（空）先跑 quizforge import <pdf>")
        return 0
    for r in rows:
        print(f"{r['id']}  {r['name']}  题目数={r['question_count']}  状态={r['status']}")
    return 0


def cmd_practice(args: argparse.Namespace) -> int:
    settings = get_settings()
    db.init_db(settings.db_path)
    llm = _build_llm() if args.explain else None
    provider = _build_provider() if args.explain else None
    with db.connect(settings.db_path) as conn:
        bank = conn.execute(
            "SELECT * FROM banks WHERE id = ? OR name = ? ORDER BY created_at DESC LIMIT 1",
            (args.bank, args.bank),
        ).fetchone()
        if not bank:
            print(f"未找到题库: {args.bank}（先用 quizforge banks 查看）")
            return 1
        session = PracticeSession(conn, bank["id"])
        print(f"开始练习：{bank['name']}（{session.stats()['total']} 题）")
        if args.explain:
            print("（答错后自动生成带引用的讲解）")
        done = 0
        while done < args.n:
            view = session.next_question()
            if view is None:
                print("题库为空。")
                break
            print(f"\n—— 第 {view.q_no} 题 [{view.type}]（来自{view.pool}池）——")
            print(view.stem)
            for opt in view.options:
                print(f"  {opt['key']}. {opt['text']}")
            if view.type == "fill_blank":
                ans = input("你的作答（填空）：").strip()
                result, q = session.submit(view, ans, self_rating="known" if ans else "unknown")
                print(f"参考答案：{q.stem}")
                print("自评：会了/不会" if ans else "（标记为不会）")
            else:
                ans = input("你的作答（如 C / AB / 对错）：").strip()
                result, q = session.submit(view, ans)
                mark = "✓ 正确" if result.correct else f"✗ 错误（正确答案：{result.expected}）"
                print(mark)
                if q.analysis:
                    print(f"解析：{q.analysis}")
            if args.explain and result.correct is False and llm and llm.available:
                print("\n[讲解生成中…]")
                try:
                    exp = explain_question(conn, view.question_id, q, llm, provider)
                    print(f"【讲解】{exp.text}")
                    if exp.citations:
                        print("引用来源：")
                        for c in exp.citations:
                            print(f"  [{c.index}] {c.title[:40]} — {c.domain}")
                    if not exp.grounded:
                        print(f"⚠ 无检索支撑（{exp.degraded_reason}），以上为模型知识，非教材口径")
                except Exception as exc:  # noqa: BLE001 - 讲解失败不影响练习
                    print(f"⚠ 讲解生成失败：{type(exc).__name__}: {exc}")
            done += 1
        print(f"\n本轮完成 {done} 题。统计：{session.stats()}")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    """为指定题目生成讲解（M3 讲解增强）。"""
    settings = get_settings()
    db.init_db(settings.db_path)
    llm = _build_llm()
    provider = _build_provider()
    with db.connect(settings.db_path) as conn:
        bank = conn.execute(
            "SELECT * FROM banks WHERE id = ? OR name = ? ORDER BY created_at DESC LIMIT 1",
            (args.bank, args.bank),
        ).fetchone()
        if not bank:
            print(f"未找到题库: {args.bank}")
            return 1
        rows = db.get_questions(conn, bank["id"])
        if not rows:
            print("题库为空。")
            return 1
        target = None
        if args.seq is not None:
            for qid, q in rows:
                if q.seq == args.seq:
                    target = (qid, q)
                    break
        if target is None and args.q_no is not None:
            matches = [(qid, q) for qid, q in rows if q.q_no == args.q_no]
            if len(matches) > 1:
                print(f"提示：卷面第 {args.q_no} 题有 {len(matches)} 道（题号按题型节重号），"
                      f"取第 1 道；可用 --seq 精确指定。")
            if matches:
                target = matches[0]
        if target is None:
            target = rows[0]
        qid, q = target
        print(f"题目（卷面第 {q.q_no} 题 / seq {q.seq}）：{q.stem}")
        if q.options:
            for o in q.options:
                print(f"  {o.key}. {o.text}")
        print(f"正确答案：{q.answer}")
        if q.analysis:
            print(f"题目自带解析：{q.analysis}")
        print("\n[检索 + 生成中…]")
        exp = explain_question(conn, qid, q, llm, provider)
        print(f"\n检索 query：{exp.query}")
        print(f"\n【讲解】\n{exp.text}\n")
        if exp.citations:
            print("引用来源：")
            for c in exp.citations:
                print(f"  [{c.index}] {c.title[:50]}")
                print(f"       {c.url}")
        if not exp.grounded:
            print(f"⚠ 无检索支撑（{exp.degraded_reason}）：以上为模型知识，非教材口径")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quizforge", description="PDF 题库抽取与智能刷题系统")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="导入题库 PDF")
    p_import.add_argument("pdf", help="题库 PDF 路径")
    p_import.add_argument("--name", help="题库名称（默认取文件名）")
    p_import.add_argument("--no-llm", action="store_true", help="关闭 LLM 兜底")
    p_import.set_defaults(func=cmd_import)

    p_banks = sub.add_parser("banks", help="列出题库")
    p_banks.set_defaults(func=cmd_banks)

    p_practice = sub.add_parser("practice", help="终端练习")
    p_practice.add_argument("bank", help="bank_id 或名称")
    p_practice.add_argument("-n", type=int, default=10, help="本轮题数")
    p_practice.add_argument("--explain", action="store_true",
                            help="答错后自动生成带引用的讲解（需博查 + DeepSeek key）")
    p_practice.set_defaults(func=cmd_practice)

    p_explain = sub.add_parser("explain", help="为指定题目生成带引用的讲解")
    p_explain.add_argument("bank", help="bank_id 或名称")
    p_explain.add_argument("--q", dest="q_no", type=int, default=None, help="卷面题号")
    p_explain.add_argument("--seq", type=int, default=None, help="库内全局序号（题号重号时用）")
    p_explain.set_defaults(func=cmd_explain)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
