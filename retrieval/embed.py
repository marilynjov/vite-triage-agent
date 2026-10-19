"""Embeddings, local and free. ~7 chunks/s measured; the whole corpus in ~166s."""
from __future__ import annotations

from ollama import Client

from . import config as R

_client = Client(host=R.OLLAMA_HOST)


def _vectors(response) -> list[list[float]]:
    return response["embeddings"] if isinstance(response, dict) else response.embeddings


def embed_documents(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), R.EMBED_BATCH):
        batch = [R.DOC_PREFIX + t for t in texts[i : i + R.EMBED_BATCH]]
        out.extend(_vectors(_client.embed(model=R.EMBED_MODEL, input=batch)))
    return out


def embed_query(text: str) -> list[float]:
    """Queries take a different prefix than documents. Same model, same space."""
    return _vectors(_client.embed(model=R.EMBED_MODEL, input=[R.QUERY_PREFIX + text]))[0]
