"""Shared data structures and small parsing helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ISO 8601 duration, e.g. "PT1H2M30S", "PT45S", "PT12M".
_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)


def parse_iso8601_duration(value: str) -> int:
    """Convert an ISO 8601 duration (YouTube's ``contentDetails.duration``) to seconds.

    Returns 0 for anything unparseable (e.g. live streams report "P0D").
    """
    if not value:
        return 0
    match = _ISO_DURATION.match(value)
    if not match:
        return 0
    parts = {k: int(v) for k, v in match.groupdict().items() if v}
    return (
        parts.get("days", 0) * 86400
        + parts.get("hours", 0) * 3600
        + parts.get("minutes", 0) * 60
        + parts.get("seconds", 0)
    )


def parse_rfc3339(value: str) -> datetime:
    """Parse a YouTube RFC3339 timestamp (e.g. "2026-06-24T10:00:00Z") to aware UTC."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class Candidate:
    """A single video under consideration, hydrated with stats and metadata."""

    video_id: str
    title: str
    description: str
    channel_id: str
    channel_title: str
    published_at: datetime
    duration_seconds: int
    view_count: int
    like_count: int
    category_id: str | None

    # Discovery provenance.
    from_allowlist: bool = False  # came from a trusted allowlist channel
    forced_category: str | None = None  # allowlist uploads route straight to their category

    # Filled in during selection.
    assigned_category: str | None = None
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def engagement_ratio(self) -> float:
        if self.view_count <= 0:
            return 0.0
        return self.like_count / self.view_count

    def age_hours(self, now: datetime) -> float:
        """Hours since publication (floored at 0.5h to avoid divide-by-zero spikes)."""
        return max((now - self.published_at).total_seconds() / 3600.0, 0.5)

    def text_blob(self) -> str:
        """Lowercased title + description, for keyword matching."""
        return f"{self.title}\n{self.description}".lower()


@dataclass
class PlaylistItem:
    """An entry already in the target playlist (needed for age-out pruning)."""

    item_id: str  # playlistItem id — required to delete the entry
    video_id: str
    added_at: datetime  # when it was added to the playlist
