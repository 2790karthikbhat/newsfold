from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

EventStatus = Literal["scheduled", "live", "finished"]


@dataclass(frozen=True)
class Event:
    sport: str  # "football" | "cricket" | "f1"
    title: str
    subtitle: str
    start_time: datetime | None  # naive UTC, per newsfold.timeutil convention
    status: EventStatus
    result: str | None
    detail_url: str | None = None
