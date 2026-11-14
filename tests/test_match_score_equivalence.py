"""The compiled similarity is an upper bound, not an equivalent final score."""

from difflib import SequenceMatcher
import random

import pytest
from rapidfuzz.fuzz import ratio

from bench.bench_text_matching import fixture, load_baseline
from core.graph import Entity
from core.graph import GraphEngine
from core.graph.models import _Node
from core.graph.matching import _entity_keys, _node_keys, _node_match_score, _matched_node_to_dict


def term_pairs():
    rng = random.Random(41)
    pairs = [("tide", "diet"), ("ababab", "aaaaab"), ("GPT-4o", "gpt-4o"), ("知识图谱", "知识图谱构建")]
    for _ in range(300):
        length = rng.randint(4, 25)
        pairs.append(tuple("".join(rng.choices("abcde知识图谱", k=length)) for _ in range(2)))
    return pairs


def test_indel_is_an_upper_bound_but_not_a_drop_in_replacement():
    differences = []
    for left, right in term_pairs():
        original = SequenceMatcher(None, left, right).ratio()
        upper = ratio(left, right) / 100.0
        assert upper + 1e-12 >= original
        differences.append(abs(upper - original))
    assert sum(value >= 0.02 for value in differences) == 144
    assert max(differences) == pytest.approx(1 / 3)


def test_precomputed_match_scores_equal_the_original():
    original = load_baseline("graph_engine")
    for index, (left, right) in enumerate(term_pairs()):
        entity = Entity(left, "concept", left, ["Alias"] if index % 3 == 0 else [], 0.9)
        args = (str(index), right, left if index % 5 == 0 else "Summary", [left] if index % 7 == 0 else [], ["Tag"], 1.0, 1)
        expected = original._node_match_score(entity, original._Node(*args))
        assert _node_match_score(_entity_keys(entity), _node_keys(_Node(*args))) == expected


@pytest.mark.parametrize("threshold", [0.0, 0.5, 0.7, 0.85, 1.0])
@pytest.mark.parametrize("confidence", [0.0, 0.4, 1.0])
def test_matching_results_equal_the_original_at_confidence_boundaries(threshold, confidence):
    original = load_baseline("graph_engine")
    engine = object.__new__(GraphEngine)
    connection, rows = fixture(80)
    try:
        entities = [Entity(row[1][:-2], "concept", row[1][:-2], [], confidence) for row in rows[:3]]
        expected = original.GraphEngine._match_nodes(engine, connection, entities, threshold)
        actual = engine._match_nodes(connection, entities, threshold)
        assert [_matched_node_to_dict(item) for item in actual] == [original._matched_node_to_dict(item) for item in expected]
    finally:
        connection.close()
