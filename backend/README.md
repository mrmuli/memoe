# Memoe Backend

FastAPI and CLI backend for the Memoe local demo.

The backend owns:

- schema initialization
- fixture loading
- source normalization
- observation generation
- reflection generation
- memory embedding refresh
- chat orchestration with LangGraph

## Local Commands

Initialize the database:

```bash
uv run memoe database init
```

Load the demo fixture scenario:

```bash
uv run memoe seed load demo
```

List normalized evidence for a service:

```bash
uv run memoe seed evidence --service orders
```

Run an observation:

```bash
uv run memoe observations run --service orders --provider bedrock
```

Bedrock provider commands require AWS credentials for the configured `AWS_PROFILE` where applicable.

Run a reflection across current memory:

```bash
uv run memoe reflections run --provider bedrock --goal "Reflect across all services and identify reliability risks SREs should investigate next."
```

Refresh memory embeddings:

```bash
uv run memoe embeddings refresh
```

Search memory:

```bash
uv run memoe memory search "orders customer impact ticket"
```

## API

Run the local API directly:

```bash
uv run uvicorn memoe.api.app:app --host 127.0.0.1 --port 8000
```

Or run it through Docker Compose from the repository root:

```bash
docker compose up -d --build
```

Useful endpoints:

```text
GET  /health
GET  /services
GET  /observations
GET  /reflections
POST /chat
POST /observations/run
POST /reflections/run
GET  /reflections/jobs/latest
```

## Notes

The backend logs high-level observation and embedding activity. For example, when an observation runs it logs the service, provider, model, source-table counts, event-type counts, and time range of the evidence bundle.

Raw source rows are persisted in CockroachDB, but logs intentionally avoid dumping full model prompts, raw ticket descriptions, credentials, or API keys.
