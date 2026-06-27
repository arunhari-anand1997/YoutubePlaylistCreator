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
import unicodedata
from datetime import datetime, timedelta

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
    r"tier|theory\s+explained|ending\s+explained|easter\s+eggs|"
    r"caught\s+on\s+camera|bodycam|body\s+cam|911\s+call|doorbell\s+camera|"
    r"my\s+partner\s+was\s+murdered|true\s+crime|"
    r"noah'?s\s+flood|creationist|young\s+earth|flat\s+earth|ancient\s+aliens|"
    r"werewolf|bigfoot|sasquatch|loch\s+ness|"
    r"murder\s+trial|opening\s+statements|child\s+victims|courtroom|court\s+cam|on\s+trial|"
    r"jonbenet|cold\s+case|who\s+killed|unsolved\s+(?:murder|case)|the\s+case\s+of"
    r")\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    """Fold typographic apostrophes and accents so the blocklist matches reliably.

    e.g. "Noah's" -> "Noah's", "JonBenét" -> "JonBenet".
    """
    text = text.replace("’", "'").replace("‘", "'")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))
# Season/episode dumps, e.g. "(S18, E13)" or "S2 E3".
_EPISODE_TAG = re.compile(r"\bS\d{1,2}\s*[,.]?\s*E\d{1,2}\b", re.IGNORECASE)


# A real game-highlights title: the word "highlights", an explicit full-game tag,
# or a scoreline like "5-0" / "1 - 0". Rejects press conferences, training
# sessions, "Team Feature", draft profiles, top-100 lists, etc.
_GAME_HIGHLIGHT = re.compile(
    r"(highlight|full[\s-]?(game|match|time)|match\s+recap|extended\s+highlights|"
    r"\b\d{1,2}\s*[-–]\s*\d{1,2}\b)",
    re.IGNORECASE,
)


# Press conferences, interviews, reactions, previews — NOT game highlights, even
# though their titles often carry a scoreline ("Press Conference ... on the 5-0 win").
_NOT_GAME_HIGHLIGHT = re.compile(
    r"press\s+conference|presser|post[-\s]?match\s+(?:interview|press|reaction|analysis)|"
    r"player\s+interview|\binterviews?\b|takes?\s+questions|\breaction\b|"
    r"pre[-\s]?match|\bpreview\b|prediction|build[-\s]?up|mic'?d\s+up",
    re.IGNORECASE,
)


def looks_like_game_highlight(title: str) -> bool:
    text = title or ""
    if _NOT_GAME_HIGHLIGHT.search(text):
        return False
    return bool(_GAME_HIGHLIGHT.search(text))


# Pull the two sides of a matchup out of a highlights title, so the same game
# posted by a league channel AND a team channel collapses to one entry.
_VS = re.compile(r"(.+?)\s+(?:vs\.?|v\.?|@)\s+(.+)", re.IGNORECASE)
_SCORELINE = re.compile(r"(.+?)\s+\d{1,2}\s*[-–]\s*\d{1,2}\s+(.+)")
_TEAM_NOISE = re.compile(
    r"\b(full|game|match|extended|highlights?|recap|hls?|fifa|world|cup|copa|mundial|de|la|"
    r"mlb|nba|nfl|nhl|laliga|liga|serie|premier|league|ligue|bundesliga|uefa|champions|"
    r"20\d\d|wk|week|men's|women's)\b",
    re.IGNORECASE,
)


def _team_token(side: str) -> str:
    cleaned = _TEAM_NOISE.sub(" ", side)
    cleaned = re.sub(r"[^a-zA-Z\s]", " ", cleaned)  # strip flags/emoji/punct
    toks = cleaned.split()
    return toks[0].lower() if toks else ""


def matchup_key(title: str):
    """Return a frozenset of the two teams in a highlights title, or None."""
    for pattern in (_VS, _SCORELINE):
        m = pattern.search(title or "")
        if m:
            a, b = _team_token(m.group(1)), _team_token(m.group(2))
            if a and b and a != b:
                return frozenset({a, b})
    return None


def dedupe_matchups(pool: list[Candidate]) -> list[Candidate]:
    """Collapse duplicate postings of the same game, keeping the highest score."""
    best: dict = {}
    out: list[Candidate] = []
    for c in pool:
        key = matchup_key(c.title)
        if key is None:
            out.append(c)
            continue
        if key not in best or c.score > best[key].score:
            best[key] = c
    out.extend(best.values())
    return out


def is_low_info(title: str, extra_terms: list[str]) -> bool:
    """True if a title looks like entertainment fluff / low information value."""
    normalized = _normalize(title)
    if _LOW_INFO.search(normalized) or _EPISODE_TAG.search(normalized):
        return True
    lowered = normalized.lower()
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
    if candidate.view_count < scoring.min_search_views:
        return False  # not "gaining traction" — likely a random low-signal upload
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
        if category.highlights_only and not looks_like_game_highlight(cand.title):
            continue  # keep only real game highlights, not pressers/features/training
        if category.max_age_hours is not None and cand.published_at < now - timedelta(hours=category.max_age_hours):
            continue  # too old for this category (e.g. yesterday's viral game in a "last night" bucket)
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
        cat = cat_by_name[name]
        if cat.dedupe_matchups:
            pool = dedupe_matchups(pool)
        pool.sort(key=lambda c: c.score, reverse=True)
        selected[name] = pool[: cat.target]
    return selected


def interleave(selected: dict[str, list[Candidate]], category_order: list[str]) -> list[Candidate]:
    """Evenly spread each category's picks across the whole playlist.

    Rather than round-robin (which leaves a large category clumped at the tail
    once the small ones empty), each item gets a fractional position in [0,1)
    spaced evenly within its category, then all items are merged by position.
    A 10-item category and a 3-item category both span the full list, so topics
    stay interspersed no matter how lopsided the counts are.
    """
    ranked: list[tuple[float, int, Candidate]] = []
    for ci, name in enumerate(category_order):
        picks = selected.get(name, [])
        n = len(picks)
        for i, cand in enumerate(picks):
            position = (i + 0.5) / n
            ranked.append((position, ci, cand))
    ranked.sort(key=lambda t: (t[0], t[1]))
    return [cand for _, _, cand in ranked]
