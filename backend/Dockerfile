# Lightweight Python image + uv package manager
FROM python:3.11-slim

ENV UV_SYSTEM_PYTHON=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates build-essential git \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh -s -- -y
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy project metadata first (for layer caching)
COPY pyproject.toml ./
# If you create a uv.lock later, copy it too:
# COPY uv.lock ./

# Install deps
RUN uv sync --no-install-project || true

# Copy source
COPY app ./app
COPY etl ./etl
COPY tests ./tests

EXPOSE 8000