"""搜索适配器包。"""

from .base import SearchProvider
from .bocha import BochaProvider

__all__ = ["BochaProvider", "SearchProvider"]
