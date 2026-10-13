"""Streamlit 启动入口：`streamlit run app.py`。

放在仓库根目录而不是 ui/ 包内，是为了让 `streamlit run` 能找到包
（相对导入在 `streamlit run` 直接执行子模块时会失效）。
"""

from quizforge.ui.app import main

main()
