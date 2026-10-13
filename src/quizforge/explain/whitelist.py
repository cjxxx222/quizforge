"""来源白名单与重排。

开放域检索最大的风险是"口径不一致"——营销号、自媒体、另一套教材的说法会把
用户带偏。因此对来源分层打分，权威/教学资源优先，低质来源降权但不直接丢弃
（记入 tier，评测时统计"白名单命中率"）。
"""

from __future__ import annotations

from .models import SearchResult

# tier 1：权威（官方/教育机构/百科）
_TIER1_SUFFIX = (".edu.cn", ".gov.cn", ".ac.cn", ".org.cn")
_TIER1_DOMAINS = (
    "baike.baidu.com",
    "zh.wikipedia.org",
    "wikisource.org",
    "cnki.net",
    "wanfangdata.com.cn",
)

# tier 2：教学资源（学案/教案/题库/教育门户——本场景最贴合的来源）
_TIER2_DOMAINS = (
    "zxxk.com",          # 学科网
    "21cnjy.com",        # 二一教育
    "jyeoo.com",         # 菁优网
    "doc88.com",         # 道客巴巴（学案扫描）
    "docin.com",         # 豆丁网
    "book118.com",
    "renrendoc.com",     # 人人文库
    "1kejian.com",
    "51test.net",
    "gaokao.com",
    "yuwenmi.com",
    "zujuan.com",
)

# 明确降权：内容农场/聚合站
_LOW_QUALITY = ("baijiahao.baidu.com", "zhihu.com", "sohu.com", "163.com", "toutiao.com")


def classify(domain: str) -> int:
    """返回来源分层：1 权威 / 2 教学资源 / 3 其他。"""
    d = domain.lower()
    if any(d.endswith(s) for s in _TIER1_SUFFIX) or any(t in d for t in _TIER1_DOMAINS):
        return 1
    if any(t in d for t in _TIER2_DOMAINS):
        return 2
    return 3


def score(result: SearchResult, query: str = "") -> float:
    """来源可信度打分（不含相关性；相关性由搜索 API 排序负责）。

    tier 权重为主，摘要长度与关键词命中做微调。
    """
    result.tier = classify(result.domain)
    base = {1: 1.0, 2: 0.8, 3: 0.5}.get(result.tier, 0.5)
    if any(t in result.domain for t in _LOW_QUALITY):
        base -= 0.25
    if len(result.summary) >= 80:
        base += 0.1
    if query and any(tok and tok in result.summary for tok in _query_tokens(query)):
        base += 0.1
    result.score = round(max(base, 0.0), 3)
    return result.score


def _query_tokens(query: str) -> list[str]:
    """粗粒度关键词：取长度 ≥2 的中文串与英文词。"""
    import re

    return [t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", query)][:6]


def rank(results: list[SearchResult], query: str = "", top_k: int = 5) -> list[SearchResult]:
    """按来源可信度重排并截断。"""
    for r in results:
        score(r, query)
    ranked = sorted(results, key=lambda r: (-r.score,))
    # 去重：同域名最多保留 2 条，避免单一站点占满上下文
    out: list[SearchResult] = []
    per_domain: dict[str, int] = {}
    for r in ranked:
        if per_domain.get(r.domain, 0) >= 2:
            continue
        per_domain[r.domain] = per_domain.get(r.domain, 0) + 1
        out.append(r)
        if len(out) >= top_k:
            break
    return out
