"""Harness auto-detection and streaming CLI execution adapters."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

SANDBOX_DIR = Path("/tmp/harness-proxy-sandbox")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")
SUBPROCESS_LIMIT = 16 * 1024 * 1024  # 16 MiB per stream line


class HarnessError(RuntimeError):
    """Raised when a harness fails or returns an error payload."""


@dataclass
class ModelInfo:
    id: str
    label: str
    harness: str


@dataclass
class HarnessInfo:
    id: str
    name: str
    binary: Optional[str]
    available: bool
    default_model: str
    models: List[ModelInfo] = field(default_factory=list)
    detect_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "binary": self.binary,
            "available": self.available,
            "default_model": self.default_model,
            "models": [asdict(m) for m in self.models],
            "detect_error": self.detect_error,
        }


@dataclass
class StreamChunk:
    delta: str = ""
    usage: Optional[Dict[str, Any]] = None


def ensure_sandbox_dir() -> Path:
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    return SANDBOX_DIR


@contextmanager
def request_workdir() -> Iterator[Path]:
    """Create a fresh empty directory per request and remove it afterwards."""
    base = ensure_sandbox_dir()
    workdir = Path(tempfile.mkdtemp(prefix="req_", dir=str(base)))
    try:
        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _delete_opencode_session(binary: str, session_id: str) -> None:
    """Best-effort cleanup of the ephemeral OpenCode session."""
    proc = await asyncio.create_subprocess_exec(
        binary,
        "session",
        "delete",
        session_id,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=10.0)
    except Exception:
        await _kill_proc(proc)


async def _run_discovery_cmd(cmd: List[str], timeout: float = 15.0) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        limit=SUBPROCESS_LIMIT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise HarnessError(f"Discovery command timed out: {' '.join(cmd)}") from exc
    text = stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise HarnessError(f"Exit {proc.returncode}: {text.strip()[:300]}")
    return text


async def _detect_opencode() -> HarnessInfo:
    binary = shutil.which("opencode")
    if not binary:
        return HarnessInfo(
            id="opencode",
            name="OpenCode",
            binary=None,
            available=False,
            default_model="",
        )
    models: List[ModelInfo] = []
    err: Optional[str] = None
    try:
        out = await _run_discovery_cmd([binary, "models"])
        for raw in out.splitlines():
            line = ANSI_RE.sub("", raw).strip()
            if not line or "/" not in line or " " in line:
                continue
            models.append(ModelInfo(id=line, label=line, harness="opencode"))
    except Exception as exc:
        err = str(exc)

    preferred = [
        "opencode/minimax-m3.1-flash-free",
        "opencode/deepseek-v4.1-flash-free",
        "opencode/glm-5.3-flash-free",
    ]
    model_ids = {m.id for m in models}
    default_model = next((m for m in preferred if m in model_ids), "")
    if not default_model and models:
        free_models = [m.id for m in models if m.id.endswith("-free")]
        default_model = free_models[0] if free_models else models[0].id

    return HarnessInfo(
        id="opencode",
        name="OpenCode",
        binary=binary,
        available=True,
        default_model=default_model,
        models=models,
        detect_error=err,
    )


async def _detect_agy() -> HarnessInfo:
    binary = shutil.which("agy")
    if not binary:
        return HarnessInfo(
            id="agy",
            name="Antigravity (agy)",
            binary=None,
            available=False,
            default_model="",
        )
    models: List[ModelInfo] = []
    err: Optional[str] = None
    try:
        out = await _run_discovery_cmd([binary, "models"])
        for raw in out.splitlines():
            cleaned = ANSI_RE.sub("", raw).strip()
            if not cleaned or "Fetching available models" in cleaned:
                continue
            parts = cleaned.split(None, 1)
            if not parts:
                continue
            model_id = parts[0]
            if not re.match(r"^[a-zA-Z0-9._-]+$", model_id):
                continue
            label = parts[1].strip() if len(parts) > 1 else model_id
            models.append(ModelInfo(id=model_id, label=label, harness="agy"))
    except Exception as exc:
        err = str(exc)

    preferred = ["gemini-3.8-flash-low", "gemini-3.8-flash-medium", "gemini-4-hydrogen-medium"]
    model_ids = {m.id for m in models}
    default_model = next((m for m in preferred if m in model_ids), "")
    if not default_model and models:
        default_model = models[0].id

    return HarnessInfo(
        id="agy",
        name="Antigravity (agy)",
        binary=binary,
        available=True,
        default_model=default_model,
        models=models,
        detect_error=err,
    )


async def _detect_claude() -> HarnessInfo:
    binary = shutil.which("claude")
    if not binary:
        return HarnessInfo(
            id="claude",
            name="Claude Code",
            binary=None,
            available=False,
            default_model="",
        )
    models = [
        ModelInfo(id="sonnet", label="Claude Sonnet (alias)", harness="claude"),
        ModelInfo(id="haiku", label="Claude Haiku (alias)", harness="claude"),
        ModelInfo(id="opus", label="Claude Opus (alias)", harness="claude"),
    ]
    return HarnessInfo(
        id="claude",
        name="Claude Code",
        binary=binary,
        available=True,
        default_model="sonnet",
        models=models,
    )


async def detect_all_harnesses() -> Dict[str, HarnessInfo]:
    """Discover installed harnesses and their available models concurrently."""
    ensure_sandbox_dir()
    results = await asyncio.gather(
        _detect_opencode(),
        _detect_agy(),
        _detect_claude(),
    )
    return {info.id: info for info in results}


async def _iter_lines_with_timeout(
    proc: asyncio.subprocess.Process,
    timeout: float,
) -> AsyncIterator[str]:
    assert proc.stdout is not None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise HarnessError(f"Harness timed out after {int(timeout)}s")
        raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
        if not raw:
            break
        yield raw.decode("utf-8", errors="replace").strip()


async def _kill_proc(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()


async def stream_opencode(
    binary: str,
    model: str,
    prompt: str,
    timeout: float,
) -> AsyncIterator[StreamChunk]:
    cmd = [binary, "run", "--format", "json"]
    if model:
        cmd.extend(["--model", model])

    with request_workdir() as workdir:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir),
            limit=SUBPROCESS_LIMIT,
        )
        seen_part_len: Dict[str, int] = {}
        usage: Optional[Dict[str, Any]] = None
        session_id: Optional[str] = None
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            async for line in _iter_lines_with_timeout(proc, timeout):
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not session_id and isinstance(ev.get("sessionID"), str):
                    session_id = ev["sessionID"]
                ev_type = ev.get("type")
                if ev_type == "error":
                    err_obj = ev.get("error") or {}
                    msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
                    raise HarnessError(msg or json.dumps(ev))
                if ev_type == "text":
                    part = ev.get("part") or {}
                    part_id = part.get("id") or "default"
                    text = part.get("text") or ""
                    prev = seen_part_len.get(part_id, 0)
                    if len(text) > prev and text.startswith(text[:prev]):
                        delta = text[prev:]
                        seen_part_len[part_id] = len(text)
                    else:
                        delta = text
                        seen_part_len[part_id] = prev + len(text)
                    if delta:
                        yield StreamChunk(delta=delta)
                elif ev_type == "step_finish":
                    part = ev.get("part") or {}
                    if isinstance(part.get("tokens"), dict):
                        usage = part["tokens"]

            rc = await proc.wait()
            if rc != 0:
                stderr_bytes = await proc.stderr.read() if proc.stderr else b""
                err_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
                raise HarnessError(err_msg or f"opencode exited with code {rc}")
            if usage:
                yield StreamChunk(usage=usage)
        finally:
            await _kill_proc(proc)
            if session_id:
                await _delete_opencode_session(binary, session_id)


async def stream_agy(
    binary: str,
    model: str,
    prompt: str,
    timeout: float,
) -> AsyncIterator[StreamChunk]:
    with request_workdir() as workdir:
        prompt_arg = prompt
        # Keep well below Linux MAX_ARG_STRLEN (128 KiB) for a single argv element.
        if len(prompt.encode("utf-8")) > 110_000:
            fd, tmp_name = tempfile.mkstemp(prefix="prompt_", suffix=".txt", dir=str(workdir))
            os.close(fd)
            temp_file = Path(tmp_name)
            temp_file.write_text(prompt, encoding="utf-8")
            prompt_arg = (
                f"Read {temp_file} using view_file and respond directly to the prompt inside it."
            )

        cmd = [
            binary,
            "--new-project",
            "--output-format",
            "stream-json",
            "--disable-slash-commands",
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append(f"-p={prompt_arg}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir),
            limit=SUBPROCESS_LIMIT,
        )
        streamed_any = False
        usage: Optional[Dict[str, Any]] = None
        try:
            async for line in _iter_lines_with_timeout(proc, timeout):
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev_name = ev.get("event")
                if ev_name == "step_update":
                    su = ev.get("step_update") or {}
                    if su.get("step_type") == "agent_response" and "text_delta" in su:
                        delta = su.get("text_delta") or ""
                        if delta:
                            streamed_any = True
                            yield StreamChunk(delta=delta)
                elif ev_name == "result":
                    res = ev.get("result") or {}
                    if res.get("status") != "SUCCESS":
                        raise HarnessError(
                            res.get("error") or res.get("response") or f"agy status={res.get('status')}"
                        )
                    usage = res.get("usage")
                    if not streamed_any and res.get("response"):
                        yield StreamChunk(delta=res["response"])

            rc = await proc.wait()
            if rc != 0:
                stderr_bytes = await proc.stderr.read() if proc.stderr else b""
                err_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
                raise HarnessError(err_msg or f"agy exited with code {rc}")
            if usage:
                yield StreamChunk(usage=usage)
        finally:
            await _kill_proc(proc)


async def stream_claude(
    binary: str,
    model: str,
    prompt: str,
    timeout: float,
) -> AsyncIterator[StreamChunk]:
    cmd = [
        binary,
        "-p",
        "--tools",
        "",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if model:
        cmd.extend(["--model", model])

    with request_workdir() as workdir:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir),
            limit=SUBPROCESS_LIMIT,
        )
        streamed_any = False
        usage: Optional[Dict[str, Any]] = None
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            async for line in _iter_lines_with_timeout(proc, timeout):
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev_type = ev.get("type")
                if ev_type == "stream_event":
                    inner = ev.get("event") or {}
                    if inner.get("type") == "content_block_delta":
                        delta_obj = inner.get("delta") or {}
                        if delta_obj.get("type") == "text_delta":
                            delta = delta_obj.get("text") or ""
                            if delta:
                                streamed_any = True
                                yield StreamChunk(delta=delta)
                elif ev_type == "assistant" and not streamed_any:
                    msg = ev.get("message") or {}
                    for block in msg.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text") or ""
                            if text:
                                streamed_any = True
                                yield StreamChunk(delta=text)
                elif ev_type == "result":
                    if ev.get("is_error"):
                        raise HarnessError(
                            ev.get("result") or ev.get("api_error") or "claude returned an error"
                        )
                    usage = ev.get("usage")
                    if not streamed_any and ev.get("result"):
                        yield StreamChunk(delta=ev["result"])

            rc = await proc.wait()
            if rc != 0:
                stderr_bytes = await proc.stderr.read() if proc.stderr else b""
                err_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
                raise HarnessError(err_msg or f"claude exited with code {rc}")
            if usage:
                yield StreamChunk(usage=usage)
        finally:
            await _kill_proc(proc)


async def stream_harness(
    harness_info: HarnessInfo,
    model: str,
    prompt: str,
    timeout: float,
) -> AsyncIterator[StreamChunk]:
    """Dispatch streaming execution to the selected harness."""
    if not harness_info.available or not harness_info.binary:
        raise HarnessError(f"Harness '{harness_info.id}' is not installed")

    if harness_info.id == "opencode":
        async for chunk in stream_opencode(harness_info.binary, model, prompt, timeout):
            yield chunk
    elif harness_info.id == "agy":
        async for chunk in stream_agy(harness_info.binary, model, prompt, timeout):
            yield chunk
    elif harness_info.id == "claude":
        async for chunk in stream_claude(harness_info.binary, model, prompt, timeout):
            yield chunk
    else:
        raise HarnessError(f"Unsupported harness: {harness_info.id}")


def format_messages_as_prompt(messages: List[Dict[str, Any]]) -> str:
    """Convert OpenAI chat messages array into a single clean prompt string."""
    if len(messages) == 1 and messages[0].get("role") == "user":
        content = messages[0].get("content", "")
        return _extract_text(content)

    parts: List[str] = []
    for msg in messages:
        role = (msg.get("role") or "user").upper()
        text = _extract_text(msg.get("content", ""))
        if not text:
            continue
        if role == "SYSTEM":
            parts.append(f"[SYSTEM INSTRUCTIONS]\n{text}")
        elif role == "ASSISTANT":
            parts.append(f"[ASSISTANT]\n{text}")
        else:
            parts.append(f"[USER]\n{text}")
    return "\n\n".join(parts)


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text", "")))
            elif isinstance(item, str):
                chunks.append(item)
        return "\n".join(chunks)
    return str(content or "")
