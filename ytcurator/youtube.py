"""Thin wrapper over the YouTube Data API v3.

Keeps all googleapiclient quirks (pagination, batching, field selection) in one
place so the rest of the codebase deals in plain Python objects. Discovery is
search-only — there are no subscription calls.
"""

from __future__ import annotations

import logging
from datetime import datetime

from googleapiclient.discovery import build

from .models import Candidate, PlaylistItem, parse_iso8601_duration, parse_rfc3339

log = logging.getLogger(__name__)

# Quota costs (units) for reference — daily default is 10,000:
#   search.list = 100   videos.list = 1   playlists.* = 1
#   playlistItems.list/insert/delete = 1


class YouTubeClient:
    def __init__(self, credentials):
        self._svc = build("youtube", "v3", credentials=credentials, cache_discovery=False)

    # ----------------------------------------------------------------- allowlist channels
    def resolve_channel_id(self, handle_or_id: str) -> str | None:
        """Resolve an @handle (or bare name) to a channel id. Pass UC… ids through."""
        value = handle_or_id.strip()
        if value.startswith("UC") and len(value) == 24:
            return value
        handle = value.lstrip("@")
        try:
            resp = self._svc.channels().list(part="id", forHandle=handle).execute()
            items = resp.get("items", [])
            if items:
                return items[0]["id"]
        except Exception as exc:
            log.warning("Could not resolve channel handle %r: %s", handle_or_id, exc)
        return None

    def get_uploads_playlist_ids(self, channel_ids: list[str]) -> dict[str, str]:
        """Map each channel id to its 'uploads' playlist id (batched 50/call)."""
        out: dict[str, str] = {}
        for batch in _chunks(channel_ids, 50):
            resp = (
                self._svc.channels()
                .list(part="contentDetails", id=",".join(batch), maxResults=50)
                .execute()
            )
            for item in resp.get("items", []):
                uploads = item["contentDetails"]["relatedPlaylists"].get("uploads")
                if uploads:
                    out[item["id"]] = uploads
        return out

    def get_recent_upload_ids(self, uploads_playlist_id: str, limit: int) -> list[str]:
        """Return the most recent video ids from an uploads playlist."""
        resp = (
            self._svc.playlistItems()
            .list(part="contentDetails", playlistId=uploads_playlist_id, maxResults=min(limit, 50))
            .execute()
        )
        return [it["contentDetails"]["videoId"] for it in resp.get("items", [])]

    # ----------------------------------------------------------------- search
    def search_video_ids(
        self,
        query: str,
        *,
        published_after: datetime,
        region_code: str,
        relevance_language: str,
        category_id: str | None,
        max_results: int,
    ) -> list[str]:
        params = dict(
            part="id",
            q=query,
            type="video",
            order="relevance",
            publishedAfter=published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            regionCode=region_code,
            relevanceLanguage=relevance_language,
            safeSearch="none",
            maxResults=min(max_results, 50),
        )
        if category_id:
            params["videoCategoryId"] = category_id
        resp = self._svc.search().list(**params).execute()
        return [it["id"]["videoId"] for it in resp.get("items", []) if it.get("id", {}).get("videoId")]

    # ----------------------------------------------------------------- hydration
    def hydrate_videos(self, video_ids: list[str]) -> list[Candidate]:
        """Fetch full details for video ids and return Candidate objects.

        Live/upcoming broadcasts are skipped. Input order is not preserved.
        """
        candidates: list[Candidate] = []
        unique_ids = list(dict.fromkeys(video_ids))  # de-dupe, preserve order
        for batch in _chunks(unique_ids, 50):
            resp = (
                self._svc.videos()
                .list(part="snippet,contentDetails,statistics", id=",".join(batch), maxResults=50)
                .execute()
            )
            for item in resp.get("items", []):
                snippet = item.get("snippet", {})
                content = item.get("contentDetails", {})
                stats = item.get("statistics", {})

                if snippet.get("liveBroadcastContent", "none") != "none":
                    continue  # skip live / upcoming

                candidates.append(
                    Candidate(
                        video_id=item["id"],
                        title=snippet.get("title", ""),
                        description=snippet.get("description", ""),
                        channel_id=snippet.get("channelId", ""),
                        channel_title=snippet.get("channelTitle", ""),
                        published_at=parse_rfc3339(snippet["publishedAt"]),
                        duration_seconds=parse_iso8601_duration(content.get("duration", "")),
                        view_count=int(stats.get("viewCount", 0)),
                        like_count=int(stats.get("likeCount", 0)),
                        category_id=snippet.get("categoryId"),
                    )
                )
        return candidates

    # ----------------------------------------------------------------- playlists
    def find_playlist_by_title(self, title: str) -> str | None:
        page_token = None
        while True:
            resp = (
                self._svc.playlists()
                .list(part="snippet", mine=True, maxResults=50, pageToken=page_token)
                .execute()
            )
            for item in resp.get("items", []):
                if item["snippet"]["title"] == title:
                    return item["id"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                return None

    def create_playlist(self, title: str, description: str, privacy: str) -> str:
        resp = (
            self._svc.playlists()
            .insert(
                part="snippet,status",
                body={
                    "snippet": {"title": title, "description": description},
                    "status": {"privacyStatus": privacy},
                },
            )
            .execute()
        )
        return resp["id"]

    def list_playlist_items(self, playlist_id: str) -> list[PlaylistItem]:
        """Return the playlist's entries with their video id and added-date.

        ``snippet.publishedAt`` on a playlistItem is when it was *added* to the
        playlist — exactly what age-out pruning needs.
        """
        items: list[PlaylistItem] = []
        page_token = None
        while True:
            resp = (
                self._svc.playlistItems()
                .list(
                    part="snippet,contentDetails",
                    playlistId=playlist_id,
                    maxResults=50,
                    pageToken=page_token,
                )
                .execute()
            )
            for it in resp.get("items", []):
                items.append(
                    PlaylistItem(
                        item_id=it["id"],
                        video_id=it["contentDetails"]["videoId"],
                        added_at=parse_rfc3339(it["snippet"]["publishedAt"]),
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                return items

    def remove_playlist_item(self, item_id: str) -> None:
        self._svc.playlistItems().delete(id=item_id).execute()

    def add_video(self, playlist_id: str, video_id: str) -> None:
        self._svc.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
