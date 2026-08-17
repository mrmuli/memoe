# Memoe

[![CI](https://github.com/mrmuli/memoe/actions/workflows/ci.yml/badge.svg)](https://github.com/mrmuli/memoe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Status: Hackathon Prototype](https://img.shields.io/badge/status-hackathon%20prototype-blue)

Operational memory for SRE intelligence.

## Local Demo

Start the full Docker Compose demo stack:

```bash
docker compose up -d
```

The backend initializes the CockroachDB schema and loads the demo fixture scenario on startup.

Open:

```text
http://127.0.0.1:3000
```

The backend is available at:

```text
http://127.0.0.1:8000
```

The backend container reads `.env` and mounts your local AWS config from `~/.aws` so Bedrock can use `AWS_PROFILE`. AWS SSO may write refreshed token cache files during a chat request.

To refresh the seeded evidence manually:

```bash
docker compose exec backend uv run memoe seed load payments
```

Stop the stack:

```bash
docker compose down
```

## Local Development

Start only CockroachDB:

```bash
docker compose up -d cockroach
```

Start the FastAPI backend:

```bash
cd backend
uv run uvicorn memoe.api.app:app --host 127.0.0.1 --port 8000
```

Start the Next.js frontend:

```bash
cd frontend
npm install
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open:

```text
http://127.0.0.1:3000
```

The UI exposes:

- service selection
- current observations
- current reflections
- chat over Memoe memory
- optional reflection before answer
- buttons to run observation or reflection
