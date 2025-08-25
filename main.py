from __future__ import annotations
import os
from pathlib import Path
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
import yt_dlp

app = FastAPI(title="gcyt-dlp", version="0.1.0")

MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "2"))
REQ_QUEUE_TIMEOUT = int(os.getenv("REQ_QUEUE_TIMEOUT", "5"))  # seconds
# Mounted Cloud Storage configuration
# STORAGE_DIR: the directory path where the Cloud Storage volume is mounted
# MOUNT_BUCKET: optional, the bucket name backing STORAGE_DIR (used to derive public URLs)
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "/mnt/storage"))

def _require_secret(request: Request) -> None:
    """If WORKER_TOKEN/SECRET_TOKEN is set, require X-Worker-Token to match."""
    expected = os.getenv("WORKER_TOKEN") or os.getenv("SECRET_TOKEN")
    if expected:
        provided = request.headers.get("x-worker-token")
        if provided != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

def progress_hook(d):
    if d['status'] == 'downloading':
        total_downloaded = d['total_bytes']
        requests.post("http://localhost:8000/progress", json={"id": d['id'], "status": "downloading", "total_downloaded": total_downloaded, "speed": d['_speed_str'], "elapsed": d['_elapsed_str']})
        print(f"Downloading: {total_downloaded} at {d['_speed_str']} : {d['_elapsed_str']}")
    elif d['status'] == 'finished':
        total_downloaded = d['total_bytes']
        requests.post("http://localhost:8000/progress", json={"id": d['id'], "status": "finished", "total_downloaded": total_downloaded, "speed": d['_speed_str'], "elapsed": d['_elapsed_str']})
        print("Finished downloading")

def _download_with_ytdlp(url: str):
    out_tmpl = "%(title)s.%(ext)s"
    ydl_opts = {
        "path": str(STORAGE_DIR),
        "outtmpl": out_tmpl,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook]
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}



@app.post("/jobs/")
async def trigger_job(request: Request):
    """
    Trigger a Cloud Run Job execution with URL (and optional BUCKET/object name overrides).
    Body JSON:
      {
        "url": "https://youtu.be/...",
        "bucket": "YOUR_BUCKET",              // optional if set on the Job
        "object_name": "optional.mp4",
        "project": "PROJECT_ID",              // optional if PROJECT_ID env is set
        "region": "REGION",                   // optional if JOB_REGION/REGION env is set
        "job": "JOB_NAME"                     // optional if JOB_NAME env is set
      }
    Uses X-Worker-Token if configured.
    """
    _require_secret(request)
    body = await request.json()
    url_val = body.get("url")
    if not url_val:
        raise HTTPException(status_code=400, detail="url is required")

    try:
        task = BackgroundTask(_download_with_ytdlp, url=url_val)
        return JSONResponse({"status": "dispatched", "task_id": id(task)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Entrypoint for local run: uvicorn main:app --reload
