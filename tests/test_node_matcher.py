"""Literal recall, equivalent reranking and content-based index invalidation."""

import json

import pytest

from bench.bench_text_matching import fixture, load_baseline
from core.settings.schema import AppConfig
from core.storage.connections import connect_graph
from core.graph.entity import Entity
from core.graph.engine import GraphEngine
from core.graph.errors import GraphQueryError
from core.graph.matching import _matched_node_to_dict
from core.graph.index import NodeMatcher


def insert_node(connection, node_id, keyword, *, aliases=(), summary='', tags=(), ref_count=1):
    connection.execute(
        'INSERT INTO nodes VALUES(?,?,?,?,?,?,?)',
        (node_id, keyword, summary, json.dumps(list(aliases)), json.dumps(list(tags)), 1.0, ref_count),
    )


def test_all_overlaps_aliases_casefold_and_mixed_language_offsets():
    matcher = NodeMatcher([
        ('kg', ['知识图谱', '语义网络']), ('graph', ['图谱']),
        ('sql', ['SQLite FTS5']), ('gpt', ['GPT-4o']),
    ])
    matches = matcher.match_terms(['查询知识图谱与语义网络', 'ＳＱＬｉｔｅ\t FTS5 GPT-4o'])
    assert matches['kg'] == [(2, 6), (7, 11)]
    assert matches['graph'] == [(4, 6)]
    assert matches['sql'] == [(0, 11)]
    assert matches['gpt'] == [(12, 18)]
    assert set(matcher.match_terms(['知识'])) == {'kg'}
    assert matcher.match_terms(['知识'], include_containing=False) == {}


@pytest.mark.parametrize('query', ['', '   ', '...!?', '图'])
def test_empty_symbols_and_single_character_substrings_do_not_expand(query):
    matcher = NodeMatcher([('kg', ['知识图谱'])])
    assert matcher.match_terms([query]) == {}


def test_exact_single_character_keywords_and_shared_aliases_are_retained():
    matcher = NodeMatcher([('a', ['图', '语义网络']), ('b', ['语义网络'])])
    assert matcher.match_terms(['图']) == {'a': [(0, 1)]}
    assert set(matcher.match_terms(['语义网络'])) == {'a', 'b'}


def test_index_follows_insert_update_rollback_and_delete():
    engine = object.__new__(GraphEngine)
    connection, _ = fixture(0)
    entity = Entity('Alpha', 'concept', 'Alpha', [], 1.0)
    try:
        assert engine._match_nodes(connection, [entity], 0.85) == []
        insert_node(connection, 'a', 'Alpha')
        connection.commit()
        assert engine._match_nodes(connection, [entity], 0.85)[0].node.node_id == 'a'
        connection.execute("UPDATE nodes SET keyword='Other',aliases='[\"Alpha\"]' WHERE node_id='a'")
        hit = engine._match_nodes(connection, [entity], 0.85)[0]
        assert hit.node.keyword == 'Other' and hit.match_score == 0.9
        connection.rollback()
        assert engine._match_nodes(connection, [entity], 0.85)[0].node.keyword == 'Alpha'
        connection.execute("UPDATE nodes SET keyword='Other' WHERE node_id='a'")
        connection.commit()
        assert engine._match_nodes(connection, [entity], 0.85) == []
        connection.execute("DELETE FROM nodes WHERE node_id='a'")
        connection.commit()
        assert engine._match_nodes(connection, [entity], 0.85) == []
    finally:
        connection.close()


def test_cached_nodes_are_not_mutated_by_public_results():
    engine = object.__new__(GraphEngine)
    connection, _ = fixture(0)
    try:
        insert_node(connection, 'a', 'Alpha', aliases=['First'], tags=['stable'])
        entity = Entity('Alpha', 'concept', 'Alpha', [], 1.0)
        result = _matched_node_to_dict(engine._match_nodes(connection, [entity], 0.7)[0])
        result['aliases'].append('Changed')
        result['tags'].clear()
        again = _matched_node_to_dict(engine._match_nodes(connection, [entity], 0.7)[0])
        assert again['aliases'] == ['First'] and again['tags'] == ['stable']
    finally:
        connection.close()


@pytest.mark.parametrize('threshold', [0.0, 0.7, 0.78, 0.85, 0.9, 1.0])
def test_candidates_preserve_fuzzy_summary_and_alias_results(threshold):
    original = load_baseline('graph_engine')
    engine = object.__new__(GraphEngine)
    connection, _ = fixture(0)
    try:
        for index in range(24):
            insert_node(connection, str(index), 'Product ' + str(index), summary='知识图谱的构建流程', ref_count=index)
        insert_node(connection, 'alias', 'Index', aliases=['SQLite FTS5'])
        insert_node(connection, 'fuzzy', 'abcdefghij')
        insert_node(connection, 'blank', '', aliases=['空白'])
        for entity in [
            Entity('知识图谱', 'concept', '知识图谱', [], 1.0),
            Entity('abcdefghiX', 'concept', 'abcdefghiX', [], 0.1),
            Entity('SQLite', 'concept', 'SQLite', ['SQLite FTS5'], 0.5),
            Entity('空白', 'concept', '', [], 1.0),
        ]:
            expected = original.GraphEngine._match_nodes(engine, connection, [entity], threshold)
            actual = engine._match_nodes(connection, [entity], threshold)
            assert [_matched_node_to_dict(item) for item in actual] == [original._matched_node_to_dict(item) for item in expected]
        summary_hits = engine._match_nodes(connection, [Entity('知识图谱', 'concept', '知识图谱', [], 1.0)], 0.7)
        assert len(summary_hits) == 20
    finally:
        connection.close()


def test_raw_query_recalls_nodes_omitted_by_extractor(tmp_path):
    engine = GraphEngine(
        tmp_path / 'data',
        settings=AppConfig(log_dir=str(tmp_path / 'log'), query_planning={'mode': 'off'}),
        entity_extractor=lambda _: [],
    )
    connection = connect_graph(engine.paths)
    try:
        with connection:
            connection.executemany(
                "INSERT INTO nodes(node_id,keyword,summary,aliases) VALUES(?,?,'',?)",
                [('kg', '知识图谱', '["语义网络"]'), ('gpt', 'GPT-4o', '[]')],
            )
    finally:
        connection.close()
    assert {node['node_id'] for node in engine.query('语义网络与 GPT-4o', depth=1)['hit_nodes']} == {'kg', 'gpt'}
    assert engine.query('...!?', depth=1)['hit_nodes'] == []
    for invalid in ['', 'a' * 1001, 'bad\0query']:
        with pytest.raises(GraphQueryError):
            engine.query(invalid)
