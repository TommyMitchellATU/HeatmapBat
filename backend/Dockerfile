# Backend API container with dev + test deps (includes pytest)
FROM python:3.11-slim

ENV UV_SYSTEM_PYTHON=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates build-essential git \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh -s --
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy dependency manifests early for layer caching
COPY pyproject.toml uv.lock* ./

# Install all dependencies (including dev: pytest, ruff, mypy)
RUN uv sync --dev

# Copy application source
COPY app ./app

EXPOSE 8000

# Default command: FastAPI dev server (hot reload in compose bind mount)
CMD ["uv", "run", "fastapi", "dev", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]