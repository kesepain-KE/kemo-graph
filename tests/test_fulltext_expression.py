"""Real bundled tokenizer expressions, pruning and FTS execution."""

import sqlite3

import pytest

from core.storage.fulltext import _prune_expression, compile_fulltext, load_simple


@pytest.fixture
def database():
    connection = sqlite3.connect(":memory:")
    load_simple(connection)
    try:
        yield connection
    finally:
        connection.close()


def test_model_comparison_omits_function_words_and_standalone_hyphens(database):
    expression = compile_fulltext(database, "GPT-4o 和 o1-preview 有什么区别")
    assert '"-"' not in expression
    assert '"和"' not in expression
    assert '"什么"' not in expression
    assert "gpt*" in expression and "preview*" in expression
    database.execute("CREATE VIRTUAL TABLE examples USING fts5(text, tokenize='simple 0')")
    database.execute("INSERT INTO examples(text) VALUES('GPT-4o o1-preview')")
    assert database.execute("SELECT count(*) FROM examples WHERE examples MATCH ?", (expression,)).fetchone()[0] == 1


def test_chinese_word_expression_and_programming_names_stay_literal(database):
    assert compile_fulltext(database, "知识图谱") == '"知识" AND "图谱"'
    assert compile_fulltext(database, "C++") == '"C++"'
    assert compile_fulltext(database, "C#") == '"C#"'


@pytest.mark.parametrize("query", ["和 什么 如何", "what is the", '" OR * NOT', "...!?", ""])
def test_empty_function_word_and_operator_queries_produce_no_expression(database, query):
    assert compile_fulltext(database, query) == ""


def test_pruning_respects_quoted_and_and_escaped_quotes():
    assert _prune_expression('"Research AND Development" AND "-" AND "和"') == '"Research AND Development"'
    assert _prune_expression('"A"" AND B" AND "*" AND graph*') == '"A"" AND B" AND graph*'
    assert _prune_expression('andromeda* AND graph*') == 'andromeda* AND graph*'


@pytest.mark.parametrize("query", ["a" * 1001, "bad\0query"])
def test_invalid_query_limits_are_unchanged(database, query):
    with pytest.raises(ValueError, match="1000"):
        compile_fulltext(database, query)
