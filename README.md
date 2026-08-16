# Memoe

Operational memory for SRE intelligence.

## Local Demo

Start the full Docker Compose demo stack:

```bash
docker compose up -d cockroach backend frontend
```

Open:

```text
http://127.0.0.1:3000
```

The backend is available at:

```text
http://127.0.0.1:8000
```

The backend container reads `.env` and mounts your local AWS config from `~/.aws` read-only so Bedrock can use `AWS_PROFILE`.

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
