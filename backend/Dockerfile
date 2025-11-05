# Backend API container: lightweight Python base + uv package manager
# Rationale:
# - Python 3.11 slim: small runtime, matches CI and type-check target
# - uv: fast resolver/installer, used consistently in dev/CI/containers
FROM python:3.11-slim

ENV UV_SYSTEM_PYTHON=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Minimal build deps (curl to install uv, build tools for wheels if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates build-essential git \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager); installs to ~/.local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh -s --
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy project metadata first (for layer caching)
COPY pyproject.toml ./
# Intentionally not copying uv.lock (not present yet) to avoid version drift

# Install deps
# Pre-resolve dependencies without copying the full app (speeds rebuilds)
RUN uv sync --dev --no-install-project || true

# Copy source
# Copy the application code (bind-mounted in dev for hot reload)
COPY app ./app

EXPOSE 8000
