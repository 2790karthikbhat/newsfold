"""Read-only listing/reading of already-rendered digest files in `out/`.

Treats `out/` itself as the history index — there's no Run-to-file link table, so
history browsing works directly off the filenames `digest-YYYY-MM-DD.{html,md}`
written by `digest/render.write_digest`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from newsfold.config import OUT_DIR

_FILENAME_RE = re.compile(r"^digest-(\d{4}-\d{2}-\d{2})\.(html|md)$")


@dataclass
class DigestFile:
    run_date: str
    format: str
    path: Path


def list_digest_files(*, out_dir: Path | None = None) -> list[DigestFile]:
    out_dir = out_dir or OUT_DIR
    if not out_dir.exists():
        return []
    files = []
    for path in out_dir.iterdir():
        match = _FILENAME_RE.match(path.name)
        if match:
            files.append(DigestFile(run_date=match.group(1), format=match.group(2), path=path))
    return sorted(files, key=lambda f: (f.run_date, f.format), reverse=True)


def read_digest_file(
    run_date: str, *, fmt: str = "html", out_dir: Path | None = None
) -> str | None:
    out_dir = out_dir or OUT_DIR
    path = out_dir / f"digest-{run_date}.{fmt}"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")
