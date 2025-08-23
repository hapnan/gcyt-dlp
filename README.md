# gcyt-dlp

FastAPI worker that downloads videos with yt-dlp. Designed for Google Cloud Run. Uses uv for Python env and dependency management. ffmpeg is installed in the container for muxing/merging.

- Short downloads (<60 min): call the service’s /download.
- Long recordings (e.g., live streams >30–60 min): trigger a Cloud Run Job via /jobs/trigger (Jobs can run up to 24h).

## Endpoints

- GET /healthz — health check
- POST /download — query params:
  - url: required video URL
  - to_gcs: bool, if true uploads to GCS and returns metadata
  - bucket: GCS bucket (required when to_gcs=true)
  - object_name: optional object name
- POST /jobs/trigger — trigger a Cloud Run Job execution for long downloads
  - Body JSON:
    - url: required video URL
    - bucket: optional (if BUCKET env is set on the Job, this can be omitted)
    - object_name: optional object name
    - project/region/job: optional overrides; otherwise taken from env (PROJECT_ID, JOB_REGION/REGION, JOB_NAME)

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

## Deploy to Cloud Run (service, for short jobs)

```powershell
gcloud run deploy gcyt-dlp `
  --source . `
  --region YOUR_REGION `
  --allow-unauthenticated `
  --concurrency 2 `
  --cpu 2 `
  --memory 4Gi `
  --timeout 3600 `
  --ephemeral-storage 16Gi `
  --set-env-vars MAX_CONCURRENCY=2 `
  --set-env-vars WORKER_TOKEN=your-secret
```

Notes:
- Cloud Run service max request time is 60 minutes. Use Jobs for longer tasks.
- Set concurrency low (1–2) and size memory/ephemeral storage based on expected parallel downloads and file sizes.

## Cloud Run Job (for long recordings)

Create the Job once, using your built image:

```powershell
# Example using an image published to GHCR (ensure the region can pull the image)
gcloud run jobs create gcyt-dlp-job `
  --image ghcr.io/OWNER/REPO:latest `
  --region YOUR_REGION `
  --cpu 2 --memory 4Gi --ephemeral-storage 16Gi `
  --task-timeout 24h `
  --set-env-vars BUCKET=your-bucket `
  --command uv `
  --args "run,python,job_main.py"
```

Grant the Cloud Run service account permission to run Jobs:

```powershell
# SERVICE_ACCOUNT is typically the Cloud Run service’s runtime SA
gcloud projects add-iam-policy-binding PROJECT_ID `
  --member "serviceAccount:SERVICE_ACCOUNT_EMAIL" `
  --role "roles/run.developer"
```

Configure the service with defaults for triggering:

```powershell
gcloud run services update gcyt-dlp `
  --region YOUR_REGION `
  --set-env-vars PROJECT_ID=PROJECT_ID,JOB_REGION=YOUR_REGION,JOB_NAME=gcyt-dlp-job,BUCKET=your-bucket
```

Trigger from your UI via the API:

```powershell
curl "https://YOUR-SERVICE-URL/jobs/trigger" `
  -X POST `
  -H "Content-Type: application/json" `
  -H "X-Worker-Token: your-secret" `
  -d '{ "url": "https://youtu.be/...", "object_name": "my-live.mp4" }'
```

You can also execute directly with gcloud:

```powershell
gcloud run jobs execute gcyt-dlp-job `
  --region YOUR_REGION `
  --set-env-vars URL="https://youtu.be/...",OBJECT_NAME="my-live.mp4"
```

## Environment variables

- WORKER_TOKEN (or SECRET_TOKEN): if set, requests must include X-Worker-Token header.
- MAX_CONCURRENCY: per-instance cap for concurrent downloads (default 2).
- REQ_QUEUE_TIMEOUT: seconds to wait for a concurrency slot (default 5).
- PROJECT_ID, JOB_REGION/REGION, JOB_NAME: defaults for /jobs/trigger.
- BUCKET: default GCS bucket (used by /jobs/trigger if not provided).
- GOOGLE_APPLICATION_CREDENTIALS: local dev only, path to a service account JSON for GCS access.

## Concurrency and scaling

- Each request uses a unique temp dir and cleans up after completion.
- Keep Cloud Run container concurrency low (1–2) and let autoscaling handle throughput.
- Tune memory and ephemeral storage to the sum of concurrent outputs.
- Prefer uploading to GCS and returning metadata for large files.

## GitHub Actions (CI/CD)

A workflow at .github/workflows/ci.yml:
- Installs dependencies with uv and does a smoke import test.
- Builds the Docker image with Buildx.
- Pushes to GHCR on pushes to master and tags (ghcr.io/OWNER/REPO:...).

Ensure GitHub Packages is enabled and that your repo/user has permission to publish to
