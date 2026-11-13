"""In-memory monitoring state and SSE event broadcaster."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class RequestRecord:
    """Tracked lifecycle of a single completion request."""

    id: str
    started_at: float
    harness: str
    model: str
    requested_model: str
    stream: bool
    status: str = "running"  # running | ok | error
    latency_ms: Optional[int] = None
    input_chars: int = 0
    output_chars: int = 0
    usage: Dict[str, Any] = field(default_factory=dict)
    prompt_preview: str = ""
    response_preview: str = ""
    error: Optional[str] = None


class MonitorState:
    """Tracks active requests, recent history, metrics, and SSE subscribers."""

    def __init__(self, max_history: int = 100) -> None:
        self.active: Dict[str, RequestRecord] = {}
        self.history: deque[RequestRecord] = deque(maxlen=max_history)
        self.total_requests: int = 0
        self.success_count: int = 0
        self.error_count: int = 0
        self.total_latency_ms: int = 0
        self._subscribers: Set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subscribers.discard(q)

    def notify(self, event: str = "update") -> None:
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def start_request(
        self,
        *,
        harness: str,
        model: str,
        requested_model: str,
        stream: bool,
        prompt: str,
    ) -> RequestRecord:
        rec = RequestRecord(
            id=uuid.uuid4().hex[:10],
            started_at=time.time(),
            harness=harness,
            model=model,
            requested_model=requested_model,
            stream=stream,
            input_chars=len(prompt),
            prompt_preview=prompt[:240],
        )
        self.total_requests += 1
        self.active[rec.id] = rec
        self.notify("request_start")
        return rec

    def finish_request(
        self,
        rec: RequestRecord,
        *,
        output: str = "",
        usage: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        rec.latency_ms = int((time.time() - rec.started_at) * 1000)
        rec.output_chars = len(output)
        rec.response_preview = output[:300]
        if usage:
            rec.usage = usage
        if error:
            rec.status = "error"
            rec.error = error
            self.error_count += 1
        else:
            rec.status = "ok"
            self.success_count += 1
        self.total_latency_ms += rec.latency_ms
        self.active.pop(rec.id, None)
        self.history.appendleft(rec)
        self.notify("request_end")

    def snapshot(self) -> Dict[str, Any]:
        completed = self.success_count + self.error_count
        avg_latency_ms = int(self.total_latency_ms / completed) if completed else 0
        return {
            "metrics": {
                "total_requests": self.total_requests,
                "active_count": len(self.active),
                "success_count": self.success_count,
                "error_count": self.error_count,
                "avg_latency_ms": avg_latency_ms,
            },
            "active": [asdict(r) for r in self.active.values()],
            "history": [asdict(r) for r in self.history],
        }
