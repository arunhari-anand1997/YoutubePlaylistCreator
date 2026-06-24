"""Top-level orchestration: discover → select → reconcile the playlist.

The rolling playlist is *accretive*: each run removes only entries that have
aged out (been in the playlist longer than ``age_out_days``) and tops up with
fresh picks that aren't already present. It never wipes the list, so an unwatched
backlog survives day to day.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import scoring
from .config import Config
from .discovery import gather_candidates
from .models import Candidate, PlaylistItem
from .youtube import YouTubeClient

log = logging.getLogger(__name__)


@dataclass
class CurationResult:
    playlist_id: str | None
    playlist_title: str
    ordered: list[Candidate]
    per_category: dict[str, list[Candidate]]
    candidates_considered: int
    removed: int = 0
    kept: int = 0
    added: int = 0
    added_video_ids: set[str] = field(default_factory=set)
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)


def _playlist_title(config: Config, now: datetime) -> str:
    if config.playlist.mode == "dated":
        return f"{config.playlist.title} — {now:%Y-%m-%d}"
    return config.playlist.title


def _resolve_playlist(
    client: YouTubeClient, config: Config, title: str, dry_run: bool
) -> tuple[str | None, bool]:
    """Resolve the target playlist, returning ``(playlist_id, created)``.

    ``created`` is True when a brand-new playlist was made this run, so callers
    can skip pruning (it's empty and not yet queryable via playlistItems.list).
    """
    if config.playlist.mode == "rolling":
        existing = client.find_playlist_by_title(title)
        if existing:
            return existing, False
    if dry_run:
        return None, False
    new_id = client.create_playlist(title, config.playlist.description, config.playlist.privacy)
    return new_id, True


def partition_prune(
    items: list[PlaylistItem], now: datetime, age_out_days: int
) -> tuple[list[str], set[str]]:
    """Split current playlist entries into (item_ids_to_remove, kept_video_ids).

    An entry is removed once it's been in the playlist longer than
    ``age_out_days``. Pure function — no API calls — so it's easy to test.
    """
    cutoff = now - timedelta(days=age_out_days)
    to_remove: list[str] = []
    kept_video_ids: set[str] = set()
    for it in items:
        if it.added_at < cutoff:
            to_remove.append(it.item_id)
        else:
            kept_video_ids.add(it.video_id)
    return to_remove, kept_video_ids


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

    playlist_id, created = _resolve_playlist(client, config, title, dry_run)
    result.playlist_id = playlist_id

    # ---- Dated mode: a brand-new playlist each day, filled with the picks. ----
    if config.playlist.mode == "dated":
        result.added_video_ids = {c.video_id for c in ordered}
        if dry_run:
            result.added = len(ordered)
            result.notes.append("Dry run: no playlist changes were made.")
            return result
        assert playlist_id is not None
        for cand in ordered:
            _try_add(client, playlist_id, cand, result)
        return result

    # ---- Rolling mode: age-out + top-up, never a full wipe. ----
    existing = client.list_playlist_items(playlist_id) if (playlist_id and not created) else []
    remove_ids, kept_video_ids = partition_prune(existing, now, config.playlist.age_out_days)
    result.kept = len(kept_video_ids)

    # Don't re-add something that's still present, nor resurrect one we're aging
    # out this very run.
    removing = set(remove_ids)
    removed_video_ids = {it.video_id for it in existing if it.item_id in removing}
    excluded = kept_video_ids | removed_video_ids
    new_picks = [c for c in ordered if c.video_id not in excluded]
    space = max(0, config.playlist.max_size - len(kept_video_ids))
    if len(new_picks) > space:
        result.notes.append(
            f"max_size={config.playlist.max_size} reached — {len(new_picks) - space} new pick(s) held back."
        )
    new_picks = new_picks[:space]
    result.added_video_ids = {c.video_id for c in new_picks}

    if dry_run:
        result.removed = len(remove_ids)
        result.added = len(new_picks)
        result.notes.append("Dry run: no playlist changes were made.")
        return result

    assert playlist_id is not None
    for item_id in remove_ids:
        try:
            client.remove_playlist_item(item_id)
            result.removed += 1
        except Exception as exc:
            log.warning("Failed to remove item %s: %s", item_id, exc)
            result.notes.append(f"Could not remove item {item_id}: {exc}")

    for cand in new_picks:
        _try_add(client, playlist_id, cand, result)

    return result


def _try_add(client: YouTubeClient, playlist_id: str, cand: Candidate, result: CurationResult) -> None:
    try:
        client.add_video(playlist_id, cand.video_id)
        result.added += 1
    except Exception as exc:
        log.warning("Failed to add %s: %s", cand.video_id, exc)
        result.notes.append(f"Could not add {cand.video_id}: {exc}")
