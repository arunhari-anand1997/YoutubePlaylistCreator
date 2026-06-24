"""Top-level orchestration: discover → select → publish to a playlist."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import scoring
from .config import Config
from .discovery import gather_candidates
from .models import Candidate
from .youtube import YouTubeClient

log = logging.getLogger(__name__)


@dataclass
class CurationResult:
    playlist_id: str | None
    playlist_title: str
    ordered: list[Candidate]
    per_category: dict[str, list[Candidate]]
    candidates_considered: int
    cleared: int = 0
    added: int = 0
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)


def _playlist_title(config: Config, now: datetime) -> str:
    if config.playlist.mode == "dated":
        return f"{config.playlist.title} — {now:%Y-%m-%d}"
    return config.playlist.title


def _resolve_playlist(
    client: YouTubeClient, config: Config, title: str, dry_run: bool
) -> tuple[str | None, bool]:
    """Resolve the target playlist.

    Returns ``(playlist_id, created)``. ``created`` is True when a brand-new
    playlist was made this run — callers can then skip the clear step, since a
    fresh playlist is already empty (and isn't yet queryable via
    ``playlistItems.list`` due to YouTube's eventual consistency).
    """
    if config.playlist.mode == "rolling":
        existing = client.find_playlist_by_title(title)
        if existing:
            return existing, False
    if dry_run:
        return None, False
    new_id = client.create_playlist(title, config.playlist.description, config.playlist.privacy)
    return new_id, True


def curate(client: YouTubeClient, config: Config, *, dry_run: bool = False, now: datetime | None = None) -> CurationResult:
    now = now or datetime.now(timezone.utc)
    title = _playlist_title(config, now)

    candidates = gather_candidates(client, config, now=now)
    selected = scoring.select(candidates, config, now)
    category_order = [c.name for c in config.categories]
    ordered = scoring.interleave(selected, category_order)

    result = CurationResult(
        playlist_id=None,
        playlist_title=title,
        ordered=ordered,
        per_category=selected,
        candidates_considered=len(candidates),
        dry_run=dry_run,
    )

    if not ordered:
        result.notes.append("No videos matched the criteria today — playlist left unchanged.")
        return result

    playlist_id, created = _resolve_playlist(client, config, title, dry_run)
    result.playlist_id = playlist_id

    if dry_run:
        result.notes.append("Dry run: no playlist changes were made.")
        return result

    assert playlist_id is not None
    # Only an existing rolling playlist needs clearing/renaming. A freshly created
    # one is already empty and was created with the right metadata — and clearing
    # it immediately would 404 (YouTube hasn't propagated it yet).
    if config.playlist.mode == "rolling" and not created:
        result.cleared = client.clear_playlist(playlist_id)
        client.update_playlist_metadata(
            playlist_id, title, config.playlist.description, config.playlist.privacy
        )

    for cand in ordered:
        try:
            client.add_video(playlist_id, cand.video_id)
            result.added += 1
        except Exception as exc:
            log.warning("Failed to add %s: %s", cand.video_id, exc)
            result.notes.append(f"Could not add {cand.video_id}: {exc}")

    return result
