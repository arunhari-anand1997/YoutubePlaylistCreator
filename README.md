# YoutubePlaylistCreator

Auto-curate a **daily YouTube playlist** on your own account — built to surface
**high-information depth content** and ruthlessly filter out clickbait and
entertainment fluff. Its backbone is a **curated allowlist of trusted, high-density
channels** per topic (*not* your subscription feed), supplemented by
strictly-filtered open search. It keeps an *accreting* playlist that ages out
instead of wiping. Categories:

- 🌍 World affairs & geopolitics
- 💹 Economics, finance & markets
- 🔬 Science, tech & history
- 🎙️ Expert interviews & podcasts
- 🏟️ Sports highlights (today's games)
- 📊 Sports analysis & storylines

Channels, categories, ranking weights, filters, and the age-out window are all
driven by [`config.yaml`](config.yaml) — no code changes needed to retune it.

## How it works

```
allowlist uploads ─┐
                   ├─► hydrate ─► quality gate ─► classify/route ─► score & rank
topic searches ────┘            (drop clickbait & low-info)            │
                                                                       ▼
            rolling "Daily Mix"  ◄──  age out entries > N days, then top up
```

1. **Discovery (allowlist + strict search).** Recent uploads from a hand-picked
   set of high-signal channels per category (trusted, routed straight to their
   category), **plus** per-category topic searches over a recent window
   (`lookback_hours`). No subscriptions.
2. **Quality gate.** Open-search results must clear a **clickbait cutoff** and a
   **low-information blocklist** (reactions, tier lists, rankings, compilations,
   trailers, IP/franchise fluff, TV episode dumps…). Allowlist channels and the
   highlights category bypass this gate.
3. **Classify / route.** Allowlist uploads go straight to the category they were
   pulled for; search results are classified by YouTube category id + keywords.
4. **Scoring.** Videos compete within a category on a weighted blend of: a
   **trust boost** for allowlist sources, **view-velocity** (views-per-hour —
   "gaining traction"), engagement (likes/views), recency, raw views, and
   keyword relevance — *minus* a **clickbait penalty**. The top `target` per
   category are kept.
5. **Reconcile (age-out + top-up).** The picks are interleaved (so topics mix)
   into one rolling playlist. Each run **removes only entries that have been in
   the playlist longer than `age_out_days`** and adds fresh picks that aren't
   already there — so your unwatched backlog survives day to day. Switch to a new
   dated playlist per day with `playlist.mode: dated`.

> **A note on "watched" detection.** The YouTube Data API does **not** expose
> watch history or playback progress, so the tool can't know which videos you've
> actually watched. Age-out (drop anything older than `age_out_days`) is the
> automatable proxy. If you'd rather prune by a deliberate signal, you can remove
> watched videos yourself in the YouTube app — the dedup logic won't re-add a
> video that's still inside the playlist, and aged-out videos fall outside the
> search window so they don't come back.

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
| `playlist` | `mode` | `rolling` (accretive, age-out + top-up) or `dated` (new per day) |
| `playlist` | `age_out_days` | remove entries that have been in the playlist longer than this |
| `playlist` | `max_size` | safety cap on total playlist length |
| `playlist` | `privacy` | `private` / `unlisted` / `public` |
| `discovery` | `lookback_hours` | only consider videos published within this window |
| `discovery` | `search_results_per_query` | results requested per topic search |
| `scoring` | `weights.velocity` | weight on views-per-hour ("gaining traction") |
| `scoring` | `weights.clickbait` | how hard to penalise baity / provocative titles |
| `scoring` | `weights` | relative influence of every ranking signal |
| `categories[]` | `target` | how many videos to keep per category |
| `categories[]` | `queries` / `keywords` | search terms and classification keywords |

### A note on API quota

The YouTube Data API gives 10,000 quota units/day by default. `search.list` costs
100 units each (so ~13 searches with the sample config), while video hydration and
playlist add/remove operations cost ~1 unit each. The defaults stay well under the
cap; raising `search_results_per_query` doesn't change cost, but adding more
`queries` does (×100 each).

## Development

```bash
pip install -r requirements.txt
python -m pytest          # unit tests cover config, parsing, and ranking
```

The ranking and classification logic in `ytcurator/scoring.py` is pure and fully
unit-tested — no API access required.
