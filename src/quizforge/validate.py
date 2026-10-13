"""校验规则集（入库门禁，技术方案 3.4）。任一不过 → 隔离区，不阻塞其余题目。"""

from __future__ import annotations

from .models import Question, QuestionType

# prompt 注入残留特征（LLM 兜底输出的题干中不应出现）
_INJECTION_MARKERS = ("请输出", "请按", "JSON", "json", "schema", "```")

MIN_STEM_LEN = 5


def validate_question(q: Question) -> list[str]:
    problems: list[str] = []

    if q.q_no is None:
        problems.append("缺题号")
    if len(q.stem) < MIN_STEM_LEN:
        problems.append(f"题干过短({len(q.stem)})")
    if any(marker in q.stem for marker in _INJECTION_MARKERS):
        problems.append("题干疑似含指令残留")

    if q.type in (QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE):
        if len(q.options) < 2:
            problems.append("选项少于 2 个")
        keys = q.option_keys
        if keys != [chr(ord("A") + i) for i in range(len(keys))]:
            problems.append(f"选项键不连续: {keys}")
        if any(not o.text.strip() for o in q.options):
            problems.append("存在空选项文本")
        if not q.answer:
            problems.append("缺答案")
        elif q.type is QuestionType.SINGLE_CHOICE:
            if len(q.answer) != 1 or q.answer not in keys:
                problems.append(f"单选答案非法: {q.answer}")
        else:  # 多选
            if not set(q.answer) <= set(keys):
                problems.append(f"多选答案含非法键: {q.answer}")
    elif q.type is QuestionType.TRUE_FALSE:
        if q.answer not in ("true", "false"):
            problems.append(f"判断答案未归一: {q.answer}")
    elif q.type is QuestionType.FILL_BLANK:
        if q.answer is not None:
            problems.append("填空题不应带自动判分答案")

    return problems
