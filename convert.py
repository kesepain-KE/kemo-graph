"""项目根目录的文档转换 CLI 入口。

用法：
    python convert.py input.pdf -o output.md
    python -m markitdown input.pdf -o output.md
"""

from __future__ import annotations

from markitdown.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
