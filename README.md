# gcyt-dlp

FastAPI worker that downloads videos with yt-dlp. Designed for Google Cloud Run. Uses uv for Python env and dependency management. ffmpeg is installed in the container for muxing/merging.

Storage model: downloads are written to a mounted Cloud Storage volume (GCS Fuse). Set a volume mount at runtime and point `STORAGE_DIR` to it. If you set `MOUNT_BUCKET` to the same bucket, the service can publicize the object in-place without re-uploading.

## Endpoints

- GET /healthz — health check
- POST /jobs — trigger a Cloud Run Job execution 
  - Body JSON:
    - url: required video URL

Security: if WORKER_TOKEN (or SECRET_TOKEN) is set, send header X-Worker-Token: <token> with requests.

## Local development

Use uv to install and run:

```powershell
# Create venv and install deps
uv sync

# Optional: enable GCS uploads locally
uv sync --extra gcs
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\path\to\service-account.json"

# Run API
uv run uvicorn main:app --reload
```

Quick tests:

```powershell
# Stream a file back (remember the worker token if set)
curl "http://127.0.0.1:8000/download?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ" `
  -X POST -OJ `
  -H "X-Worker-Token: your-secret"

# Upload to GCS instead of streaming
curl "http://127.0.0.1:8000/download?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ&to_gcs=true&bucket=your-bucket&object_name=demo.mp4" `
  -X POST `
  -H "X-Worker-Token: your-secret"

# Trigger a long-running download via Cloud Run Job (once you’ve created the Job)
curl "http://127.0.0.1:8000/jobs/trigger" `
  -X POST `
  -H "Content-Type: application/json" `
  -H "X-Worker-Token: your-secret" `
  -d '{ "url": "https://youtu.be/...", "object_name": "my-live.mp4", "project": "PROJECT_ID", "region": "REGION", "job": "gcyt-dlp-job" }'
```

## Container build

```powershell
# Build locally
docker build -t ghcr.io/OWNER/REPO:gcyt-dlp .
```

The Dockerfile uses uv for fast, reproducible installs and installs ffmpeg.


## Cloud Run Job 

Create the Job once, using your built image:

```powershell
# Example using an image published to GHCR (ensure the region can pull the image)
gcloud run jobs create gcyt-dlp-job `
  --image ghcr.io/OWNER/REPO:job `
  --region YOUR_REGION `
  --cpu 2 --memory 4Gi `
  --task-timeout 24h `
  --add-volume name=gcs,cloud-storage-bucket=YOUR_BUCKET `
  --add-volume-mount volume=gcs,mount-path=/mnt/storage `
  --set-env-vars STORAGE_DIR=/mnt/storage`
  --set-env-vars MODE=job
```

Notes:
- The image includes an entrypoint that reads `MODE`. For Jobs, set `MODE=job` to run the long-running downloader (equivalent to `uv run python job_main.py`).
- Alternatively, you can override the command/args instead of setting MODE.


Trigger from your UI via the API:

```powershell
curl "https://YOUR-SERVICE-URL/jobs/" `
  -X POST `
  -H "Content-Type: application/json" `
  -H "X-Worker-Token: your-secret" `
  -d '{ "url": "https://youtu.be/..." }'
```

You can also execute directly with gcloud:

```powershell
gcloud run jobs execute gcyt-dlp-job `
  --region YOUR_REGION `
  --set-env-vars URL="https://youtu.be/..."
```


