"""Local embedding service.

Uses sentence-transformers/all-MiniLM-L6-v2 — 384 dims, ~80MB on disk, runs
on CPU in tens of milliseconds per batch. No external API call, so the
"survives docker compose restart" and "no missing env vars" requirements
both hold even when the reviewer has no internet egress from the container.

The model is cached as a module-level singleton. First call pays the load
cost (~3s); subsequent calls are fast. We pre-warm a small set of canonical
"signal" embeddings at startup so context lookups don't pay the embed cost
on the hot path.
"""
from __future__ import annotations

import logging
import threading
from typing import Iterable

from app.config import settings

log = logging.getLogger("nevup.embed")

_lock = threading.Lock()
_model = None
_canonical_embeddings: dict[str, list[float]] = {}

# Canonical signal phrases — pre-embedded at startup so GET /context lookups
# don't pay the embedding cost. Each pathology has a short natural-language
# description that's semantically close to how the coach narrates it.
CANONICAL_SIGNALS: dict[str, str] = {
    "revenge_trading": "trader re-enters quickly after a loss while emotionally activated",
    "overtrading": "trader opens many positions in rapid succession in a short window",
    "fomo_entries": "trader chases a price that has already moved, fearing they will miss out",
    "plan_non_adherence": "trader takes setups they admit are not in their written plan",
    "premature_exit": "trader closes winning positions too quickly, before the move plays out",
    "loss_running": "trader holds losing positions longer than winners, hoping for a bounce",
    "session_tilt": "trader's session quality decays as losses accumulate within the session",
    "time_of_day_bias": "trader performs significantly worse in specific hours of the day",
    "position_sizing_inconsistency": "trader sizes positions unevenly without a clear risk basis",
}


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                # Imported lazily — avoids slow import at app boot for tests
                # that don't touch embeddings.
                from sentence_transformers import SentenceTransformer

                log.info(
                    f'{{"event": "embed.load.start", "model": "{settings.embedding_model}"}}'
                )
                _model = SentenceTransformer(settings.embedding_model, device="cpu")
                log.info('{"event": "embed.load.done"}')
    return _model


def embed(text: str) -> list[float]:
    """Embed a single string. Returns a 384-float list."""
    return embed_batch([text])[0]


def embed_batch(texts: Iterable[str]) -> list[list[float]]:
    """Embed many strings in one forward pass. Faster than looping."""
    model = _get_model()
    arr = model.encode(list(texts), convert_to_numpy=True, normalize_embeddings=True)
    return [vec.tolist() for vec in arr]


def warmup() -> None:
    """Load the model and pre-compute canonical signal embeddings."""
    if _canonical_embeddings:
        return
    keys = list(CANONICAL_SIGNALS.keys())
    vecs = embed_batch([CANONICAL_SIGNALS[k] for k in keys])
    for k, v in zip(keys, vecs):
        _canonical_embeddings[k] = v
    log.info('{"event": "embed.warmup.done", "canonical": ' + str(len(keys)) + "}")


def canonical_embedding(signal_or_text: str) -> list[float]:
    """Resolve a signal name to its pre-cached embedding, else embed on-the-fly."""
    if signal_or_text in _canonical_embeddings:
        return _canonical_embeddings[signal_or_text]
    return embed(signal_or_text)
