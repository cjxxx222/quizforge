"""博查（Bochaai）搜索适配器。

选型理由（实测）：DuckDuckGo / 维基百科在本机网络超时不可达，百度百科直接
403（安全验证），Bing 搜索页返回降级内容；博查是国内为 AI 设计的官方搜索 API，
网络可达、返回结构化结果且自带 summary 字段（可直接作为检索上下文）。
"""

from __future__ import annotations

import httpx

from ..models import SearchResult


class BochaProvider:
    name = "bocha"

    def __init__(self, api_key: str | None, base_url: str, timeout: float = 20.0):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, count: int = 8) -> list[SearchResult]:
        if not self.available:
            raise RuntimeError("博查 API key 未配置")
        resp = httpx.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "count": count, "summary": True},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") not in (200, None):
            raise RuntimeError(f"博查返回错误: code={payload.get('code')} msg={payload.get('msg')}")
        pages = ((payload.get("data") or {}).get("webPages") or {}).get("value") or []
        results: list[SearchResult] = []
        for p in pages:
            url = p.get("url") or ""
            if not url:
                continue
            results.append(
                SearchResult(
                    title=(p.get("name") or "").strip(),
                    url=url,
                    summary=(p.get("summary") or p.get("snippet") or "").strip(),
                    published=p.get("datePublished") or p.get("dateLastCrawled"),
                )
            )
        return results
