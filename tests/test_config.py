import textwrap

import pytest

from ytcurator.config import load_config


def write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(text))
    return p


def test_minimal_config_uses_defaults(tmp_path):
    path = write(
        tmp_path,
        """
        categories:
          - name: News
            keywords: [news]
            queries: [today]
        """,
    )
    cfg = load_config(path)
    assert cfg.playlist.mode == "rolling"
    assert cfg.playlist.privacy == "private"
    assert cfg.discovery.lookback_hours == 48
    assert cfg.scoring.weights["recency"] == 1.6  # default preserved
    assert len(cfg.categories) == 1


def test_partial_weights_merge_over_defaults(tmp_path):
    path = write(
        tmp_path,
        """
        scoring:
          weights:
            views: 5.0
        categories:
          - name: News
        """,
    )
    cfg = load_config(path)
    assert cfg.scoring.weights["views"] == 5.0       # overridden
    assert cfg.scoring.weights["recency"] == 1.6     # untouched default


def test_effective_duration_falls_back_to_global(tmp_path):
    path = write(
        tmp_path,
        """
        scoring:
          min_duration_seconds: 100
          max_duration_seconds: 5000
        categories:
          - name: A
            min_duration_seconds: 300
          - name: B
        """,
    )
    cfg = load_config(path)
    cat_a, cat_b = cfg.categories
    assert cfg.effective_min_duration(cat_a) == 300   # category override
    assert cfg.effective_min_duration(cat_b) == 100   # global fallback
    assert cfg.effective_max_duration(cat_b) == 5000


def test_invalid_privacy_raises(tmp_path):
    path = write(
        tmp_path,
        """
        playlist:
          privacy: secret
        categories:
          - name: A
        """,
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_no_categories_raises(tmp_path):
    path = write(tmp_path, "playlist:\n  mode: rolling\n")
    with pytest.raises(ValueError):
        load_config(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")
