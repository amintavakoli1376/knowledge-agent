"""Embedding provider — multilingual-e5-small via ONNX Runtime (no torch).

چرا ONNX؟ اندازه‌گیری روی همین سرور نشان داد ۴۲۹MB از ۹۱۶MB مصرف قبلی فقط
هزینهٔ `import torch` + `import sentence-transformers` بود. این پیاده‌سازی همان
وزن‌های e5-small را با نسخهٔ int8 رسمی (۱۱۸MB) و بدون torch اجرا می‌کند.

قواعد e5 که اینجا رعایت می‌شود:
  - هر متن باید prefix داشته باشد: "query: " برای سؤال، "passage: " برای سند.
    (کد قبلی هیچ prefix ای نمی‌زد → بخشی از کیفیت بازیابی از دست می‌رفت.)
  - mean pooling با احتساب attention mask و سپس L2 normalize.
"""
from __future__ import annotations

import logging
import os
import threading

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

logger = logging.getLogger(__name__)

_DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "models",
    "e5-small-int8",
)
MODEL_DIR = os.environ.get("EMBED_MODEL_DIR", _DEFAULT_DIR)
EMBEDDING_DIM = 384
MAX_SEQ_LEN = 512
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


class Embedder:
    """Lazy ONNX embedder (leaf: cannot delegate). یک session مشترک و thread-safe."""

    def __init__(self, model_dir: str = MODEL_DIR):
        self.model_dir = model_dir
        self._session = None
        self._tokenizer = None
        self._lock = threading.Lock()

    def _load(self):
        if self._session is not None:
            return self._session, self._tokenizer
        with self._lock:
            if self._session is not None:
                return self._session, self._tokenizer
            onnx_path = os.path.join(self.model_dir, "model.onnx")
            tok_path = os.path.join(self.model_dir, "tokenizer.json")
            if not (os.path.exists(onnx_path) and os.path.exists(tok_path)):
                raise FileNotFoundError(
                    f"مدل ONNX در {self.model_dir} پیدا نشد (model.onnx + tokenizer.json لازم است)."
                )
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = int(os.environ.get("EMBED_THREADS", "2"))
            opts.inter_op_num_threads = 1
            logger.info("Loading ONNX embedding model: %s", onnx_path)
            self._session = ort.InferenceSession(
                onnx_path, sess_options=opts, providers=["CPUExecutionProvider"]
            )
            tok = Tokenizer.from_file(tok_path)
            tok.enable_truncation(max_length=MAX_SEQ_LEN)
            tok.enable_padding(pad_id=1, pad_token="<pad>")  # XLM-R pad id
            self._tokenizer = tok
            logger.info("ONNX embedding model loaded.")
        return self._session, self._tokenizer

    def _encode(self, texts):
        session, tok = self._load()
        encs = tok.encode_batch([t if t is not None else "" for t in texts])
        input_ids = np.asarray([e.ids for e in encs], dtype=np.int64)
        attention = np.asarray([e.attention_mask for e in encs], dtype=np.int64)
        feeds = {"input_ids": input_ids, "attention_mask": attention}
        if any(i.name == "token_type_ids" for i in session.get_inputs()):
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        hidden = session.run(None, feeds)[0]

        mask = attention[..., None].astype(np.float32)
        summed = (hidden * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), 1e-9, None)
        vectors = summed / counts
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.clip(norms, 1e-12, None)
        return vectors.astype(np.float32).tolist()

    # ---------- public API ----------
    def embed_passages(self, texts):
        """برای ایندکس کردن اسناد — prefix «passage: » اضافه می‌شود."""
        if not texts:
            return []
        return self._encode([PASSAGE_PREFIX + (t or "") for t in texts])

    def embed_query(self, text):
        """برای سؤال کاربر — prefix «query: » اضافه می‌شود."""
        vecs = self._encode([QUERY_PREFIX + (text or "")])
        return vecs[0] if vecs else []

    def embed(self, texts):
        """بدون prefix — فقط برای مقایسهٔ خام و اسکریپت‌های مهاجرت."""
        if not texts:
            return []
        return self._encode(list(texts))

    def embed_one(self, text):
        result = self.embed([text])
        return result[0] if result else []

    def similarity(self, a, b) -> float:
        va = np.asarray(a, dtype="float32")
        vb = np.asarray(b, dtype="float32")
        return float(np.dot(va, vb))


_embedder = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
