from __future__ import annotations
import os
import asyncio
import tempfile
import shutil
from pathlib import Path
from typing import Optional, TYPE_CHECKING
import json
import base64  # added

from fastapi import FastAPI, HTTPException,  Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask  # added

try:
    from google.cloud import storage  # type: ignore
    _HAS_GCS = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_GCS = False
    if TYPE_CHECKING:  # help type-checkers
        from google.cloud import storage  # type: ignore

import yt_dlp

app = FastAPI(title="gcyt-dlp", version="0.1.0")

MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "2"))
REQ_QUEUE_TIMEOUT = int(os.getenv("REQ_QUEUE_TIMEOUT", "5"))  # seconds
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

def _require_secret(request: Request) -> None:
    """If WORKER_TOKEN/SECRET_TOKEN is set, require X-Worker-Token to match."""
    expected = os.getenv("WORKER_TOKEN") or os.getenv("SECRET_TOKEN")
    if expected:
        provided = request.headers.get("x-worker-token")
        if provided != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

def _download_with_ytdlp(url: str, tmpdir: Path) -> Path:
    out_tmpl = str(tmpdir / "%(title)s.%(ext)s")
    ydl_opts = {
        "outtmpl": out_tmpl,
        # Best progressive (contains audio) or best audio+video merged
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # When merging, the final path is in _filename or returned value
        if info and "requested_downloads" in info and info["requested_downloads"]:
            # Last requested file likely the merged file
            filename = info["requested_downloads"][-1]["_filename"]
        else:
            filename = ydl.prepare_filename(info)
    path = Path(filename)
    if not path.exists():
        # Fallback: find any file in tmpdir
        candidates = list(tmpdir.glob("*"))
        if not candidates:
            raise FileNotFoundError("Downloaded file not found")
        # choose largest
        path = max(candidates, key=lambda p: p.stat().st_size)
    return path

def _upload_to_gcs(file_path: Path, bucket_name: str, object_name: Optional[str] = None) -> str:
    if not _HAS_GCS:
        raise RuntimeError("google-cloud-storage is not installed; install the 'gcs' extra")
    if not bucket_name:
        raise ValueError("bucket_name required")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    if object_name is None:
        object_name = file_path.name
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(file_path))
    blob.make_public()  # optional: ease access; consider signed URLs in prod
    return blob.public_url

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.post("/download")
async def download(request: Request, url: str, to_gcs: bool = False, bucket: str | None = None, object_name: str | None = None):
    _require_secret(request)  # added
    try:
        # Optional: fail fast if queue is full
        await asyncio.wait_for(_semaphore.acquire(), timeout=REQ_QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        # Too many in-flight downloads on this instance
        raise HTTPException(status_code=503, detail="Busy, try again later")

    tmpdir: Optional[str] = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="yt_")
        tmpdir_path = Path(tmpdir)

        # Run blocking yt-dlp off the event loop
        video_path = await run_in_threadpool(_download_with_ytdlp, url, tmpdir_path)

        if to_gcs:
            if not bucket:
                raise HTTPException(status_code=400, detail="bucket is required when to_gcs=true")
            public_url = _upload_to_gcs(video_path, bucket, object_name)
            return JSONResponse({
                "status": "uploaded",
                "bucket": bucket,
                "object_name": object_name or video_path.name,
                "url": public_url,
                "size": video_path.stat().st_size,
            })

        # Stream file and delete temp dir after response is sent
        return FileResponse(
            path=str(video_path),
            filename=video_path.name,
            media_type="application/octet-stream",
            background=BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True),  # cleanup after send
        )
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"yt-dlp error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _semaphore.release()
        # If we uploaded to GCS or errored before streaming, ensure cleanup now
        if tmpdir is not None and to_gcs:
            shutil.rmtree(tmpdir, ignore_errors=True)

# --- Background worker endpoints ---

@app.post("/tasks/handle")
async def handle_cloud_tasks(request: Request):
    """
    Cloud Tasks HTTP target.
    Body JSON: { "url": "...", "bucket": "...", "object_name": "optional" }
    Secured via X-Worker-Token if WORKER_TOKEN/SECRET_TOKEN is set.
    Always uploads to GCS (recommended for background).
    """
    _require_secret(request)
    body = await request.json()
    url = body.get("url")
    bucket = body.get("bucket")
    object_name = body.get("object_name")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    if not bucket:
        raise HTTPException(status_code=400, detail="bucket is required for background jobs")

    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=REQ_QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Busy, try again later")

    tmpdir: Optional[str] = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="yt_")
        tmpdir_path = Path(tmpdir)
        video_path = await run_in_threadpool(_download_with_ytdlp, url, tmpdir_path)
        public_url = _upload_to_gcs(video_path, bucket, object_name)
        return JSONResponse({
            "status": "uploaded",
            "bucket": bucket,
            "object_name": object_name or video_path.name,
            "url": public_url,
            "size": video_path.stat().st_size,
        })
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"yt-dlp error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _semaphore.release()
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)

@app.post("/pubsub/push")
async def handle_pubsub_push(request: Request):
    """
    Pub/Sub push subscription target.
    Expects the standard push payload:
    {
      "message": { "data": base64(json({url,bucket,object_name})), "attributes": {...} },
      "subscription": "..."
    }
    Secured via X-Worker-Token if WORKER_TOKEN/SECRET_TOKEN is set.
    Always uploads to GCS.
    """
    _require_secret(request)
    body = await request.json()
    message = body.get("message") or {}
    data_b64 = message.get("data")
    if not data_b64:
        raise HTTPException(status_code=400, detail="missing message.data")
    try:
        payload = json.loads(base64.b64decode(data_b64).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid message.data payload")

    url = payload.get("url")
    bucket = payload.get("bucket")
    object_name = payload.get("object_name")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    if not bucket:
        raise HTTPException(status_code=400, detail="bucket is required for background jobs")

    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=REQ_QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Busy, try again later")

    tmpdir: Optional[str] = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="yt_")
        tmpdir_path = Path(tmpdir)
        video_path = await run_in_threadpool(_download_with_ytdlp, url, tmpdir_path)
        public_url = _upload_to_gcs(video_path, bucket, object_name)
        # Pub/Sub only needs 2xx; return details for logs/visibility.
        return JSONResponse({
            "status": "uploaded",
            "bucket": bucket,
            "object_name": object_name or video_path.name,
            "url": public_url,
            "size": video_path.stat().st_size,
        })
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"yt-dlp error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _semaphore.release()
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)

# Entrypoint for local run: uvicorn main:app --reload
