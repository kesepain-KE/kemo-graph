"""Organizer candidate equivalence, thresholds, all aliases and stable limits."""

from difflib import SequenceMatcher
import json
import math

import pytest

from bench.bench_text_matching import fixture, load_baseline
from core.graph.candidates import _find_candidates


def insert_node(connection, node_id, keyword, *, aliases=(), summary=''):
    connection.execute('INSERT INTO nodes VALUES(?,?,?,?,?,?,?)', (node_id, keyword, summary, json.dumps(list(aliases)), '[]', 1.0, 1))


def test_hash_groups_find_keywords_shared_aliases_and_cross_aliases():
    connection, _ = fixture(0)
    try:
        insert_node(connection, 'a', 'GPT', aliases=['Language model'])
        insert_node(connection, 'b', 'gpt', aliases=['Other'])
        insert_node(connection, 'c', 'Third', aliases=['Language Model'])
        insert_node(connection, 'd', 'Language Model')
        candidates = _find_candidates(connection, threshold=1.0)
        assert {(item.left_node_id, item.right_node_id) for item in candidates} == {('a', 'b'), ('a', 'c'), ('a', 'd'), ('c', 'd')}
        assert all(item.reason == 'exact_term' and item.score == 1.0 for item in candidates)
    finally:
        connection.close()


def test_similarity_keeps_lexical_threshold_equality():
    connection, _ = fixture(0)
    try:
        insert_node(connection, 'a', 'abcdefghij')
        insert_node(connection, 'b', 'abcdefghiX')
        assert _find_candidates(connection, threshold=0.9)[0].score == 0.9
        assert _find_candidates(connection, threshold=math.nextafter(0.9, math.inf)) == []
    finally:
        connection.close()


def test_weighted_summary_and_adjacency_can_rescue_lower_lexical_scores():
    connection, _ = fixture(0)
    try:
        insert_node(connection, 'a', 'abcd', summary='Shared overview')
        insert_node(connection, 'b', 'abef', summary='Shared overview')
        insert_node(connection, 'z', 'ZZZZ')
        connection.executemany('INSERT INTO edges VALUES(?,?,?)', [('a', 'uses', 'z'), ('b', 'uses', 'z')])
        lexical = SequenceMatcher(None, 'abcd', 'abef').ratio()
        expected = lexical * 0.65 + 1.0 * 0.2 + 1.0 * 0.15
        candidates = _find_candidates(connection, threshold=expected)
        assert len(candidates) == 1
        assert candidates[0].left_node_id == 'a' and candidates[0].right_node_id == 'b'
        assert candidates[0].reason == 'semantic_similarity'
        assert candidates[0].score == expected
        assert _find_candidates(connection, threshold=math.nextafter(expected, math.inf)) == []
    finally:
        connection.close()


def test_short_aliases_participate_in_similarity():
    connection, _ = fixture(0)
    try:
        insert_node(connection, 'a', 'xxxxxxxxxxxxxxxxxxxxxxxxx', aliases=['abcdefghi'])
        insert_node(connection, 'b', 'zzzzzzzzzzzzzzzzzzzzzzzzz', aliases=['abcdefghX'])
        candidates = _find_candidates(connection, threshold=0.88)
        assert len(candidates) == 1 and candidates[0].reason == 'semantic_similarity'
        assert candidates[0].score == pytest.approx(8 / 9)
    finally:
        connection.close()


def test_exact_page_is_stably_sorted_and_limited_to_200():
    connection, _ = fixture(0)
    try:
        for index in reversed(range(24)):
            insert_node(connection, f'n{index:02d}', 'Same')
        candidates = _find_candidates(connection, threshold=0.86)
        expected_pairs = [(f'n{left:02d}', f'n{right:02d}') for left in range(24) for right in range(left + 1, 24)]
        assert [(item.left_node_id, item.right_node_id) for item in candidates] == expected_pairs[:200]
    finally:
        connection.close()


@pytest.mark.parametrize('threshold', [0.0, 0.65, 0.86, 1.0])
def test_small_candidate_results_and_scores_are_identical(threshold):
    original = load_baseline('graph_organizer')
    connection, _ = fixture(12)
    try:
        assert [item.as_dict() for item in _find_candidates(connection, threshold=threshold)] == [item.as_dict() for item in original._find_candidates(connection, threshold=threshold)]
    finally:
        connection.close()


def test_500_node_candidate_results_and_scores_are_identical():
    original = load_baseline('graph_organizer')
    connection, rows = fixture(500)
    try:
        for index in range(1, 13):
            connection.execute('UPDATE nodes SET keyword=? WHERE node_id=?', (rows[index - 1][1][:-1] + 'X', rows[index][0]))
        for index in range(20, 30, 2):
            connection.execute('UPDATE nodes SET aliases=? WHERE node_id=?', (json.dumps([rows[index + 1][1]]), rows[index][0]))
        connection.executemany('INSERT INTO edges VALUES(?,?,?)', [(rows[index][0], 'uses', rows[499][0]) for index in range(12)])
        expected = original._find_candidates(connection, threshold=0.86)
        actual = _find_candidates(connection, threshold=0.86)
        assert expected and {item.reason for item in expected} == {'exact_term', 'semantic_similarity'}
        assert [item.as_dict() for item in actual] == [item.as_dict() for item in expected]
    finally:
        connection.close()
