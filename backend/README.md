# Memoe Backend

Python backend package for the CLI-first Memoe memory experiment.

The first target is documented in:

- [`../ideas/first-cli-memory-test.md`](../ideas/first-cli-memory-test.md)

## Local Commands

```bash
uv run memoe seed load payments
uv run memoe observations run --service payments --provider ollama
uv run memoe observations show latest
```

## API

Run the local API:

```bash
uv run uvicorn memoe.api.app:app --host 127.0.0.1 --port 8000
```

Useful endpoints:

```text
GET  /services
GET  /observations
GET  /reflections
POST /chat
POST /observations/run
POST /reflections/run
```
