FROM python:3.14-slim AS base

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project --no-dev

COPY . .
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

FROM base AS api
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "btk.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS bot
CMD ["uv", "run", "btk-bot"]
