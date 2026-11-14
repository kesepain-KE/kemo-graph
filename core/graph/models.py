from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class _Node:
    node_id: str
    keyword: str
    summary: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    weight: float
    ref_count: int

@dataclass(frozen=True)
class _NodeKeys:
    keyword: str
    aliases: frozenset[str]
    descriptive: frozenset[str]
    terms: frozenset[str]

@dataclass(frozen=True)
class _EntityKeys:
    primary: str
    alternate: frozenset[str]
    terms: frozenset[str]

@dataclass(frozen=True)
class _MatchedNode:
    node: _Node
    match_score: float

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class MergeCandidate:
    left_node_id: str
    right_node_id: str
    left_keyword: str
    right_keyword: str
    score: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "left_node_id": self.left_node_id,
            "right_node_id": self.right_node_id,
            "left_keyword": self.left_keyword,
            "right_keyword": self.right_keyword,
            "score": round(self.score, 6),
            "reason": self.reason,
        }
