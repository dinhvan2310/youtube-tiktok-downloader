from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.paths import project_root


def default_db_path() -> Path:
    return project_root() / "data" / "downloads.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloaded_videos (
    id INTEGER PRIMARY KEY,
    page_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    title TEXT,
    source_url TEXT,
    file_path TEXT,
    downloaded_at TEXT NOT NULL,
    UNIQUE(page_id, video_id)
);
CREATE INDEX IF NOT EXISTS idx_page ON downloaded_videos(page_id);

CREATE TABLE IF NOT EXISTS crawled_videos (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    title TEXT,
    source_url TEXT,
    upload_date TEXT,
    duration REAL,
    thumbnail TEXT,
    view_count INTEGER,
    like_count INTEGER,
    metadata_status TEXT NOT NULL DEFAULT 'pending',
    metadata_updated_at TEXT,
    hold_reason TEXT,
    hold_note TEXT,
    review_at TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    crawled_at TEXT NOT NULL,
    UNIQUE(source_id, video_id)
);
CREATE INDEX IF NOT EXISTS idx_crawled_source ON crawled_videos(source_id);

CREATE TABLE IF NOT EXISTS crawl_checkpoints (
    source_id TEXT PRIMARY KEY,
    batch_number INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_stats (
    source_id TEXT PRIMARY KEY,
    last_crawl_at TEXT,
    last_download_at TEXT,
    crawled_count INTEGER NOT NULL DEFAULT 0,
    downloaded_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS source_lifecycle (
    source_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active',
    archived_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_events (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'local-user',
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approval_video ON approval_events(source_id, video_id, created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    source_ids TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    event TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DownloadDB:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            # Non-destructive migrations for databases created by older builds.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(crawled_videos)")}
            for name, definition in (
                ("duration", "REAL"), ("thumbnail", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'discovered'"),
                ("view_count", "INTEGER"), ("like_count", "INTEGER"),
                ("metadata_status", "TEXT NOT NULL DEFAULT 'pending'"), ("metadata_updated_at", "TEXT"),
                ("hold_reason", "TEXT"), ("hold_note", "TEXT"), ("review_at", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE crawled_videos ADD COLUMN {name} {definition}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_crawled_status ON crawled_videos(source_id, status)")

    def _sid(self, source_id: int | str) -> str:
        return str(source_id)

    def is_downloaded(self, source_id: int | str, video_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM downloaded_videos WHERE page_id = ? AND video_id = ?",
                (self._sid(source_id), video_id),
            ).fetchone()
        return row is not None

    def get_downloaded_ids(self, source_id: int | str) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT video_id FROM downloaded_videos WHERE page_id = ?",
                (self._sid(source_id),),
            ).fetchall()
        return {row["video_id"] for row in rows}

    def insert(
        self,
        page_id: str,
        video_id: str,
        platform: str,
        title: str,
        source_url: str,
        file_path: str,
    ) -> bool:
        """page_id stores source_id as string for backward-compatible column name."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO downloaded_videos
                    (page_id, video_id, platform, title, source_url, file_path, downloaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (self._sid(page_id), video_id, platform, title, source_url, file_path, _now()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def clear_crawled(self, source_id: int | str) -> int:
        """Delete crawl queue for a source (downloaded history kept). Returns rows deleted."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM crawled_videos WHERE source_id = ?",
                (self._sid(source_id),),
            )
            return int(cur.rowcount or 0)

    def delete_crawled(self, source_id: int | str, video_id: str) -> None:
        """Drop one crawled item (e.g. removed/private on YouTube)."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM crawled_videos WHERE source_id = ? AND video_id = ?",
                (self._sid(source_id), video_id),
            )

    def upsert_crawled(
        self,
        source_id: int | str,
        video_id: str,
        platform: str,
        title: str,
        source_url: str,
        upload_date: str | None,
        duration: float | None = None,
        thumbnail: str | None = None,
        view_count: int | None = None,
        like_count: int | None = None,
        metadata_status: str = "pending",
        status: str = "discovered",
    ) -> bool:
        """Insert crawled video if new. Returns True if inserted."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO crawled_videos
                    (source_id, video_id, platform, title, source_url, upload_date, duration, thumbnail,
                     view_count, like_count, metadata_status, status, crawled_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._sid(source_id),
                        video_id,
                        platform,
                        title,
                        source_url,
                        upload_date or "",
                        duration,
                        thumbnail or "",
                        view_count,
                        like_count,
                        metadata_status,
                        status,
                        _now(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_crawled_count(self, source_id: int | str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM crawled_videos WHERE source_id = ?",
                (self._sid(source_id),),
            ).fetchone()
        return int(row["n"]) if row else 0

    def get_overview_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS discovered,
                    SUM(CASE WHEN status = 'discovered' THEN 1 ELSE 0 END) AS review,
                    SUM(CASE WHEN c.status IN ('queued', 'approved') AND d.video_id IS NULL THEN 1 ELSE 0 END) AS queued,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
                FROM crawled_videos c
                LEFT JOIN downloaded_videos d
                  ON d.page_id = c.source_id AND d.video_id = c.video_id
                """
            ).fetchone()
            downloaded = conn.execute("SELECT COUNT(*) AS n FROM downloaded_videos").fetchone()
        return {
            "discovered": int(row["discovered"] or 0) if row else 0,
            "review": int(row["review"] or 0) if row else 0,
            "queued": int(row["queued"] or 0) if row else 0,
            "failed": int(row["failed"] or 0) if row else 0,
            "downloaded": int(downloaded["n"] or 0) if downloaded else 0,
        }

    def get_source_overview_counts(self) -> dict[str, dict[str, int]]:
        """Return review/queue/download counts for every source in one query."""
        with self._connect() as conn:
            crawled = conn.execute(
                """
                SELECT c.source_id,
                       COUNT(*) AS discovered_count,
                       SUM(CASE WHEN c.status = 'discovered' THEN 1 ELSE 0 END) AS review_count,
                       SUM(CASE WHEN c.status IN ('queued', 'approved') AND d.video_id IS NULL THEN 1 ELSE 0 END) AS queued_count
                FROM crawled_videos c
                LEFT JOIN downloaded_videos d
                  ON d.page_id = c.source_id AND d.video_id = c.video_id
                GROUP BY c.source_id
                """
            ).fetchall()
            downloaded = conn.execute(
                "SELECT page_id AS source_id, COUNT(*) AS downloaded_count FROM downloaded_videos GROUP BY page_id"
            ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in crawled:
            result[str(row["source_id"])] = {
                "discovered_count": int(row["discovered_count"] or 0),
                "review_count": int(row["review_count"] or 0),
                "queued_count": int(row["queued_count"] or 0),
                "downloaded_count": 0,
            }
        for row in downloaded:
            result.setdefault(str(row["source_id"]), {"discovered_count": 0, "review_count": 0, "queued_count": 0, "downloaded_count": 0})
            result[str(row["source_id"])] ["downloaded_count"] = int(row["downloaded_count"] or 0)
        return result

    def get_crawled_ids(self, source_id: int | str) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT video_id FROM crawled_videos WHERE source_id = ?",
                (self._sid(source_id),),
            ).fetchall()
        return {str(row["video_id"]) for row in rows}

    def get_downloaded_count(self, source_id: int | str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM downloaded_videos WHERE page_id = ?",
                (self._sid(source_id),),
            ).fetchone()
        return int(row["n"]) if row else 0

    def get_pending_downloads(self, source_id: int | str) -> list[sqlite3.Row]:
        """Crawled videos not yet downloaded, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.video_id, c.platform, c.title, c.source_url, c.upload_date, c.duration, c.thumbnail
                FROM crawled_videos c
                LEFT JOIN downloaded_videos d
                  ON d.page_id = c.source_id AND d.video_id = c.video_id
                WHERE c.source_id = ? AND d.video_id IS NULL AND c.status IN ('queued', 'approved')
                ORDER BY c.upload_date DESC, c.id ASC
                """,
                (self._sid(source_id),),
            ).fetchall()
        return list(rows)

    def list_crawled(self, source_id: int | str, *, keyword: str = "", status: str | None = None) -> list[sqlite3.Row]:
        clauses = ["source_id = ?"]
        params: list[object] = [self._sid(source_id)]
        if keyword.strip():
            clauses.append("LOWER(title) LIKE ?")
            params.append(f"%{keyword.strip().lower()}%")
        if status:
            clauses.append("status = ?")
            params.append(status)
        with self._connect() as conn:
            return list(conn.execute(
                f"SELECT * FROM crawled_videos WHERE {' AND '.join(clauses)} ORDER BY upload_date DESC, id ASC", params
            ).fetchall())

    def set_crawled_status(self, source_id: int | str, video_ids: list[str], status: str) -> int:
        if not video_ids:
            return 0
        with self._connect() as conn:
            cur = conn.executemany(
                "UPDATE crawled_videos SET status = ? WHERE source_id = ? AND video_id = ?",
                [(status, self._sid(source_id), vid) for vid in video_ids],
            )
            return int(cur.rowcount or 0)

    def transition_videos(
        self,
        items: list[tuple[str, str]],
        *,
        status: str,
        expected_status: str | None = None,
        actor: str = "local-user",
        reason: str = "",
        note: str = "",
        review_at: str = "",
    ) -> int:
        """Atomically transition video state and append an audit event."""
        if not items:
            return 0
        changed = 0
        with self._connect() as conn:
            for source_id, video_id in items:
                params: list[object] = [status, self._sid(source_id), str(video_id)]
                sql = "UPDATE crawled_videos SET status = ? WHERE source_id = ? AND video_id = ?"
                if expected_status:
                    sql += " AND status = ?"
                    params.append(expected_status)
                cur = conn.execute(sql, params)
                if not cur.rowcount:
                    continue
                changed += 1
                conn.execute(
                    "INSERT INTO approval_events(source_id,video_id,action,actor,reason,created_at) VALUES(?,?,?,?,?,?)",
                    (self._sid(source_id), str(video_id), status, actor, reason, _now()),
                )
                if status == "hold":
                    conn.execute(
                        "UPDATE crawled_videos SET hold_reason=?,hold_note=?,review_at=? WHERE source_id=? AND video_id=?",
                        (reason, note, review_at or None, self._sid(source_id), str(video_id)),
                    )
                else:
                    conn.execute(
                        "UPDATE crawled_videos SET hold_reason=NULL,hold_note=NULL,review_at=NULL WHERE source_id=? AND video_id=?",
                        (self._sid(source_id), str(video_id)),
                    )
        return changed

    def update_video_metadata(self, source_id: str, video_id: str, data: dict, *, status: str = "ready") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE crawled_videos SET title=COALESCE(?,title), source_url=COALESCE(?,source_url),
                  upload_date=COALESCE(NULLIF(?,''),upload_date), duration=COALESCE(?,duration),
                  thumbnail=COALESCE(NULLIF(?,''),thumbnail), view_count=COALESCE(?,view_count),
                  like_count=COALESCE(?,like_count), metadata_status=?, metadata_updated_at=?
                WHERE source_id=? AND video_id=?
                """,
                (data.get("title"), data.get("webpage_url"), data.get("upload_date") or "", data.get("duration"),
                 data.get("thumbnail") or "", data.get("view_count"), data.get("like_count"), status, _now(),
                 self._sid(source_id), str(video_id)),
            )

    def metadata_candidates(self, source_ids: list[str], limit: int = 300) -> list[sqlite3.Row]:
        if not source_ids:
            return []
        marks = ",".join("?" for _ in source_ids)
        with self._connect() as conn:
            return list(conn.execute(
                f"""SELECT * FROM crawled_videos WHERE source_id IN ({marks}) AND metadata_status != 'ready'
                    ORDER BY CASE status WHEN 'discovered' THEN 0 WHEN 'hold' THEN 1 ELSE 2 END, id DESC LIMIT ?""",
                [*map(self._sid, source_ids), max(1, min(int(limit), 1000))],
            ).fetchall())

    def list_videos_global(
        self,
        *,
        status: str = "",
        source_id: str = "",
        keyword: str = "",
        limit: int = 200,
        offset: int = 0,
        exclude_downloaded: bool = False,
        min_duration: float | None = None,
        max_duration: float | None = None,
        min_views: int | None = None,
        published_after: str = "",
        published_before: str = "",
        metadata_status: str = "",
        content_type: str = "",
        exclude_keyword: str = "",
        sort: str = "newest",
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            statuses = [item.strip() for item in status.split(",") if item.strip()]
            clauses.append(f"c.status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if source_id:
            clauses.append("c.source_id = ?")
            params.append(self._sid(source_id))
        if keyword.strip():
            clauses.append("LOWER(COALESCE(c.title, '')) LIKE ?")
            params.append(f"%{keyword.strip().lower()}%")
        if exclude_keyword.strip():
            clauses.append("LOWER(COALESCE(c.title, '')) NOT LIKE ?")
            params.append(f"%{exclude_keyword.strip().lower()}%")
        if min_duration is not None:
            clauses.append("c.duration >= ?"); params.append(float(min_duration))
        if max_duration is not None:
            clauses.append("c.duration <= ?"); params.append(float(max_duration))
        if min_views is not None:
            clauses.append("c.view_count >= ?"); params.append(int(min_views))
        if published_after:
            clauses.append("c.upload_date >= ?"); params.append(published_after.replace("-", ""))
        if published_before:
            clauses.append("c.upload_date <= ?"); params.append(published_before.replace("-", ""))
        if metadata_status:
            clauses.append("c.metadata_status = ?"); params.append(metadata_status)
        if content_type == "shorts":
            clauses.append("c.duration IS NOT NULL AND c.duration <= 60")
        elif content_type == "videos":
            clauses.append("c.duration IS NOT NULL AND c.duration > 60")
        if exclude_downloaded:
            clauses.append("d.video_id IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order_by = {
            "views": "COALESCE(c.view_count,-1) DESC, c.id DESC",
            "duration_asc": "COALESCE(c.duration,1e12) ASC, c.id DESC",
            "duration_desc": "COALESCE(c.duration,-1) DESC, c.id DESC",
            "trending": "CASE WHEN c.upload_date != '' THEN COALESCE(c.view_count,0) * 1.0 / MAX(1,julianday('now')-julianday(substr(c.upload_date,1,4)||'-'||substr(c.upload_date,5,2)||'-'||substr(c.upload_date,7,2))) ELSE -1 END DESC, c.id DESC",
        }.get(sort, "c.upload_date DESC, c.id DESC")
        params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        with self._connect() as conn:
            return list(conn.execute(
                f"""
                SELECT c.*, d.downloaded_at, d.file_path
                FROM crawled_videos c
                LEFT JOIN downloaded_videos d
                  ON d.page_id = c.source_id AND d.video_id = c.video_id
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall())

    def get_source_statuses(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT source_id,status FROM source_lifecycle").fetchall()
        return {str(row["source_id"]): str(row["status"]) for row in rows}

    def set_source_status(self, source_id: int | str, status: str) -> None:
        if status not in {"active", "paused", "archived", "error"}:
            raise ValueError(f"Invalid source status: {status}")
        archived_at = _now() if status == "archived" else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO source_lifecycle(source_id,status,archived_at,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(source_id) DO UPDATE SET status=excluded.status,
                    archived_at=excluded.archived_at, updated_at=excluded.updated_at
                """,
                (self._sid(source_id), status, archived_at, _now()),
            )

    def delete_source_data(self, source_id: int | str) -> dict[str, int]:
        """Remove app-owned records for a source without touching downloaded files."""
        sid = self._sid(source_id)
        deleted: dict[str, int] = {}
        with self._connect() as conn:
            for key, table, column in (
                ("downloads", "downloaded_videos", "page_id"),
                ("videos", "crawled_videos", "source_id"),
                ("approvals", "approval_events", "source_id"),
                ("checkpoint", "crawl_checkpoints", "source_id"),
                ("stats", "source_stats", "source_id"),
                ("lifecycle", "source_lifecycle", "source_id"),
            ):
                cursor = conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (sid,))
                deleted[key] = int(cursor.rowcount)
        return deleted

    def create_job_record(self, job_id: str, kind: str, source_ids: list[str], status: str = "queued") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs(id,kind,status,source_ids,started_at) VALUES(?,?,?,?,?)",
                (job_id, kind, status, json.dumps(source_ids), _now()),
            )

    def update_job_record(self, job_id: str, status: str, *, result: dict | None = None, error: str = "") -> None:
        finished = _now() if status in {"done", "failed", "stopped", "interrupted"} else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, finished_at=COALESCE(?,finished_at), result_json=?, error=? WHERE id=?",
                (status, finished, json.dumps(result, ensure_ascii=False) if result is not None else None, error, job_id),
            )

    def add_job_event(self, job_id: str, event: str, *, message: str = "", level: str = "info", data: dict | None = None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO job_events(job_id,event,level,message,data_json,created_at) VALUES(?,?,?,?,?,?)",
                (job_id, event, level, message, json.dumps(data or {}, ensure_ascii=False), _now()),
            )
            return int(cur.lastrowid)

    def list_jobs(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            jobs = conn.execute("SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
            result: list[dict] = []
            for row in jobs:
                events = conn.execute(
                    "SELECT id,event,level,message,data_json,created_at FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT 80",
                    (row["id"],),
                ).fetchall()
                item = dict(row)
                item["source_ids"] = json.loads(item.pop("source_ids") or "[]")
                item["result"] = json.loads(item.pop("result_json") or "null")
                item["events"] = [{**dict(event), "data": json.loads(event["data_json"] or "{}")} for event in reversed(events)]
                for event in item["events"]:
                    event.pop("data_json", None)
                result.append(item)
        return result

    def interrupt_unfinished_jobs(self) -> int:
        """Close jobs left running when the desktop app or API process restarted."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE jobs
                SET status='failed', finished_at=?, error='App restarted before this job completed'
                WHERE status IN ('queued','running','stopping')
                """,
                (_now(),),
            )
            return int(cur.rowcount)

    def set_crawl_checkpoint(self, source_id: int | str, batch_number: int) -> None:
        sid = self._sid(source_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO crawl_checkpoints(source_id,batch_number,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET batch_number=excluded.batch_number, updated_at=excluded.updated_at",
                (sid, int(batch_number), _now()),
            )

    def get_crawl_checkpoint(self, source_id: int | str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT batch_number FROM crawl_checkpoints WHERE source_id = ?", (self._sid(source_id),)).fetchone()
        return int(row["batch_number"]) if row else 0

    def clear_crawl_checkpoint(self, source_id: int | str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM crawl_checkpoints WHERE source_id = ?", (self._sid(source_id),))

    def get_source_stats(self, source_id: int | str) -> dict[str, str | int | None]:
        sid = self._sid(source_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_stats WHERE source_id = ?",
                (sid,),
            ).fetchone()
        if not row:
            return {
                "last_crawl_at": None,
                "last_download_at": None,
                "crawled_count": self.get_crawled_count(sid),
                "downloaded_count": self.get_downloaded_count(sid),
            }
        return {
            "last_crawl_at": row["last_crawl_at"],
            "last_download_at": row["last_download_at"],
            "crawled_count": int(row["crawled_count"]),
            "downloaded_count": int(row["downloaded_count"]),
        }

    def mark_crawl_done(self, source_id: int | str) -> None:
        sid = self._sid(source_id)
        crawled = self.get_crawled_count(sid)
        downloaded = self.get_downloaded_count(sid)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO source_stats
                (source_id, last_crawl_at, last_download_at, crawled_count, downloaded_count)
                VALUES (?, ?, NULL, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_crawl_at = excluded.last_crawl_at,
                    crawled_count = excluded.crawled_count,
                    downloaded_count = excluded.downloaded_count
                """,
                (sid, _now(), crawled, downloaded),
            )

    def mark_download_done(self, source_id: int | str) -> None:
        sid = self._sid(source_id)
        crawled = self.get_crawled_count(sid)
        downloaded = self.get_downloaded_count(sid)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT last_crawl_at FROM source_stats WHERE source_id = ?",
                (sid,),
            ).fetchone()
            last_crawl = existing["last_crawl_at"] if existing else None
            conn.execute(
                """
                INSERT INTO source_stats
                (source_id, last_crawl_at, last_download_at, crawled_count, downloaded_count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_download_at = excluded.last_download_at,
                    crawled_count = excluded.crawled_count,
                    downloaded_count = excluded.downloaded_count
                """,
                (sid, last_crawl, _now(), crawled, downloaded),
            )
