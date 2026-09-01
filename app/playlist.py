from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shutil
from typing import Callable
from urllib.parse import urlsplit

import yt_dlp
from curl_cffi import requests as curl_requests
from http.cookiejar import MozillaCookieJar

from app.config_models import GlobalConfig, SourceConfig, classify_link
from app.ytdlp_cookies import apply_tiktok_cookies, apply_youtube_cookies, cookie_file_fallback_options, is_browser_cookie_locked


# Expected when expanding channel → /shorts or /videos; not a real crawl failure.
_SKIP_TAB_MARKERS = (
    "does not have a shorts tab",
    "does not have a videos tab",
)


@dataclass
class VideoEntry:
    video_id: str
    title: str
    url: str
    platform: str
    duration: float | None
    upload_date: str | None
    thumbnail: str | None = None
    view_count: int | None = None
    like_count: int | None = None


class _YtdlpLogger:
    """Route yt-dlp messages; soft-skip missing tabs instead of ERROR spam."""

    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        text = str(msg)
        if _is_missing_tab(text):
            # Soft skip — channel has no /shorts or /videos tab
            self._log(f"Skip: {text.strip()}")
            return
        if "unable to extract secondary user id" in text.lower():
            # The TikTok profile fallback resolves this from a recent embed video.
            return
        self._log(f"yt-dlp: {text}")


def _is_missing_tab(message: str) -> bool:
    lower = message.lower()
    return any(m in lower for m in _SKIP_TAB_MARKERS)


def _base_ydl_opts(
    log: Callable[[str], None] | None = None,
    *,
    platform: str = "",
    youtube_cookies_file: str | None = None,
    youtube_cookies_browser: str | None = None,
    tiktok_cookies_file: str | None = None,
) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "source_address": "0.0.0.0",
        # Enable YouTube's JS challenge solver on systems with Node.js.
        "js_runtimes": {"node": {"path": shutil.which("node") or "node"}},
        "remote_components": {"ejs:github"},
    }
    if log is not None:
        opts["logger"] = _YtdlpLogger(log)
    if platform == "youtube":
        apply_youtube_cookies(
            opts,
            cookies_file=youtube_cookies_file,
            cookies_browser=youtube_cookies_browser,
        )
        opts["extractor_args"] = {"youtubetab": {"approximate_date": [""]}}
    elif platform == "tiktok":
        apply_tiktok_cookies(opts, cookies_file=tiktok_cookies_file)
    return opts


def _platform_from_url(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    if host in {"youtube.com", "youtu.be"} or host.endswith(".youtube.com"):
        return "youtube"
    return ""


def _extract_info_with_cookie_fallback(
    opts: dict,
    url: str,
    *,
    cookies_file: str | None = None,
    log: Callable[[str], None] | None = None,
) -> dict | None:
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as error:
        fallback_opts = cookie_file_fallback_options(opts, cookies_file)
        if not fallback_opts or not is_browser_cookie_locked(error):
            raise
        if log:
            log("Browser cookies are locked; retrying with cookies.txt")
        with yt_dlp.YoutubeDL(fallback_opts) as ydl:
            return ydl.extract_info(url, download=False)


def _extract_tiktok_profile_with_fallback(
    opts: dict, url: str, *, cookies_file: str | None = None, log: Callable[[str], None] | None = None,
) -> dict | None:
    """Recover TikTok's secondary user ID from the profile's embed page.

    TikTok sometimes serves a challenge page for the normal profile extractor.
    The embed page still exposes a recent video ID; the video extractor then
    provides ``channel_id`` and lets yt-dlp use its normal profile API.
    """
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info:
                return info
            raise RuntimeError("TikTok profile extractor returned no data")
    except Exception as original:
        parts = urlsplit(url)
        host = parts.netloc.lower().removeprefix("www.")
        if host != "tiktok.com" or not parts.path.startswith("/@"):
            raise
        username = parts.path[2:].split("/", 1)[0]
        cookies: dict[str, str] = {}
        if cookies_file:
            try:
                jar = MozillaCookieJar(str(cookies_file))
                jar.load(ignore_discard=True, ignore_expires=False)
                cookies = {c.name: c.value for c in jar if "tiktok" in c.domain.lower()}
            except Exception:
                pass
        video_id = None
        for client in ("safari", "edge"):
            try:
                response = curl_requests.get(
                    f"https://www.tiktok.com/embed/@{username}",
                    impersonate=client, cookies=cookies or None, timeout=20,
                )
                match = re.search(
                    r'<script[^>]+id=["\']__FRONTITY_CONNECT_STATE__["\'][^>]*>(.*?)</script>',
                    response.text, re.S,
                )
                if not match:
                    continue
                state = json.loads(match.group(1))
                data = next(
                    (value for key, value in (state.get("source", {}).get("data", {}) or {}).items()
                     if key.rstrip("/").lower() == f"/embed/@{username}".lower()),
                    {},
                )
                video_id = next((item.get("id") for item in data.get("videoList", []) if item.get("id")), None)
                if video_id:
                    break
            except Exception:
                continue
        if not video_id:
            raise original

        # Import lazily to avoid the playlist ↔ downloader module cycle.
        from app.downloader import VideoDownloader
        entry = VideoEntry(str(video_id), str(video_id), f"https://www.tiktok.com/@{username}/video/{video_id}", "tiktok", None, None)
        with yt_dlp.YoutubeDL(opts) as ydl:
            detail = VideoDownloader._extract_tiktok_safari_info(ydl, entry)
            channel_id = detail.get("channel_id")
        if not channel_id:
            raise original
        fallback_url = f"tiktokuser:{channel_id}"
        if log:
            log("TikTok profile blocked; recovered channel_id automatically from a recent public video")
        retry_opts = dict(opts)
        with yt_dlp.YoutubeDL(retry_opts) as ydl:
            # Return the extractor's entry generator directly. Passing a large
            # account back through YoutubeDL.extract_info would eagerly walk
            # thousands of posts before the batched crawler receives item one.
            return ydl.get_info_extractor("TikTokUser").extract(fallback_url)


def _match_filter_for_link(content_type: str) -> str | None:
    if content_type == "videos":
        return "!is_live & duration > 60"
    if content_type == "shorts":
        return "!is_live & duration <= 60"
    return "!is_live"


def _sort_newest(entries: list[VideoEntry]) -> list[VideoEntry]:
    return sorted(entries, key=lambda e: e.upload_date or "", reverse=True)


def _expand_youtube_link(link: str, content_type: str) -> list[tuple[str, str]]:
    if content_type != "all":
        return [(link, content_type)]
    base_link = link.rstrip("/")
    return [
        (f"{base_link}/videos", "videos"),
        (f"{base_link}/shorts", "shorts"),
    ]


def _entries_from_info(
    info: dict,
    platform: str,
    crawl_type: str,
    global_cfg: GlobalConfig,
    skip_ids: set[str],
    seen_ids: set[str],
) -> list[VideoEntry]:
    items: list[VideoEntry] = []
    entries = info.get("entries") or [info]
    for raw in entries:
        if not raw:
            continue
        vid = raw.get("id")
        if not vid or vid in seen_ids or vid in skip_ids:
            continue
        title = raw.get("title") or vid
        url = raw.get("url") or raw.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"
        duration = raw.get("duration")
        if crawl_type == "videos" and duration is not None and duration <= 60:
            continue
        seen_ids.add(vid)
        items.append(
            VideoEntry(
                video_id=vid,
                title=title,
                url=url,
                platform=platform,
                duration=float(duration) if duration is not None else None,
                upload_date=raw.get("upload_date"),
                thumbnail=raw.get("thumbnail") or (f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if platform == "youtube" else None),
                view_count=raw.get("view_count"),
                like_count=raw.get("like_count"),
            )
        )
    return items


def fetch_all_entries(
    source: SourceConfig,
    global_cfg: GlobalConfig,
    skip_ids: set[str] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[VideoEntry]:
    """Crawl all source links fully, return newest-first list."""
    log = log or (lambda _m: None)
    skip_ids = skip_ids or set()
    seen_ids: set[str] = set()
    all_entries: list[VideoEntry] = []

    for link in source.links:
        classified = classify_link(link)
        if not classified:
            log(f"Skip invalid link: {link}")
            continue
        platform, content_type = classified
        for crawl_link, crawl_type in _expand_youtube_link(link, content_type):
            match_filter = _match_filter_for_link(crawl_type)
            opts = _base_ydl_opts(
                log,
                platform=platform,
                youtube_cookies_file=global_cfg.youtube_cookies_file,
                youtube_cookies_browser=global_cfg.youtube_cookies_browser,
                tiktok_cookies_file=global_cfg.tiktok_cookies_file,
            )
            if match_filter:
                opts["match_filter"] = match_filter

            log(f"Crawling: {crawl_link}")
            try:
                if platform == "tiktok" and crawl_type == "profile":
                    info = _extract_tiktok_profile_with_fallback(
                        opts, crawl_link, cookies_file=global_cfg.tiktok_cookies_file, log=log
                    )
                else:
                    info = _extract_info_with_cookie_fallback(
                        opts,
                        crawl_link,
                        cookies_file=global_cfg.youtube_cookies_file if platform == "youtube" else None,
                        log=log,
                    )
            except Exception as e:
                if _is_missing_tab(str(e)):
                    log(f"Skip (no {crawl_type} tab): {crawl_link}")
                else:
                    log(f"Crawl error {crawl_link}: {e}")
                continue

            if not info:
                continue
            all_entries.extend(
                _entries_from_info(
                    info,
                    platform,
                    crawl_type,
                    global_cfg,
                    skip_ids,
                    seen_ids,
                )
            )

    return _sort_newest(all_entries)


def fetch_entries_batched(
    source: SourceConfig,
    global_cfg: GlobalConfig,
    *,
    batch_size: int = 50,
    skip_ids: set[str] | None = None,
    known_stop_after: int = 24,
    log: Callable[[str], None] | None = None,
):
    """Yield a full channel crawl incrementally while yt-dlp exposes entries."""
    log = log or (lambda _m: None)
    skip_ids = skip_ids or set()
    seen_ids: set[str] = set()
    size = max(1, int(batch_size))
    batch: list[VideoEntry] = []
    for link in source.links:
        classified = classify_link(link)
        if not classified:
            log(f"Skip invalid link: {link}")
            continue
        platform, content_type = classified
        for crawl_link, crawl_type in _expand_youtube_link(link, content_type):
            opts = _base_ydl_opts(
                log,
                platform=platform,
                youtube_cookies_file=global_cfg.youtube_cookies_file,
                youtube_cookies_browser=global_cfg.youtube_cookies_browser,
                tiktok_cookies_file=global_cfg.tiktok_cookies_file,
            )
            opts["lazy_playlist"] = True
            match_filter = _match_filter_for_link(crawl_type)
            if match_filter:
                opts["match_filter"] = match_filter
            log(f"Crawling: {crawl_link}")
            try:
                if platform == "tiktok" and crawl_type == "profile":
                    info = _extract_tiktok_profile_with_fallback(
                        opts, crawl_link, cookies_file=global_cfg.tiktok_cookies_file, log=log
                    )
                else:
                    info = _extract_info_with_cookie_fallback(
                        opts,
                        crawl_link,
                        cookies_file=global_cfg.youtube_cookies_file if platform == "youtube" else None,
                        log=log,
                    )
                entries = (info or {}).get("entries") or [info]
                known_streak = 0
                for raw in entries:
                    if not raw:
                        continue
                    raw_id = raw.get("id")
                    if raw_id and raw_id in skip_ids:
                        known_streak += 1
                        if known_streak >= max(1, int(known_stop_after)):
                            log(f"Incremental stop: {known_streak} known videos reached for {crawl_link}")
                            break
                        continue
                    converted = _entries_from_info({"entries": [raw]}, platform, crawl_type, global_cfg, skip_ids, seen_ids)
                    if converted:
                        known_streak = 0
                        batch.extend(converted)
                    if len(batch) >= size:
                        yield _sort_newest(batch)
                        batch = []
            except Exception as e:
                if _is_missing_tab(str(e)):
                    log(f"Skip (no {crawl_type} tab): {crawl_link}")
                else:
                    log(f"Crawl error {crawl_link}: {e}")
    if batch:
        yield _sort_newest(batch)


def fetch_video_metadata(url: str, global_cfg: GlobalConfig) -> dict:
    """Fetch detail metadata for one review candidate without downloading media."""
    platform = _platform_from_url(url)
    opts = _base_ydl_opts(
        platform=platform,
        youtube_cookies_file=global_cfg.youtube_cookies_file,
        youtube_cookies_browser=global_cfg.youtube_cookies_browser,
        tiktok_cookies_file=global_cfg.tiktok_cookies_file,
    )
    opts.update({"extract_flat": False, "noplaylist": True})
    info = _extract_info_with_cookie_fallback(
        opts,
        url,
        cookies_file=global_cfg.youtube_cookies_file if platform == "youtube" else None,
    ) or {}
    return {
        "title": info.get("title"), "webpage_url": info.get("webpage_url") or url,
        "upload_date": info.get("upload_date"), "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"), "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
    }
