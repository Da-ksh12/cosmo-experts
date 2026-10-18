# syntax=docker/dockerfile:1.7
# Cosmo Experts backend. NOT BUILT in the development environment (no container runtime there): see docs/DEPLOYMENT.md "Verification status".
FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# tesseract: OCR. espeak-ng: offline text-to-speech (pyttsx3 on Linux). curl is not installed; the health check uses Python.
# Debian point-release pins break rebuilds, so the base image tag is pinned instead.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng espeak-ng libespeak-ng1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# Optional: research-model serving and training need PyTorch (CPU wheels). Off by default to keep the image small:
#   docker build --build-arg WITH_TORCH=1 ...
ARG WITH_TORCH=0
# requirements.txt is pinned; torch comes from the official CPU index and is optional.
# hadolint ignore=DL3013
RUN pip install -r requirements.txt \
 && if [ "$WITH_TORCH" = "1" ]; then pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu; fi

COPY app ./app
COPY scripts ./scripts
COPY content ./content

# Unprivileged user; all state lives in /data (a volume). Models are fetched explicitly after the first start:
#   docker compose run --rm backend python -m scripts.fetch_models
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin cosmo \
 && mkdir -p /data && chown cosmo:cosmo /data
USER cosmo
ENV COSMO_DATA_DIR=/data COSMO_MODE=production

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=4).status == 200 else 1)"]

# One worker: the job queue and model caches are per process. --proxy-headers makes the client address come from the reverse proxy
# (set FORWARDED_ALLOW_IPS to the proxy's network, never '*' on an exposed port).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-172.16.0.0/12}\""]
