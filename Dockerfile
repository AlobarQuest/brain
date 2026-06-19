FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .
RUN useradd -m appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
# Self-contained healthcheck (no curl/wget in slim image); Coolify uses this for
# dockerimage health gating. /api/health returns 503 when the DB is unreachable,
# which urlopen surfaces as a non-zero exit -> unhealthy.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"]
CMD ["sh", "/app/scripts/start.sh"]
