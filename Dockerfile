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

# Copy only project metadata first for better layer caching
COPY pyproject.toml /app/

# Sync deps into a virtualenv in /app/.venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-editable || \
    uv sync --no-install-project --no-editable

# Now copy the rest of the source
COPY . /app

# Install project into venv (editable off in container)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-editable

ENV VIRTUAL_ENV=/app/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Cloud Run expects to listen on PORT
ENV PORT=8080
EXPOSE 8080

# Use runtime PORT if provided (Cloud Run)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
