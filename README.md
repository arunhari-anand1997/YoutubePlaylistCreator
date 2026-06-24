# YoutubePlaylistCreator

Auto-curate a **daily YouTube playlist** on your own account. Every morning it
pulls fresh videos from the channels you follow *and* from topic searches, ranks
them, and refreshes a single rolling playlist with the best of:

- 🏟️ Sports highlights
- 🗞️ High-value news & analysis
- 🎙️ Long-form interviews
- 🎬 Mini-documentaries
- ✍️ Video essays

Categories, counts, and ranking weights are all driven by [`config.yaml`](config.yaml)
— no code changes needed to retune it.

## How it works

```
subscriptions ─┐
               ├─► gather candidates ─► classify into categories ─► score & rank
topic searches ─┘                                                        │
                                                                         ▼
                            rolling "Daily Mix" playlist  ◄── clear & refill
```

1. **Discovery (hybrid).** Recent uploads from up to N subscribed channels, plus
   per-category keyword searches to fill any thin category and surface channels
   you don't follow yet. Everything is filtered to a recent time window
   (`lookback_hours`).
2. **Classification.** Each video is assigned to its single best-fitting category
   using YouTube's category id and your keyword lists.
3. **Scoring.** Videos compete within a category on a weighted blend of: view
   count, recency, engagement (likes/views), a boost for channels you follow, and
   keyword relevance. The top `target` per category are kept.
4. **Publish.** The picks are interleaved (so topics mix) and written to one
   rolling playlist that's cleared and refilled each day. Switch to a new dated
   playlist per day with `playlist.mode: dated`.

## Setup

### 1. Create a Google Cloud OAuth client

1. In the [Google Cloud Console](https://console.cloud.google.com/), create (or
   pick) a project.
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   application type **Desktop app**. Download the JSON as `client_secret.json`.
4. On the OAuth consent screen, add your Google account as a **Test user** (so
   the refresh token doesn't expire while the app is in "testing").

### 2. Mint a refresh token (one time, locally)

```bash
pip install -r requirements.txt
python scripts/authorize.py client_secret.json
```

A browser opens; grant access to **your** YouTube account. This writes
`token.json` (for local testing) and prints three values:

```
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
YOUTUBE_REFRESH_TOKEN=...
```

### 3. Add the secrets to GitHub

Repo **Settings → Secrets and variables → Actions → New repository secret** and
add `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, and `YOUTUBE_REFRESH_TOKEN`.

That's it — the [daily workflow](.github/workflows/daily-playlist.yml) runs at
13:00 UTC. Change the `cron:` line to adjust the time, or trigger it manually
from the **Actions** tab (with an optional dry-run toggle).

## Running locally

```bash
# Preview today's picks without touching any playlist:
python -m ytcurator.cli --config config.yaml --dry-run

# Actually create/refresh the playlist:
python -m ytcurator.cli --config config.yaml
```

Local runs use `token.json`; CI runs use the environment secrets.

## Configuration

See [`config.yaml`](config.yaml) for the full, commented schema. Highlights:

| Section | Key | What it does |
|---|---|---|
| `playlist` | `mode` | `rolling` (one playlist, cleared daily) or `dated` (new per day) |
| `playlist` | `privacy` | `private` / `unlisted` / `public` |
| `discovery` | `lookback_hours` | only consider videos newer than this |
| `discovery` | `max_subscription_channels` | quota guard for the subscriptions scan |
| `scoring` | `weights` | relative influence of each ranking signal |
| `categories[]` | `target` | how many videos to keep per category |
| `categories[]` | `queries` / `keywords` | search terms and classification keywords |

### A note on API quota

The YouTube Data API gives 10,000 quota units/day by default. `search.list` costs
100 units each (so ~10 searches with the sample config), while the subscription
scan and playlist writes cost ~1 unit each. The defaults stay comfortably under
the cap; raising `search_results_per_query` doesn't change cost, but adding more
`queries` does (×100 each).

## Development

```bash
pip install -r requirements.txt
python -m pytest          # unit tests cover config, parsing, and ranking
```

The ranking and classification logic in `ytcurator/scoring.py` is pure and fully
unit-tested — no API access required.
