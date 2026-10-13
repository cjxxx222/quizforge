"""讲解生成管道（M3）：检索 → 白名单重排 → 上下文组装 → 带引用生成 → 落库。

反幻觉约束（沿用技术方案 V1 的设计原则，此处才真正用上）：
- 上下文只含检索结果，不给参数化知识留发挥空间；
- 强制每个结论句标注引用编号 [1][2]；
- 资料不足时必须明说"资料未覆盖"，不允许编造；
- 无检索支撑时（搜索不可用/无结果）降级为 LLM 直讲，并**明确标注无引用**。
"""

from __future__ import annotations

import json
import re

from ..models import Question
from ..storage import db
from . import whitelist
from .models import Citation, Explanation, SearchResult
from .query import build_query

_SYSTEM_GROUNDED = """你是备考讲解助手。**只允许基于给定资料回答，不得使用资料之外的任何知识。**

硬性要求：
1. 每个结论句末尾必须标注引用编号，如 [1][2]，编号只能来自给定资料；
2. 若资料不足以回答该问题，直接写"资料未覆盖该知识点"，不要编造、不要用记忆补全；
3. 结构固定为三段：**考点**（这道题在考什么）、**讲解**（知识点本身）、**易混淆点**（如有）；
4. 用中文，Markdown 输出，总字数不超过 400 字。"""

_SYSTEM_UNGROUNDED = """你是备考讲解助手。注意：本次**没有可用的检索资料**，
你的回答来自模型自身知识，必须如实说明这一点。

硬性要求：
1. 开头第一句必须写明"以下内容来自模型知识，非教材原文，请以教材为准"；
2. 不要编造引用编号，不要假装有出处；
3. 结构：**考点** / **讲解** / **易混淆点**（如有），中文 Markdown，不超过 350 字。"""


def _build_context(results: list[SearchResult]) -> str:
    blocks = []
    for i, r in enumerate(results, start=1):
        blocks.append(
            f"[{i}] 标题：{r.title}\n来源：{r.domain}\n内容：{r.summary}"
        )
    return "\n\n".join(blocks)


def _used_indices(text: str, max_index: int) -> list[int]:
    idx = {int(m) for m in re.findall(r"\[(\d{1,2})\]", text)}
    return sorted(i for i in idx if 1 <= i <= max_index)


def explain_question(
    conn,
    question_id: int,
    question: Question,
    llm_client,
    provider,
    top_k: int = 5,
    query_hint: str | None = None,
) -> Explanation:
    """为一道题生成讲解（带引用）。异常不致命：检索/生成失败均降级并记录原因。"""
    if query_hint is None:
        from ..config import get_settings

        query_hint = get_settings().explain_query_hint
    query = build_query(question, hint=query_hint)
    degraded_reason: str | None = None
    results: list[SearchResult] = []

    if provider is not None and getattr(provider, "available", False):
        try:
            results = provider.search(query, count=max(top_k * 2, 8))
        except Exception as exc:  # noqa: BLE001 - 检索失败降级，不阻断练习
            degraded_reason = f"检索失败: {type(exc).__name__}: {exc}"
    else:
        degraded_reason = "未配置搜索 API"

    ranked = whitelist.rank(results, query, top_k=top_k) if results else []

    if not ranked:
        text = llm_client.chat(
            _SYSTEM_UNGROUNDED,
            f"题目：{question.stem}\n正确答案：{question.answer or '（无）'}\n"
            f"题目自带解析：{question.analysis or '（无）'}",
            max_tokens=700,
        )
        explanation = Explanation(
            question_id=question_id,
            query=query,
            text=text,
            citations=[],
            grounded=False,
            degraded_reason=degraded_reason or "检索无结果",
        )
        _persist(conn, explanation)
        return explanation

    # 快照落库（引用可验证）
    citations: list[Citation] = []
    for i, r in enumerate(ranked, start=1):
        snapshot_id = db.insert_snapshot(conn, r.url, r.title, r.domain, r.tier, r.summary)
        citations.append(
            Citation(index=i, title=r.title, url=r.url, domain=r.domain,
                     snapshot_id=snapshot_id, fetched_at=db.now_iso())
        )

    context = _build_context(ranked)
    user_prompt = (
        f"题目：{question.stem}\n"
        f"正确答案：{question.answer or '（无）'}\n"
        f"题目自带解析：{question.analysis or '（无）'}\n\n"
        f"=== 给定资料（只能依据这些内容回答）===\n{context}"
    )
    try:
        text = llm_client.chat(_SYSTEM_GROUNDED, user_prompt, max_tokens=800)
    except Exception as exc:  # noqa: BLE001 - 生成失败降级为直讲
        text = llm_client.chat(
            _SYSTEM_UNGROUNDED,
            f"题目：{question.stem}\n正确答案：{question.answer or '（无）'}",
            max_tokens=700,
        )
        explanation = Explanation(
            question_id=question_id, query=query, text=text, citations=[],
            grounded=False, degraded_reason=f"生成失败: {type(exc).__name__}",
        )
        _persist(conn, explanation)
        return explanation

    used = _used_indices(text, len(citations))
    if not used:
        # 未标注任何引用 = 未按约束回答，只保留引用列表但在降级原因中记录
        degraded_reason = "模型未标注引用编号"

    explanation = Explanation(
        question_id=question_id, query=query, text=text,
        citations=[c for c in citations if not used or c.index in used],
        grounded=True, degraded_reason=degraded_reason,
    )
    _persist(conn, explanation)
    return explanation


def _persist(conn, explanation: Explanation) -> None:
    db.upsert_explanation(
        conn,
        question_id=explanation.question_id,
        query=explanation.query,
        text=explanation.text,
        citations_json=json.dumps(
            [c.__dict__ for c in explanation.citations], ensure_ascii=False
        ),
        grounded=explanation.grounded,
        degraded_reason=explanation.degraded_reason,
    )
