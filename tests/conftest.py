"""pytest 公共：把 src 加入 path，提供 Line 构造与合成题库夹具。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quizforge.models import Option, Question, QuestionType, SourceQuality

# 合成题库：复刻真实样本的关键版式特征（技术方案 3.1）
# - 题型分节 + 无标题主题切换
# - 单选答案内嵌题干括号（含空格）
# - 选项分隔符 A．/A./A、 混用，同行多选项
# - 选项尾中文夹注解析
# - 判断行尾 √/× + 夹注解析
# - 题干折行（%%PAGE%% 为分页标记，用于模拟跨页折行）
# - 单选题号跳号（缺 2）
SAMPLE_BANK = """一、填空
1.光的本质是：波粒二象性。
2.标准模型中传递弱相互作用的粒子是：W和Z玻色子。
二、单选
1.下列不属于光的三原色的是（ B ）
A．红 B．绿 C．蓝 D．黄
3.关于沸腾的说法正确的是（ C ）
A.水在0摄氏度沸腾（这是错误的常见误解）
B.任何液体压强越大沸点越高
C.标准大气压下纯水的沸点是100摄氏度
D.水的沸点与压强无关
%%PAGE%%
4.下列物理量中属于守恒量的是（ A ）
A．能量 B．瞬时速度 C．温度 D．体积
三、判断是非题
1.地球是太阳系中体积最大的行星。√
2.金星是太阳系表面平均温度最高的行星。×（温度最高的是金星，该判断为错是为了测试夹注解析）
3.光在真空中的传播速度约为每秒30万公里。√
"""


def make_lines(raw: str):
    """按 %%PAGE%% 分页标记构造 clean.Line 列表。"""
    from quizforge.parsing.clean import Line

    lines: list[Line] = []
    page = 1
    for text in raw.splitlines():
        if text.strip() == "%%PAGE%%":
            page += 1
            continue
        if text.strip():
            lines.append(Line(text=text, page_no=page))
    return lines


@pytest.fixture
def sample_lines():
    return make_lines(SAMPLE_BANK)


@pytest.fixture
def single_choice_question():
    return Question(
        bank_id="b_test",
        q_no=1,
        type=QuestionType.SINGLE_CHOICE,
        stem="下列不属于光的三原色的是",
        options=[Option(key=k, text=t) for k, t in
                 zip("ABCD", ["红", "绿", "蓝", "黄"])],
        answer="B",
        source_quality=SourceQuality.RULE,
    )


@pytest.fixture
def tf_question():
    return Question(
        bank_id="b_test",
        q_no=2,
        type=QuestionType.TRUE_FALSE,
        stem="地球是太阳系中体积最大的行星。",
        answer="true",
        source_quality=SourceQuality.RULE,
    )
