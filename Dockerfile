FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml requirements.lock LICENSE NOTICE ./
COPY gateway/ ./gateway/
RUN python -m pip install -r requirements.lock && \
    python -m pip install --no-deps . && \
    groupadd --gid 10001 clikvm && \
    useradd --uid 10001 --gid 10001 --no-create-home clikvm && \
    mkdir -p /state && chown 10001:10001 /state
ENV CLIK_STATE_DIR=/state
USER 10001:10001
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health',timeout=3).read()"
CMD ["clikvm", "serve", "--host", "0.0.0.0", "--port", "8787"]
