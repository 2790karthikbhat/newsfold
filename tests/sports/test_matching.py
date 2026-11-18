from __future__ import annotations

from newsfold.sports.matching import matches_team


def test_matches_team_exact_word() -> None:
    assert matches_team("India", "India", "West Indies") is True


def test_matches_team_does_not_match_substring_inside_a_longer_word() -> None:
    # "India" is a plain substring of "Indians" — must not match on that alone.
    assert matches_team("India", "Mumbai Indians", "Chennai Super Kings") is False


def test_matches_team_multi_word_term() -> None:
    assert matches_team("Manchester United", "Manchester United FC", "Liverpool FC") is True
    assert matches_team("Manchester United", "Manchester City FC", "Liverpool FC") is False


def test_matches_team_case_insensitive() -> None:
    assert matches_team("india", "INDIA", "Australia") is True


def test_matches_team_ignores_empty_names() -> None:
    assert matches_team("India", "", None) is False  # type: ignore[arg-type]
