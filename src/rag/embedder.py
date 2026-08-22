"""Embedding provider — multilingual-e5-small via sentence-transformers."""
import logging
import numpy as np
from functools import lru_cache

logger = logging.getLogger(__name__)

MODEL_NAME = "intfloat/multilingual-e5-small"
EMBEDDING_DIM = 384


class Embedder:
    """Lazy-loaded sentence-transformers embedder (leaf: cannot delegate)."""

    def __init__(self, model_name: str = MODEL_NAME):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name, trust_remote_code=True)
            logger.info("Embedding model loaded.")
        return self._model

    def embed(self, texts):
        """Return list of normalized embeddings (float lists).
        
        Model is loaded on first use and unloaded after to save RAM.
        """
        if not texts:
            return []
        import gc
        import torch
        model = self._load()
        emb = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        result = [np.asarray(e, dtype="float32").tolist() for e in emb]
        # آزادسازی کامل رم (شامل PyTorch cache)
        del model
        self._model = None
        gc.collect()
        if hasattr(torch, 'cuda') and torch.cuda.is_available():
            torch.cuda.empty_cache()
        # پاکسازی internal cache sentence-transformers
        try:
            from sentence_transformers import SentenceTransformer
            SentenceTransformer._model_cache = {}
        except:
            pass
        return result

    def embed_one(self, text: str):
        result = self.embed([text])
        return result[0] if result else []

    def similarity(self, a, b) -> float:
        """Cosine similarity between two embedding lists."""
        va = np.asarray(a, dtype="float32")
        vb = np.asarray(b, dtype="float32")
        return float(np.dot(va, vb))


_embedder = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder