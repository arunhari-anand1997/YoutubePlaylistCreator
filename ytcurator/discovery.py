"""Candidate gathering — the 'hybrid' source strategy.

Two streams feed the candidate pool:

* **Subscriptions** — recent uploads from channels you follow (high signal).
* **Topic search** — per-category queries fill any thin category and surface
  great videos from channels you don't follow yet.

Both streams produce bare video ids; they're merged, de-duplicated, and hydrated
with a single batched ``videos.list`` pass to keep quota low.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .config import Config
from .models import Candidate
from .youtube import YouTubeClient

log = logging.getLogger(__name__)


def gather_candidates(client: YouTubeClient, config: Config, now: datetime | None = None) -> list[Candidate]:
    now = now or datetime.now(timezone.utc)
    published_after = now - timedelta(hours=config.discovery.lookback_hours)

    subscribed_ids: set[str] = set()
    video_ids: list[str] = []

    # --- Stream 1: subscription uploads -------------------------------------
    if config.discovery.use_subscriptions:
        channel_ids = client.get_subscription_channel_ids(config.discovery.max_subscription_channels)
        subscribed_ids = set(channel_ids)
        log.info("Found %d subscribed channels", len(channel_ids))
        uploads_map = client.get_uploads_playlist_ids(channel_ids)
        for channel_id, uploads_playlist in uploads_map.items():
            try:
                ids = client.get_recent_upload_ids(uploads_playlist, config.discovery.uploads_per_channel)
                video_ids.extend(ids)
            except Exception as exc:  # one bad channel shouldn't sink the run
                log.warning("Skipping uploads for channel %s: %s", channel_id, exc)
        log.info("Collected %d candidate ids from subscriptions", len(video_ids))

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

    # --- Hydrate + window filter --------------------------------------------
    candidates = client.hydrate_videos(video_ids, subscribed_ids)
    fresh = [c for c in candidates if c.published_at >= published_after]
    log.info("Hydrated %d videos, %d within the %dh window",
             len(candidates), len(fresh), config.discovery.lookback_hours)
    return fresh
