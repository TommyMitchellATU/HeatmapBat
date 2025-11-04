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
RUN curl -LsSf https://astral.sh/uv/install.sh | sh -s --
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy project metadata first (for layer caching)
COPY pyproject.toml ./
# Intentionally not copying uv.lock to avoid Python version mismatches during resolution

# Install deps
RUN uv sync --dev --no-install-project || true

# Copy source
COPY app ./app

EXPOSE 8000
