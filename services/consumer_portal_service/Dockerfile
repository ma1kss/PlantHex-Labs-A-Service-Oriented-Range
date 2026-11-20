FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Apply Debian security updates and upgrade build tooling with known fixes.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY cyber_range_core ./cyber_range_core
RUN pip install --no-cache-dir --upgrade "wheel>=0.46.2" setuptools \
    && pip install --no-cache-dir -e .

COPY services/consumer_portal_service ./service
WORKDIR /app/service

EXPOSE 8006

# -k: the edge may serve HTTPS with a self-signed cert; fall back to HTTP.
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
    CMD curl -fsSk https://localhost:8006/health || curl -fsS http://localhost:8006/health || exit 1

CMD ["python", "app.py"]
