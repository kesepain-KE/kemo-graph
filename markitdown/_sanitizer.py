"""Markdown 中面向知识库的内容净化。

转换器的职责是尽可能还原文档结构，但知识图谱和向量索引只需要文本。
因此这里集中移除图片、媒体、脚本以及意外混入的 Base64/二进制内容，
并把带有说明文字的媒体替换成短的纯文本占位符。
"""

from __future__ import annotations

import re


_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*(?:<[^>]*>|[^)\s]+)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)",
    re.IGNORECASE,
)
_MARKDOWN_REFERENCE_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\s*\[[^\]]*\]", re.IGNORECASE
)
_MEDIA_TAG_RE = re.compile(
    r"<(?P<tag>img|svg|video|audio|iframe|object|embed|canvas)\b"
    r"(?P<attrs>[^>]*?)(?:/>|>.*?</(?P=tag)\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_MEDIA_VOID_TAG_RE = re.compile(
    r"<(?P<tag>img|svg|video|audio|iframe|object|embed|canvas)\b"
    r"(?P<attrs>[^>]*?)/?>",
    re.IGNORECASE,
)
_MEDIA_ATTR_RE = re.compile(
    r"(?:alt|title|aria-label)\s*=\s*([\"'])(.*?)\1",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_STYLE_RE = re.compile(
    r"<(?:script|style|noscript|template)\b[^>]*>.*?</(?:script|style|noscript|template)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_DATA_URI_RE = re.compile(
    r"data:[^\s<>\"')]+",
    re.IGNORECASE,
)
_LONG_BASE64_LINE_RE = re.compile(r"[A-Za-z0-9+/=_-]{2048,}")
_INVISIBLE_OBJECT_RE = re.compile(r"[\ufffc\ufffe\uffff]")


def _media_label(tag: str, attrs: str) -> str:
    match = _MEDIA_ATTR_RE.search(attrs)
    label = " ".join((match.group(2) if match else "").split())
    kind = "图片" if tag.casefold() == "img" else "媒体"
    return f"[{kind}：{label}]" if label else f"[{kind}内容已清理]"


def _replace_markdown_image(match: re.Match[str]) -> str:
    alt = " ".join(match.group("alt").split())
    return f"[图片：{alt}]" if alt else "[图片内容已清理]"


def _replace_media_tag(match: re.Match[str]) -> str:
    return _media_label(match.group("tag"), match.group("attrs"))


def _remove_long_base64_lines(text: str) -> str:
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if len(stripped) >= 2048 and _looks_like_base64_payload(stripped):
            lines.append("[二进制内容已清理]")
        else:
            lines.append(line)
    return "\n".join(lines)


def _looks_like_base64_payload(value: str) -> bool:
    """保守识别独立的 Base64 行，避免误删普通长文本。

    仅字符集匹配会把 ``aaaa...`` 一类合法但正常的文本误判为编码。
    图片编码通常同时包含大小写、数字或 ``+/=_-``，且具有足够的字符
    多样性；这些条件也让本规则只负责防御明显的二进制碎片。
    """

    if _LONG_BASE64_LINE_RE.fullmatch(value) is None:
        return False
    if len(set(value)) < 12:
        return False
    classes = (
        any(char.islower() for char in value),
        any(char.isupper() for char in value),
        any(char.isdigit() for char in value),
        any(char in "+/=_-" for char in value),
    )
    return sum(classes) >= 3


def sanitize_knowledge_markdown(text: str) -> str:
    """只保留适合知识索引的文本，并保持可读的 Markdown 结构。

    该函数不读取文件、不访问网络且是幂等的。它不会根据文件名或哈希
    修改源文件；调用方可以继续用原始字节计算来源身份，只把返回值送入
    Graph/RAG 和预览链路。
    """

    value = str(text)
    value = _SCRIPT_STYLE_RE.sub("", value)
    value = _HTML_COMMENT_RE.sub("", value)
    value = _MARKDOWN_IMAGE_RE.sub(_replace_markdown_image, value)
    value = _MARKDOWN_REFERENCE_IMAGE_RE.sub(_replace_markdown_image, value)
    value = _MEDIA_TAG_RE.sub(_replace_media_tag, value)
    value = _MEDIA_VOID_TAG_RE.sub(_replace_media_tag, value)
    value = _DATA_URI_RE.sub("[二进制内容已清理]", value)
    value = _INVISIBLE_OBJECT_RE.sub("", value)
    return _remove_long_base64_lines(value)


__all__ = ["sanitize_knowledge_markdown"]
