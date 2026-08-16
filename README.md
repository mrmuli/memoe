# Memoe

Operational memory for SRE intelligence.

## Local Demo

Start CockroachDB:

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
