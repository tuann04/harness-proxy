"""Local CPU embeddings via fastembed."""

from __future__ import annotations

import asyncio
import threading
from typing import List, Optional, Sequence, Union

import numpy as np

DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"

_lock = threading.Lock()
_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        with _lock:
            if _embedder is None:
                from fastembed import TextEmbedding

                _embedder = TextEmbedding(model_name=DEFAULT_EMBED_MODEL)
    return _embedder


def _embed_sync(texts: List[str], dimensions: Optional[int]) -> List[List[float]]:
    model = _get_embedder()
    vectors = list(model.embed(texts))
    result: List[List[float]] = []
    for vec in vectors:
        arr = np.asarray(vec, dtype=np.float32)
        if dimensions and 0 < dimensions < arr.shape[0]:
            arr = arr[:dimensions]
            norm = float(np.linalg.norm(arr))
            if norm > 0:
                arr = arr / norm
        result.append(arr.tolist())
    return result


async def compute_embeddings(
    inputs: Union[str, Sequence[Union[str, int, Sequence[int]]]],
    dimensions: Optional[int] = None,
) -> List[List[float]]:
    """Compute normalized embedding vectors on a worker thread."""
    if isinstance(inputs, str):
        texts = [inputs]
    else:
        texts = [item if isinstance(item, str) else str(item) for item in inputs]
    return await asyncio.to_thread(_embed_sync, texts, dimensions)
