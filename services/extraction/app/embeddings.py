import logging
import os
from functools import lru_cache

from sentence_transformers import SentenceTransformer

logger = logging.getLogger("extraction.embeddings")

_MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "intfloat/e5-large-v2")


@lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    logger.info("Loading embedding model %s", _MODEL_NAME)
    m = SentenceTransformer(_MODEL_NAME)
    logger.info("Embedding model ready (dims=%d)", m.get_sentence_embedding_dimension())
    return m


def embed_passage(text: str) -> list[float]:
    """Embed a document chunk. e5 models require the 'passage: ' prefix."""
    vec = _model().encode(f"passage: {text}", normalize_embeddings=True)
    return vec.tolist()


def embed_passages_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple document chunks in one model forward pass."""
    prefixed = [f"passage: {t}" for t in texts]
    vecs = _model().encode(prefixed, normalize_embeddings=True)
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> list[float]:
    """Embed a search query. e5 models require the 'query: ' prefix."""
    vec = _model().encode(f"query: {text}", normalize_embeddings=True)
    return vec.tolist()


def warmup() -> None:
    """Load the model into memory at startup so the first request is not slow."""
    _model()
