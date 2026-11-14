"""Matching normalization and case-preserving entity extraction."""

import json

import pytest

from core.config import AppConfig
from core.entity_extractor import extract
from core.text_norm import norm_key


@pytest.mark.parametrize(("left", "right"), [
    ("ＧＰＴ-4o", "gpt-4o"),
    ("Straße", "STRASSE"),
    ("  SQLite\t FTS5\n", "sqlite fts5"),
    ("知识图谱", "知识图谱"),
    ("", "   "),
])
def test_matching_keys_fold_width_case_and_whitespace(left, right):
    assert norm_key(left) == norm_key(right)


def test_matching_keys_keep_word_boundaries():
    assert norm_key("武汉") != norm_key("武 汉")


def test_extraction_merges_matching_names_without_lowercasing_display(tmp_path):
    payload = {"entities": [
        {"text": "ＧＰＴ", "normalized": "GPT", "type": "Product", "aliases": ["gpt", "OpenAI GPT", "ＯｐｅｎＡＩ ＧＰＴ"], "confidence": 0.95},
        {"text": "gpt", "normalized": "gpt", "type": "product", "aliases": [], "confidence": 0.8},
    ]}
    entities = extract(["GPT"], settings=AppConfig(log_dir=str(tmp_path), entity_extraction={"method": "llm"}), llm=lambda *_: json.dumps(payload))
    assert len(entities) == 1
    assert entities[0].text == entities[0].normalized == "GPT"
    assert entities[0].type == "Product"
    assert entities[0].aliases == ["OpenAI GPT"]
