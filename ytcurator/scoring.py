"""Pure ranking + classification logic — no API calls, fully unit-testable.

Each candidate is first *classified* into the best-fitting category, then *scored*
within that category. The scoring favours videos that are **gaining traction**
(high views-per-hour), resonate (engagement), and match the category — while
*penalising* provocative / clickbait titles. There is deliberately no
"subscription" signal: discovery is search-only.
"""

from __future__ import annotations

import math
import re
from datetime import datetime

from .config import CategoryConfig, Config, ScoringConfig
from .models import Candidate

# Phrases that strongly signal baity / provocative framing.
_CLICKBAIT_PHRASES = (
    "you won't believe", "you wont believe", "won't believe", "wont believe",
    "you need to see", "you have to see", "watch before", "before it's deleted",
    "gone wrong", "gone too far", "will blow your mind", "blew my mind",
    "mind blown", "jaw dropping", "the truth about", "what they don't want",
    "they don't want you", "they dont want you", "shocking", "shook",
    "must see", "must watch", "exposed", "rekt", "epic fail", "insane",
    "unbelievable", "this is why you", "you should be worried", "?!",
)

# "X DESTROYS Y" rage-bait verbs — matched on word boundaries.
_RAGE_VERBS = re.compile(
    r"\b(eviscerat\w*|destroy\w*|obliterat\w*|annihilat\w*|demolish\w*|"
    r"humiliat\w*|slams|blasts|roasts|schools|torches|shreds|wrecks|owns|"
    r"claps back|fires back|goes off|melts down|loses it)\b",
    re.IGNORECASE,
)

# Low-information / entertainment-fluff markers. Open-search videos matching any
# of these are dropped before ranking (allowlist channels are exempt).
_LOW_INFO = re.compile(
    r"\b("
    r"reaction|reacts?\s+to|tier\s*list|ranked|ranking|compilation|"
    r"try\s+not\s+to|i\s+tried|i\s+spent|24\s+hours|last\s+to\s+leave|"
    r"unboxing|haul|mukbang|asmr|prank|vlog|storytime|"
    r"full\s+episode|official\s+trailer|trailer|teaser|music\s+video|"
    r"anime|manga|marvel|mcu|dc\s+universe|star\s+wars|disney|pixar|"
    r"minecraft|fortnite|gta\b|gta\s*6|pokemon|roblox|speedrun|"
    r"tier|theory\s+explained|ending\s+explained|easter\s+eggs"
    r")\b",
    re.IGNORECASE,
)
# Season/episode dumps, e.g. "(S18, E13)" or "S2 E3".
_EPISODE_TAG = re.compile(r"\bS\d{1,2}\s*[,.]?\s*E\d{1,2}\b", re.IGNORECASE)


def is_low_info(title: str, extra_terms: list[str]) -> bool:
    """True if a title looks like entertainment fluff / low information value."""
    if _LOW_INFO.search(title) or _EPISODE_TAG.search(title):
        return True
    lowered = title.lower()
    return any(term.lower() in lowered for term in extra_terms)


def clickbait_intensity(title: str) -> float:
    """Estimate how clickbait-y a title is, from 0.0 (calm) to 1.0 (screaming).

    Combines: baity stock phrases, rage-bait verbs ("EVISCERATES", "slams"),
    excessive punctuation, ALL-CAPS shouting (whole-title *and* single shouted
    words), and emoji/symbol spam. Heuristic, but it reliably down-ranks the
    "SHOCKING!! 😱" / "Fan DESTROYS Critic" school of titles.
    """
    if not title:
        return 0.0
    lowered = title.lower()
    score = 0.0

    phrase_hits = sum(1 for p in _CLICKBAIT_PHRASES if p in lowered)
    score += min(phrase_hits, 2) * 0.35

    if _RAGE_VERBS.search(title):
        score += 0.4

    if title.count("!") + title.count("?") >= 2:
        score += 0.25
    if "!!" in title or "??" in title or "?!" in title:
        score += 0.2

    words = re.findall(r"[A-Za-z]{3,}", title)
    if words:
        cap_ratio = sum(1 for w in words if w.isupper()) / len(words)
        if cap_ratio >= 0.3:
            score += min(cap_ratio, 0.6)
        # A single SHOUTED word (≥4 letters) for emphasis is itself baity,
        # even when the rest of the title is normal case (e.g. "Selling your SOUL").
        shouted = [w for w in words if w.isupper() and len(w) >= 4]
        if shouted and cap_ratio < 0.3:
            score += min(0.2 * len(shouted), 0.4)

    if re.search(r"[\U0001F000-\U0001FAFF☀-➿←-⇿]", title):
        score += 0.15

    return min(score, 1.0)


def keyword_overlap(candidate: Candidate, keywords: list[str]) -> float:
    """Fraction of a category's keywords that appear in the video text (0..1)."""
    if not keywords:
        return 0.0
    blob = candidate.text_blob()
    hits = sum(1 for kw in keywords if kw in blob)
    return hits / len(keywords)


def classify(candidate: Candidate, categories: list[CategoryConfig]) -> tuple[CategoryConfig | None, float]:
    """Pick the best category for a candidate.

    An exact YouTube category-id match is a strong base signal; keyword overlap
    refines it. Returns (category, affinity) or (None, 0) when nothing matches.
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
    """Log-scale raw views into ~0..1 (≈10M views saturates to 1)."""
    if view_count <= 0:
        return 0.0
    return min(math.log10(view_count + 1) / 7.0, 1.0)


def _normalized_velocity(candidate: Candidate, now: datetime) -> float:
    """Views-per-hour since upload, log-scaled to ~0..1 (≈100k views/hr → 1).

    This is the headline "gaining traction" signal: a 20k-view video that's 3h
    old beats a 200k-view video that's a week old.
    """
    vph = candidate.view_count / candidate.age_hours(now)
    if vph <= 0:
        return 0.0
    return min(math.log10(vph + 1) / 5.0, 1.0)


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

    ``clickbait`` contributes a negative amount. The total is under ``"total"``.
    """
    w = scoring.weights
    positives = {
        "trusted": 1.0 if candidate.from_allowlist else 0.0,
        "velocity": _normalized_velocity(candidate, now),
        "engagement": _normalized_engagement(candidate),
        "recency": _normalized_recency(candidate.published_at, now, lookback_hours),
        "views": _normalized_views(candidate.view_count),
        "keyword_match": keyword_overlap(candidate, category.keywords),
    }
    breakdown = {name: w.get(name, 0.0) * value for name, value in positives.items()}
    breakdown["clickbait"] = -w.get("clickbait", 0.0) * clickbait_intensity(candidate.title)
    breakdown["total"] = sum(breakdown.values())
    return breakdown


def passes_quality(candidate: Candidate, category: CategoryConfig, scoring: ScoringConfig) -> bool:
    """Hard gate for open-search results: reject clickbait and low-info fluff.

    Allowlist videos and categories flagged ``skip_quality_filters`` (e.g. sports
    highlights) bypass this entirely.
    """
    if candidate.from_allowlist or category.skip_quality_filters:
        return True
    if clickbait_intensity(candidate.title) >= scoring.clickbait_cutoff:
        return False
    if is_low_info(candidate.title, scoring.exclude_keywords):
        return False
    return True


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
        # Allowlist uploads route straight to the category they were pulled for;
        # everything else is classified by category-id + keywords.
        if cand.forced_category and cand.forced_category in cat_by_name:
            category = cat_by_name[cand.forced_category]
        else:
            category, affinity = classify(cand, config.categories)
            if category is None or affinity <= 0:
                continue
        if not passes_quality(cand, category, config.scoring):
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
