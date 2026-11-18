"""Shared team-name matching for the sports providers. Word-boundary, not plain
substring — "India" must not match "Mumbai Indians" (a real false positive a
naive `in` check produced), nor should any other short followed name accidentally
match inside a longer unrelated team name."""

from __future__ import annotations

import re


def matches_team(term: str, *names: str) -> bool:
    pattern = rf"\b{re.escape(term.lower())}\b"
    return any(re.search(pattern, name.lower()) for name in names if name)
