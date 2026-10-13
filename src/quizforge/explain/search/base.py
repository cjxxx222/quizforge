"""搜索适配器抽象接口：换搜索源不改业务逻辑（ADR-9）。"""

from __future__ import annotations

from typing import Protocol

from ..models import SearchResult


class SearchProvider(Protocol):
    """搜索源接口。实现者需提供 name 与 search()。"""

    name: str

    @property
    def available(self) -> bool:
        """是否可用（如 API key 是否配置）。"""
        ...

    def search(self, query: str, count: int = 8) -> list[SearchResult]:
        """返回搜索结果；网络/配额失败时抛异常，由调用方降级。"""
        ...
