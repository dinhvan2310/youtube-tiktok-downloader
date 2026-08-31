from __future__ import annotations

import re
import shutil
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

import yt_dlp

from app.config_models import GlobalConfig
from app.db import DownloadDB
from app.filename import sanitize_title, unique_filepath
from app.paths import resolve_ffmpeg
from app.playlist import VideoEntry
from app.ytdlp_cookies import apply_tiktok_cookies, apply_youtube_cookies, cookie_file_fallback_options, is_browser_cookie_locked

_download_condition = threading.Condition()
_active_downloads = 0


@contextmanager
def _download_slot(limit: int):
    global _active_downloads
    with _download_condition:
        while _active_downloads >= max(1, int(limit)):
            _download_condition.wait(timeout=1)
        _active_downloads += 1
    try:
        yield
    finally:
        with _download_condition:
            _active_downloads = max(0, _active_downloads - 1)
            _download_condition.notify_all()


def _format_selector(quality: int, platform: str = "") -> str:
    if int(quality) <= 0:
        if platform == "tiktok":
            return "best[vcodec^=avc1]/best[vcodec^=h264]/download/best"
        return "bv*+ba/b" if platform == "youtube" else "bestvideo+bestaudio/best"
    if platform == "tiktok":
        # bytevc1/h265 often advertises AAC but the file is video-only (silent).
        # Prefer H264 / download; never let bare "best" win first (picks silent HEVC).
        return (
            f"best[vcodec^=avc1][height<={quality}]/"
            f"best[vcodec^=h264][height<={quality}]/"
            f"best[vcodec^=avc1]/"
            f"best[vcodec^=h264]/"
            f"download/best"
        )
    if platform == "youtube":
        # Prefer YouTube quality label (1080p/720p/...), not raw pixel height.
        # Shorts "1080p" is often 1080x1440 — height<=1080 alone picks 720p wrongly.
        q = int(quality)
        label = f"{q}p"
        return (
            f"bv*[format_note*={label}]+ba/"
            f"b[format_note*={label}]/"
            f"bv*[height={q}]+ba/"
            f"bv*[width={q}]+ba/"
            f"bv*[height<={q}]+ba/"
            f"bv*[width<={q}]+ba/"
            f"b[height={q}]/b[width={q}]/"
            f"b[height<={q}]/b[width<={q}]/"
            f"bv*+ba/b"
        )
    return f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"


class VideoDownloader:
    def __init__(
        self,
        global_cfg: GlobalConfig,
        db: DownloadDB,
        log: Callable[[str], None] | None = None,
        progress_log: Callable[[str], None] | None = None,
        progress_event: Callable[[dict], None] | None = None,
        stop_check: Callable[[], bool] | None = None,
    ) -> None:
        self.global_cfg = global_cfg
        self.db = db
        self._log = log or (lambda _m: None)
        self._progress_log = progress_log or (lambda _m: None)
        self._progress_event = progress_event or (lambda _data: None)
        self._stop_check = stop_check or (lambda: False)

    def download_one(self, source_id: str, entry: VideoEntry, page_dir: Path) -> bool:
        with _download_slot(self.global_cfg.global_download_concurrency):
            return self._download_one(source_id, entry, page_dir)

    def _download_one(self, source_id: str, entry: VideoEntry, page_dir: Path) -> bool:
        if self._stop_check():
            return False
        if self.db.is_downloaded(source_id, entry.video_id):
            self._log(f"Already downloaded, skip: {entry.title}")
            return False

        page_dir.mkdir(parents=True, exist_ok=True)
        tmp_outtmpl = str(page_dir / f"__tmp_{entry.video_id}.%(ext)s")

        progress_state = {"last_download_line": ""}
        self._progress_event({"stage": "starting", "source_id": source_id, "video_id": entry.video_id, "title": entry.title})
        opts = {
            "format": _format_selector(self.global_cfg.quality_height, entry.platform),
            "outtmpl": tmp_outtmpl,
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "socket_timeout": 30,
            # YouTube requests can stall on IPv6 on some Windows networks.
            "source_address": "0.0.0.0",
            "retries": 2,
            "extractor_retries": 2,
            "fragment_retries": 2,
            # YouTube now requires an external JS runtime (EJS challenge).
            # Deno is yt-dlp's default, but Windows installs commonly have Node only.
            "js_runtimes": {"node": {"path": shutil.which("node") or "node"}},
            "remote_components": {"ejs:github"},
            "progress_hooks": [self._make_progress_hook(source_id, entry, progress_state)],
        }
        if entry.platform == "tiktok":
            # Prefer H264 over silent bytevc1/h265 when sorting ties.
            opts["format_sort"] = ["vcodec:h264", "res", "br"]
            apply_tiktok_cookies(opts, cookies_file=self.global_cfg.tiktok_cookies_file)
        elif entry.platform == "youtube":
            apply_youtube_cookies(
                opts,
                cookies_file=self.global_cfg.youtube_cookies_file,
                cookies_browser=self.global_cfg.youtube_cookies_browser,
            )
        ffmpeg = resolve_ffmpeg()
        if ffmpeg:
            opts["ffmpeg_location"] = str(Path(ffmpeg).parent)
        else:
            # Without FFmpeg yt-dlp cannot merge separate video/audio streams.
            # Prefer a progressive stream so the download still completes.
            opts["format"] = "b/best"
            self._log("FFmpeg not found; using a progressive YouTube format")

        try:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(entry.url, download=True)
            except Exception as error:
                fallback_opts = cookie_file_fallback_options(opts, self.global_cfg.youtube_cookies_file)
                if not fallback_opts or not is_browser_cookie_locked(error):
                    raise
                self._log("Browser cookies are locked; retrying this video with cookies.txt")
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(entry.url, download=True)
            if not info:
                self._cleanup_entry_files(page_dir, entry.video_id)
                self._log(f"Download failed: {entry.title}")
                return False

            ext = info.get("ext") or "mp4"
            final_name = sanitize_title(info.get("title") or entry.title, fallback=entry.video_id)
            tmp_path = page_dir / f"__tmp_{entry.video_id}.{ext}"
            if not tmp_path.exists():
                tmp_path = self._resolve_downloaded_path(
                    page_dir, f"__tmp_{entry.video_id}", ext, f"__tmp_{entry.video_id}"
                )
            if tmp_path is None or not tmp_path.exists():
                self._cleanup_entry_files(page_dir, entry.video_id)
                self._log(f"File not found after download: {entry.title}")
                return False

            target = unique_filepath(page_dir, final_name, tmp_path.suffix)
            if tmp_path != target:
                tmp_path.rename(target)
            downloaded_path = target

            if not self.db.insert(
                page_id=source_id,
                video_id=entry.video_id,
                platform=entry.platform,
                title=entry.title,
                source_url=entry.url,
                file_path=str(downloaded_path),
            ):
                downloaded_path.unlink(missing_ok=True)
                self._cleanup_entry_files(page_dir, entry.video_id)
                self._log(f"DB duplicate, removed file: {entry.title}")
                return False

            self._log(f"Downloaded: {downloaded_path.name}")
            self._progress_event({"stage": "completed", "source_id": source_id, "video_id": entry.video_id, "title": entry.title})
            return True

        except Exception as e:
            clean_error = self._clean_error_text(str(e))
            if entry.platform == "youtube" and self._is_youtube_bot_check(clean_error):
                auth_hint = "Open Settings → YouTube access, then select your signed-in browser or a cookies.txt file."
                clean_error = f"YouTube needs account verification. {auth_hint}"
            elif entry.platform == "tiktok" and self._is_tiktok_cookie_error(clean_error):
                clean_error = "TikTok needs fresh access cookies. Open Settings → Platform access and select a TikTok cookies.txt file."
            if progress_state["last_download_line"]:
                self._progress_log(f"{progress_state['last_download_line']}ERROR: {clean_error}")
            else:
                self._progress_log(f"ERROR: {clean_error}")
            self._cleanup_entry_files(page_dir, entry.video_id)
            self._progress_event({"stage": "failed", "source_id": source_id, "video_id": entry.video_id, "title": entry.title, "error": clean_error})
            if self._is_gone_video_error(clean_error):
                self.db.delete_crawled(source_id, entry.video_id)
                self._log(f"Dropped from queue (gone): {entry.title}")
            self._log(f"Download error {entry.title}: {e}")
            return False

    @staticmethod
    def _is_youtube_bot_check(message: str) -> bool:
        lower = message.lower()
        return "sign in to confirm you\u2019re not a bot" in lower or "sign in to confirm you're not a bot" in lower or "cookies-from-browser" in lower

    @staticmethod
    def _is_tiktok_cookie_error(message: str) -> bool:
        lower = message.lower()
        markers = ("fresh cookies", "login required", "status code 403", "http error 403", "forbidden")
        return any(marker in lower for marker in markers)

    @staticmethod
    def _is_gone_video_error(message: str) -> bool:
        lower = message.lower()
        markers = (
            "video unavailable",
            "has been removed",
            "private video",
            "is not available",
            "account associated with this video has been terminated",
        )
        return any(m in lower for m in markers)

    def _cleanup_entry_files(self, page_dir: Path, video_id: str) -> None:
        for path in page_dir.glob(f"__tmp_{video_id}*"):
            if path.is_file():
                path.unlink(missing_ok=True)

    def _make_progress_hook(self, source_id: str, entry: VideoEntry, progress_state: dict[str, str]):
        # ponytail: throttle UI progress spam so Tk main thread stays responsive
        last: dict[str, float | str] = {"t": 0.0, "pct": ""}

        def hook(data: dict) -> None:
            status = data.get("status")
            if status == "downloading":
                percent_value = data.get("_percent_str", "").strip()
                total = data.get("_total_bytes_str") or data.get("_total_bytes_estimate_str") or "?"
                speed = data.get("_speed_str") or "?"
                eta = data.get("_eta_str") or "?"
                line = f"[download] {percent_value:>6} of {total:>10} at {speed:>10} ETA {eta}"
                progress_state["last_download_line"] = line
                now = time.monotonic()
                if percent_value == last["pct"] and now - float(last["t"]) < 1.0:
                    return
                last["pct"] = percent_value
                last["t"] = now
                self._progress_log(line)
                downloaded = int(data.get("downloaded_bytes") or 0)
                total_bytes = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
                percent = round(downloaded * 100 / total_bytes, 1) if total_bytes else None
                self._progress_event({
                    "stage": "downloading", "source_id": source_id, "video_id": entry.video_id,
                    "title": entry.title, "percent": percent, "downloaded_bytes": downloaded,
                    "total_bytes": total_bytes, "eta": data.get("eta"),
                })
            elif status == "finished":
                self._progress_log(f"100% | {entry.title} | finishing file...")
                self._progress_event({"stage": "finishing", "source_id": source_id, "video_id": entry.video_id, "title": entry.title, "percent": 100})

        return hook

    def _clean_error_text(self, text: str) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", text).replace("\r", " ").replace("\n", " ").strip()

    def _resolve_downloaded_path(
        self, page_dir: Path, final_name: str, ext: str, safe_title: str
    ) -> Path | None:
        ext = ext if ext.startswith(".") else f".{ext}"
        candidates = [
            page_dir / f"{final_name}{ext}",
            page_dir / f"{safe_title}{ext}",
        ]
        for c in candidates:
            if c.exists():
                return c
        matches = [
            p
            for p in page_dir.iterdir()
            if p.is_file() and p.stem in {final_name, safe_title}
        ]
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)
        recent = sorted(
            [p for p in page_dir.iterdir() if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return recent[0] if recent else None
