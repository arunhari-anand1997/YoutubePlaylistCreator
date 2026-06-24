from datetime import datetime, timedelta, timezone

from ytcurator.config import (
    CategoryConfig,
    Config,
    DiscoveryConfig,
    PlaylistConfig,
    ScoringConfig,
)
from ytcurator.curator import partition_prune
from ytcurator.models import Candidate, PlaylistItem
from ytcurator import scoring

NOW = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)


def make_candidate(**kw) -> Candidate:
    defaults = dict(
        video_id="vid",
        title="Title",
        description="desc",
        channel_id="ch",
        channel_title="Chan",
        published_at=NOW - timedelta(hours=2),
        duration_seconds=600,
        view_count=10_000,
        like_count=500,
        category_id=None,
    )
    defaults.update(kw)
    return Candidate(**defaults)


def make_config() -> Config:
    return Config(
        playlist=PlaylistConfig(),
        discovery=DiscoveryConfig(lookback_hours=36),
        scoring=ScoringConfig(min_duration_seconds=90, max_duration_seconds=7200),
        categories=[
            CategoryConfig(
                name="Sports",
                target=2,
                youtube_category_id="17",
                queries=["x"],
                keywords=["highlights", "recap"],
                min_duration_seconds=120,
                max_duration_seconds=1800,
            ),
            CategoryConfig(
                name="Essays",
                target=2,
                queries=["y"],
                keywords=["essay", "deep dive"],
                min_duration_seconds=480,
            ),
        ],
    )


# --- classification ---------------------------------------------------------

def test_keyword_overlap():
    c = make_candidate(title="Match highlights and full recap")
    assert scoring.keyword_overlap(c, ["highlights", "recap"]) == 1.0
    assert scoring.keyword_overlap(c, ["highlights", "missing"]) == 0.5
    assert scoring.keyword_overlap(c, []) == 0.0


def test_classify_prefers_category_id_match():
    cfg = make_config()
    c = make_candidate(category_id="17", title="random")
    cat, affinity = scoring.classify(c, cfg.categories)
    assert cat.name == "Sports"
    assert affinity >= 1.0


def test_classify_by_keywords_when_no_id():
    cfg = make_config()
    c = make_candidate(title="A wonderful video essay deep dive", category_id=None)
    cat, _ = scoring.classify(c, cfg.categories)
    assert cat.name == "Essays"


def test_classify_returns_none_for_no_match():
    cfg = make_config()
    c = make_candidate(title="cooking pasta", description="recipe", category_id=None)
    cat, affinity = scoring.classify(c, cfg.categories)
    assert cat is None and affinity == 0.0


# --- scoring signals --------------------------------------------------------

def test_velocity_rewards_recent_traction():
    cfg = make_config()
    cat = cfg.categories[1]
    fresh = make_candidate(view_count=10_000, published_at=NOW - timedelta(hours=2), title="essay")
    stale = make_candidate(view_count=10_000, published_at=NOW - timedelta(hours=30), title="essay")
    assert (
        scoring.score(fresh, cat, cfg.scoring, NOW, 36)["velocity"]
        > scoring.score(stale, cat, cfg.scoring, NOW, 36)["velocity"]
    )


def test_clickbait_intensity_scale():
    assert scoring.clickbait_intensity("A measured look at the economy") < 0.2
    assert scoring.clickbait_intensity("SHOCKING!! You won't BELIEVE what happened?!") > 0.6


def test_clickbait_penalizes_score():
    cfg = make_config()
    cat = cfg.categories[1]
    calm = make_candidate(title="essay: a calm analysis")
    baity = make_candidate(title="SHOCKING essay you won't BELIEVE!!")
    breakdown = scoring.score(baity, cat, cfg.scoring, NOW, 36)
    assert breakdown["clickbait"] < 0  # penalty is negative
    assert (
        scoring.score(calm, cat, cfg.scoring, NOW, 36)["total"]
        > breakdown["total"]
    )


def test_recency_decays():
    cfg = make_config()
    cat = cfg.categories[1]
    fresh = make_candidate(published_at=NOW - timedelta(hours=1), title="essay")
    old = make_candidate(published_at=NOW - timedelta(hours=30), title="essay")
    assert (
        scoring.score(fresh, cat, cfg.scoring, NOW, 36)["recency"]
        > scoring.score(old, cat, cfg.scoring, NOW, 36)["recency"]
    )


def test_duration_filter_excludes_shorts():
    cfg = make_config()
    short = make_candidate(duration_seconds=45, title="essay essay")
    ok = make_candidate(duration_seconds=600, title="essay essay")
    assert not scoring.passes_duration(short, cfg, cfg.categories[1])
    assert scoring.passes_duration(ok, cfg, cfg.categories[1])


# --- selection --------------------------------------------------------------

def test_select_dedupes_and_caps_target():
    cfg = make_config()
    cands = [
        make_candidate(video_id=f"e{i}", title="video essay", duration_seconds=600, view_count=1000 * (i + 1))
        for i in range(5)
    ]
    selected = scoring.select(cands, cfg, NOW)
    assert len(selected["Essays"]) == 2  # capped at target
    assert selected["Essays"][0].view_count >= selected["Essays"][1].view_count


def test_select_assigns_video_to_single_category():
    cfg = make_config()
    c = make_candidate(
        video_id="dup",
        title="highlights recap essay deep dive",
        category_id="17",
        duration_seconds=600,
    )
    selected = scoring.select([c], cfg, NOW)
    assert sum(len(v) for v in selected.values()) == 1


def test_interleave_round_robins():
    a1, a2 = make_candidate(video_id="a1"), make_candidate(video_id="a2")
    b1 = make_candidate(video_id="b1")
    ordered = scoring.interleave({"A": [a1, a2], "B": [b1]}, ["A", "B"])
    assert [c.video_id for c in ordered] == ["a1", "b1", "a2"]


# --- age-out pruning --------------------------------------------------------

def test_partition_prune_ages_out_old_items():
    items = [
        PlaylistItem("i1", "v1", NOW - timedelta(days=5)),    # older than 4d -> remove
        PlaylistItem("i2", "v2", NOW - timedelta(days=1)),    # keep
        PlaylistItem("i3", "v3", NOW - timedelta(hours=2)),   # keep
    ]
    remove, kept = partition_prune(items, NOW, age_out_days=4)
    assert remove == ["i1"]
    assert kept == {"v2", "v3"}


def test_partition_prune_empty():
    remove, kept = partition_prune([], NOW, age_out_days=4)
    assert remove == [] and kept == set()
