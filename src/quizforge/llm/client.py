"""LLM 兜底客户端：OpenAI 兼容协议（DeepSeek）。

职责边界（技术方案 ADR-2/3/4）：
- 只兜底"规则解析失败/校验存疑"的题块，成功过的字段不允许 LLM 改动；
- 输出必须过 Pydantic Schema 校验，不合规重试一次，再失败入隔离区；
- 不参与判分。
"""

from __future__ import annotations

import json

from ..models import Option, Question, QuestionType, SourceQuality

_SCHEMA_HINT = """{
  "q_no": 12,
  "type": "single_choice | multi_choice | true_false | fill_blank",
  "stem": "题干（去掉行首题号与答案括号）",
  "options": [{"key": "A", "text": "选项文本"}],
  "answer": "单选单个字母 / 多选字母组合 / 判断 true或false / 填空 null",
  "analysis": "解析（无则 null）"
}"""

_SYSTEM_PROMPT = (
    "你是题库结构化抽取引擎。给定一段从 PDF 抽取的原始题块文本（可能含折行、"
    "页眉页脚残留、答案内嵌题干括号、选项分隔符混乱），请抽取出结构化题目。"
    "只输出 JSON（不要 Markdown 代码块），字段如下：\n" + _SCHEMA_HINT + "\n"
    "规则：题干末尾括号内的单选字母（如 (C )）是答案，要从题干中剥离；"
    "判断题行尾的 √/× 对应 answer 的 true/false；括号中文夹注通常是解析。"
)


class LLMClient:
    def __init__(self, api_key: str | None, base_url: str, model: str, context_blocks: int = 2):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.context_blocks = context_blocks
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def chat(self, system: str, user: str, json_mode: bool = False, max_tokens: int = 800) -> str:
        """通用对话调用（M3 讲解生成用）。失败抛异常，由调用方降级。"""
        if not self.available:
            raise RuntimeError("LLM API key 未配置")
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._get_client().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
            **kwargs,
        )
        return (resp.choices[0].message.content or "").strip()

    def rescue_block(self, raw_block: str, context: str = "", qtype_hint=None) -> Question | None:
        """对单个失败题块做一次兜底抽取；Schema 不过则重试一次；仍失败返回 None。"""
        if not self.available:
            return None
        user_content = f"原始题块：\n{raw_block}"
        if context:
            user_content += f"\n\n相邻题块（仅供判断题型与格式，不要抽取）：\n{context}"
        last_error: Exception | None = None
        for _ in range(2):  # 一次 + 重试一次
            try:
                resp = self._get_client().chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                payload = json.loads(resp.choices[0].message.content)
                return self._to_question(payload)
            except Exception as exc:  # noqa: BLE001 - 重试一次后交隔离区
                last_error = exc
        raise RuntimeError(f"LLM 兜底两次尝试均失败: {last_error}")

    @staticmethod
    def _to_question(payload: dict) -> Question:
        """按 Schema 构造题目（Pydantic 强校验）。

        注意：语义校验（题号/答案归属等）不在这里跑——q_no 等信息 LLM 无从
        从文本推断，由管道回填后统一过 validate_question（技术方案 3.4）。
        """
        qtype = QuestionType(payload["type"])
        options = [Option(**o) for o in payload.get("options", [])]
        answer = payload.get("answer")
        if qtype is QuestionType.MULTI_CHOICE and isinstance(answer, str):
            answer = "".join(sorted(set(answer.upper())))
        return Question(
            bank_id="pending",
            q_no=payload.get("q_no"),
            type=qtype,
            stem=(payload.get("stem") or "").strip(),
            options=options,
            answer=answer,
            analysis=payload.get("analysis"),
            source_quality=SourceQuality.LLM,
        )
