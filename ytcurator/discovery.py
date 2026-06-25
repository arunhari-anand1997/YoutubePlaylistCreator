"""Candidate gathering — curated allowlist + strictly-filtered open search.

Two streams feed the pool:

* **Allowlist uploads** — recent uploads from a hand-picked set of high-signal
  channels per category. These are *trusted*: routed straight to the category
  they were pulled for, and exempt from the open-search quality filters.
* **Topic search** — per-category queries across all of YouTube. These are NOT
  trusted; selection applies hard clickbait/low-info filters to them.

There is no subscription stream. Everything is de-duplicated and hydrated with a
single batched ``videos.list`` pass to keep quota low.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .config import Config
from .models import Candidate
from .youtube import YouTubeClient

log = logging.getLogger(__name__)


def _resolve_allowlist(client: YouTubeClient, config: Config) -> dict[str, str]:
    """Resolve every category's channel handles to ids → {channel_id: category_name}.

    Resolution is cached across categories within this run by handle.
    """
    channel_to_category: dict[str, str] = {}
    handle_cache: dict[str, str | None] = {}
    for category in config.categories:
        for handle in category.channels:
            if handle not in handle_cache:
                handle_cache[handle] = client.resolve_channel_id(handle)
            channel_id = handle_cache[handle]
            if channel_id and channel_id not in channel_to_category:
                channel_to_category[channel_id] = category.name
    return channel_to_category


def gather_candidates(client: YouTubeClient, config: Config, now: datetime | None = None) -> list[Candidate]:
    now = now or datetime.now(timezone.utc)
    published_after = now - timedelta(hours=config.discovery.lookback_hours)

    video_ids: list[str] = []
    forced_category: dict[str, str] = {}  # video_id -> category, for allowlist uploads
    allowlist_channel_ids: set[str] = set()

    # --- Stream 1: allowlist uploads ----------------------------------------
    channel_to_category = _resolve_allowlist(client, config)
    allowlist_channel_ids = set(channel_to_category)
    cat_by_name = {c.name: c for c in config.categories}
    log.info("Resolved %d allowlist channels", len(allowlist_channel_ids))
    if allowlist_channel_ids:
        uploads_map = client.get_uploads_playlist_ids(list(allowlist_channel_ids))
        for channel_id, uploads_playlist in uploads_map.items():
            try:
                category = channel_to_category[channel_id]
                limit = cat_by_name[category].uploads_per_channel or config.discovery.uploads_per_channel
                ids = client.get_recent_upload_ids(uploads_playlist, limit)
                for vid in ids:
                    video_ids.append(vid)
                    forced_category.setdefault(vid, category)
            except Exception as exc:
                log.warning("Skipping uploads for channel %s: %s", channel_id, exc)

    # --- Stream 2: topic search ---------------------------------------------
    for category in config.categories:
        for query in category.queries:
            try:
                ids = client.search_video_ids(
                    query,
                    published_after=published_after,
                    region_code=config.discovery.region_code,
                    relevance_language=config.discovery.relevance_language,
                    category_id=category.youtube_category_id,
                    max_results=config.discovery.search_results_per_query,
                )
                video_ids.extend(ids)
                log.info("Search '%s' (%s) -> %d ids", query, category.name, len(ids))
            except Exception as exc:
                log.warning("Search failed for query %r: %s", query, exc)

    # --- Hydrate, tag provenance, window filter -----------------------------
    candidates = client.hydrate_videos(video_ids)
    for c in candidates:
        if c.channel_id in allowlist_channel_ids:
            c.from_allowlist = True
        if c.video_id in forced_category:
            c.forced_category = forced_category[c.video_id]

    fresh = [c for c in candidates if c.published_at >= published_after]
    log.info("Hydrated %d videos (%d allowlist), %d within the %dh window",
             len(candidates), sum(c.from_allowlist for c in candidates),
             len(fresh), config.discovery.lookback_hours)
    return fresh
