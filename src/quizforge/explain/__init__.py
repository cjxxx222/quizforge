"""M3 讲解增强：答错后检索外部资料并生成带引用的讲解。

架构（技术方案 M3，开放域检索版）：
    题干 → 查询构造 → 搜索适配器（博查）→ 来源白名单重排 → 上下文组装
         → LLM 生成（强制引用编号 / 资料不足则明说）→ 讲解落库 + 快照

可插拔设计：SearchProvider 是抽象接口，换搜索源不改业务逻辑（ADR-9）。
"""

from .models import Citation, Explanation, SearchResult

__all__ = ["Citation", "Explanation", "SearchResult"]
