from __future__ import annotations
import tempfile
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

try:
    from google.cloud import storage  # type: ignore
    _HAS_GCS = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_GCS = False
    if TYPE_CHECKING:  # help type-checkers
        from google.cloud import storage  # type: ignore

import yt_dlp

app = FastAPI(title="gcyt-dlp", version="0.1.0")


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
def download(
    url: str = Query(..., description="Video URL"),
    to_gcs: bool = Query(False, description="If true, upload to GCS and return URL"),
    bucket: Optional[str] = Query(None, description="GCS bucket name when to_gcs=true"),
    object_name: Optional[str] = Query(None, description="GCS object name; defaults to filename"),
):
    try:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            video_path = _download_with_ytdlp(url, tmpdir)
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
            # Otherwise, stream the file back
            return FileResponse(
                path=str(video_path),
                filename=video_path.name,
                media_type="application/octet-stream",
            )
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"yt-dlp error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Entrypoint for local run: uvicorn main:app --reload
