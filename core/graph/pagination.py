from __future__ import annotations

from core.common.text import required_text as _required_text
from core.graph.errors import GraphRevisionChangedError

def _validate_pagination(
    page: int,
    page_size: int,
    *,
    max_page_size: int,
) -> tuple[int, int]:
    return (
        _validate_integer(page, "page", minimum=1),
        _validate_integer(
            page_size,
            "page_size",
            minimum=1,
            maximum=max_page_size,
        ),
    )

def _validate_integer(
    value: int,
    name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} 必须是整数")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ValueError(f"{name} 必须大于等于 {minimum}")
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value

def _require_revision(expected: str | None, current: str) -> None:
    if expected is None:
        return
    normalized = _required_text(expected, "expected_revision").casefold()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("expected_revision 必须是 64 位十六进制字符串")
    if normalized != current:
        raise GraphRevisionChangedError(normalized, current)

def _pagination(page: int, page_size: int, total: int) -> dict[str, int]:
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }
