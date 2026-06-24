"""Pure ranking + classification logic — no API calls, fully unit-testable.

Each candidate is first *classified* into the best-fitting category, then *scored*
within that category. Keeping these functions side-effect-free makes the curation
behaviour reproducible and easy to test.
"""

from __future__ import annotations

import math
from datetime import datetime

from .config import CategoryConfig, Config, ScoringConfig
from .models import Candidate


def keyword_overlap(candidate: Candidate, keywords: list[str]) -> float:
    """Fraction of a category's keywords that appear in the video text (0..1)."""
    if not keywords:
        return 0.0
    blob = candidate.text_blob()
    hits = sum(1 for kw in keywords if kw in blob)
    return hits / len(keywords)


def classify(candidate: Candidate, categories: list[CategoryConfig]) -> tuple[CategoryConfig | None, float]:
    """Pick the best category for a candidate.

    Scoring: an exact YouTube category-id match is worth a strong base signal;
    keyword overlap refines it. Returns (category, affinity) or (None, 0) when the
    video matches nothing and so should be dropped.
    """
    best: CategoryConfig | None = None
    best_affinity = 0.0
    for cat in categories:
        affinity = 0.0
        if cat.youtube_category_id and candidate.category_id == cat.youtube_category_id:
            affinity += 1.0
        affinity += keyword_overlap(candidate, cat.keywords)
        if affinity > best_affinity:
            best_affinity = affinity
            best = cat
    return best, best_affinity


def _normalized_views(view_count: int) -> float:
    """Log-scale views into ~0..1 (≈10M views saturates to 1)."""
    if view_count <= 0:
        return 0.0
    return min(math.log10(view_count + 1) / 7.0, 1.0)


def _normalized_recency(published_at: datetime, now: datetime, lookback_hours: int) -> float:
    """1.0 for brand-new, decaying linearly to 0 at the edge of the lookback window."""
    age_hours = (now - published_at).total_seconds() / 3600.0
    if lookback_hours <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - age_hours / lookback_hours))


def _normalized_engagement(candidate: Candidate) -> float:
    """Like/view ratio scaled so a healthy ~5% ratio approaches 1.0."""
    return min(candidate.engagement_ratio * 20.0, 1.0)


def score(
    candidate: Candidate,
    category: CategoryConfig,
    scoring: ScoringConfig,
    now: datetime,
    lookback_hours: int,
) -> dict[str, float]:
    """Return the weighted score breakdown for a candidate within a category.

    The total is stored under the ``"total"`` key.
    """
    w = scoring.weights
    features = {
        "views": _normalized_views(candidate.view_count),
        "recency": _normalized_recency(candidate.published_at, now, lookback_hours),
        "engagement": _normalized_engagement(candidate),
        "subscribed_boost": 1.0 if candidate.from_subscription else 0.0,
        "keyword_match": keyword_overlap(candidate, category.keywords),
    }
    breakdown = {name: w.get(name, 0.0) * value for name, value in features.items()}
    breakdown["total"] = sum(breakdown.values())
    return breakdown


def passes_duration(candidate: Candidate, config: Config, category: CategoryConfig) -> bool:
    lo = config.effective_min_duration(category)
    hi = config.effective_max_duration(category)
    if config.scoring.exclude_shorts and candidate.duration_seconds < max(lo, 61):
        return False
    return lo <= candidate.duration_seconds <= hi


def select(
    candidates: list[Candidate],
    config: Config,
    now: datetime,
) -> dict[str, list[Candidate]]:
    """Classify, score, de-duplicate, and pick the top-N per category.

    A video is assigned to exactly one category (its best-affinity match). Within
    each category it competes on score; the top ``target`` survive.
    """
    lookback = config.discovery.lookback_hours
    by_category: dict[str, list[Candidate]] = {c.name: [] for c in config.categories}
    cat_by_name = {c.name: c for c in config.categories}
    seen: set[str] = set()

    for cand in candidates:
        if cand.video_id in seen:
            continue
        category, affinity = classify(cand, config.categories)
        if category is None or affinity <= 0:
            continue
        if not passes_duration(cand, config, category):
            continue
        breakdown = score(cand, category, config.scoring, now, lookback)
        cand.assigned_category = category.name
        cand.score = breakdown["total"]
        cand.score_breakdown = breakdown
        by_category[category.name].append(cand)
        seen.add(cand.video_id)

    selected: dict[str, list[Candidate]] = {}
    for name, pool in by_category.items():
        pool.sort(key=lambda c: c.score, reverse=True)
        selected[name] = pool[: cat_by_name[name].target]
    return selected


def interleave(selected: dict[str, list[Candidate]], category_order: list[str]) -> list[Candidate]:
    """Round-robin the per-category picks so the playlist mixes topics."""
    queues = [list(selected.get(name, [])) for name in category_order]
    ordered: list[Candidate] = []
    while any(queues):
        for q in queues:
            if q:
                ordered.append(q.pop(0))
    return ordered
