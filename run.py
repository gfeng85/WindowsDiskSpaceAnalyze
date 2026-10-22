"""DiskScope 启动入口。

用途：不依赖 `python -m diskscope`，避免在 embeddable Python 下
（其 ._pth 不把当前目录加入 sys.path）出现 "No module named diskscope"。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diskscope.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
