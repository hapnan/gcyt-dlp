# gcyt-dlp

FastAPI service that downloads videos using yt-dlp. Designed for Google Cloud Run. Uses uv for Python and dependency management.

## Endpoints

- GET /healthz — health check
- POST /download — query params:
	- url: required video URL
	- to_gcs: bool, if true uploads to GCS and returns metadata
	- bucket: GCS bucket (required when to_gcs=true)
	- object_name: optional object name

## Local dev

Use uv to install and run:

```powershell
# Install deps into a venv
uv sync

# Run API
uv run uvicorn main:app --reload

# Test
curl "http://127.0.0.1:8000/download?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ" -X POST -OJ
```

To enable GCS uploads, install the extra and set credentials:

```powershell
uv sync --extra gcs
$env:GOOGLE_APPLICATION_CREDENTIALS = "c:\\path\\to\\service-account.json"
```

## Container build

The Dockerfile uses uv for fast, reproducible installs.

```powershell
docker build -t gcr.io/PROJECT_ID/gcyt-dlp:latest .
```

## Deploy to Cloud Run

```powershell
gcloud run deploy gcyt-dlp `
	--source . `
	--region YOUR_REGION `
	--allow-unauthenticated `
	--max-instances 3 `
	--port 8080
```

If using GCS uploads, grant the service account storage permissions and set the default bucket via request parameters.
