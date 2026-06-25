"""Configuration loading and validation.

The whole curator is driven by a single YAML file (see ``config.yaml``). This
module parses it into typed dataclasses with sensible defaults so that a minimal
config still works and a malformed one fails loudly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class PlaylistConfig:
    title: str = "🎯 Daily Mix"
    description: str = "Auto-curated daily by YoutubePlaylistCreator."
    privacy: str = "private"  # private | unlisted | public
    mode: str = "rolling"  # rolling (age-out + top-up) | dated (new playlist per day)
    age_out_days: int = 4  # remove entries older than this many days
    max_size: int = 100  # safety cap on playlist length

    def __post_init__(self) -> None:
        if self.privacy not in {"private", "unlisted", "public"}:
            raise ValueError(f"playlist.privacy must be private|unlisted|public, got {self.privacy!r}")
        if self.mode not in {"rolling", "dated"}:
            raise ValueError(f"playlist.mode must be rolling|dated, got {self.mode!r}")


@dataclass
class DiscoveryConfig:
    region_code: str = "US"
    relevance_language: str = "en"
    lookback_hours: int = 48  # only consider videos published within this window
    search_results_per_query: int = 12
    uploads_per_channel: int = 5  # recent uploads pulled per allowlist channel


@dataclass
class ScoringConfig:
    # Relative weights for the ranking signals. ``clickbait`` is a PENALTY
    # (subtracted), the rest are positive contributions.
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "trusted": 2.5,  # from a curated allowlist channel
            "velocity": 1.6,  # views-per-hour — "gaining traction"
            "engagement": 1.2,  # like-to-view ratio — resonance/quality
            "recency": 1.4,  # freshness within the lookback window
            "views": 0.6,  # raw reach (deliberately minor)
            "keyword_match": 1.0,  # category keyword overlap
            "clickbait": 2.5,  # PENALTY weight for provocative/baity titles
        }
    )
    exclude_shorts: bool = True
    min_duration_seconds: int = 90
    max_duration_seconds: int = 9000
    clickbait_cutoff: float = 0.35  # open-search videos at/above this are dropped outright
    min_search_views: int = 2000  # open-search videos below this view count are dropped
    exclude_keywords: list[str] = field(default_factory=list)  # extra low-info terms to block


@dataclass
class CategoryConfig:
    name: str
    target: int = 3
    youtube_category_id: str | None = None
    channels: list[str] = field(default_factory=list)  # allowlist: @handles or UC… ids
    queries: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    skip_quality_filters: bool = False  # e.g. highlights: don't drop "GAME HIGHLIGHTS!!"
    highlights_only: bool = False  # only accept titles that look like actual game highlights
    dedupe_matchups: bool = False  # collapse the same game posted by multiple channels
    uploads_per_channel: int | None = None  # override discovery default (e.g. more for sports)
    # Per-category duration overrides; fall back to the global scoring bounds when None.
    min_duration_seconds: int | None = None
    max_duration_seconds: int | None = None


@dataclass
class Config:
    playlist: PlaylistConfig
    discovery: DiscoveryConfig
    scoring: ScoringConfig
    categories: list[CategoryConfig]

    def effective_min_duration(self, category: CategoryConfig) -> int:
        return category.min_duration_seconds or self.scoring.min_duration_seconds

    def effective_max_duration(self, category: CategoryConfig) -> int:
        return category.max_duration_seconds or self.scoring.max_duration_seconds


def _build_category(raw: dict[str, Any]) -> CategoryConfig:
    if "name" not in raw:
        raise ValueError("each category requires a 'name'")
    return CategoryConfig(
        name=str(raw["name"]),
        target=int(raw.get("target", 3)),
        youtube_category_id=(str(raw["youtube_category_id"]) if raw.get("youtube_category_id") is not None else None),
        channels=[str(c) for c in raw.get("channels", [])],
        queries=[str(q) for q in raw.get("queries", [])],
        keywords=[str(k).lower() for k in raw.get("keywords", [])],
        skip_quality_filters=bool(raw.get("skip_quality_filters", False)),
        highlights_only=bool(raw.get("highlights_only", False)),
        dedupe_matchups=bool(raw.get("dedupe_matchups", False)),
        uploads_per_channel=raw.get("uploads_per_channel"),
        min_duration_seconds=raw.get("min_duration_seconds"),
        max_duration_seconds=raw.get("max_duration_seconds"),
    )


def load_config(path: str | Path) -> Config:
    """Load and validate the YAML config at ``path``."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")

    playlist = PlaylistConfig(**(data.get("playlist") or {}))

    discovery = DiscoveryConfig(**(data.get("discovery") or {}))

    scoring_raw = dict(data.get("scoring") or {})
    # Merge user weights over defaults so partial weight maps still work.
    default_scoring = ScoringConfig()
    merged_weights = {**default_scoring.weights, **(scoring_raw.pop("weights", None) or {})}
    scoring = ScoringConfig(weights=merged_weights, **scoring_raw)

    categories = [_build_category(c) for c in (data.get("categories") or [])]
    if not categories:
        raise ValueError("config must define at least one category")

    return Config(playlist=playlist, discovery=discovery, scoring=scoring, categories=categories)
