from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from provider.engine import chat_structured
from core.settings import AppConfig
from core.documents.chunker.rules import _pre_split_by_paragraphs, _semantic_blocks, _validated_block_ranges

_CHUNKING_PROMPT_PATH = Path(__file__).resolve().parents[3] / "config" / "chunking_prompt.md"

_CHUNKING_PROMPT_VERSION = "boundary-v2"

_CHUNKING_SCHEMA = {
    "type": "object",
    "properties": {
        "chunks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "start_block": {"type": "integer", "minimum": 1},
                    "end_block": {"type": "integer", "minimum": 1},
                },
                "required": ["start_block", "end_block"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["chunks"],
    "additionalProperties": False,
}

LOGGER = logging.getLogger(__name__)

def _llm_chunk_ranges(text: str, settings: AppConfig) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    offset = 0
    for part in _pre_split_by_paragraphs(
        text,
        settings.chunking_llm_max_input_chars,
    ):
        if part.strip():
            ranges.extend(
                (offset + start, offset + end)
                for start, end in _llm_part_ranges(part, settings)
            )
        offset += len(part)
    if offset != len(text):
        raise ValueError("长文档预切结果未完整覆盖原文")
    return ranges

def _llm_part_ranges(text: str, settings: AppConfig) -> list[tuple[int, int]]:
    blocks = _semantic_blocks(text)
    if not blocks:
        return []
    if len(blocks) == 1:
        return [(blocks[0].char_start, blocks[0].char_end)]

    prompt = _CHUNKING_PROMPT_PATH.read_text(encoding="utf-8")
    user = json.dumps(
        {
            "blocks": [
                {"id": index, "text": block.text}
                for index, block in enumerate(blocks, start=1)
            ]
        },
        ensure_ascii=False,
    )
    response = chat_structured(
        prompt,
        user,
        _CHUNKING_SCHEMA,
        settings=settings,
        tool_name="submit_chunk_boundaries",
    )
    return _validated_block_ranges(response, blocks)

def _chunking_prompt_digest() -> str:
    try:
        content = _CHUNKING_PROMPT_PATH.read_bytes()
    except OSError:
        return "missing"
    return hashlib.sha256(content).hexdigest()
