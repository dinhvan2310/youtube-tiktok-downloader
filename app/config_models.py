from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from app.ytdlp_cookies import cookie_file_issue

# ponytail: permissive slug after @, channel/, c/, user/; unquote runs before match in classify_link.
_YT_CHANNEL = r"(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)"
_YT_BASE = rf"^https?://(?:www\.)?youtube\.com/{_YT_CHANNEL}"

YOUTUBE_VIDEOS_RE = re.compile(rf"{_YT_BASE}/videos/?$", re.I)
YOUTUBE_SHORTS_RE = re.compile(rf"{_YT_BASE}/shorts/?$", re.I)
YOUTUBE_CHANNEL_RE = re.compile(rf"{_YT_BASE}/?$", re.I)
TIKTOK_PROFILE_RE = re.compile(
    r"^https?://(?:www\.)?tiktok\.com/@[^/?#]+/?$",
    re.I,
)
DOUYIN_USER_RE = re.compile(
    r"^https?://(?:www\.)?douyin\.com/user/[^/?#]+/?$",
    re.I,
)
DOUYIN_VIDEO_RE = re.compile(
    r"^https?://(?:www\.)?douyin\.com/video/\d+/?(?:[?#].*)?$",
    re.I,
)

_LINK_IN_TEXT_RE = re.compile(r"https?://[^\s<>'\"]+", re.I)

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov"}

REUP_YOUTUBE_TIKTOK = "youtube_tiktok"
REUP_DOUYIN = "douyin"
REUP_SOURCES = (REUP_YOUTUBE_TIKTOK, REUP_DOUYIN)

_REUP_LABELS = {
    REUP_YOUTUBE_TIKTOK: "YouTube/TikTok",
    REUP_DOUYIN: "Douyin",
}


def normalize_reup_source(value: str | None) -> str:
    raw = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "youtube_tiktok": REUP_YOUTUBE_TIKTOK,
        "youtube": REUP_YOUTUBE_TIKTOK,
        "tiktok": REUP_YOUTUBE_TIKTOK,
        "yt": REUP_YOUTUBE_TIKTOK,
        "yt_tiktok": REUP_YOUTUBE_TIKTOK,
        "douyin": REUP_DOUYIN,
        "dy": REUP_DOUYIN,
        "抖音": REUP_DOUYIN,
    }
    return aliases.get(raw, REUP_YOUTUBE_TIKTOK)


def reup_label(reup_source: str) -> str:
    return _REUP_LABELS.get(normalize_reup_source(reup_source), "YouTube/TikTok")


def is_douyin_source(source: "SourceConfig") -> bool:
    return normalize_reup_source(source.reup_source) == REUP_DOUYIN


@dataclass
class GlobalConfig:
    quality_height: int = 0
    target_videos_per_page: int = 20
    source_thread_count: int = 4
    download_source_concurrency: int = 4
    global_download_concurrency: int = 12
    metadata_workers: int = 6
    source_root_path: str = ""
    youtube_cookies_file: str = ""
    youtube_cookies_browser: str = ""
    tiktok_cookies_file: str = ""
    douyin_cookie: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "quality_height": self.quality_height,
            "target_videos_per_page": self.target_videos_per_page,
            "source_thread_count": self.source_thread_count,
            "download_source_concurrency": self.download_source_concurrency,
            "global_download_concurrency": self.global_download_concurrency,
            "metadata_workers": self.metadata_workers,
            "source_root_path": self.source_root_path,
            "youtube_cookies_file": self.youtube_cookies_file,
            "youtube_cookies_browser": self.youtube_cookies_browser,
            "tiktok_cookies_file": self.tiktok_cookies_file,
            "douyin_cookie": self.douyin_cookie,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalConfig:
        # Migrate old page_thread_count → source_thread_count
        source_threads = data.get("source_thread_count", data.get("page_thread_count", 4))
        # Legacy output_base_folder → source_root_path when missing
        root = data.get("source_root_path", data.get("output_base_folder", ""))
        # Legacy douyin_url/path/mode/max_counts keys are ignored if present.
        return cls(
            quality_height=int(data.get("quality_height", 0)),
            target_videos_per_page=int(data.get("target_videos_per_page", 20)),
            source_thread_count=int(source_threads),
            download_source_concurrency=int(data.get("download_source_concurrency", 4)),
            global_download_concurrency=int(data.get("global_download_concurrency", 12)),
            metadata_workers=int(data.get("metadata_workers", 6)),
            source_root_path=str(root or "").strip(),
            youtube_cookies_file=str(data.get("youtube_cookies_file", "")).strip(),
            youtube_cookies_browser=str(data.get("youtube_cookies_browser", "")).strip().lower(),
            tiktok_cookies_file=str(data.get("tiktok_cookies_file", "")).strip(),
            douyin_cookie=str(data.get("douyin_cookie", "")),
        )


@dataclass
class SourceConfig:
    id: str
    path_download: str
    links: list[str] = field(default_factory=list)
    note: str = ""
    reup_source: str = REUP_YOUTUBE_TIKTOK

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path_download": self.path_download,
            "links": list(self.links),
            "note": self.note,
            "reup_source": normalize_reup_source(self.reup_source),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceConfig:
        raw_id = data.get("id", "")
        return cls(
            id=str(raw_id).strip() if raw_id is not None and str(raw_id).strip() else "",
            path_download=str(data.get("path_download", "")).strip(),
            links=[str(x).strip() for x in data.get("links", []) if str(x).strip()],
            note=str(data.get("note", "")).strip(),
            reup_source=normalize_reup_source(str(data.get("reup_source", "") or "")),
        )


def source_label(source: SourceConfig) -> str:
    """Human label for logs/UI — never the internal id."""
    note = (source.note or "").strip()
    if note:
        return note
    name = Path(source.path_download).name
    return name or "Source"


@dataclass
class AppConfig:
    global_config: GlobalConfig = field(default_factory=GlobalConfig)
    sources: list[SourceConfig] = field(default_factory=list)

    def new_source_id(self) -> str:
        existing = {s.id for s in self.sources}
        for _ in range(32):
            sid = secrets.token_hex(8)
            if sid not in existing:
                return sid
        # Extremely unlikely fallback
        return secrets.token_hex(16)

    def next_source_id(self) -> str:
        # Back-compat alias
        return self.new_source_id()

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.global_config.to_dict(),
            "sources": [s.to_dict() for s in self.sources],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        sources_data = data.get("sources")
        if sources_data is None:
            # Migrate legacy pages[] → sources[]
            sources = _migrate_pages(data)
        else:
            sources = [SourceConfig.from_dict(s) for s in sources_data]
        # Ensure every source has a non-empty id (legacy / hand-edited JSON)
        used = {s.id for s in sources if s.id}
        for source in sources:
            if source.id:
                continue
            while True:
                sid = secrets.token_hex(8)
                if sid not in used:
                    source.id = sid
                    used.add(sid)
                    break

        skip_keys = {"sources", "pages"}
        global_data = {k: v for k, v in data.items() if k not in skip_keys}
        return cls(
            global_config=GlobalConfig.from_dict(global_data),
            sources=sources,
        )


def _migrate_pages(data: dict[str, Any]) -> list[SourceConfig]:
    pages = data.get("pages") or []
    base = str(data.get("output_base_folder", "")).strip()
    sources: list[SourceConfig] = []
    for i, page in enumerate(pages, start=1):
        page_id = str(page.get("page_id", "")).strip()
        path = str(Path(base) / page_id) if base and page_id else ""
        links = [str(x).strip() for x in page.get("links", []) if str(x).strip()]
        note = str(page.get("note", page.get("page_name", ""))).strip()
        sources.append(SourceConfig(id=str(i), path_download=path, links=links, note=note))
    return sources


def classify_link(url: str) -> tuple[str, str] | None:
    """Return (platform, content_type) or None if invalid."""
    url = unquote(url.strip().rstrip(".,;)]"))
    if YOUTUBE_VIDEOS_RE.match(url):
        return "youtube", "videos"
    if YOUTUBE_SHORTS_RE.match(url):
        return "youtube", "shorts"
    if YOUTUBE_CHANNEL_RE.match(url):
        return "youtube", "all"
    if TIKTOK_PROFILE_RE.match(url):
        return "tiktok", "profile"
    if DOUYIN_USER_RE.match(url):
        return "douyin", "post"
    if DOUYIN_VIDEO_RE.match(url):
        return "douyin", "one"
    return None


def parse_links_text(text: str) -> list[str]:
    """Extract http(s) URLs from pasted text; ignore non-URL lines like stray words."""
    links: list[str] = []
    seen: set[str] = set()
    for match in _LINK_IN_TEXT_RE.finditer(text):
        url = unquote(match.group(0).rstrip(".,;)]"))
        if url in seen:
            continue
        seen.add(url)
        links.append(url)
    return links


def validate_source(source: SourceConfig) -> list[str]:
    errors: list[str] = []
    if not source.path_download:
        errors.append("Download path is required")
    if not source.links:
        errors.append("At least one source link is required")
    reup = normalize_reup_source(source.reup_source)
    for link in source.links:
        classified = classify_link(link)
        if classified is None:
            errors.append(f"Invalid link: {link}")
            continue
        platform, _ = classified
        if reup == REUP_DOUYIN and platform != "douyin":
            errors.append(f"Douyin source needs Douyin URL: {link}")
        elif reup == REUP_YOUTUBE_TIKTOK and platform == "douyin":
            errors.append(f"YouTube/TikTok source cannot use Douyin URL: {link}")
    return errors


def validate_global_config(cfg: GlobalConfig) -> list[str]:
    errors: list[str] = []
    if cfg.quality_height < 0:
        errors.append("Invalid video quality")
    if cfg.target_videos_per_page < 1:
        errors.append("Target videos must be >= 1")
    if cfg.source_thread_count < 1:
        errors.append("Parallel sources must be >= 1")
    if not 1 <= cfg.download_source_concurrency <= 8:
        errors.append("Parallel download sources must be between 1 and 8")
    if not 1 <= cfg.global_download_concurrency <= 32:
        errors.append("Global downloads must be between 1 and 32")
    if not 1 <= cfg.metadata_workers <= 16:
        errors.append("Metadata workers must be between 1 and 16")
    for label, cookie_path, domain in (
        ("YouTube", cfg.youtube_cookies_file, ".youtube.com"),
        ("TikTok", cfg.tiktok_cookies_file, ".tiktok.com"),
    ):
        issue = cookie_file_issue(cookie_path, domain=domain)
        if issue:
            errors.append(f"{label} cookies {issue}: {cookie_path}")
    return errors
