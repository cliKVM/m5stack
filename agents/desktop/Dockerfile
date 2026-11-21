FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY agents/desktop/ ./agents/desktop/
RUN python -m pip install './agents/desktop[rfb]' && \
    groupadd --gid 10001 clikvm && useradd --uid 10001 --gid 10001 --no-create-home clikvm && \
    mkdir /state && chown 10001:10001 /state
USER 10001:10001
ENTRYPOINT ["clikvm-node", "--config", "/config/node.json"]
CMD ["run"]
