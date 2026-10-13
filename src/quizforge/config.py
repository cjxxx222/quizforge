"""配置管理：环境变量 + .env 文件 + 默认值。密钥只从环境读取，不进代码与仓库。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载（KEY=VALUE，# 注释），不引第三方依赖。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    llm_api_key: str | None
    llm_base_url: str
    llm_model: str
    # M3 讲解增强：博查搜索 API（缺失时讲解降级为 LLM 直讲并标注无引用）
    bocha_api_key: str | None = None
    bocha_base_url: str = "https://api.bochaai.com/v1/web-search"
    # 检索 query 的来源引导词：实测可把来源白名单命中率从 53% 提到 73%
    explain_query_hint: str = "知识点 讲解 教材"
    # LLM 兜底请求附带的相邻题块数（上下文）
    llm_context_blocks: int = 2
    # 兜底比例告警线：超过该比例触发规则复查（技术方案 3.5）
    llm_fallback_ratio_alert: float = 0.30
    # 抽取验收门禁（技术方案 3.5）
    min_field_accuracy_rule: float = 0.95
    min_field_accuracy_llm: float = 0.90


def get_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    _load_dotenv(project_root / ".env")
    data_dir = Path(os.getenv("QUIZFORGE_DATA_DIR", project_root / "data"))
    db_path = Path(os.getenv("QUIZFORGE_DB_PATH", data_dir / "quizforge.db"))
    return Settings(
        data_dir=data_dir,
        db_path=db_path,
        llm_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
        llm_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        llm_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        bocha_api_key=os.getenv("BOCHA_API_KEY") or None,
        bocha_base_url=os.getenv(
            "BOCHA_BASE_URL", "https://api.bochaai.com/v1/web-search"
        ),
        explain_query_hint=os.getenv("EXPLAIN_QUERY_HINT", "知识点 讲解 教材"),
    )
