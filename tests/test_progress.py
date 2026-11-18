from __future__ import annotations

import io

from newsfold.progress import noop, null_stage, stage_progress


class _Stream(io.StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_noop_and_null_stage() -> None:
    noop(1, 2, "x")  # no error, no output
    with null_stage("whatever") as cb:
        assert cb is noop


def test_stage_progress_plain_stream_writes_lines() -> None:
    stream = _Stream(tty=False)
    with stage_progress("Decoding", stream=stream) as cb:
        cb(1, 10, "site-a")  # first call always prints
        cb(2, 10, "site-b")  # throttled (within 3s)
        cb(10, 10, "site-j")  # final -> prints despite throttle
    text = stream.getvalue()
    assert "Decoding…" in text
    assert "1/10" in text
    assert "10/10" in text
    assert "2/10" not in text  # throttled out


def test_stage_progress_tty_path_does_not_crash() -> None:
    stream = _Stream(tty=True)
    with stage_progress("Ranking", stream=stream) as cb:
        cb(0, 3, "F1")
        cb(1, 3, "F1")
        cb(3, 3, "done")
    # rich rendered to a non-real terminal; just assert the callback ran cleanly
    assert isinstance(stream.getvalue(), str)
