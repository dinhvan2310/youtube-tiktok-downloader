"""yt-dlp helpers for YouTube (cookies + JS challenge solver)."""

from __future__ import annotations

from pathlib import Path


_SUPPORTED_BROWSERS = {"brave", "chrome", "chromium", "edge", "firefox", "opera", "vivaldi", "whale"}


def _existing_cookie_file(value: str | None) -> str:
    file_path = (value or "").strip()
    return file_path if file_path and Path(file_path).is_file() else ""


def normalize_youtube_cookie_browser(value: str | None) -> str:
    """Return a yt-dlp supported local browser name, or an empty value."""
    browser = (value or "").strip().lower()
    return browser if browser in _SUPPORTED_BROWSERS else ""


def cookie_file_fallback_options(opts: dict, cookies_file: str | None) -> dict | None:
    """Fall back to an exported cookie file when a live browser database is locked."""
    file_path = _existing_cookie_file(cookies_file)
    if "cookiesfrombrowser" not in opts or not file_path:
        return None
    fallback = dict(opts)
    fallback.pop("cookiesfrombrowser", None)
    fallback["cookiefile"] = file_path
    return fallback


def is_browser_cookie_locked(error: Exception | str) -> bool:
    text = str(error).lower()
    return "could not copy chrome cookie database" in text or "failed to load cookies" in text


def apply_youtube_cookies(
    opts: dict,
    *,
    cookies_file: str | None = None,
    cookies_browser: str | None = None,
) -> dict:
    """
    Apply YouTube-specific yt-dlp options:
    - Netscape cookies.txt when present (stable on Windows)
    - Cookies from an explicitly selected browser only when no file is available
    - Allow EJS remote components (required since mid-2026 for real video formats)
    """
    file_path = _existing_cookie_file(cookies_file)
    browser = normalize_youtube_cookie_browser(cookies_browser)
    if file_path:
        opts["cookiefile"] = file_path
    elif browser:
        # Chromium databases are frequently locked on Windows, so this is a
        # fallback rather than the primary credential source.
        opts["cookiesfrombrowser"] = (browser,)
    # Without this, yt-dlp often only sees storyboards → "Requested format is not available"
    opts["remote_components"] = ["ejs:github"]
    return opts


def apply_tiktok_cookies(opts: dict, *, cookies_file: str | None = None) -> dict:
    """Attach only an explicitly configured TikTok Netscape cookie jar."""
    file_path = _existing_cookie_file(cookies_file)
    if file_path:
        opts["cookiefile"] = file_path
    return opts
