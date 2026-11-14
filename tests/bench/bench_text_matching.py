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
        # Keep the pinned algorithms unchanged; only resolve their moved imports.
        for old_module, new_module in {
            "config": "core.settings",
            "db": "core.storage",
            "locks": "core.storage",
            "logger": "core.observability",
            "rag_engine": "core.rag",
            "entity_extractor": "core.graph.entity",
            "query_planner": "core.documents.query_planner",
            "query_progress": "core.observability",
        }.items():
            source = source.replace(f"from .{old_module} import", f"from {new_module} import")
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
    parser.add_argument("--baseline-timeout", type=float, default=120.0)
    parser.add_argument("--baseline-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    from core.graph import Entity
    from core.graph import GraphEngine
    from core.graph.matching import _indexed_nodes
    from core.graph.candidates import _find_candidates
    if args.baseline_only:
        connection, _ = fixture(args.nodes[0])
        try:
            original = load_baseline("graph_organizer")
            elapsed, expected = timed(lambda: original._find_candidates(connection, threshold=0.86))
            print(json.dumps([elapsed, [item.as_dict() for item in expected]]), flush=True)
        finally:
            connection.close()
        return
    engine = object.__new__(GraphEngine)
    print("| mode | nodes | entities | original seconds | cold seconds | warm seconds | warm speedup | equal |")
    print("|---|---:|---:|---:|---:|---:|---:|---|")
    for count in args.nodes:
        connection, rows = fixture(count)
        try:
            if args.organizer:
                try:
                    baseline = subprocess.run(
                        [sys.executable, str(Path(__file__).resolve()), "--organizer", "--baseline-only", "--nodes", str(count)],
                        capture_output=True, text=True, check=True, timeout=args.baseline_timeout,
                    )
                    before, expected = json.loads(baseline.stdout)
                except subprocess.TimeoutExpired:
                    before, expected = None, None
                after, actual = timed(lambda: _find_candidates(connection, threshold=0.86))
                if expected is not None:
                    assert [item.as_dict() for item in actual] == expected
                    print(f"| organizer | {count} | - | {before:.6f} | {after:.6f} | - | {before / after:.2f}x | yes |", flush=True)
                else:
                    print(f"| organizer | {count} | - | timeout ({args.baseline_timeout:g}s) | {after:.6f} | - | - | checked separately at 500 nodes |", flush=True)
            else:
                original = load_baseline("graph_engine")
                for entities_count in args.entities:
                    entities = [Entity(row[1], "concept", row[1], [], 1.0) for row in rows[:entities_count]]
                    before, expected = timed(lambda: original.GraphEngine._match_nodes(engine, connection, entities, 0.7))
                    _indexed_nodes.cache_clear()
                    cold, _ = timed(lambda: engine._match_nodes(connection, entities, 0.7))
                    after, actual = timed(lambda: engine._match_nodes(connection, entities, 0.7), repeats=3)
                    expected_values = [original._matched_node_to_dict(item) for item in expected]
                    from core.graph.matching import _matched_node_to_dict
                    assert [_matched_node_to_dict(item) for item in actual] == expected_values
                    print(f"| matching | {count} | {entities_count} | {before:.6f} | {cold:.6f} | {after:.6f} | {before / after:.2f}x | yes |", flush=True)
        finally:
            connection.close()


if __name__ == "__main__":
    main()
