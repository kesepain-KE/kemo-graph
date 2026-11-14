"""Overlapping literal candidates and compiled fuzzy upper bounds."""

from collections import defaultdict
from collections.abc import Iterable

from ahocorasick_rs import AhoCorasick
import numpy as np
from rapidfuzz import fuzz, process

from .text_norm import norm_key


class NodeMatcher:
    def __init__(self, nodes: Iterable[tuple[str, Iterable[str]]]):
        by_term: dict[str, set[str]] = defaultdict(set)
        for node_id, values in nodes:
            for value in values:
                term = norm_key(value)
                by_term[term].add(node_id)
        self._empty_owners = by_term.pop("", set())
        self._terms = tuple(by_term)
        self._owners = tuple(frozenset(by_term[term]) for term in self._terms)
        self._automaton = AhoCorasick(list(self._terms)) if self._terms else None
        bigrams: dict[str, set[int]] = defaultdict(set)
        for index, term in enumerate(self._terms):
            for offset in range(len(term) - 1):
                bigrams[term[offset:offset + 2]].add(index)
        self._bigrams = {key: frozenset(values) for key, values in bigrams.items()}

    def match_terms(
        self, texts: Iterable[str], *, include_containing: bool = True,
    ) -> dict[str, list[tuple[int, int]]]:
        """Return all literal overlaps, with spans in each normalized input text.

        For a node term containing the entire input, the span covers that input.
        Raw-query recall disables that reverse direction to keep literal anchors precise.
        """
        matches: dict[str, list[tuple[int, int]]] = {}
        if self._automaton is None:
            return matches
        for value in texts:
            text = norm_key(value)
            if not text or not any(character.isalnum() for character in text):
                continue
            hits = self._automaton.find_matches_as_indexes(text, overlapping=True)
            indexes = [(index, start, end) for index, start, end in hits if end - start >= 2 or text == self._terms[index]]
            if include_containing and len(text) >= 2:
                indexes.extend(
                    (index, 0, len(text))
                    for index in self._bigrams.get(text[:2], ())
                    if text in self._terms[index]
                )
            for index, start, end in indexes:
                for node_id in self._owners[index]:
                    spans = matches.setdefault(node_id, [])
                    if (start, end) not in spans:
                        spans.append((start, end))
        return matches

    def fuzzy_candidates(self, texts: Iterable[str], *, cutoff: float) -> set[str]:
        normalized = tuple(norm_key(value) for value in texts)
        candidates = set(self._empty_owners) if "" in normalized else set()
        terms = tuple(term for term in normalized if term)
        if not terms or not self._terms:
            return candidates
        # A one-ULP margin avoids discarding exact threshold equality in floating point.
        floor = np.nextafter(cutoff * 100.0, -np.inf)
        scores = process.cdist(
            terms, self._terms, scorer=fuzz.ratio, score_cutoff=max(0.0, floor),
            dtype=np.float64,
        )
        for index in np.flatnonzero(np.any(scores >= floor, axis=0)):
            candidates.update(self._owners[index])
        return candidates
