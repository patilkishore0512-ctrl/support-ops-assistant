"""
Retrieval: hybrid search -> metadata filter -> optional re-rank -> confidence.

Why hybrid
  Dense vectors match meaning ("let browsers call my API from another site" -> CORS) but
  miss exact identifiers. BM25 matches exact terms (`CORSMiddleware`, `OAuth2PasswordBearer`,
  `--workers`) but misses paraphrases. Support questions contain both kinds, so both run
  and their rankings are merged with Reciprocal Rank Fusion:

      rrf(d) = sum over lists of 1 / (k + rank(d))      (k = 60)

  RRF uses ranks only, so the two very different score scales never need calibrating.

Why re-rank
  A cross-encoder reads question and passage together, which separates "mentions the
  words" from "answers the question". It runs only on the ~20 fused candidates, so the
  cost stays small. Its score is also the best confidence signal we have.

Metadata filters
  Optional `source_prefix` (e.g. "fastapi/deployment"), `section_contains`, and
  `updated_after` (YYYY-MM-DD) narrow the candidate set before fusion.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from rag_service.documents import Chunk
from rag_service.index import SearchIndex, tokenize
from rag_service.models import Reranker


@dataclass
class Filters:
    source_prefix: str | None = None
    section_contains: str | None = None
    updated_after: str | None = None

    def matches(self, c: Chunk) -> bool:
        if self.source_prefix and not c.source.startswith(self.source_prefix):
            return False
        if self.section_contains and self.section_contains.lower() not in f"{c.title} {c.section}".lower():
            return False
        if self.updated_after and (not c.last_updated or c.last_updated < self.updated_after):
            return False
        return True

    @property
    def active(self) -> bool:
        return any((self.source_prefix, self.section_contains, self.updated_after))


@dataclass
class Hit:
    chunk: Chunk
    rrf: float
    dense: float | None = None
    bm25: float | None = None
    rerank: float | None = None
    dense_rank: int | None = None
    bm25_rank: int | None = None


@dataclass
class RetrievalResult:
    hits: list[Hit]
    confidence: float
    mode: str
    reranked: bool
    timings_ms: dict = field(default_factory=dict)


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def term_coverage(query: str, text: str) -> float:
    """Share of the question's content words that appear in the passage."""
    q = set(tokenize(query))
    if not q:
        return 0.0
    return len(q & set(tokenize(text))) / len(q)


class Retriever:
    def __init__(self, index: SearchIndex, reranker: Reranker, candidate_k: int = 20, rrf_k: int = 60):
        self.index = index
        self.reranker = reranker
        self.candidate_k = candidate_k
        self.rrf_k = rrf_k

    @property
    def can_rerank(self) -> bool:
        return self.reranker.name != "none"

    def search(self, query: str, top_k: int = 5, filters: Filters | None = None,
               mode: str = "hybrid", rerank: bool = True) -> RetrievalResult:
        """mode: hybrid | dense | bm25 (the single-method modes exist for the Part C experiment)."""
        filters = filters or Filters()
        timings: dict[str, float] = {}
        # with a filter, score every chunk so the filter cannot starve the candidate list
        pool = len(self.index.chunks) if filters.active else self.candidate_k

        t = time.perf_counter()
        dense = self.index.dense_search(query, pool) if mode in ("hybrid", "dense") else []
        timings["dense"] = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        bm25 = self.index.bm25_search(query, pool) if mode in ("hybrid", "bm25") else []
        timings["bm25"] = (time.perf_counter() - t) * 1000

        if filters.active:
            dense = [(i, s) for i, s in dense if filters.matches(self.index.chunks[i])][: self.candidate_k]
            bm25 = [(i, s) for i, s in bm25 if filters.matches(self.index.chunks[i])][: self.candidate_k]

        hits: dict[int, Hit] = {}
        for rank, (i, s) in enumerate(dense, start=1):
            h = hits.setdefault(i, Hit(self.index.chunks[i], 0.0))
            h.dense, h.dense_rank = s, rank
            h.rrf += 1.0 / (self.rrf_k + rank)
        for rank, (i, s) in enumerate(bm25, start=1):
            h = hits.setdefault(i, Hit(self.index.chunks[i], 0.0))
            h.bm25, h.bm25_rank = s, rank
            h.rrf += 1.0 / (self.rrf_k + rank)

        candidates = sorted(hits.values(), key=lambda h: h.rrf, reverse=True)[: self.candidate_k]

        reranked = False
        if rerank and self.can_rerank and candidates:
            t = time.perf_counter()
            scores = self.reranker.score(query, [h.chunk.search_text for h in candidates])
            for h, s in zip(candidates, scores):
                h.rerank = float(s)
            candidates.sort(key=lambda h: h.rerank, reverse=True)
            timings["rerank"] = (time.perf_counter() - t) * 1000
            reranked = True

        top = candidates[:top_k]
        return RetrievalResult(top, self.confidence(query, top, reranked, mode), mode, reranked,
                               {k: round(v, 2) for k, v in timings.items()})

    @staticmethod
    def confidence(query: str, hits: list[Hit], reranked: bool, mode: str = "hybrid") -> float:
        """
        0..1 estimate that the top passage answers the question.
          with a cross-encoder : 0.5 * sigmoid(top logit) + 0.5 * the heuristic below.
                                 The cross-encoder ranks very well, but its raw score is low for
                                 correct passages when the question is phrased in everyday words
                                 (it was trained on web search queries). Blending keeps its
                                 ranking power while the retrieval evidence prevents false
                                 "I don't know" answers (Part C, experiment 1).
          without              : weighted evidence from the top hit -
                                 0.45 * share of question words found in the passage
                                 0.35 * dense cosine similarity          (if dense search ran)
                                 0.20 * agreement: top hit in both the   (hybrid only)
                                        dense and BM25 top 5
                                 weights are renormalised over the signals the mode can produce,
                                 so single-method modes are not penalised for a missing signal.
        The weights are hand-set; the abstain threshold is chosen with the Part C sweep.
        """
        if not hits:
            return 0.0
        top = hits[0]
        signals = [(0.45, term_coverage(query, top.chunk.search_text))]
        if mode in ("hybrid", "dense"):
            signals.append((0.35, max(top.dense or 0.0, 0.0)))
        if mode == "hybrid":
            signals.append((0.20, 1.0 if (top.dense_rank or 99) <= 5 and (top.bm25_rank or 99) <= 5 else 0.0))
        total = sum(w for w, _ in signals)
        heuristic = sum(w * v for w, v in signals) / total
        if reranked and top.rerank is not None:
            return round(0.5 * sigmoid(top.rerank) + 0.5 * heuristic, 3)
        return round(heuristic, 3)
