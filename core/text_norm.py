"""Shared matching keys; display text retains its original capitalization."""

import unicodedata


def norm_key(value: str) -> str:
    """Apply NFKC, case folding and whitespace folding for text comparisons."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
