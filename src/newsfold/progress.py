"""CLI progress reporting.

Stage functions take an optional `ProgressCallback` and call it once per item with
`(done, total, note)` — `total` is 0 until known. The CLI wraps each stage in
`stage_progress()`: a live `rich` bar on a TTY, a throttled plain line otherwise
(cron, pipes). Business logic never imports `rich`.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TextIO

ProgressCallback = Callable[[int, int, str], None]


def noop(done: int, total: int, note: str) -> None:  # noqa: ARG001
    """Default no-op progress sink."""


@contextmanager
def null_stage(description: str) -> Iterator[ProgressCallback]:  # noqa: ARG001
    yield noop


@contextmanager
def stage_progress(description: str, *, stream: TextIO | None = None) -> Iterator[ProgressCallback]:
    out = stream or sys.stderr

    if not out.isatty():
        out.write(f"{description}…\n")
        out.flush()
        yield _throttled_line(description, out)
        return

    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=22),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=Console(file=out),
        transient=True,
    ) as bar:
        task = bar.add_task(description, total=None)

        def cb(done: int, total: int, note: str) -> None:
            label = f"{description}: {note}"[:58] if note else description
            bar.update(task, completed=done, total=total or None, description=label)

        yield cb


def _throttled_line(description: str, out: TextIO) -> ProgressCallback:
    last = [0.0]

    def cb(done: int, total: int, note: str) -> None:
        now = time.monotonic()
        done_final = bool(total) and done >= total
        if now - last[0] < 3.0 and not done_final:
            return
        last[0] = now
        count = f"{done}/{total}" if total else str(done)
        line = f"  {description}: {count}" + (f" — {note}" if note else "")
        out.write(line[:118] + "\n")
        out.flush()

    return cb
