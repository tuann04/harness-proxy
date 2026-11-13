# Harness Proxy

OpenAI-compatible local HTTP proxy and monitoring UI for installed agent CLI harnesses (`opencode`, `agy`, `claude`).

## Features

- Auto-detects `opencode`, `agy`, and `claude` in `PATH` and discovers available models
- Exposes OpenAI-compatible `GET /v1/models` and `POST /v1/chat/completions` (streaming SSE and non-streaming)
- Runs each request in an isolated ephemeral working directory and cleans up session state where supported
- Web dashboard (`http://127.0.0.1:8090/`) built with `@ui-sdk/react` for live request monitoring, routing config, and prompt testing

## Setup

Backend:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python fastapi "uvicorn[standard]" pydantic
```

Frontend:

```bash
cd ui
npm install
npm run build
```

## Run

```bash
./run.sh
```

Then open `http://127.0.0.1:8090/` or point any OpenAI-compatible client to:

```bash
OPENAI_BASE_URL=http://127.0.0.1:8090/v1
OPENAI_API_KEY=harness-proxy
```
