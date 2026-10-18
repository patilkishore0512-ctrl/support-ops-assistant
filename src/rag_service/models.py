"""
Embedding and re-ranking backends.

Embeddings
  sentence-transformers  BAAI/bge-small-en-v1.5 (default when installed and downloadable).
                         Small (33M params), strong on retrieval benchmarks, runs on CPU.
  lsa                    TF-IDF (1-2 grams) + truncated SVD, fitted on the corpus itself.
                         Fully offline and deterministic. Used when no model can be loaded
                         (air-gapped machines, CI) so the service always starts.

Re-ranking
  cross-encoder          cross-encoder/ms-marco-MiniLM-L-6-v2 scores (question, passage)
                         pairs jointly, which is more accurate than comparing two separate
                         embeddings. Its score also drives the confidence signal.
  none                   keep the hybrid (RRF) order.

"auto" picks the model backend if it loads and falls back otherwise, logging which one is used.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("rag.models")

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _normalize(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (x / norms).astype("float32")


class Embedder:
    name = "base"

    def fit(self, texts: list[str]) -> None:  # only LSA needs fitting
        pass

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        raise NotImplementedError

    def save(self, directory: Path) -> None:
        pass

    def load(self, directory: Path) -> None:
        pass


class SentenceTransformerEmbedder(Embedder):
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.name = f"sentence-transformers:{model_name}"
        self.model = SentenceTransformer(model_name, device="cpu")
        self.query_prefix = BGE_QUERY_PREFIX if "bge" in model_name.lower() else ""

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype="float32")

    def embed_query(self, text: str) -> np.ndarray:
        vec = self.model.encode([self.query_prefix + text], normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vec, dtype="float32")


class LSAEmbedder(Embedder):
    name = "lsa:tfidf-svd"

    def __init__(self, dims: int = 256):
        self.dims = dims
        self.vectorizer = None
        self.svd = None

    def fit(self, texts: list[str]) -> None:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.vectorizer = TfidfVectorizer(
            sublinear_tf=True, ngram_range=(1, 2), min_df=1, stop_words="english",
            token_pattern=r"(?u)\b\w[\w\-]*\b",
        )
        tfidf = self.vectorizer.fit_transform(texts)
        dims = max(2, min(self.dims, tfidf.shape[0] - 1, tfidf.shape[1] - 1))
        self.svd = TruncatedSVD(n_components=dims, random_state=42)
        self.svd.fit(tfidf)

    def _transform(self, texts: list[str]) -> np.ndarray:
        return _normalize(self.svd.transform(self.vectorizer.transform(texts)))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._transform(texts)

    def embed_query(self, text: str) -> np.ndarray:
        return self._transform([text])

    def save(self, directory: Path) -> None:
        import joblib

        joblib.dump({"vectorizer": self.vectorizer, "svd": self.svd}, directory / "lsa.joblib")

    def load(self, directory: Path) -> None:
        import joblib

        state = joblib.load(directory / "lsa.joblib")
        self.vectorizer, self.svd = state["vectorizer"], state["svd"]


def make_embedder(backend: str, model_name: str) -> Embedder:
    if backend in ("auto", "sentence-transformers"):
        try:
            emb = SentenceTransformerEmbedder(model_name)
            log.info("embedding backend: %s", emb.name)
            return emb
        except Exception as exc:  # not installed, or model download blocked
            if backend == "sentence-transformers":
                raise
            log.warning("sentence-transformers unavailable (%s); using offline LSA embeddings", type(exc).__name__)
    log.info("embedding backend: %s", LSAEmbedder.name)
    return LSAEmbedder()


class Reranker:
    name = "none"

    def score(self, query: str, passages: list[str]) -> np.ndarray | None:
        return None


class CrossEncoderReranker(Reranker):
    def __init__(self, model_name: str):
        from sentence_transformers import CrossEncoder

        self.name = f"cross-encoder:{model_name}"
        self.model = CrossEncoder(model_name, device="cpu")

    def score(self, query: str, passages: list[str]) -> np.ndarray:
        # raw logits; confidence later applies a sigmoid
        return np.asarray(self.model.predict([(query, p) for p in passages], show_progress_bar=False), dtype="float32")


def make_reranker(backend: str, model_name: str) -> Reranker:
    if backend in ("auto", "cross-encoder"):
        try:
            rr = CrossEncoderReranker(model_name)
            log.info("reranker: %s", rr.name)
            return rr
        except Exception as exc:
            if backend == "cross-encoder":
                raise
            log.warning("cross-encoder unavailable (%s); re-ranking disabled", type(exc).__name__)
    return Reranker()
