# Tool Download Video

Web/Electron application with a FastAPI backend for crawling and downloading YouTube/TikTok videos per source folder. Config is JSON; crawl queue and download history live in SQLite.

## Requirements

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/download.html) on `PATH` (or beside the exe under `ffmpeg/bin/`)
- Windows

## Install

```powershell
cd D:\workspace\code\tool_download_video
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Run FastAPI directly

```powershell
python main.py
```

## Electron application

The desktop UI can also be run as an Electron client with the FastAPI backend:

```powershell
pip install -r requirements.txt
cd electron
npm install
npm start
```

Electron starts `uvicorn backend.main:app` on localhost and uses the SQLite queue and
download modules. For a packaged build, install the
Python dependencies on the target machine and run `npm run dist`; set `TDV_PYTHON`
if the Python launcher is not available as `py`/`python3`.

## Web UI

1. **Manager Source** — add/edit sources, import/export Excel and open folders.
2. **Auto-sync** — the app incrementally crawls all configured channels in the background; batches arrive in Review without a separate crawl screen.
3. **Review / Queue** — filter and preview results, approve intentionally, then download only queued items.

## Valid links

- YouTube long-form: `https://www.youtube.com/@channel/videos`
- YouTube shorts: `https://www.youtube.com/@channel/shorts`
- YouTube channel (both): `https://www.youtube.com/@channel`
- TikTok profile: `https://www.tiktok.com/@user`

## Behavior

- Each source downloads into its own `path_download`
- Crawl stores new video IDs in SQLite; download takes pending newest-first until the folder has `target_videos_per_page` files
- `downloaded_videos` prevents re-downloading the same `video_id` per source
- File name = sanitized video title
- `thread_count` = parallel downloads per source; `source_thread_count` = parallel sources
- The Electron workflow is staged: crawl → review/preview → approve → download
- Crawl results arrive incrementally through SSE while the full channel continues in the background
- Only videos explicitly approved into the queue are eligible for download
- Auto-sync skips known IDs and stops after a streak of known videos; a 15-minute freshness window avoids redundant launches

## Config

See [`config/settings.example.json`](config/settings.example.json). Legacy `pages` + `output_base_folder` configs are migrated on load.

