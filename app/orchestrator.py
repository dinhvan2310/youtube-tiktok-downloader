from __future__ import annotations

import threading
from math import ceil
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.config_models import AppConfig, SourceConfig, source_label
from app.db import DownloadDB
from app.downloader import VideoDownloader
from app.filename import count_videos_in_folder
from app.playlist import VideoEntry, fetch_all_entries, fetch_entries_batched


@dataclass
class RunStats:
    crawled: int = 0
    downloaded: int = 0
    skipped: int = 0
    errors: int = 0
    sources_done: int = 0
    messages: list[str] = field(default_factory=list)


class DownloadOrchestrator:
    def __init__(
        self,
        config: AppConfig,
        log: Callable[[str], None] | None = None,
        progress: Callable[[str], None] | None = None,
        progress_log: Callable[[str], None] | None = None,
        progress_event: Callable[[str, dict], None] | None = None,
        db_path: Path | str | None = None,
    ) -> None:
        self.config = config
        self._log = log or (lambda _m: None)
        self._progress = progress or (lambda _m: None)
        self._progress_log = progress_log or (lambda _m: None)
        self._progress_event = progress_event or (lambda _event, _data: None)
        self.db = DownloadDB(db_path)
        self._stop_event = threading.Event()
        self._stats_lock = threading.Lock()

    def stop(self) -> None:
        self._stop_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def _yt_sources(self) -> list[SourceConfig]:
        return list(self.config.sources)

    def _run_source_pool(
        self,
        sources: list[SourceConfig],
        submit_fn: Callable[[ThreadPoolExecutor, SourceConfig, RunStats], Future],
        *,
        worker_label: str,
        done_msg: str,
        workers: int | None = None,
    ) -> RunStats:
        stats = RunStats()
        worker_count = max(1, workers or self.config.global_config.source_thread_count)
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {submit_fn(pool, source, stats): source for source in sources}
            for fut in as_completed(futures):
                if self.is_stopped():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                source = futures[fut]
                try:
                    fut.result()
                    with self._stats_lock:
                        stats.sources_done += 1
                except Exception as e:  # noqa: BLE001
                    with self._stats_lock:
                        stats.errors += 1
                    self._log(f"[{source_label(source)}] {worker_label} error: {e}")
        self._log(done_msg.format(stats=stats))
        return stats

    def crawl_sources(self, sources: list[SourceConfig] | None = None) -> RunStats:
        source_list = sources if sources is not None else self._yt_sources()

        def submit(pool: ThreadPoolExecutor, source: SourceConfig, stats: RunStats) -> Future:
            return pool.submit(self._crawl_one, source, stats)

        return self._run_source_pool(
            source_list,
            submit,
            worker_label="Crawl worker",
            done_msg=(
                "Crawl done — new: {stats.crawled}, skipped: {stats.skipped}, "
                "errors: {stats.errors}"
            ),
        )

    def crawl_sources_batched(
        self,
        sources: list[SourceConfig] | None = None,
        *,
        batch_size: int = 50,
        on_batch: Callable[[SourceConfig, int, list[dict]], None] | None = None,
        start_batch: int = 0,
    ) -> RunStats:
        """Full crawl with incremental commits/callbacks for UI delivery."""
        stats = RunStats()
        source_list = sources if sources is not None else self._yt_sources()
        for source in source_list:
            if self.is_stopped():
                break
            label = source_label(source)
            self._log(f"[{label}] Starting full crawl (batched)")
            # Auto-crawl is incremental: retain review state and skip IDs already in SQLite.
            # This avoids clearing approved items and allows yt-dlp to stop after a known streak.
            source_start = 0
            known_ids = self.db.get_crawled_ids(source.id)
            for batch_no, entries in enumerate(fetch_entries_batched(
                source, self.config.global_config, batch_size=batch_size, skip_ids=known_ids, log=self._log
            ), 1):
                if batch_no <= source_start:
                    continue
                if self.is_stopped():
                    break
                inserted = 0
                payload: list[dict] = []
                for entry in entries:
                    if self.db.upsert_crawled(
                        source_id=source.id, video_id=entry.video_id, platform=entry.platform,
                        title=entry.title, source_url=entry.url, upload_date=entry.upload_date,
                        duration=entry.duration, thumbnail=entry.thumbnail, view_count=entry.view_count,
                        like_count=entry.like_count,
                        metadata_status="ready" if entry.upload_date and entry.view_count is not None else "pending",
                        status="discovered",
                    ):
                        inserted += 1
                    payload.append({
                        "video_id": entry.video_id, "platform": entry.platform, "title": entry.title,
                        "source_url": entry.url, "upload_date": entry.upload_date,
                        "duration": entry.duration, "thumbnail": entry.thumbnail,
                        "view_count": entry.view_count, "like_count": entry.like_count,
                    })
                with self._stats_lock:
                    stats.crawled += inserted
                    stats.skipped += max(0, len(entries) - inserted)
                if on_batch:
                    on_batch(source, batch_no, payload)
                self.db.set_crawl_checkpoint(source.id, batch_no)
            self.db.mark_crawl_done(source.id)
            with self._stats_lock:
                stats.sources_done += 1
        self._log(f"Batched crawl done — new: {stats.crawled}, skipped: {stats.skipped}, errors: {stats.errors}")
        return stats

    def download_sources(self, sources: list[SourceConfig] | None = None) -> RunStats:
        g = self.config.global_config
        source_list = sources if sources is not None else self._yt_sources()
        active_source_limit = max(1, min(len(source_list), g.download_source_concurrency))
        per_source_parallelism = max(1, ceil(g.global_download_concurrency / active_source_limit))
        self._log(
            "Download concurrency — "
            f"up to {active_source_limit} sources, {per_source_parallelism} per source, "
            f"{g.global_download_concurrency} total"
        )
        downloader = VideoDownloader(
            global_cfg=g,
            db=self.db,
            log=self._log,
            progress_log=self._progress_log,
            progress_event=lambda data: self._progress_event(f"download_{data.get('stage', 'progress')}", data),
            stop_check=self.is_stopped,
        )

        def submit(pool: ThreadPoolExecutor, source: SourceConfig, stats: RunStats) -> Future:
            return pool.submit(
                self._download_one_source,
                source,
                g.target_videos_per_page,
                per_source_parallelism,
                downloader,
                stats,
            )

        return self._run_source_pool(
            source_list,
            submit,
            worker_label="Download worker",
            done_msg=(
                "Download done — new: {stats.downloaded}, skipped: {stats.skipped}, "
                "errors: {stats.errors}"
            ),
            workers=active_source_limit,
        )

    def _crawl_one(self, source: SourceConfig, stats: RunStats) -> None:
        if self.is_stopped():
            return
        label = source_label(source)
        self._log(f"[{label}] Starting crawl ({len(source.links)} link(s))")
        self._progress(f"Crawling {label}...")

        # Replace queue: drop stale crawled IDs (removed/private) so download won't retry them.
        removed = self.db.clear_crawled(source.id)
        if removed:
            self._log(f"[{label}] Cleared {removed} old crawled item(s)")

        # Crawl all videos; do not skip by downloaded so queue stays complete for later download.
        entries = fetch_all_entries(
            source,
            self.config.global_config,
            skip_ids=set(),
            log=self._log,
        )
        inserted = 0
        for entry in entries:
            if self.is_stopped():
                break
            if self.db.upsert_crawled(
                source_id=source.id,
                video_id=entry.video_id,
                platform=entry.platform,
                title=entry.title,
                source_url=entry.url,
                upload_date=entry.upload_date,
            ):
                inserted += 1

        self.db.mark_crawl_done(source.id)
        with self._stats_lock:
            stats.crawled += inserted
            stats.skipped += max(0, len(entries) - inserted)

        total = self.db.get_crawled_count(source.id)
        self._log(f"[{label}] Crawl finished — queued: {inserted}, total in queue: {total}")
        self._progress_log(f"[{label}] Crawled {total} video(s)")

    def _download_one_source(
        self,
        source: SourceConfig,
        target: int,
        per_source_parallelism: int,
        downloader: VideoDownloader,
        stats: RunStats,
    ) -> None:
        label = source_label(source)
        out_dir = Path(source.path_download)
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            existing = count_videos_in_folder(out_dir)
            self._progress(f"{label}: {existing}/{target} videos")
            if existing >= target:
                self._log(f"[{label}] Folder already has {existing}/{target} videos, skip")
                with self._stats_lock:
                    stats.skipped += 1
                return

            pending = self.db.get_pending_downloads(source.id)
            if not pending:
                self._log(f"[{label}] No pending crawled videos. Run Crawl first.")
                self._progress_log(f"[{label}] Nothing to download")
                return

            need = target - existing
            planned = min(need, len(pending))
            source_progress = {"total": planned, "completed": 0, "failed": 0}
            self._progress_event("download_source_plan", {
                "source_id": str(source.id), "source_label": label, "planned": planned, "target": target, "existing": existing,
                "completed": 0, "total": planned,
            })
            self._log(f"[{label}] Pending: {len(pending)}, need {need} more (max {target})")
            sid = str(source.id)

            with ThreadPoolExecutor(max_workers=max(1, per_source_parallelism)) as pool:
                futures: dict[Future[bool], VideoEntry] = {}
                for row in pending:
                    if self.is_stopped():
                        break

                    while (
                        not self.is_stopped()
                        and len(futures) >= max(1, per_source_parallelism)
                    ):
                        should_stop = self._drain_futures(
                            futures, label, out_dir, target, stats, source_progress
                        )
                        if should_stop:
                            break

                    if self.is_stopped():
                        break

                    # Cap in-flight so parallel downloads cannot overshoot folder max.
                    in_folder = count_videos_in_folder(out_dir)
                    slots = target - in_folder - len(futures)
                    if slots <= 0:
                        if not futures:
                            break
                        should_stop = self._drain_futures(
                            futures, label, out_dir, target, stats, source_progress
                        )
                        if should_stop or count_videos_in_folder(out_dir) >= target:
                            break
                        continue

                    entry = VideoEntry(
                        video_id=row["video_id"],
                        title=row["title"] or row["video_id"],
                        url=row["source_url"],
                        platform=row["platform"],
                        duration=None,
                        upload_date=row["upload_date"] or None,
                    )
                    futures[pool.submit(downloader.download_one, sid, entry, out_dir)] = entry
                    self._log(f"[{label}] Queued: {entry.title}")

                while futures and not self.is_stopped():
                    should_stop = self._drain_futures(futures, label, out_dir, target, stats, source_progress)
                    if should_stop:
                        for fut in list(futures):
                            fut.cancel()
                        futures.clear()
                        break

            self.db.mark_download_done(source.id)
            final = count_videos_in_folder(out_dir)
            self._log(f"[{label}] Download finished — folder {final}/{target}")
            self._progress_log(f"[{label}] Folder {final}/{target}")
        finally:
            removed = self._cleanup_incomplete_files(out_dir)
            if removed:
                self._log(f"[{label}] Cleanup removed {removed} temp file(s)")

    def _drain_futures(
        self,
        futures: dict[Future[bool], VideoEntry],
        label: str,
        out_dir: Path,
        target: int,
        stats: RunStats,
        source_progress: dict[str, int],
    ) -> bool:
        if not futures:
            return False
        done, _ = wait(futures.keys(), return_when=FIRST_COMPLETED)
        for fut in done:
            entry = futures.pop(fut)
            try:
                ok = fut.result()
                if ok:
                    with self._stats_lock:
                        stats.downloaded += 1
                    source_progress["completed"] += 1
                    self._progress_event("download_summary", {**source_progress, "source_label": label})
                    existing = count_videos_in_folder(out_dir)
                    self._progress(f"{label}: {existing}/{target} videos")
                    self._progress_log(f"Done {existing}/{target} | {entry.title}")
                    if existing >= target:
                        self._log(f"[{label}] Reached target {target}")
                        return True
                else:
                    with self._stats_lock:
                        stats.skipped += 1
                    source_progress["failed"] += 1
                    self._progress_event("download_summary", {**source_progress, "source_label": label})
            except Exception as e:
                with self._stats_lock:
                    stats.errors += 1
                source_progress["failed"] += 1
                self._progress_event("download_summary", {**source_progress, "source_label": label})
                self._log(f"Worker error {entry.title}: {e}")
        return False

    def _cleanup_incomplete_files(self, directory: Path) -> int:
        if not directory.exists():
            return 0
        removed = 0
        for pattern in ("__tmp_*", "*.part", "*.ytdl"):
            for path in directory.rglob(pattern):
                if path.is_file():
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed
