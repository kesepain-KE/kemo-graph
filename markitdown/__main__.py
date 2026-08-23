"""``python -m markitdown`` CLI。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import MarkItDown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="markitdown",
        description="将本地普通文档转换为 Markdown",
    )
    parser.add_argument("input_file", help="输入文件的本地路径")
    parser.add_argument("-o", "--output", help="输出 Markdown 文件；缺省时写入标准输出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = MarkItDown().convert(args.input_file)
        if args.output:
            destination = Path(args.output).expanduser()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                result.markdown or result.text_content,
                encoding="utf-8",
            )
        else:
            sys.stdout.write(result.markdown or result.text_content)
        return 0
    except Exception as exc:
        print(f"转换失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
