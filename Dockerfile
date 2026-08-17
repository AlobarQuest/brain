FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
# curl is needed for Coolify's injected health probe (it overrides the Dockerfile
# HEALTHCHECK with a curl/wget call against the configured /api/health path).
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
COPY . .
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# The commit this image was built from, reported by /api/health so the deploy job
# can tell this container from the one it replaced. Last, so a new revision
# invalidates only this layer and the metadata below it.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

EXPOSE 8000
# Self-contained healthcheck (no curl/wget in slim image); Coolify uses this for
# dockerimage health gating. /api/health returns 503 when the DB is unreachable,
# which urlopen surfaces as a non-zero exit -> unhealthy.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"]
CMD ["sh", "/app/scripts/start.sh"]
