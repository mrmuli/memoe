FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml README.md ./
COPY src ./src
COPY fixtures ./fixtures

RUN uv sync --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "memoe.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
