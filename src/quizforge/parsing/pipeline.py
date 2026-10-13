"""抽取管道编排：七步流水线（技术方案 2.2）。

extract → clean → section → split → fields(规则) → validate → (LLM 兜底) → 入库快照
规则失败/校验未过的题块：先尝试 LLM 兜底（需 API key），再失败入隔离区，不阻塞其余题目。

注意（样本实测）：题型分节内题号重新起算（填空 1-6，单选又从 1 开始），
因此卷面题号 q_no 不唯一，入库唯一性用全局 seq；题号缺口按节检测。
"""

from __future__ import annotations

import re

from ..models import (
    ImportReport,
    QuarantineItem,
    Question,
    SourceQuality,
)
from ..validate import validate_question
from . import fields, sectioning, splitting
from .clean import clean_lines
from .pdf_extract import extract_pdf


def run_pipeline(
    pdf_path: str,
    bank_id: str,
    llm_client=None,
) -> tuple[list[Question], ImportReport, list[QuarantineItem]]:
    doc = extract_pdf(pdf_path)
    lines = clean_lines(doc.pages)
    sections = sectioning.find_sections(lines)
    blocks = splitting.split_blocks(lines)

    report = ImportReport(bank_id=bank_id, source_file=pdf_path, empty_pages=doc.empty_pages)
    report.total_numbered_blocks = len(blocks)
    report.number_gaps = _number_gaps_by_section(blocks, sections)

    questions: list[Question] = []
    quarantine: list[QuarantineItem] = []
    seen_hashes: dict[str, str] = {}
    seq = 0

    for block in blocks:
        seq += 1
        text = block.text_without_number
        page = block.page_ref
        qtype = sectioning.resolve_type(
            sectioning.type_for_range(sections, block.start_idx, block.end_idx), text
        )

        parsed = None
        try:
            parsed = fields.parse_block(text, qtype, block.q_no)
        except fields.FieldParseError as exc:
            quarantine.append(
                QuarantineItem(
                    bank_id=bank_id,
                    q_no=block.q_no,
                    raw_block=text[:2000],
                    fail_reason=str(exc),
                    stage="fields",
                    page_ref=page,
                )
            )
        if parsed is not None:
            question = Question(
                bank_id=bank_id,
                q_no=parsed.q_no,
                seq=seq,
                type=parsed.type,
                stem=parsed.stem,
                options=parsed.options,
                answer=parsed.answer,
                analysis=parsed.analysis,
                page_ref=page,
                source_quality=SourceQuality.RULE,
            )
            problems = validate_question(question)
            if problems:
                quarantine.append(
                    QuarantineItem(
                        bank_id=bank_id,
                        q_no=block.q_no,
                        raw_block=text[:2000],
                        fail_reason="; ".join(problems),
                        stage="validate",
                        page_ref=page,
                    )
                )
            elif question.content_hash in seen_hashes:
                quarantine.append(
                    QuarantineItem(
                        bank_id=bank_id,
                        q_no=block.q_no,
                        raw_block=text[:2000],
                        fail_reason=f"与 {seen_hashes[question.content_hash]} 重复",
                        stage="validate",
                        page_ref=page,
                    )
                )
            else:
                seen_hashes[question.content_hash] = f"第{block.q_no}题"
                questions.append(question)
                report.imported += 1
                report.rule_direct += 1

    # LLM 兜底：仅处理 fields/validate 阶段失败的题块
    if llm_client is not None and llm_client.available:
        for item in list(quarantine):
            if item.stage not in ("fields", "validate"):
                continue
            if not _has_answer_signal(item.raw_block):
                # 源文本身没有答案线索（如空答案括号 "( )"）：LLM 只能猜，
                # 猜出来的答案不可信，宁可隔离转人工，不自动入库（ADR-3 的延伸）。
                item.fail_reason += "；源文无答案线索，需人工确认"
                continue
            if "重复" in item.fail_reason:
                # 重复是数据问题而非格式问题，送 LLM 只会浪费时间（救回了也会被去重拦截）
                continue
            context = _neighbor_context(blocks, item)
            try:
                rescued = llm_client.rescue_block(raw_block=item.raw_block, context=context)
            except Exception as exc:  # noqa: BLE001 - 兜底失败不致命，继续隔离
                item.fail_reason = f"{item.fail_reason}；LLM 兜底失败: {exc}"
                continue
            if rescued is None:
                continue
            seq += 1
            rescued.bank_id = bank_id
            rescued.seq = seq
            rescued.q_no = item.q_no  # 题号来自块位，不在文本里，LLM 无从推断，由管道回填
            rescued.source_quality = SourceQuality.LLM
            problems = validate_question(rescued)
            if problems:
                item.fail_reason = f"{item.fail_reason}；LLM 输出未过校验: {'; '.join(problems)}"
                continue
            if rescued.content_hash in seen_hashes:
                continue
            seen_hashes[rescued.content_hash] = f"第{rescued.q_no}题"
            questions.append(rescued)
            quarantine.remove(item)
            report.imported += 1
            report.llm_rescued += 1

    for item in quarantine:
        report.quarantined += 1
        key = item.fail_reason.split("；")[0][:40]
        report.quarantine_reasons[key] = report.quarantine_reasons.get(key, 0) + 1

    questions.sort(key=lambda q: q.seq)
    return questions, report, quarantine


def _has_answer_signal(raw_block: str) -> bool:
    """原始题块是否含答案线索：答案括号 / 冒号答案 / 判断符号。

    无线索 = 源文件本身缺答案（样本实测 "( )" 空括号），LLM 无法从文中推断，
    兜底只会产生不可信的猜测 → 隔离转人工（技术方案 3.5 的诚实性要求）。
    """
    if re.search(r"[\(\uff08]\s*[A-D]\s*[\)\uff09]", raw_block):
        return True
    if re.search(r"[:：]\s*[A-D](?=\s|$)", raw_block):
        return True
    return bool(re.search(r"[\u221a\u00d7Xx]", raw_block))

def _number_gaps_by_section(blocks: list, sections: list) -> list[str]:
    """按题型节检测题号缺口（样本实测缺第 7 题）。"""
    gaps: list[str] = []
    # 把题块归入所在节
    section_of_block: list[int] = []
    for block in blocks:
        sec_idx = 0
        for i, sec in enumerate(sections):
            if sec.start_idx <= block.start_idx:
                sec_idx = i
            else:
                break
        section_of_block.append(sec_idx)

    for i, sec in enumerate(sections):
        nums = sorted(
            b.q_no for b, s in zip(blocks, section_of_block) if s == i and b.q_no is not None
        )
        if not nums:
            continue
        missing = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums))
        if missing:
            label = sec.title[:12]
            gaps.append(f"{label}: 缺 {missing}")
    return gaps


def _neighbor_context(blocks: list, item: QuarantineItem) -> str:
    """给出隔离题块的前后相邻题块作为 LLM 上下文。"""
    target = None
    for idx, block in enumerate(blocks):
        if block.q_no == item.q_no and item.raw_block[:20] in block.text_without_number:
            target = idx
            break
    if target is None:
        return ""
    ctx: list[str] = []
    for j in range(max(0, target - 2), min(len(blocks), target + 3)):
        if j == target:
            continue
        ctx.append(blocks[j].text_without_number[:500])
    return "\n---\n".join(ctx)
