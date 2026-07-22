# Bakes rufo-runtime + one example agent into a single image for Cloud Run.
# Build context is the repo root (needed so `uv sync` can resolve the
# workspace's local path dependencies between packages/*).
#
#   docker build --build-arg AGENT_DIR=examples/langgraph-real-agent -t <tag> .
FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . .

# Full workspace sync, not `--package rufo-runtime`: example agents (like
# langgraph-real-agent) depend on langchain-openai/python-dotenv declared at
# the root workspace level for local dev convenience, not on rufo-runtime
# itself. Scoping to just rufo-runtime's own deps left those missing at
# runtime (a real ModuleNotFoundError caught by an actual Cloud Run deploy).
# Roadmap: give each example its own minimal pyproject.toml so this can be
# scoped tightly again without losing agent-specific dependencies.
RUN uv sync --frozen

ARG AGENT_DIR=examples/langgraph-real-agent
ENV RUFO_POLICY_PATH=/app/${AGENT_DIR}/policy.yaml
ENV RUFO_AGENT_MODULE=/app/${AGENT_DIR}/agent.py
ENV RUFO_AGENT_ENTRYPOINT=agent
ENV RUFO_DEPLOY_CONFIG_PATH=/app/${AGENT_DIR}/rufo.yaml
ENV RUFO_DB_PATH=/app/rufo_data/rufo.db

EXPOSE 8080
CMD ["sh", "-c", "uv run --project /app --package rufo-runtime uvicorn rufo_runtime.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
