"""模块入口：支持 `python -m quizforge <command>`。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
