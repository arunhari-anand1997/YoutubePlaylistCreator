from datetime import datetime, timedelta, timezone

from ytcurator.config import (
    CategoryConfig,
    Config,
    DiscoveryConfig,
    PlaylistConfig,
    ScoringConfig,
)
from ytcurator.models import Candidate
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
        from_subscription=False,
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


def test_subscription_boost_raises_score():
    cfg = make_config()
    sub = make_candidate(from_subscription=True, title="great essay")
    non = make_candidate(from_subscription=False, title="great essay")
    cat = cfg.categories[1]
    s_sub = scoring.score(sub, cat, cfg.scoring, NOW, 36)["total"]
    s_non = scoring.score(non, cat, cfg.scoring, NOW, 36)["total"]
    assert s_sub > s_non


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


def test_select_dedupes_and_caps_target():
    cfg = make_config()
    cands = [
        make_candidate(video_id=f"e{i}", title="video essay", duration_seconds=600, view_count=1000 * (i + 1))
        for i in range(5)
    ]
    selected = scoring.select(cands, cfg, NOW)
    assert len(selected["Essays"]) == 2  # capped at target
    # Highest view count should rank first (other signals equal).
    assert selected["Essays"][0].view_count >= selected["Essays"][1].view_count


def test_select_assigns_video_to_single_category():
    cfg = make_config()
    # Matches both sports keywords and essay keywords; should land in exactly one.
    c = make_candidate(
        video_id="dup",
        title="highlights recap essay deep dive",
        category_id="17",
        duration_seconds=600,
    )
    selected = scoring.select([c], cfg, NOW)
    total = sum(len(v) for v in selected.values())
    assert total == 1


def test_interleave_round_robins():
    a1, a2 = make_candidate(video_id="a1"), make_candidate(video_id="a2")
    b1 = make_candidate(video_id="b1")
    ordered = scoring.interleave({"A": [a1, a2], "B": [b1]}, ["A", "B"])
    assert [c.video_id for c in ordered] == ["a1", "b1", "a2"]
