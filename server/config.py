"""Persistent runtime configuration for harness-proxy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


class ProxyConfig(BaseModel):
    """Configurable proxy routing and execution settings."""

    active_harness: str = Field(
        default="auto",
        description="Selected harness ('auto', 'opencode', 'agy', 'claude').",
    )
    active_model: str = Field(
        default="",
        description="Selected model ID for the active harness (empty = harness default).",
    )
    override_client_model: bool = Field(
        default=True,
        description="When true, ignore client model (e.g. gpt-5-nano) unless prefixed with 'harness:'.",
    )
    timeout_seconds: int = Field(default=180, ge=10, le=1800)
    max_concurrency: int = Field(default=2, ge=1, le=16)


def load_config(path: Path = CONFIG_PATH) -> ProxyConfig:
    """Load persisted configuration or return defaults."""
    if not path.exists():
        return ProxyConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ProxyConfig.model_validate(data)
    except Exception:
        return ProxyConfig()


def save_config(cfg: ProxyConfig, path: Path = CONFIG_PATH) -> None:
    """Persist configuration to disk atomically."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(cfg.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def update_config(
    current: ProxyConfig,
    *,
    active_harness: Optional[str] = None,
    active_model: Optional[str] = None,
    override_client_model: Optional[bool] = None,
    timeout_seconds: Optional[int] = None,
    max_concurrency: Optional[int] = None,
) -> ProxyConfig:
    """Apply partial updates and persist them."""
    updates = {}
    if active_harness is not None:
        updates["active_harness"] = active_harness
    if active_model is not None:
        updates["active_model"] = active_model
    if override_client_model is not None:
        updates["override_client_model"] = override_client_model
    if timeout_seconds is not None:
        updates["timeout_seconds"] = timeout_seconds
    if max_concurrency is not None:
        updates["max_concurrency"] = max_concurrency
    updated = current.model_copy(update=updates)
    save_config(updated)
    return updated
