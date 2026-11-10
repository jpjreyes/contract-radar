"""Local embeddings and reranker (decisions D2, D3): free, private, no API key. Models download once to the
Hugging Face cache.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import torch

EMBED_MODEL = "BAAI/bge-small-en-v1.5"   # 384 dims, strong for its size
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # bge v1.5 query instruction
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


def _device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


@lru_cache(maxsize=1)
def embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL, device=_device())


@lru_cache(maxsize=1)
def reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(RERANK_MODEL, device=_device())


def embed_passages(texts: list[str], batch_size: int = 64) -> np.ndarray:
    return embedder().encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)


def embed_query(text: str) -> np.ndarray:
    return embedder().encode([QUERY_PREFIX + text], normalize_embeddings=True, show_progress_bar=False)[0]


def rerank_scores(query: str, passages: list[str]) -> list[float]:
    if not passages:
        return []
    return [float(s) for s in reranker().predict([(query, p) for p in passages], show_progress_bar=False)]
