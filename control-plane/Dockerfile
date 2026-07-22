# Rufo Cloud control plane -- distinct from the repo-root Dockerfile, which
# bakes in a customer *agent* + rufo-runtime. This one serves the
# rufo-control-plane FastAPI app itself. Build context is the repo root
# (needed so `uv sync` can resolve the workspace's local path dependencies).
#
#   docker build -f control-plane/Dockerfile -t <tag> .
FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . .

RUN uv sync --frozen --package rufo-control-plane

EXPOSE 8080
CMD ["sh", "-c", "uv run --project /app --package rufo-control-plane uvicorn rufo_control_plane.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
