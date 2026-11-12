# API image for Contract Radar (serving only: retrieval, reranking, answers, portfolio questions, radar).
# Personal-data detection runs at ingest, outside this image (the "ingest" dependency group is skipped).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/opt/hf \
    PORT=8000

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
WORKDIR /app

# Dependencies first (cached layer); CPU-only PyTorch on Linux comes from pyproject's pytorch-cpu index.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-default-groups --no-install-project

COPY src ./src
COPY replay ./replay
COPY README.md ./
RUN uv sync --frozen --no-dev --no-default-groups

# Bake the embedding model and the reranker into the image: no download on a cold start.
RUN uv run --no-sync python -c "from contract_radar.embed import embedder, reranker; embedder(); reranker()"

# Non-root runtime user
RUN useradd --create-home --uid 10001 app && chown -R app /opt/hf
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz')"
CMD ["sh", "-c", "uv run --no-sync uvicorn contract_radar.api:app --host 0.0.0.0 --port ${PORT}"]
