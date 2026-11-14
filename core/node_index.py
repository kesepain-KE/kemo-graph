from __future__ import annotations
"""Temporary migration imports; removed in stage 10."""
from core.graph.index import NodeMatcher
from collections import defaultdict
from collections.abc import Iterable
from ahocorasick_rs import AhoCorasick
import numpy as np
from rapidfuzz import fuzz, process
from core.common.normalization import norm_key
