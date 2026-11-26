# HomeRoster: small image, runs as an unprivileged user, nothing downloaded at runtime.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    HOMEROSTER_DATA=/data \
    HOMEROSTER_CONFIG=/config/homeroster.yaml

LABEL org.opencontainers.image.title="HomeRoster" \
      org.opencontainers.image.description="One place to manage the people you share your homelab with" \
      org.opencontainers.image.licenses="MIT"

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --system --no-create-home --shell /usr/sbin/nologin homeroster \
 && mkdir -p /data /config && chown 10001:10001 /data

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY src/homeroster ./homeroster

USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["python", "-m", "homeroster", "serve"]
