import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Literal, Union
from urllib.parse import urlparse, parse_qs

import requests


YOUTUBE_API_KEY = os.getenv(
    "YOUTUBE_API_KEY",
)
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


@dataclass
class ExtractedVideo:
    id: str
    title: str
    url: str
    description: str
    tags: List[str] = field(default_factory=list)
    category: Optional[str] = None
    timestamp: Optional[int] = None  # unix timestamp
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    views: Optional[int] = None
    comments: Optional[int] = None
    duration: Optional[int] = None


@dataclass
class ExtractedPlaylist:
    type: Literal["playlist", "channel"]
    title: str
    videos: List[ExtractedVideo]
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None

    # extra useful metadata
    id: Optional[str] = None
    description: str = ""
    url: Optional[str] = None


VideoOrPlaylist = Union[ExtractedVideo, ExtractedPlaylist]


# ---------- Utility: basic HTTP wrapper ----------


def _yt_get(endpoint: str, params: dict) -> dict:
    params = {**params, "key": YOUTUBE_API_KEY}
    resp = requests.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params)
    resp.raise_for_status()
    return resp.json()


# ---------- URL parsing helpers ----------


def _parse_youtube_url(url: str) -> dict:
    """
    Return a dict with one of:
      {"type": "video", "video_id": "..."}
      {"type": "playlist", "playlist_id": "..."}
      {"type": "channel", "channel_id": "..."}
    or raise if we can't classify.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "youtube.com" not in host and "youtu.be" not in host:
        raise ValueError("Not a YouTube URL")

    qs = parse_qs(parsed.query)

    # Playlist URLs
    if "list" in qs and not parsed.path.startswith("/channel/"):
        return {"type": "playlist", "playlist_id": qs["list"][0]}

    # Video URLs
    if parsed.path == "/watch" and "v" in qs:
        return {"type": "video", "video_id": qs["v"][0]}
    if host.endswith("youtu.be") and parsed.path.strip("/"):
        return {"type": "video", "video_id": parsed.path.strip("/")}

    # Channel URLs
    # /channel/UC...
    m = re.match(r"^/channel/([^/]+)$", parsed.path)
    if m:
        return {"type": "channel", "channel_id": m.group(1)}

    # /c/CustomName or /@handle – official API doesn’t accept this directly as ID,
    # but we can resolve it via search or channel lookup.
    if parsed.path.startswith("/c/") or parsed.path.startswith("/@"):
        # Treat as channel "URL"; we'll resolve via search API
        return {"type": "channel_url", "path": parsed.path}

    raise ValueError("Could not determine type of YouTube URL")


# ---------- Mapping helpers ----------

category_cache: dict[str, str] = {}


def _category_id_to_name(category_id: str) -> Optional[str]:
    """
    Optional helper: map categoryId → name using videoCategories.list.
    If you care about categories, you can cache this.
    """
    if not category_id:
        return None
    if category_id in category_cache:
        return category_cache[category_id]
    data = _yt_get(
        "videoCategories",
        {"part": "snippet", "id": f"{category_id}"},
    )
    items = data.get("items") or []
    if not items:
        return None
    category_name = items[0]["snippet"].get("title")
    category_cache[category_id] = category_name
    return category_name


def _video_from_item(item: dict) -> ExtractedVideo:
    """Map a YouTube Data API 'videos' item → ExtractedVideo."""
    vid = item["id"]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    content_details = item.get("contentDetails", {})

    # duration is ISO 8601, e.g. "PT5M33S"; you can parse if you want seconds.
    iso_duration = content_details.get("duration")

    # Simple ISO 8601 → seconds parser (handles hours/minutes/seconds)
    duration_seconds = None
    if iso_duration:
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
        if m:
            h = int(m.group(1) or 0)
            m_ = int(m.group(2) or 0)
            s = int(m.group(3) or 0)
            duration_seconds = h * 3600 + m_ * 60 + s

    # publishedAt → unix timestamp
    timestamp = None
    published_at = snippet.get("publishedAt")
    if published_at:
        # Example: 2023-01-01T12:34:56Z
        from datetime import datetime, timezone

        dt = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        timestamp = int(dt.timestamp())

    # categories: API gives categoryId (single); yt-dlp gave list.
    category_name = _category_id_to_name(snippet.get("categoryId", ""))

    return ExtractedVideo(
        id=vid,
        title=snippet.get("title") or "",
        url=f"https://www.youtube.com/watch?v={vid}",
        description=snippet.get("description") or "",
        tags=snippet.get("tags") or [],
        category=category_name,
        timestamp=timestamp,
        channel_id=snippet.get("channelId"),
        channel_name=snippet.get("channelTitle"),
        views=int(stats.get("viewCount")) if "viewCount" in stats else None,
        comments=int(stats.get("commentCount")) if "commentCount" in stats else None,
        duration=duration_seconds,
    )


# ---------- API calls for each type ----------


def _fetch_video(video_id: str) -> ExtractedVideo:
    data = _yt_get(
        "videos",
        {
            "id": video_id,
            "part": "snippet,contentDetails,statistics",
        },
    )
    items = data.get("items") or []
    if not items:
        raise ValueError(f"Video not found: {video_id}")
    return _video_from_item(items[0])


def _fetch_playlist(playlist_id: str) -> ExtractedPlaylist:
    # 1) Fetch playlist metadata
    pl_data = _yt_get(
        "playlists",
        {
            "id": playlist_id,
            "part": "snippet",
        },
    )
    pl_items = pl_data.get("items") or []
    if not pl_items:
        raise ValueError(f"Playlist not found: {playlist_id}")
    pl_item = pl_items[0]
    pl_snippet = pl_item.get("snippet", {})

    # 2) Fetch playlist items (video IDs), paginating
    videos: List[ExtractedVideo] = []
    next_page_token = None

    while True:
        pl_items_data = _yt_get(
            "playlistItems",
            {
                "playlistId": playlist_id,
                "part": "contentDetails",
                "maxResults": 50,
                "pageToken": next_page_token or "",
            },
        )
        pl_entries = pl_items_data.get("items") or []
        video_ids = [
            it["contentDetails"]["videoId"]
            for it in pl_entries
            if "contentDetails" in it and "videoId" in it["contentDetails"]
        ]

        if video_ids:
            # Batch-fetch video details
            vids_data = _yt_get(
                "videos",
                {
                    "id": ",".join(video_ids),
                    "part": "snippet,contentDetails,statistics",
                    "maxResults": 50,
                },
            )
            for v_item in vids_data.get("items", []):
                videos.append(_video_from_item(v_item))

        next_page_token = pl_items_data.get("nextPageToken")
        if not next_page_token:
            break

    return ExtractedPlaylist(
        type="playlist",
        title=pl_snippet.get("title") or "",
        videos=videos,
        channel_id=pl_snippet.get("channelId"),
        channel_name=pl_snippet.get("channelTitle"),
        id=playlist_id,
        description=pl_snippet.get("description") or "",
        url=f"https://www.youtube.com/playlist?list={playlist_id}",
    )


def _resolve_channel_from_url_path(path: str) -> Optional[str]:
    """
    Resolve /c/CustomName or /@handle to a channelId using search or channels.list.
    A simple strategy: search for the path segment as a channel.
    """
    # e.g. /@SomeChannel → "SomeChannel"
    slug = path.strip("/").lstrip("@").split("/")[0]
    if not slug:
        return None

    data = _yt_get(
        "search",
        {
            "part": "snippet",
            "q": slug,
            "type": "channel",
            "maxResults": 1,
        },
    )
    items = data.get("items") or []
    if not items:
        return None
    return items[0]["snippet"]["channelId"]


def _fetch_channel_as_playlist(channel_id: str) -> ExtractedPlaylist:
    """
    There are multiple ways to represent a channel; here we:
      - Fetch channel details
      - Fetch 'uploads' playlist (channel's uploaded videos)
      - Return as ExtractedPlaylist with type="channel"
    """
    ch_data = _yt_get(
        "channels",
        {
            "id": channel_id,
            "part": "snippet,contentDetails",
        },
    )
    ch_items = ch_data.get("items") or []
    if not ch_items:
        raise ValueError(f"Channel not found: {channel_id}")
    ch_item = ch_items[0]
    ch_snippet = ch_item.get("snippet", {})
    related_playlists = ch_item.get("contentDetails", {}).get("relatedPlaylists", {})
    uploads_playlist_id = related_playlists.get("uploads")

    videos: List[ExtractedVideo] = []

    if uploads_playlist_id:
        # reuse playlistItems logic for uploads playlist
        next_page_token = None
        while True:
            pl_items_data = _yt_get(
                "playlistItems",
                {
                    "playlistId": uploads_playlist_id,
                    "part": "contentDetails",
                    "maxResults": 50,
                    "pageToken": next_page_token or "",
                },
            )
            pl_entries = pl_items_data.get("items") or []
            video_ids = [
                it["contentDetails"]["videoId"]
                for it in pl_entries
                if "contentDetails" in it and "videoId" in it["contentDetails"]
            ]

            if video_ids:
                vids_data = _yt_get(
                    "videos",
                    {
                        "id": ",".join(video_ids),
                        "part": "snippet,contentDetails,statistics",
                        "maxResults": 50,
                    },
                )
                for v_item in vids_data.get("items", []):
                    videos.append(_video_from_item(v_item))

            next_page_token = pl_items_data.get("nextPageToken")
            if not next_page_token:
                break

    return ExtractedPlaylist(
        type="channel",
        title=ch_snippet.get("title") or "",
        videos=videos,
        channel_id=channel_id,
        channel_name=ch_snippet.get("title") or "",
        id=channel_id,
        description=ch_snippet.get("description") or "",
        url=f"https://www.youtube.com/channel/{channel_id}",
    )


# ---------- Public entrypoint: extract_url ----------


def extract_url(url: str) -> VideoOrPlaylist:
    """
    YouTube-only version of your original extract_url, backed by the official
    YouTube Data API instead of yt-dlp.
    """
    try:
        parsed = _parse_youtube_url(url)

        if parsed["type"] == "video":
            return _fetch_video(parsed["video_id"])

        if parsed["type"] == "playlist":
            return _fetch_playlist(parsed["playlist_id"])

        if parsed["type"] == "channel":
            return _fetch_channel_as_playlist(parsed["channel_id"])

        if parsed["type"] == "channel_url":
            channel_id = _resolve_channel_from_url_path(parsed["path"])
            if not channel_id:
                raise ValueError(f"Could not resolve channel from URL: {url}")
            return _fetch_channel_as_playlist(channel_id)

        # Fallback: treat as video if we somehow misclassified
        raise ValueError("Unknown YouTube URL type")

    except Exception as e:
        # you might want to log e
        print(e)
        raise Exception(f"Error extracting url via YouTube API: {url}")
