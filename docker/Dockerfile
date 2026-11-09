# WhatsApp ↔ Claude bridge + agent for a Linux server with an NVIDIA GPU (TrueNAS Custom App).
# One container: the Go bridge, the Python agent (Hebrew Whisper on the GPU) and Claude Code. All personal data lives
# in the /data volume; the code is copied there on every start (personal files are never part of the image).

# ---- the bridge (Go + sqlite3 → needs cgo) ----
FROM golang:1.26-bookworm AS bridge
WORKDIR /src
COPY whatsapp-bridge/go.mod whatsapp-bridge/go.sum ./
RUN go mod download
COPY whatsapp-bridge/ ./
RUN CGO_ENABLED=1 go build -trimpath -ldflags="-s -w" -o /out/whatsapp-bridge .

# ---- Node, for Claude Code ----
FROM node:22-bookworm-slim AS node

# ---- runtime ----
FROM python:3.12-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git tini tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g @anthropic-ai/claude-code && npm cache clean --force \
    && claude --version

# Python environments (outside /data: rebuilt with the image)
COPY agent/pyproject.toml agent/uv.lock /tmp/agent/
COPY whatsapp-mcp-server/pyproject.toml whatsapp-mcp-server/uv.lock /tmp/mcp/
RUN uv venv /opt/agent-venv --python /usr/local/bin/python3 \
    && (cd /tmp/agent && uv export --frozen --no-hashes --no-emit-project --extra voice --extra plugins -o req.txt) \
    && uv pip install --python /opt/agent-venv/bin/python -r /tmp/agent/req.txt \
       nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10" \
    && uv venv /opt/mcp-venv --python /usr/local/bin/python3 \
    && (cd /tmp/mcp && uv export --frozen --no-hashes --no-emit-project -o req.txt) \
    && uv pip install --python /opt/mcp-venv/bin/python -r /tmp/mcp/req.txt \
    && rm -rf /tmp/agent /tmp/mcp /root/.cache
# ctranslate2 (faster-whisper) finds cuBLAS / cuDNN from the pip wheels here
ENV LD_LIBRARY_PATH=/opt/agent-venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/agent-venv/lib/python3.12/site-packages/nvidia/cudnn/lib

COPY --from=bridge /out/whatsapp-bridge /usr/local/bin/whatsapp-bridge
COPY . /opt/src
RUN rm -rf /opt/src/.git /opt/src/docker/Dockerfile && chmod +x /opt/src/docker/*.sh

ENV WA_CONTAINER=1 \
    WA_MCP_PYTHON=/opt/mcp-venv/bin/python \
    HOME=/data/home \
    HF_HOME=/data/hf-cache \
    TZ=Asia/Jerusalem
VOLUME /data
HEALTHCHECK --interval=2m --timeout=20s --start-period=5m --retries=3 \
    CMD ["/opt/agent-venv/bin/python", "/opt/src/docker/healthcheck.py"]
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/src/docker/start.sh"]
