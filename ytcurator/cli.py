"""Command-line entry point for the daily curation run."""

from __future__ import annotations

import argparse
import logging
import sys

from .auth import AuthError, get_credentials
from .config import load_config
from .curator import CurationResult, curate
from .youtube import YouTubeClient


def _print_summary(result: CurationResult) -> None:
    verb = "Would" if result.dry_run else ""
    print()
    print("=" * 68)
    print(f"  Playlist: {result.playlist_title}")
    if result.playlist_id:
        print(f"  https://www.youtube.com/playlist?list={result.playlist_id}")
    print(f"  Considered {result.candidates_considered} candidate videos")
    if result.dry_run:
        print(f"  MODE: dry run — {verb.lower()} remove {result.removed}, "
              f"keep {result.kept}, add {result.added}")
    else:
        print(f"  Aged out {result.removed} · kept {result.kept} · added {result.added}")
    print("=" * 68)

    for category, picks in result.per_category.items():
        print(f"\n▶ {category} ({len(picks)})")
        if not picks:
            print("    (nothing matched)")
        for c in picks:
            mins = c.duration_seconds // 60
            # "+" = newly added this run; "·" = already present / held back
            mark = "+" if c.video_id in result.added_video_ids else "·"
            print(f"   {mark} [{c.score:5.2f}] {mins:>3}m  {c.title[:64]}")
            print(f"          {c.channel_title} · {c.view_count:,} views · {c.url}")

    for note in result.notes:
        print(f"\n! {note}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Curate a daily YouTube playlist.")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML (default: config.yaml)")
    parser.add_argument("--token-file", default="token.json", help="Local OAuth token file (default: token.json)")
    parser.add_argument("--dry-run", action="store_true", help="Select videos but do not modify any playlist")
    parser.add_argument("--reset", action="store_true", help="One-off: clear the existing playlist before topping up")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    try:
        credentials = get_credentials(args.token_file)
    except AuthError as exc:
        print(f"Auth error: {exc}", file=sys.stderr)
        return 3

    client = YouTubeClient(credentials)
    result = curate(client, config, dry_run=args.dry_run, reset=args.reset)
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
