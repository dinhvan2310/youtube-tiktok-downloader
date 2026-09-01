from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Callable

import yt_dlp
from curl_cffi import requests as curl_requests
from yt_dlp.extractor.tiktok import TikTokIE
from yt_dlp.networking.impersonate import ImpersonateTarget

from app.config_models import GlobalConfig
from app.db import DownloadDB
from app.filename import sanitize_title, unique_filepath
from app.paths import resolve_ffmpeg, resolve_ffprobe, resolve_node
from app.playlist import VideoEntry
from app.ytdlp_cookies import apply_tiktok_cookies, apply_youtube_cookies, cookie_file_fallback_options, cookie_file_issue, is_browser_cookie_locked

_download_condition = threading.Condition()
_active_downloads = 0


class _QuietExtractorLogger:
    """Keep expected TikTok challenge errors out of the shared task log."""

    def debug(self, _message: str) -> None:
        pass

    def write_debug(self, _message: str) -> None:
        pass

    def warning(self, _message: str, **_kwargs) -> None:
        pass

    def error(self, _message: str, **_kwargs) -> None:
        pass


@dataclass(frozen=True)
class DownloadResult:
    """Keep expected skips distinct from media-download failures."""

    status: str


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
    limit = max(0, int(quality))
    if platform == "tiktok":
        # Resolution is constrained separately via format_sort so portrait videos
        # are evaluated by their short edge instead of raw height.
        if limit:
            return (
                f"best[height<={limit}]/"
                f"best[width<={limit}]/"
                f"download[height<={limit}]/download[width<={limit}]"
            )
        return "download/best"
    if platform == "youtube":
        if limit:
            # Prefer streams Facebook can use directly.  YouTube's 4K AV1/VP9
            # formats otherwise force a lengthy, CPU-bound H.264 conversion.
            # Keep the generic formats as fallbacks for videos that do not
            # expose an AVC/AAC rendition.
            return (
                f"bv*[vcodec^=avc1][height<={limit}]+ba[acodec^=mp4a]/"
                f"bv*[vcodec^=avc1][width<={limit}]+ba[acodec^=mp4a]/"
                f"b[vcodec^=avc1][acodec^=mp4a][height<={limit}]/"
                f"b[vcodec^=avc1][acodec^=mp4a][width<={limit}]/"
                f"bv*[height<={limit}]+ba/bv*[width<={limit}]+ba/"
                f"b[height<={limit}]/b[width<={limit}]"
            )
        return (
            "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/"
            "b[vcodec^=avc1][acodec^=mp4a]/"
            "bv*+ba/b"
        )
    if limit:
        return (
            f"bestvideo[height<={limit}]+bestaudio/"
            f"bestvideo[width<={limit}]+bestaudio/"
            f"best[height<={limit}]/best[width<={limit}]"
        )
    return "bestvideo+bestaudio/best"


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

    def download_one(self, source_id: str, entry: VideoEntry, page_dir: Path) -> DownloadResult:
        with _download_slot(self.global_cfg.global_download_concurrency):
            return self._download_one(source_id, entry, page_dir)

    def _download_one(self, source_id: str, entry: VideoEntry, page_dir: Path) -> DownloadResult:
        if self._stop_check():
            return DownloadResult("skipped")
        if self.db.is_downloaded(source_id, entry.video_id):
            self._log(f"Already downloaded, skip: {entry.title}")
            return DownloadResult("skipped")

        page_dir.mkdir(parents=True, exist_ok=True)
        tmp_outtmpl = str(page_dir / f"__tmp_{entry.video_id}.%(ext)s")

        progress_state = {"last_download_line": ""}
        quality_limit = max(0, int(self.global_cfg.quality_height))
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
            "js_runtimes": {"node": {"path": resolve_node() or "node"}},
            "remote_components": {"ejs:github"},
            "progress_hooks": [self._make_progress_hook(source_id, entry, progress_state)],
        }
        if entry.platform == "tiktok":
            opts["logger"] = _QuietExtractorLogger()
            # Prefer the best short-edge resolution within the configured cap.
            # The final output is normalized to H.264/AAC after download.
            opts["format_sort"] = ([f"res:{quality_limit}"] if quality_limit else ["res"]) + ["br"]
            cookie_path = self.global_cfg.tiktok_cookies_file
            cookie_issue = cookie_file_issue(cookie_path, domain=".tiktok.com")
            if cookie_path:
                if cookie_issue:
                    self._log(f"TikTok cookies unavailable ({cookie_issue}); continuing without cookie file")
                else:
                    self._log("TikTok cookies file detected and attached to extractor")
            apply_tiktok_cookies(opts, cookies_file=cookie_path)
        elif entry.platform == "youtube":
            if quality_limit:
                opts["format_sort"] = [f"res:{quality_limit}"]
            apply_youtube_cookies(
                opts,
                cookies_file=self.global_cfg.youtube_cookies_file,
                cookies_browser=self.global_cfg.youtube_cookies_browser,
            )
        ffmpeg = resolve_ffmpeg()
        ffprobe = resolve_ffprobe()
        if ffmpeg:
            opts["ffmpeg_location"] = str(Path(ffmpeg).parent)
        else:
            return self._fail_before_download(
                source_id,
                entry,
                page_dir,
                "FFmpeg is required to produce Facebook-compatible downloads. Reinstall or rebuild the app to restore its media tools.",
            )
        if not ffprobe:
            return self._fail_before_download(
                source_id,
                entry,
                page_dir,
                "FFprobe is required to verify the downloaded video codec and resolution. Reinstall or rebuild the app to restore its media tools.",
            )

        try:
            if entry.platform == "tiktok":
                # TikTok's default yt-dlp webpage client is currently served a
                # challenge document. Use the CDN fallback first so the normal
                # path never emits a false failure before a successful retry.
                self._log("TikTok: using browser-compatible webpage/CDN download")
                fallback_opts = dict(opts)
                fallback_opts["impersonate"] = ImpersonateTarget("safari")
                fallback_opts["http_headers"] = {
                    **(fallback_opts.get("http_headers") or {}),
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15",
                }
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = self._download_tiktok_safari_fallback(ydl, entry, tmp_outtmpl, quality_limit, opts)
            else:
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
                return DownloadResult("failed")

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
                return DownloadResult("failed")

            media = self._probe_media(tmp_path, ffprobe)
            media_width, media_height = media["width"], media["height"]
            if not self._is_facebook_compatible(media):
                self._log(
                    f"Source format {media_width or '?'}x{media_height or '?'} "
                    f"{media['video_codec'] or 'unknown'}/{media['audio_codec'] or 'silent'}; "
                    "normalizing to MP4 H.264/AAC for Facebook compatibility"
                )
                tmp_path = self._normalize_for_facebook(
                    tmp_path, ffmpeg, entry.title, media, source_id, entry.video_id
                )
                media = self._probe_media(tmp_path, ffprobe)
                media_width, media_height = media["width"], media["height"]
                if not self._is_facebook_compatible(media):
                    raise RuntimeError("Could not produce an MP4 H.264/AAC file for Facebook upload")
            media_resolution = min(media_width, media_height) if media_width and media_height else None
            if media_resolution is None:
                return self._fail_before_download(
                    source_id,
                    entry,
                    page_dir,
                    "Downloaded file could not be verified, so it was removed instead of bypassing the media compatibility checks.",
                )
            if quality_limit and media_resolution > quality_limit:
                return self._fail_before_download(
                    source_id,
                    entry,
                    page_dir,
                    f"Downloaded resolution {media_width}x{media_height} exceeds the {quality_limit}p limit; the file was removed.",
                )

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
                return DownloadResult("skipped")

            resolution_detail = f" ({media_width}x{media_height})" if media_width and media_height else ""
            codec_detail = f" · {media['video_codec'] or 'unknown'}/{media['audio_codec'] or 'silent'}"
            self._log(f"Downloaded: {downloaded_path.name}{resolution_detail}{codec_detail}")
            self._progress_event({
                "stage": "completed", "source_id": source_id, "video_id": entry.video_id,
                "title": entry.title, "width": media_width, "height": media_height,
                "resolution": media_resolution, "quality_limit": quality_limit,
                "video_codec": media["video_codec"], "audio_codec": media["audio_codec"],
            })
            return DownloadResult("downloaded")

        except Exception as e:
            clean_error = self._clean_error_text(str(e))
            if entry.platform == "youtube" and self._is_youtube_bot_check(clean_error):
                auth_hint = "Open Settings → YouTube access, then select your signed-in browser or a cookies.txt file."
                clean_error = f"YouTube needs account verification. {auth_hint}"
            elif entry.platform == "tiktok":
                cookie_issue = cookie_file_issue(self.global_cfg.tiktok_cookies_file, domain=".tiktok.com")
                if cookie_issue:
                    clean_error = f"TikTok fallback could not use cookies ({cookie_issue}). Export a Netscape cookies.txt containing TikTok cookies, then save it in Settings."
                else:
                    clean_error = f"TikTok fallback download failed: {clean_error}"
            if progress_state["last_download_line"]:
                self._progress_log(f"{progress_state['last_download_line']}ERROR: {clean_error}")
            else:
                self._progress_log(f"ERROR: {clean_error}")
            self._cleanup_entry_files(page_dir, entry.video_id)
            self._progress_event({"stage": "failed", "source_id": source_id, "video_id": entry.video_id, "title": entry.title, "error": clean_error})
            if self._is_gone_video_error(clean_error):
                self.db.delete_crawled(source_id, entry.video_id)
                self._log(f"Dropped from queue (gone): {entry.title}")
            self._log(f"Download error {entry.title}: {clean_error}")
            return DownloadResult("failed")

    def _fail_before_download(
        self, source_id: str, entry: VideoEntry, page_dir: Path, message: str
    ) -> DownloadResult:
        self._cleanup_entry_files(page_dir, entry.video_id)
        self._progress_log(f"ERROR: {message}")
        self._progress_event({
            "stage": "failed", "source_id": source_id, "video_id": entry.video_id,
            "title": entry.title, "error": message,
        })
        self._log(f"Download error {entry.title}: {message}")
        return DownloadResult("failed")

    def _probe_media(self, path: Path, ffprobe: str) -> dict[str, int | str | None]:
        try:
            result = subprocess.run(
                [
                    ffprobe, "-v", "error", "-show_entries",
                    "stream=codec_type,codec_name,pix_fmt,width,height:format=duration", "-of", "json", str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            probe_data = json.loads(result.stdout)
            streams = probe_data.get("streams") or []
            video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
            audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
            if not video:
                return {"width": None, "height": None, "video_codec": None, "pixel_format": None, "audio_codec": None, "duration": None}
            return {
                "width": int(video.get("width") or 0) or None,
                "height": int(video.get("height") or 0) or None,
                "video_codec": str(video.get("codec_name") or "").lower() or None,
                "pixel_format": str(video.get("pix_fmt") or "").lower() or None,
                "audio_codec": str(audio.get("codec_name") or "").lower() or None,
                "duration": float((probe_data.get("format") or {}).get("duration") or 0) or None,
            }
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            self._log(f"Could not inspect downloaded media: {error}")
            return {"width": None, "height": None, "video_codec": None, "pixel_format": None, "audio_codec": None, "duration": None}

    @staticmethod
    def _is_facebook_compatible(media: dict[str, int | str | None]) -> bool:
        return (
            media.get("video_codec") == "h264"
            and media.get("pixel_format") == "yuv420p"
            and media.get("audio_codec") in {None, "aac"}
        )

    def _normalize_for_facebook(
        self,
        path: Path,
        ffmpeg: str,
        title: str,
        media: dict[str, int | str | float | None],
        source_id: str,
        video_id: str,
    ) -> Path:
        normalized = path.with_name(f"{path.stem}.facebook.mp4")
        normalized.unlink(missing_ok=True)
        copy_video = media.get("video_codec") == "h264" and media.get("pixel_format") == "yuv420p"
        copy_audio = media.get("audio_codec") in {None, "aac"}
        video_args = ["-c:v", "copy"] if copy_video else ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
        audio_args = ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "192k"]
        conversion = "remuxing compatible video" if copy_video else "encoding video to H.264"
        self._log(f"Facebook compatibility: {conversion} for {title}")
        self._progress_event({"stage": "converting", "source_id": source_id, "video_id": video_id, "title": title, "percent": 0})
        try:
            self._run_ffmpeg_with_progress(
                [
                    ffmpeg, "-y", "-i", str(path), "-map", "0:v:0", "-map", "0:a?",
                    *video_args, *audio_args, "-movflags", "+faststart",
                    "-progress", "pipe:1", "-nostats", str(normalized),
                ],
                title=title,
                duration=media.get("duration"),
                source_id=source_id,
                video_id=video_id,
            )
        except (OSError, subprocess.SubprocessError) as error:
            normalized.unlink(missing_ok=True)
            raise RuntimeError(f"Could not convert {title} to H.264/AAC: {error}") from error
        if not normalized.exists() or normalized.stat().st_size == 0:
            normalized.unlink(missing_ok=True)
            raise RuntimeError(f"Could not convert {title} to H.264/AAC")
        path.unlink(missing_ok=True)
        return normalized

    def _run_ffmpeg_with_progress(
        self, command: list[str], *, title: str, duration: object, source_id: str, video_id: str
    ) -> None:
        """Run ffmpeg without leaving the UI silent during long 4K conversions."""
        total_seconds = float(duration) if isinstance(duration, (float, int)) and duration else None
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        last_report = 0.0
        output: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            output.append(line)
            if not line.startswith(("out_time_us=", "out_time_ms=")):
                continue
            try:
                elapsed = int(line.partition("=")[2]) / 1_000_000
            except ValueError:
                continue
            now = time.monotonic()
            if now - last_report < 5:
                continue
            last_report = now
            percent = min(99.9, round(elapsed * 100 / total_seconds, 1)) if total_seconds else None
            label = f"{percent}%" if percent is not None else f"{int(elapsed)}s processed"
            self._progress_log(f"Converting | {title} | {label}")
            self._progress_event({"stage": "converting", "source_id": source_id, "video_id": video_id, "title": title, "percent": percent})
        if process.wait() != 0:
            detail = self._clean_error_text("".join(output)[-4000:])
            raise subprocess.CalledProcessError(process.returncode, command, output=detail)
        self._progress_event({"stage": "converting", "source_id": source_id, "video_id": video_id, "title": title, "percent": 100})

    @staticmethod
    def _download_tiktok_safari_fallback(
        ydl: yt_dlp.YoutubeDL,
        entry: VideoEntry,
        tmp_outtmpl: str,
        quality_limit: int,
        opts: dict,
    ) -> dict:
        info = VideoDownloader._extract_tiktok_safari_info(ydl, entry)
        formats = [f for f in info.get("formats") or [] if f.get("vcodec") not in (None, "none") and f.get("url")]
        if quality_limit:
            capped = [f for f in formats if min(f.get("width") or 0, f.get("height") or 0) <= quality_limit]
            formats = capped or formats
        if not formats:
            raise RuntimeError("TikTok fallback response contained no downloadable video format")
        selected = max(
            formats,
            key=lambda f: (
                1 if str(f.get("vcodec") or "").lower().startswith(("avc", "h264")) else 0,
                min(f.get("width") or 0, f.get("height") or 0),
                f.get("tbr") or 0,
            ),
        )
        cookie_header = (info.get("http_headers") or {}).get("Cookie", "")
        headers = {"Referer": entry.url}
        if cookie_header:
            headers["Cookie"] = cookie_header
        target = Path(tmp_outtmpl.replace("%(ext)s", "mp4"))
        last_error = "TikTok CDN rejected the fallback request"
        for client in ("safari", "edge"):
            try:
                response = curl_requests.get(selected["url"], impersonate=client, headers=headers, timeout=30, stream=True)
                if response.status_code != 200:
                    last_error = f"TikTok CDN returned HTTP {response.status_code}"
                    continue
                total = int(response.headers.get("content-length") or 0) or None
                downloaded = 0
                with target.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        output.write(chunk)
                        downloaded += len(chunk)
                        for hook in opts.get("progress_hooks") or []:
                            hook({"status": "downloading", "downloaded_bytes": downloaded, "total_bytes": total, "filename": str(target), "info_dict": info})
                for hook in opts.get("progress_hooks") or []:
                    hook({"status": "finished", "downloaded_bytes": downloaded, "total_bytes": total, "filename": str(target), "info_dict": info})
                return {**info, "ext": "mp4", "format_id": selected.get("format_id")}
            except Exception as error:
                last_error = str(error)
                target.unlink(missing_ok=True)
        raise RuntimeError(last_error)

    @staticmethod
    def _extract_tiktok_safari_info(ydl: yt_dlp.YoutubeDL, entry: VideoEntry) -> dict:
        """Extract TikTok universal data through a less-blocked browser fingerprint.

        TikTok currently serves a tiny challenge page to yt-dlp's default
        impersonation target, while Safari/Edge-compatible clients still
        receive the normal universal-data document. The parsed result is fed
        back into yt-dlp, so format selection, progress hooks and ffmpeg remain
        unchanged.
        """
        marker = "__UNIVERSAL_DATA_FOR_REHYDRATION__"
        last_error = "Safari fallback response did not contain TikTok universal data"
        cookies = {}
        cookie_path = ydl.params.get("cookiefile")
        if cookie_path:
            try:
                jar = MozillaCookieJar(str(cookie_path))
                jar.load(ignore_discard=True, ignore_expires=False)
                cookies = {cookie.name: cookie.value for cookie in jar if "tiktok" in cookie.domain.lower()}
            except Exception:
                # The normal yt-dlp path already validates the cookie jar; the
                # fallback can still work for public posts if parsing fails.
                cookies = {}
        # TikTok varies its response by TLS/browser fingerprint. Try the two
        # fingerprints that currently return the full HTML document on Windows.
        for _attempt in range(3):
            for client in ("safari", "edge"):
                try:
                    response = curl_requests.get(entry.url, impersonate=client, cookies=cookies or None, timeout=20)
                    response.raise_for_status()
                    webpage = response.text
                    marker_pos = webpage.find(marker)
                    if marker_pos < 0:
                        continue
                    start = webpage.find(">", marker_pos)
                    end = webpage.find("</script>", start)
                    if start < 0 or end < 0:
                        last_error = "Safari fallback response contained malformed TikTok data"
                        continue
                    scope = json.loads(webpage[start + 1:end]).get("__DEFAULT_SCOPE__", {})
                    detail = ((scope.get("webapp.video-detail") or {}).get("itemInfo") or {}).get("itemStruct")
                    if isinstance(detail, dict):
                        info = TikTokIE(ydl)._parse_aweme_video_web(detail, entry.url, entry.video_id)
                        # The page response sets short-lived tt_chain_token/msToken
                        # cookies that the signed CDN URL checks. Preserve only the
                        # cookie header for this one download; never persist values.
                        page_cookies = {key: value for key, value in response.cookies.items()}
                        if page_cookies:
                            cookie_header = "; ".join(f"{key}={value}" for key, value in {**cookies, **page_cookies}.items())
                            info["http_headers"] = {**(info.get("http_headers") or {}), "Cookie": cookie_header}
                            for fmt in info.get("formats") or []:
                                fmt["http_headers"] = {**(fmt.get("http_headers") or {}), "Cookie": cookie_header}
                        return info
                    last_error = "Safari fallback response did not contain TikTok video data"
                except Exception as error:
                    last_error = str(error)
        raise RuntimeError(last_error)

    @staticmethod
    def _is_youtube_bot_check(message: str) -> bool:
        lower = message.lower()
        return "sign in to confirm you\u2019re not a bot" in lower or "sign in to confirm you're not a bot" in lower or "cookies-from-browser" in lower

    @staticmethod
    def _is_tiktok_cookie_error(message: str) -> bool:
        lower = message.lower()
        markers = (
            "fresh cookies", "login required", "status code 403", "http error 403", "forbidden",
            "unexpected response from webpage request", "challenge",
        )
        return any(marker in lower for marker in markers)

    @staticmethod
    def _is_gone_video_error(message: str) -> bool:
        lower = message.lower()
        markers = (
            "video unavailable",
            "has been removed",
            "private video",
            "this video is unavailable",
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
