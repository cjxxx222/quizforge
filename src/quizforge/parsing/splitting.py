"""题号切分：按行首题号（1. / 1、 / 1．）把行序列切成题块。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_QUESTION_START = re.compile(r"^\s*(\d+)\s*[\.、．]\s*")
# 题号后无分隔符直接接内容：数字后紧跟书名号/引号/括号（样本实测 `3《篇目名》…`、`6“引文…”`）
_QUESTION_START_NO_SEP = re.compile(r"^\s*(\d+)(?=[《“\"（(])")
# 小节标题行（"二、单选"）：出现即闭合当前题块，不并入块内
_SECTION_HEADER = re.compile(r"^\s*[一二三四五六七八九十]+\s*、\s*\S")


@dataclass
class Block:
    q_no: int | None
    lines: list = field(default_factory=list)  # list[Line]
    start_idx: int = 0
    end_idx: int = 0

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def text_without_number(self) -> str:
        """剥离行首题号前缀后的题块文本（"12.xxx" / "3《xxx" -> "xxx"）。"""
        text = self.text
        m = _QUESTION_START.match(text) or _QUESTION_START_NO_SEP.match(text)
        if m:
            return text[m.end():]
        return text

    @property
    def page_ref(self) -> int | None:
        return self.lines[0].page_no if self.lines else None


def split_blocks(lines: list) -> list[Block]:
    blocks: list[Block] = []
    current: Block | None = None
    for idx, line in enumerate(lines):
        if _SECTION_HEADER.match(line.text) and current is not None:
            current.end_idx = idx
            blocks.append(current)
            current = None
            continue
        m = _QUESTION_START.match(line.text) or _QUESTION_START_NO_SEP.match(line.text)
        if m:
            if current is not None:
                current.end_idx = idx
                blocks.append(current)
            current = Block(q_no=int(m.group(1)), start_idx=idx)
            current.lines.append(line)
        elif current is not None:
            current.lines.append(line)
        # 题号出现之前的内容（如卷首说明）忽略
    if current is not None:
        current.end_idx = len(lines)
        blocks.append(current)
    return blocks


def number_gaps(blocks: list[Block]) -> list[int]:
    """题号缺口：切出的块数 < 题号序列时暴露缺失题号（样本实测存在跳号）。"""
    nums = sorted({b.q_no for b in blocks if b.q_no is not None})
    if not nums:
        return []
    full = set(range(nums[0], nums[-1] + 1))
    return sorted(full - set(nums))
