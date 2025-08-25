FROM python:3.12-slim

# Install uv by copying static binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps for yt-dlp (ffmpeg)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy project metadata first (include lock if present) for better caching
COPY pyproject.toml uv.lock* /app/

# Sync deps into a virtualenv in /app/.venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-editable || \
    uv sync --no-install-project --no-editable

# Copy the rest of the source (includes main.py and job_main.py)
COPY . /app

# Install project into venv (editable off in container)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-editable

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Default runtime env
ARG DEFAULT_MODE=api
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV MODE=${DEFAULT_MODE}

# Optional: drop privileges
# RUN useradd -u 10001 -m app && chown -R app:app /app
# USER app

# Cloud Run expects to listen on PORT
ENV PORT=8080
EXPOSE 8080

# Default to entrypoint; MODE controls behavior (api|job)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
