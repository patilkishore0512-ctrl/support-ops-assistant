"""
The search index: FAISS for dense vectors, BM25 for keywords, and a JSON metadata store.

FAISS IndexFlatIP over L2-normalized vectors = exact cosine similarity. At ~800 chunks an
exact index is the right choice (sub-millisecond, no training, no recall loss). The design
note covers the switch to an approximate index (HNSW / IVF-PQ) at millions of chunks.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from rag_service.documents import Chunk
from rag_service.models import Embedder, LSAEmbedder

STOPWORDS = set("""
a an and are as at be but by can do does for from how i if in into is it its me my of on or
so that the their then there these this to was we what when where which who why will with
you your should would could
""".split())

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokenize(text: str) -> list[str]:
    """
    Lowercased word tokens without stopwords. CamelCase and snake_case identifiers are kept
    whole and also split into parts, so 'CORSMiddleware' matches 'cors middleware'.
    """
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text):
        low = raw.lower()
        if low in STOPWORDS or len(low) < 2:
            continue
        tokens.append(low)
        parts = [p.lower() for p in CAMEL_RE.findall(raw.replace("_", " ")) if len(p) > 1]
        if len(parts) > 1:
            tokens.extend(p for p in parts if p not in STOPWORDS)
    return tokens


@dataclass
class IndexInfo:
    embedder: str
    chunks: int
    documents: int
    chunk_size_words: int
    chunk_overlap_words: int
    built_at: str
    build_seconds: float


class SearchIndex:
    def __init__(self, chunks: list[Chunk], embedder: Embedder, vectors: np.ndarray, info: IndexInfo):
        self.chunks = chunks
        self.embedder = embedder
        self.info = info
        self.faiss = faiss.IndexFlatIP(vectors.shape[1])
        self.faiss.add(vectors)
        self.bm25 = BM25Okapi([tokenize(c.search_text) for c in chunks])

    # ---------- build / persist ----------
    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder, size: int, overlap: int) -> "SearchIndex":
        if not chunks:
            raise ValueError("no chunks to index")
        start = time.perf_counter()
        texts = [c.search_text for c in chunks]
        embedder.fit(texts)
        vectors = embedder.embed_documents(texts)
        info = IndexInfo(
            embedder=embedder.name,
            chunks=len(chunks),
            documents=len({c.source for c in chunks}),
            chunk_size_words=size,
            chunk_overlap_words=overlap,
            built_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            build_seconds=0.0,
        )
        index = cls(chunks, embedder, vectors, info)
        index.info.build_seconds = round(time.perf_counter() - start, 2)
        return index

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.faiss, str(directory / "vectors.faiss"))
        (directory / "chunks.json").write_text(json.dumps([c.to_dict() for c in self.chunks]), encoding="utf-8")
        (directory / "info.json").write_text(json.dumps(self.info.__dict__, indent=2), encoding="utf-8")
        self.embedder.save(directory)

    @classmethod
    def load(cls, directory: Path, embedder: Embedder) -> "SearchIndex":
        info = IndexInfo(**json.loads((directory / "info.json").read_text(encoding="utf-8")))
        if info.embedder.startswith("lsa") and not isinstance(embedder, LSAEmbedder):
            embedder = LSAEmbedder()
        if info.embedder != embedder.name:
            raise ValueError(f"index was built with {info.embedder}, current embedder is {embedder.name}; re-run /ingest")
        embedder.load(directory)
        chunks = [Chunk(**d) for d in json.loads((directory / "chunks.json").read_text(encoding="utf-8"))]
        vectors = faiss.read_index(str(directory / "vectors.faiss"))
        index = cls.__new__(cls)
        index.chunks, index.embedder, index.info, index.faiss = chunks, embedder, info, vectors
        index.bm25 = BM25Okapi([tokenize(c.search_text) for c in chunks])
        return index

    # ---------- search primitives ----------
    def dense_search(self, query: str, k: int) -> list[tuple[int, float]]:
        q = self.embedder.embed_query(query)
        scores, ids = self.faiss.search(q, min(k, len(self.chunks)))
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0]

    def bm25_search(self, query: str, k: int) -> list[tuple[int, float]]:
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        top = np.argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in top if scores[i] > 0]
