"""Reproducible comparisons against the original implementation; excluded from pytest.

Run: python tests/bench/bench_text_matching.py --nodes 2000 --entities 5
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
from time import perf_counter
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BASE_REVISION = "4a03285"


def load_baseline(name: str):
    module_name = "core._benchmark_original_" + name
    if module_name not in sys.modules:
        source = subprocess.check_output(
            ["git", "show", f"{BASE_REVISION}:core/{name}.py"], cwd=ROOT,
        ).decode("utf-8")
        module = ModuleType(module_name)
        module.__package__ = "core"
        sys.modules[module_name] = module
        exec(compile(source, f"{BASE_REVISION}:core/{name}.py", "exec"), module.__dict__)
    return sys.modules[module_name]


def fixture(count: int):
    rng = random.Random(42)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE nodes(node_id TEXT PRIMARY KEY,keyword TEXT,summary TEXT,aliases TEXT,tags TEXT,weight REAL,ref_count INTEGER);"
        "CREATE TABLE edges(source_node_id TEXT,relation TEXT,target_node_id TEXT);"
    )
    rows = []
    for index in range(count):
        name = "".join(rng.choices("abcdefghijklmnopqrstuvwxyz", k=14))
        rows.append((f"n{index:05d}", name, "Description of " + name, json.dumps([name + "-alias"]), "[]", 1.0, index % 7))
    connection.executemany("INSERT INTO nodes VALUES(?,?,?,?,?,?,?)", rows)
    return connection, rows


def timed(call, repeats=1):
    elapsed = []
    for _ in range(repeats):
        start = perf_counter()
        result = call()
        elapsed.append(perf_counter() - start)
    return min(elapsed), result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, nargs="+", default=[2000])
    parser.add_argument("--entities", type=int, nargs="+", default=[5])
    parser.add_argument("--organizer", action="store_true")
    args = parser.parse_args()
    from core.entity_extractor import Entity
    from core.graph_engine import GraphEngine, _indexed_nodes
    from core.graph_organizer import _find_candidates
    engine = object.__new__(GraphEngine)
    print("| mode | nodes | entities | original seconds | cold seconds | warm seconds | warm speedup | equal |")
    print("|---|---:|---:|---:|---:|---:|---:|---|")
    for count in args.nodes:
        connection, rows = fixture(count)
        try:
            if args.organizer:
                original = load_baseline("graph_organizer")
                before, expected = timed(lambda: original._find_candidates(connection, threshold=0.86))
                after, actual = timed(lambda: _find_candidates(connection, threshold=0.86))
                assert [item.as_dict() for item in actual] == [item.as_dict() for item in expected]
                print(f"| organizer | {count} | - | {before:.6f} | {after:.6f} | - | {before / after:.2f}x | yes |", flush=True)
            else:
                original = load_baseline("graph_engine")
                for entities_count in args.entities:
                    entities = [Entity(row[1], "concept", row[1], [], 1.0) for row in rows[:entities_count]]
                    before, expected = timed(lambda: original.GraphEngine._match_nodes(engine, connection, entities, 0.7))
                    _indexed_nodes.cache_clear()
                    cold, _ = timed(lambda: engine._match_nodes(connection, entities, 0.7))
                    after, actual = timed(lambda: engine._match_nodes(connection, entities, 0.7), repeats=3)
                    expected_values = [original._matched_node_to_dict(item) for item in expected]
                    from core.graph_engine import _matched_node_to_dict
                    assert [_matched_node_to_dict(item) for item in actual] == expected_values
                    print(f"| matching | {count} | {entities_count} | {before:.6f} | {cold:.6f} | {after:.6f} | {before / after:.2f}x | yes |", flush=True)
        finally:
            connection.close()


if __name__ == "__main__":
    main()
