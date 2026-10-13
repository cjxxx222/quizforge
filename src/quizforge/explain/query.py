"""查询构造：把题目转成检索 query。

设计要点：检索 query 要去掉题干里的"套话"（下列…不正确的是 / 以下哪项…），
保留真正的知识点与实体，否则搜索会被套话污染。
"""

from __future__ import annotations

import re

from ..models import Question

# 题干套话（检索噪声）
_BOILERPLATE = [
    r"下列[^,，。]{0,12}(?:说法|表述|陈述|理解|分析|赏析|各项)?[^,，。]{0,8}(?:不正确|有误|错误|不恰当)的?(?:一项|是)?",
    r"以下[^,，。]{0,12}(?:说法|表述|陈述)?[^,，。]{0,8}(?:不正确|有误|错误)的?(?:一项|是)?",
    r"关于[^,，。]{0,20}(?:说法|表述|理解)[^,，。]{0,10}(?:正确|不正确|有误|错误)的?(?:一项|是)?",
    r"(?:下列|以下)(?:各项|哪一项|哪项)?(?:中)?",
    r"请(?:简要|简要地)?(?:梳理|分析|说明|简述)",
    r"[（(][^）)]{0,10}[）)]\s*$",
]


def build_query(question: Question, max_len: int = 60, hint: str = "") -> str:
    """构造检索 query：实体（书名号/引号内容）优先 + 去套话后的题干 + 可选来源提示。

    注意：套话剥离后题干可能变空（整句都是套话），此时**不能回退到原始题干**
    （会把噪声塞回 query），而应只保留实体。

    hint 是来源引导词（如"知识点 讲解 教材"）：实测能把白名单命中率从 53% 提到 73%
    （见 eval/baseline/explain_eval_*.json 的对照实验），默认由配置注入。
    """
    stem = question.stem
    entities = re.findall(r"《[^》]{1,20}》|“[^”]{1,20}”", stem)

    text = re.sub(r"_{2,}", " ", stem)
    for pat in _BOILERPLATE:
        text = re.sub(pat, " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,，。:：、")

    parts: list[str] = [e for e in dict.fromkeys(entities[:2]) if e not in text]
    if len(text) >= 4:
        parts.append(text)
    if not parts:
        parts.append(stem[:max_len])
    query = " ".join(dict.fromkeys(parts))
    query = query[:max_len].strip()
    if hint:
        query = f"{query} {hint}".strip()
    return query
