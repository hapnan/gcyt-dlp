from __future__ import annotations
import os
import asyncio
import tempfile
import shutil
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Dict
import json
import urllib.request

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask

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
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info and "requested_downloads" in info and info["requested_downloads"]:
            filename = info["requested_downloads"][-1]["_filename"]
        else:
            filename = ydl.prepare_filename(info)
    path = Path(filename)
    if not path.exists():
        candidates = list(tmpdir.glob("*"))
        if not candidates:
            raise FileNotFoundError("Downloaded file not found")
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
    blob.make_public()  # consider signed URLs in production
    return blob.public_url

def _get_gcp_access_token() -> str:
    """Fetch an access token from the metadata server (works on Cloud Run)."""
    req = urllib.request.Request(
        "http://metadata/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["access_token"]

def _run_cloud_run_job(project: str, region: str, job: str, env_overrides: Dict[str, Optional[str]]) -> dict:
    """
    Call Cloud Run Jobs v2 run API with env overrides.
    Requires the service account to have run.jobs.run (e.g., roles/run.admin or roles/run.developer).
    """
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run"
    token = _get_gcp_access_token()
    body = {
        "overrides": {
            "containerOverrides": [
                {
                    "env": [
                        {"name": k, "value": v}
                        for k, v in env_overrides.items()
                        if v is not None
                    ]
                }
            ]
        }
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.post("/download")
async def download(request: Request, url: str, to_gcs: bool = False, bucket: str | None = None, object_name: str | None = None):
    _require_secret(request)
    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=REQ_QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Busy, try again later")

    tmpdir: Optional[str] = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="yt_")
        tmpdir_path = Path(tmpdir)
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

        return FileResponse(
            path=str(video_path),
            filename=video_path.name,
            media_type="application/octet-stream",
            background=BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True),
        )
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"yt-dlp error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _semaphore.release()
        if tmpdir is not None and to_gcs:
            shutil.rmtree(tmpdir, ignore_errors=True)

@app.post("/jobs/trigger")
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

    project = body.get("project") or os.getenv("PROJECT_ID")
    region = body.get("region") or os.getenv("JOB_REGION") or os.getenv("REGION")
    job = body.get("job") or os.getenv("JOB_NAME")
    bucket = body.get("bucket") or os.getenv("BUCKET")
    object_name = body.get("object_name")

    if not project or not region or not job:
        raise HTTPException(status_code=400, detail="project, region, and job are required (via body or env)")
    env_overrides = {"URL": url_val, "BUCKET": bucket, "OBJECT_NAME": object_name}

    try:
        op = await run_in_threadpool(_run_cloud_run_job, project, region, job, env_overrides)
        return JSONResponse({"status": "dispatched", "operation": op.get("name", ""), "job": job, "region": region, "project": project})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Entrypoint for local run: uvicorn main:app --reload
