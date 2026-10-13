"""清洗：全半角归一、页眉页脚/页码行剔除、软换行合并。

要点（源自真实样本剖析）：
- 题干与选项会折行跨页，合并规则：非结构行并入上一行；
- 页码行（纯数字）、"第 x 页" 行直接剔除；
- （）→ ()、．→ . 归一，便于后续正则统一处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 结构行判定（不参与软换行合并）
_OPTION_START = re.compile(r"^\s*[A-D][．.、]\s*\S")
_QUESTION_START = re.compile(r"^\s*\d+\s*[\.、．]\s*\S")
_SECTION_START = re.compile(r"^\s*[一二三四五六七八九十]+\s*、")

# 页眉页脚/页码行
_PAGE_NUM = re.compile(r"^\s*\d{1,4}\s*$")
_PAGE_LABEL = re.compile(r"^\s*第\s*\d+\s*页\s*$")

# 全半角归一表（仅影响结构字符，不动正文汉字）
_NORMALIZE = str.maketrans({"（": "(", "）": ")", "．": ".", "，": ",", "：": ":", "；": ";"})


@dataclass
class Line:
    text: str
    page_no: int


def _is_structural(line: str) -> bool:
    return bool(_OPTION_START.match(line) or _QUESTION_START.match(line) or _SECTION_START.match(line))


def clean_lines(pages: list) -> list[Line]:
    """输入 ExtractedDoc.pages，输出合并软换行后的行（保留页码）。"""
    out: list[Line] = []
    for page in pages:
        for raw in page.text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if _PAGE_NUM.match(line) or _PAGE_LABEL.match(line):
                continue
            line = line.translate(_NORMALIZE)
            if out and not _is_structural(line):
                prev = out[-1]
                if not _is_structural(prev.text):
                    prev.text = prev.text + line  # 软换行：直接拼接（中文无空格）
                    continue
            out.append(Line(text=line, page_no=page.page_no))
    return out
