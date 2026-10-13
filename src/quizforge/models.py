"""数据模型：题目 / 题库 / 隔离项 / 导入报告。Pydantic v2，LLM 输出也过这套 Schema。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class QuestionType(str, Enum):
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    TRUE_FALSE = "true_false"
    FILL_BLANK = "fill_blank"
    SELF_RATED = "self_rated"  # 兜底/降级题型，由用户自评


class SourceQuality(str, Enum):
    RULE = "rule"
    LLM = "llm"


class Option(BaseModel):
    key: str  # "A" / "B" / ...
    text: str


class Question(BaseModel):
    bank_id: str
    q_no: int | None = None      # 卷面题号（按节重号，样本实测：填空 1-6、单选又从 1 开始）
    seq: int = 0                 # 库内全局序号（入库唯一性依据）
    type: QuestionType
    stem: str
    options: list[Option] = Field(default_factory=list)
    answer: str | None = None  # 单选 "C"；多选 "AB"；判断 "true"/"false"；填空 None
    analysis: str | None = None
    chapter: str | None = None
    page_ref: int | None = None
    source_quality: SourceQuality = SourceQuality.RULE
    content_hash: str = ""

    def model_post_init(self, __context, /) -> None:
        if not self.content_hash:
            self.content_hash = stem_hash(self.stem)

    @property
    def option_keys(self) -> list[str]:
        return [o.key for o in self.options]


def stem_hash(stem: str) -> str:
    """题干预拿去重哈希：忽略空白与首尾标点差异。"""
    normalized = "".join(stem.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class QuarantineItem(BaseModel):
    bank_id: str
    q_no: int | None
    raw_block: str
    fail_reason: str
    stage: str  # split / fields / validate / llm
    page_ref: int | None = None


class ImportReport(BaseModel):
    bank_id: str
    source_file: str
    total_numbered_blocks: int = 0  # 按题号切出的块数（切分完整率的分母）
    imported: int = 0
    quarantined: int = 0
    rule_direct: int = 0  # 规则直出（未经 LLM 兜底）
    llm_rescued: int = 0  # LLM 兜底成功
    number_gaps: list[str] = Field(default_factory=list)  # 题号缺口（按节描述，如 "单选: 2"）
    empty_pages: list[int] = Field(default_factory=list)  # 疑似扫描页
    quarantine_reasons: dict[str, int] = Field(default_factory=dict)
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def split_completeness(self) -> float:
        if self.total_numbered_blocks == 0:
            return 0.0
        return self.imported / self.total_numbered_blocks

    @property
    def rule_direct_ratio(self) -> float:
        if self.imported == 0:
            return 0.0
        return self.rule_direct / self.imported
