"""评测入口（M1 骨架）：跑导入 → 汇总质量指标 → 输出基线报告。

黄金标注集就位后的完整流程（M1）：
  1. python eval/run_eval.py import <pdf>            # 规则路径导入
  2. 人工按 eval/golden/template.jsonl 标注分层抽样题目
  3. python eval/run_eval.py score --bank <id>      # 对照标注集算字段准确率
当前为 M0 骨架：只产出自动指标（切分完整率/规则直出率/隔离原因分布）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quizforge.config import get_settings
from quizforge.importer import import_bank


def cmd_import(args: argparse.Namespace) -> int:
    report, _, _ = import_bank(args.pdf, use_llm=not args.no_llm)
    summary = {
        "bank_id": report.bank_id,
        "source_file": report.source_file,
        "total_numbered_blocks": report.total_numbered_blocks,
        "imported": report.imported,
        "quarantined": report.quarantined,
        "split_completeness": round(report.split_completeness, 4),
        "rule_direct_ratio": round(report.rule_direct_ratio, 4),
        "llm_rescued": report.llm_rescued,
        "number_gaps": report.number_gaps,
        "empty_pages": report.empty_pages,
        "quarantine_reasons": report.quarantine_reasons,
    }
    out_dir = Path(__file__).resolve().parent / "baseline"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{report.bank_id}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n基线报告已写入: {out_path}")

    # M0 自动门槛（技术方案 3.5 的门禁需黄金标注集，M1 补）
    if summary["rule_direct_ratio"] < 0.8 and summary["imported"]:
        print("⚠ 规则直出率 < 80%，触发规则复查")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="QuizForge 评测")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import", help="导入并汇总自动指标")
    p.add_argument("pdf")
    p.add_argument("--no-llm", action="store_true")
    p.set_defaults(func=cmd_import)

    pe = sub.add_parser("explain-eval", help="M3 讲解增强评测（白名单命中率等）")
    pe.add_argument("bank")
    pe.add_argument("-n", type=int, default=5, help="评测题数")
    pe.set_defaults(func=cmd_explain_eval)

    args = parser.parse_args()
    return args.func(args)




def cmd_explain_eval(args: argparse.Namespace) -> int:
    """M3 讲解增强评测：跑 N 道题，统计白名单命中率 / 引用数 / 降级率。

    开放域检索无法算 Recall@5（没有固定正确块集合），因此口径改为：
    - 白名单命中率：tier1+tier2 来源占比（越高越可信）
    - 引用覆盖率：有引用编号的讲解占比
    - 降级率：无检索支撑（LLM 直讲）占比
    """
    from quizforge.explain.pipeline import explain_question
    from quizforge.explain.search import BochaProvider
    from quizforge.llm.client import LLMClient
    from quizforge.storage import db as _db

    settings = get_settings()
    _db.init_db(settings.db_path)
    llm = LLMClient(api_key=settings.llm_api_key, base_url=settings.llm_base_url,
                    model=settings.llm_model)
    provider = BochaProvider(api_key=settings.bocha_api_key, base_url=settings.bocha_base_url)

    with _db.connect(settings.db_path) as conn:
        bank = conn.execute(
            "SELECT * FROM banks WHERE id = ? OR name = ? ORDER BY created_at DESC LIMIT 1",
            (args.bank, args.bank),
        ).fetchone()
        if not bank:
            print(f"未找到题库: {args.bank}")
            return 1
        rows = [r for r in _db.get_questions(conn, bank["id"])
                if r[1].type.value in ("single_choice", "multi_choice")][: args.n]
        stats = {"total": 0, "grounded": 0, "with_citation": 0,
                 "tier1": 0, "tier2": 0, "tier3": 0, "degraded": 0}
        for qid, q in rows:
            exp = explain_question(conn, qid, q, llm, provider)
            stats["total"] += 1
            if exp.grounded:
                stats["grounded"] += 1
            else:
                stats["degraded"] += 1
            if exp.citations:
                stats["with_citation"] += 1
            for c in exp.citations:
                from quizforge.explain.whitelist import classify
                tier = classify(c.domain)
                stats[f"tier{tier}"] = stats.get(f"tier{tier}", 0) + 1
            print(f"  q{q.q_no}: {'有引用' if exp.citations else '降级'} | {q.stem[:30]}")

    cited = stats["tier1"] + stats["tier2"] + stats["tier3"]
    summary = {
        "questions": stats["total"],
        "grounded_rate": round(stats["grounded"] / max(stats["total"], 1), 3),
        "citation_coverage": round(stats["with_citation"] / max(stats["total"], 1), 3),
        "whitelist_hit_rate": round((stats["tier1"] + stats["tier2"]) / max(cited, 1), 3),
        "tier_distribution": {f"tier{i}": stats.get(f"tier{i}", 0) for i in (1, 2, 3)},
    }
    out = Path(__file__).resolve().parent / "baseline" / f"explain_eval_{bank['id']}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n评测报告: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
