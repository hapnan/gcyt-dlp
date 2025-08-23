from __future__ import annotations
import os
import tempfile
import shutil
from pathlib import Path

from main import _download_with_ytdlp, _upload_to_gcs  # reuse helpers

def main() -> None:
    url = os.environ.get("URL")
    bucket = os.environ.get("BUCKET")
    object_name = os.environ.get("OBJECT_NAME")  # optional

    if not url:
        raise SystemExit("ERROR: URL env var is required")
    if not bucket:
        raise SystemExit("ERROR: BUCKET env var is required")

    tmpdir = tempfile.mkdtemp(prefix="yt_job_")
    try:
        video_path = _download_with_ytdlp(url, Path(tmpdir))
        public_url = _upload_to_gcs(video_path, bucket, object_name)
        print(f"uploaded={public_url}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == "__main__":
    main()