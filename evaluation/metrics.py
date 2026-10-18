"""
Metric functions for the evaluation. Pure functions, unit tested in tests/evaluation.

Retrieval (docs questions that have an answer)
  hit@k       1 if any expected source file appears among the top-k retrieved chunks
  recall@k    share of the expected source files that appear in the top-k
  MRR         1 / rank of the first chunk from an expected source (0 if not in the list)

Answer quality (heuristic, no LLM needed)
  correctness   share of key-fact groups found in the answer; a group is a list of
                acceptable strings, any one of which satisfies it
  faithfulness  share of answer sentences supported by the retrieved passages: a sentence
                is supported when >= 70% of its content words appear in one passage
"""

from __future__ import annotations

import re

from rag_service.index import tokenize

CITATION_RE = re.compile(r"\[\d+\]")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def hit_at_k(retrieved_sources: list[str], expected: list[str], k: int) -> float:
    return 1.0 if set(retrieved_sources[:k]) & set(expected) else 0.0


def recall_at_k(retrieved_sources: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return 0.0
    return len(set(retrieved_sources[:k]) & set(expected)) / len(set(expected))


def reciprocal_rank(retrieved_sources: list[str], expected: list[str]) -> float:
    for rank, source in enumerate(retrieved_sources, start=1):
        if source in expected:
            return 1.0 / rank
    return 0.0


def _norm(text: str) -> str:
    return re.sub(r"[`*_]", "", text).lower()


def correctness(answer: str, key_facts: list[list[str]]) -> float:
    if not key_facts:
        return 0.0
    text = _norm(answer)
    found = sum(1 for group in key_facts if any(_norm(str(s)) in text for s in group))
    return found / len(key_facts)


def answer_sentences(answer: str) -> list[str]:
    text = CITATION_RE.sub("", answer)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if len(tokenize(s)) >= 3]


def faithfulness(answer: str, passages: list[str], threshold: float = 0.7) -> float | None:
    """None when there is nothing to judge (no sentences)."""
    sentences = answer_sentences(answer)
    if not sentences:
        return None
    passage_tokens = [set(tokenize(p)) for p in passages]
    supported = 0
    for s in sentences:
        toks = set(tokenize(s))
        if toks and any(len(toks & p) / len(toks) >= threshold for p in passage_tokens):
            supported += 1
    return supported / len(sentences)


def contains_all(haystack: str, needles: list[str]) -> bool:
    text = haystack.lower()
    return all(str(n).lower() in text for n in needles)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[idx]


def mean(values: list[float]) -> float:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0
