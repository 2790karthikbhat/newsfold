from __future__ import annotations

from newsfold.web.digest_history import list_digest_files, read_digest_file


def test_list_digest_files_sorted_newest_first(tmp_path) -> None:
    (tmp_path / "digest-2026-01-01.html").write_text("a", encoding="utf-8")
    (tmp_path / "digest-2026-01-03.html").write_text("b", encoding="utf-8")
    (tmp_path / "digest-2026-01-02.md").write_text("c", encoding="utf-8")
    (tmp_path / "not-a-digest.txt").write_text("x", encoding="utf-8")

    files = list_digest_files(out_dir=tmp_path)

    assert [f.run_date for f in files] == ["2026-01-03", "2026-01-02", "2026-01-01"]


def test_list_digest_files_missing_dir_returns_empty(tmp_path) -> None:
    assert list_digest_files(out_dir=tmp_path / "missing") == []


def test_read_digest_file_roundtrip(tmp_path) -> None:
    (tmp_path / "digest-2026-01-01.html").write_text("<p>hi</p>", encoding="utf-8")

    assert read_digest_file("2026-01-01", out_dir=tmp_path) == "<p>hi</p>"
    assert read_digest_file("2026-01-01", fmt="md", out_dir=tmp_path) is None
    assert read_digest_file("2099-01-01", out_dir=tmp_path) is None
