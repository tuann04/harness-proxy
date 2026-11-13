"""FastAPI server exposing OpenAI-compatible endpoints and monitor/config UI."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server.config import ProxyConfig, load_config, update_config
from server.harnesses import (
    HarnessError,
    HarnessInfo,
    detect_all_harnesses,
    format_messages_as_prompt,
    stream_harness,
)
from server.state import MonitorState

STATIC_DIR = Path(__file__).resolve().parent / "static"
AUTO_PRIORITY = ("opencode", "agy", "claude")


class RuntimeHolder:
    def __init__(self) -> None:
        self.config: ProxyConfig = load_config()
        self.harnesses: Dict[str, HarnessInfo] = {}
        self.monitor: MonitorState = MonitorState()
        self.semaphore: asyncio.Semaphore = asyncio.Semaphore(self.config.max_concurrency)


runtime = RuntimeHolder()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    runtime.harnesses = await detect_all_harnesses()
    runtime.monitor.notify("init")
    yield


app = FastAPI(title="Harness Proxy", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _pick_auto_harness() -> HarnessInfo:
    for hid in AUTO_PRIORITY:
        info = runtime.harnesses.get(hid)
        if info and info.available:
            return info
    for info in runtime.harnesses.values():
        if info.available:
            return info
    raise HarnessError("No supported harness (opencode, agy, claude) found in PATH.")


def resolve_target(requested_model: str = "") -> Tuple[HarnessInfo, str]:
    """Resolve (HarnessInfo, model_id) for an incoming request."""
    req = (requested_model or "").strip()

    # Explicit prefix syntax: "<harness>:<model>"
    if ":" in req:
        prefix, rest = req.split(":", 1)
        info = runtime.harnesses.get(prefix)
        if info and info.available:
            return info, rest or info.default_model

    # Respect client model when override is disabled and model matches a known model
    if not runtime.config.override_client_model and req:
        for info in runtime.harnesses.values():
            if info.available and any(m.id == req for m in info.models):
                return info, req

    # Use active config
    selected = runtime.config.active_harness
    if selected == "auto":
        info = _pick_auto_harness()
    else:
        info = runtime.harnesses.get(selected)
        if not info or not info.available:
            info = _pick_auto_harness()

    model = runtime.config.active_model.strip()
    if model and selected != "auto":
        return info, model
    if model and selected == "auto" and any(m.id == model for m in info.models):
        return info, model
    return info, info.default_model


def _sse_chunk(
    completion_id: str,
    created: int,
    model_name: str,
    *,
    delta: Optional[Dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "delta": delta or {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n"


if (STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models():
    data = []
    now = int(time.time())
    for info in runtime.harnesses.values():
        if not info.available:
            continue
        for m in info.models:
            data.append(
                {
                    "id": m.id,
                    "object": "model",
                    "created": now,
                    "owned_by": info.id,
                }
            )
    return {"object": "list", "data": data}


@app.post("/v1/embeddings")
async def embeddings_not_supported():
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "message": "Agent CLI harnesses do not provide vector embeddings.",
                "type": "not_implemented",
            }
        },
    )


class ChatCompletionRequest(BaseModel):
    model: str = ""
    messages: List[Dict[str, Any]]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


@app.post("/v1/chat/completions")
async def chat_completions(body: ChatCompletionRequest):
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    try:
        harness_info, target_model = resolve_target(body.model)
    except HarnessError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    prompt = format_messages_as_prompt(body.messages)
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    served_model = f"{harness_info.id}:{target_model}" if target_model else harness_info.id
    timeout = float(runtime.config.timeout_seconds)

    if body.stream:
        rec = runtime.monitor.start_request(
            harness=harness_info.id,
            model=target_model,
            requested_model=body.model or "(default)",
            stream=True,
            prompt=prompt,
        )

        async def event_stream():
            output_parts: List[str] = []
            usage: Dict[str, Any] = {}
            sent_role = False
            try:
                async with runtime.semaphore:
                    async for chunk in stream_harness(harness_info, target_model, prompt, timeout):
                        if chunk.usage:
                            usage = chunk.usage
                        if chunk.delta:
                            if not sent_role:
                                yield _sse_chunk(
                                    completion_id,
                                    created,
                                    served_model,
                                    delta={"role": "assistant", "content": ""},
                                )
                                sent_role = True
                            output_parts.append(chunk.delta)
                            yield _sse_chunk(
                                completion_id,
                                created,
                                served_model,
                                delta={"content": chunk.delta},
                            )
                if not sent_role:
                    yield _sse_chunk(
                        completion_id,
                        created,
                        served_model,
                        delta={"role": "assistant", "content": ""},
                    )
                yield _sse_chunk(completion_id, created, served_model, finish_reason="stop")
                yield "data: [DONE]\n\n"
                runtime.monitor.finish_request(rec, output="".join(output_parts), usage=usage)
            except Exception as exc:
                err_text = str(exc)
                runtime.monitor.finish_request(
                    rec, output="".join(output_parts), usage=usage, error=err_text
                )
                err_payload = {"error": {"message": err_text, "type": "harness_error"}}
                yield f"data: {json.dumps(err_payload)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # Non-streaming path
    rec = runtime.monitor.start_request(
        harness=harness_info.id,
        model=target_model,
        requested_model=body.model or "(default)",
        stream=False,
        prompt=prompt,
    )
    output_parts: List[str] = []
    usage: Dict[str, Any] = {}
    try:
        async with runtime.semaphore:
            async for chunk in stream_harness(harness_info, target_model, prompt, timeout):
                if chunk.usage:
                    usage = chunk.usage
                if chunk.delta:
                    output_parts.append(chunk.delta)
        full_text = "".join(output_parts)
        runtime.monitor.finish_request(rec, output=full_text, usage=usage)
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": served_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": full_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": int(usage.get("input_tokens") or usage.get("input") or 0),
                "completion_tokens": int(usage.get("output_tokens") or usage.get("output") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            },
        }
    except Exception as exc:
        err_text = str(exc)
        runtime.monitor.finish_request(rec, output="".join(output_parts), usage=usage, error=err_text)
        return JSONResponse(
            status_code=502,
            content={"error": {"message": err_text, "type": "harness_error"}},
        )


@app.get("/api/status")
async def api_status():
    resolved_harness = ""
    resolved_model = ""
    try:
        info, model = resolve_target("")
        resolved_harness = info.id
        resolved_model = model
    except HarnessError:
        pass
    return {
        "config": runtime.config.model_dump(),
        "resolved": {"harness": resolved_harness, "model": resolved_model},
        "harnesses": {hid: info.to_dict() for hid, info in runtime.harnesses.items()},
        "monitor": runtime.monitor.snapshot(),
    }


class ConfigPatch(BaseModel):
    active_harness: Optional[str] = None
    active_model: Optional[str] = None
    override_client_model: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    max_concurrency: Optional[int] = None


@app.post("/api/config")
async def api_update_config(patch: ConfigPatch):
    prev_concurrency = runtime.config.max_concurrency
    runtime.config = update_config(
        runtime.config,
        active_harness=patch.active_harness,
        active_model=patch.active_model,
        override_client_model=patch.override_client_model,
        timeout_seconds=patch.timeout_seconds,
        max_concurrency=patch.max_concurrency,
    )
    if runtime.config.max_concurrency != prev_concurrency:
        runtime.semaphore = asyncio.Semaphore(runtime.config.max_concurrency)
    runtime.monitor.notify("config")
    return await api_status()


@app.post("/api/refresh")
async def api_refresh():
    runtime.harnesses = await detect_all_harnesses()
    runtime.monitor.notify("refresh")
    return await api_status()


class TestPromptRequest(BaseModel):
    prompt: str = "Reply with only: OK"
    harness: Optional[str] = None
    model: Optional[str] = None


@app.post("/api/test")
async def api_test_prompt(body: TestPromptRequest):
    if body.harness and body.harness != "auto":
        info = runtime.harnesses.get(body.harness)
        if not info or not info.available:
            raise HTTPException(status_code=400, detail=f"Harness '{body.harness}' unavailable")
        target_model = (body.model or info.default_model).strip()
    else:
        try:
            info, target_model = resolve_target("")
        except HarnessError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if body.model:
            target_model = body.model.strip()

    rec = runtime.monitor.start_request(
        harness=info.id,
        model=target_model,
        requested_model="(ui-test)",
        stream=False,
        prompt=body.prompt,
    )
    output_parts: List[str] = []
    usage: Dict[str, Any] = {}
    try:
        async with runtime.semaphore:
            async for chunk in stream_harness(
                info, target_model, body.prompt, float(runtime.config.timeout_seconds)
            ):
                if chunk.usage:
                    usage = chunk.usage
                if chunk.delta:
                    output_parts.append(chunk.delta)
        full_text = "".join(output_parts)
        runtime.monitor.finish_request(rec, output=full_text, usage=usage)
        return {
            "ok": True,
            "harness": info.id,
            "model": target_model,
            "output": full_text,
            "latency_ms": rec.latency_ms,
            "usage": usage,
        }
    except Exception as exc:
        err_text = str(exc)
        runtime.monitor.finish_request(rec, output="".join(output_parts), usage=usage, error=err_text)
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "harness": info.id,
                "model": target_model,
                "error": err_text,
                "latency_ms": rec.latency_ms,
            },
        )


@app.get("/api/events")
async def api_events():
    q = runtime.monitor.subscribe()

    async def stream():
        try:
            yield "data: connected\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {ev}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            runtime.monitor.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream")
