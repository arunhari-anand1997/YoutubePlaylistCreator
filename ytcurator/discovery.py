"""Candidate gathering — search-only discovery.

There is intentionally no subscriptions stream. The point of this tool is to
surface *gaining-traction* depth content (analysis, interviews, documentaries,
explainers, tactical sports breakdowns) regardless of who you follow — and to
avoid the provocative/clickbait skew of a subscription feed. Per-category topic
searches build the candidate pool, which is then de-duplicated and hydrated with
a single batched ``videos.list`` pass to keep quota low.
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

    video_ids: list[str] = []
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

    candidates = client.hydrate_videos(video_ids)
    fresh = [c for c in candidates if c.published_at >= published_after]
    log.info("Hydrated %d videos, %d within the %dh window",
             len(candidates), len(fresh), config.discovery.lookback_hours)
    return fresh
