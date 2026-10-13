"""字段识别（规则优先）：题干/选项/答案/解析。

规则依据真实样本（详见技术方案 3.1）：
- 单选/多选：答案内嵌题干末尾括号，形如 "(C )"（含空格）；选项标记 "A．/A./A、"
  三种分隔符并存，且允许同行多选项（"A.xxx。 B.xxx。"）；
- 判断：行尾 √/×；同句括号夹注为解析（如 "×(是冉雍)"）；
- 解析一般为括号夹注，可能挂在题干尾或选项尾；
- 填空：无选项、无显式答案，v1 标记为自评题型。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Option, QuestionType

# 选项标记：行首或空白之后的 A-D + 分隔符
_OPTION_MARK = re.compile(r"([A-D])[．.、]\s*")
# 题干末尾答案括号（半角含空格，归一后；允许后随句读标点：样本实测 "他( C )。"）
_ANSWER_TAIL = re.compile(r"\(\s*([A-D])\s*\)\s*[。.；;，,：:]?\s*$")
# 答案无括号形态：样本实测 "提到的正面人物:A A.尹少荣…"
_ANSWER_COLON = re.compile(r"[:：]\s*([A-D])\s*$")
# 句尾中文夹注（解析）
_ANALYSIS_TAIL = re.compile(r"\(([^()]{2,60}[\u4e00-\u9fff][^()]*)\)\s*$")
# 判断题答案符号：√/× 为主；样本实测存在用大写 X 代 ×（"…。"X原文里…"）
_TF_SYMBOL = re.compile(r"[\u221a\u00d7]")
_TF_SYMBOL_X = re.compile(r"(?<=[\u3002\u201d\u300b\uff09\uff1a])\s*[Xx](?=[\u4e00-\u9fff\uff08])")
_TF_ANALYSIS = re.compile(r"\(([^()]{2,60}[\u4e00-\u9fff][^()]*)\)")


# 全半角归一表（与 clean 一致；parse_block 入口防御性归一）
_NORMALIZE = str.maketrans({"（": "(", "）": ")", "．": ".", "，": ",", "：": ":", "；": ";"})


@dataclass
class ParsedQuestion:
    q_no: int | None
    type: QuestionType
    stem: str
    options: list[Option] = field(default_factory=list)
    answer: str | None = None
    analysis: str | None = None


class FieldParseError(Exception):
    """规则解析失败 → 交 LLM 兜底 / 隔离区。"""


def _take_trailing_analysis(text: str, sink: list[str]) -> str:
    """剥离并收集句尾中文夹注解析。"""
    while True:
        m = _ANALYSIS_TAIL.search(text)
        if not m:
            return text
        sink.append(m.group(1).strip())
        text = text[: m.start()].rstrip()


# 选项标记允许的前导字符：行首 / 空白 / 中文句读（样本实测 "…法事。B、…"、"…戏剧家D.…"）
_MARK_LEAD = set(" \n\u3002\uff1b;！？\uff01\uff1f\uff09)")


def _is_contiguous(keys: list[str]) -> bool:
    return bool(keys) and keys == [chr(ord("A") + i) for i in range(len(keys))]


def _find_option_markers(text: str) -> list[tuple[int, str]]:
    """返回 [(位置, 键)]。

    两轮判定（样本实测 "…八位戏剧家D.八位散文家"——选项之间无任何分隔符）：
    1. 严格：标记前必须是行首/空白/中文句读；
    2. 宽松：任意前导字符，但**仅当严格集合不连续、而宽松集合恰好 A/B/C/D 连续时**采信。
    用"连续性"做二次确认，避免把正文里的 "A." 误当选项。
    """
    strict: list[tuple[int, str]] = []
    lenient: list[tuple[int, str]] = []
    for m in _OPTION_MARK.finditer(text):
        pos = m.start()
        item = (pos, m.group(1))
        if pos == 0 or text[pos - 1] in _MARK_LEAD:
            strict.append(item)
        else:
            lenient.append(item)
    if _is_contiguous([k for _, k in strict]):
        # 严格集合已连续，但仍可能因"选项间无分隔"漏掉末尾选项（如 C.xxxD.yyy），
        # 故优先取"更长的连续集合"。
        merged = sorted(strict + lenient)
        if _is_contiguous([k for _, k in merged]) and len(merged) > len(strict):
            return merged
        return strict
    merged = sorted(strict + lenient)
    if _is_contiguous([k for _, k in merged]):
        return merged
    return strict


def _parse_choice(block_text: str, qtype: QuestionType, q_no: int | None) -> ParsedQuestion:
    markers = _find_option_markers(block_text)
    if len(markers) < 2:
        raise FieldParseError(f"选项标记不足 2 个（实际 {len(markers)}）")
    keys = [k for _, k in markers]
    if keys != sorted(set(keys)) or keys != [chr(ord("A") + i) for i in range(len(keys))]:
        raise FieldParseError(f"选项键不连续: {keys}")

    head = block_text[: markers[0][0]].rstrip()
    analysis_sink: list[str] = []
    # 先剥句尾解析夹注（可能盖在答案括号之后，如 "( C )(三句排比)"）
    head = _take_trailing_analysis(head, analysis_sink)
    answer: str | None = None
    m = _ANSWER_TAIL.search(head)
    if m:
        answer = m.group(1)
        head = head[: m.start()].rstrip()
    else:
        mc = _ANSWER_COLON.search(head)
        if mc:
            answer = mc.group(1)
            head = head[: mc.start()].rstrip()
        else:
            # 题干中部孤立单字母括号（样本实测 "与(B)并称…"、"中( D)表现了…"）
            # 仅当全题仅有这一个孤立答案括号时才采信；移除后留空占位保持题干可读
            singles = [
                x for x in re.finditer(r"\(\s*([A-D])\s*\)", head)
            ]
            # 排除与夹注解析重叠的（夹注含中文，天然不匹配该正则）
            if len(singles) == 1:
                answer = singles[0].group(1)
                head = (head[: singles[0].start()] + "____" + head[singles[0].end():]).strip()
    head = _take_trailing_analysis(head, analysis_sink)
    if not head or len(head) < 4:
        raise FieldParseError("题干为空或过短")

    options: list[Option] = []
    for i, (pos, key) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(block_text)
        text = block_text[pos + len(key) + 1 : end].strip()
        text = _take_trailing_analysis(text, analysis_sink)
        if not text:
            raise FieldParseError(f"选项 {key} 文本为空")
        options.append(Option(key=key, text=text))

    if answer is None:
        raise FieldParseError("题干末尾未找到答案括号")
    if answer not in {o.key for o in options}:
        raise FieldParseError(f"答案 {answer} 不在选项集合内")
    if qtype is QuestionType.SINGLE_CHOICE and len(answer) != 1:
        raise FieldParseError("单选答案应为单个字母")
    if qtype is QuestionType.MULTI_CHOICE:
        answer = "".join(sorted(set(answer)))

    return ParsedQuestion(
        q_no=q_no,
        type=qtype,
        stem=head,
        options=options,
        answer=answer,
        analysis="；".join(a for a in analysis_sink if a) or None,
    )


def _parse_true_false(block_text: str, q_no: int | None) -> ParsedQuestion:
    m = _TF_SYMBOL.search(block_text)
    if m:
        answer = "true" if m.group(0) == "√" else "false"
    else:
        mx = _TF_SYMBOL_X.search(block_text)
        if not mx:
            raise FieldParseError("判断题未找到 √/×（含 X）")
        m = mx
        answer = "false"  # X 只可能代 ×（误判为正确不会用 X）
    stem = block_text[: m.start()].rstrip()
    tail = block_text[m.end() :]
    analysis: str | None = None
    am = _TF_ANALYSIS.search(tail)
    if am:
        analysis = am.group(1).strip()
    if len(stem) < 4:
        raise FieldParseError("判断题干过短")
    return ParsedQuestion(q_no=q_no, type=QuestionType.TRUE_FALSE, stem=stem, answer=answer, analysis=analysis)


def _parse_fill_blank(block_text: str, q_no: int | None) -> ParsedQuestion:
    stem = block_text.strip()
    if len(stem) < 4:
        raise FieldParseError("填空题干过短")
    return ParsedQuestion(q_no=q_no, type=QuestionType.FILL_BLANK, stem=stem)


def parse_block(block_text: str, qtype: QuestionType, q_no: int | None) -> ParsedQuestion:
    block_text = block_text.translate(_NORMALIZE)
    if qtype in (QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE):
        return _parse_choice(block_text, qtype, q_no)
    if qtype is QuestionType.TRUE_FALSE:
        return _parse_true_false(block_text, q_no)
    return _parse_fill_blank(block_text, q_no)
