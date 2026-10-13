"""题型分节：识别"一、填空 / 二、单选 / 三、判断是非题"这类小节标题，
把每道题块归入题型上下文；无标题区域按版式信号推断。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import QuestionType

_SECTION_HEADER = re.compile(r"^\s*[一二三四五六七八九十]+\s*、\s*(.+?)\s*$")

_TYPE_KEYWORDS: list[tuple[re.Pattern, QuestionType]] = [
    (re.compile(r"多项选择|多选"), QuestionType.MULTI_CHOICE),
    (re.compile(r"单项选择|单选"), QuestionType.SINGLE_CHOICE),
    (re.compile(r"判断"), QuestionType.TRUE_FALSE),
    (re.compile(r"填空"), QuestionType.FILL_BLANK),
    (re.compile(r"简答|问答|论述|名词解释|计算"), QuestionType.SELF_RATED),
]

# 无标题区域的版式信号
_OPTION_ANY = re.compile(r"[A-D][．.、]\s*\S")
_TF_TAIL = re.compile(r"[√×]\s*[（(]?\s*$")


# 无标题区域的版式信号
_OPTION_ANY = re.compile(r"[A-D][．.、]\s*\S")
_TF_TAIL = re.compile(r"[√×]\s*[（(]?\s*$")


@dataclass
class Section:
    title: str
    qtype: QuestionType | None
    start_idx: int  # 在行列表中的起始下标


def _type_from_title(title: str) -> QuestionType | None:
    for pattern, qtype in _TYPE_KEYWORDS:
        if pattern.search(title):
            return qtype
    return None


def find_sections(lines: list) -> list[Section]:
    """返回所有小题标题（按出现顺序）。"""
    sections: list[Section] = []
    for idx, line in enumerate(lines):
        m = _SECTION_HEADER.match(line.text)
        if not m:
            continue
        title = m.group(1)
        qtype = _type_from_title(title)
        # 只把能映射到题型的标题当分节点（"一、项目概述"这类不会命中关键词）
        if qtype is not None:
            sections.append(Section(title=title, qtype=qtype, start_idx=idx))
    return sections


def infer_type_for_block(text: str) -> QuestionType:
    """无标题区域的题型推断：有选项→单选；行尾√/×→判断；否则填空。"""
    if _OPTION_ANY.search(text):
        return QuestionType.SINGLE_CHOICE
    if _TF_TAIL.search(text):
        return QuestionType.TRUE_FALSE
    return QuestionType.FILL_BLANK


def resolve_type(section_type: QuestionType | None, text: str) -> QuestionType:
    """节标题类型 × 版式信号 的最终裁定。

    样本实测：文档存在"无标题的题型切换"（题号重开但无小节标题），
    节标题会连带误判后续整段。因此版式信号与节标题冲突时以版式信号为准：
    - 节标题说判断，但文本无 √/×/X 且有选项 → 单选；
    - 节标题说判断/填空，但文本无选项且有 √/×/X → 判断；
    - 节标题说选择，但文本有选项 → 选择（尊重标题，多选与否由字段层断）。
    """
    has_options = bool(_OPTION_ANY.search(text))
    has_tf = bool(_TF_TAIL.search(text)) or bool(re.search(r"[√×Xx](?=[\u4e00-\u9fff（(])", text))
    if section_type is None:
        return infer_type_for_block(text)
    if section_type is QuestionType.TRUE_FALSE and not has_tf and has_options:
        return QuestionType.SINGLE_CHOICE
    if section_type in (QuestionType.TRUE_FALSE, QuestionType.FILL_BLANK) and has_tf and not has_options:
        return QuestionType.TRUE_FALSE
    return section_type


def type_for_range(sections: list[Section], start_idx: int, end_idx: int) -> QuestionType | None:
    """题块 [start_idx, end_idx) 所属题型（取最后一个起始于 start_idx 之前的标题）。"""
    current: QuestionType | None = None
    for sec in sections:
        if sec.start_idx <= start_idx:
            current = sec.qtype
        else:
            break
    return current
