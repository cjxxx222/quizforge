"""讲解增强的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SearchResult:
    title: str
    url: str
    summary: str          # 搜索 API 返回的摘要（检索上下文主来源）
    domain: str = ""
    published: str | None = None
    tier: int = 3         # 来源分层：1 权威 / 2 教学资源 / 3 其他（白名单打分）
    score: float = 0.0

    def __post_init__(self) -> None:
        if not self.domain:
            from urllib.parse import urlparse

            self.domain = urlparse(self.url).netloc.lower()


@dataclass
class Citation:
    index: int            # 引用编号 [1][2]…
    title: str
    url: str
    domain: str
    snapshot_id: int | None = None   # 本地快照 id（防网页失效，引用可验证）
    fetched_at: str | None = None


@dataclass
class Explanation:
    question_id: int
    query: str
    text: str
    citations: list[Citation] = field(default_factory=list)
    grounded: bool = True            # False = 无检索支撑（LLM 直讲降级）
    degraded_reason: str | None = None
    generated_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
