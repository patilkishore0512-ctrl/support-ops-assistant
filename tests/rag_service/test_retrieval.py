import pytest

from rag_service.documents import load_corpus
from rag_service.index import SearchIndex, tokenize
from rag_service.models import LSAEmbedder, Reranker
from rag_service.retrieval import Filters, Retriever


@pytest.fixture(scope="module")
def retriever(kb_dir):
    chunks = load_corpus(kb_dir, size=120, overlap=20)
    return Retriever(SearchIndex.build(chunks, LSAEmbedder(), 120, 20), Reranker(), candidate_k=10)


def test_tokenize_splits_identifiers():
    toks = tokenize("Use CORSMiddleware and allow_origins")
    assert {"corsmiddleware", "cors", "middleware", "allow_origins", "allow", "origins"} <= set(toks)
    assert "and" not in toks


def test_exact_identifier_found(retriever):
    res = retriever.search("CORSMiddleware allow_origins")
    assert res.hits[0].chunk.source == "docs/tutorial/cors.md"
    assert res.mode == "hybrid" and not res.reranked


def test_semantic_question_found(retriever):
    res = retriever.search("How should passwords be stored and verified?")
    assert res.hits[0].chunk.source == "docs/tutorial/security/oauth2-jwt.md"


def test_hybrid_scores_come_from_both_lists(retriever):
    hit = retriever.search("CORSMiddleware allow_origins").hits[0]
    assert hit.dense_rank is not None and hit.bm25_rank is not None
    assert hit.rrf == pytest.approx(1 / (60 + hit.dense_rank) + 1 / (60 + hit.bm25_rank))


def test_single_method_modes(retriever):
    assert all(h.bm25 is None for h in retriever.search("cors", mode="dense").hits)
    assert all(h.dense is None for h in retriever.search("cors", mode="bm25").hits)


def test_filters(retriever):
    res = retriever.search("processes workers containers", filters=Filters(source_prefix="docs/deployment"))
    assert res.hits and {h.chunk.source for h in res.hits} == {"docs/deployment/docker.md"}
    res = retriever.search("cors", filters=Filters(updated_after="2026-01-01"))
    assert all(h.chunk.last_updated >= "2026-01-01" for h in res.hits)
    res = retriever.search("cors", filters=Filters(section_contains="corsmiddleware"))
    assert all("CORSMiddleware" in h.chunk.section for h in res.hits)


def test_confidence_separates_on_and_off_topic(retriever):
    on = retriever.search("How do I configure CORSMiddleware allowed origins?").confidence
    off = retriever.search("What is the capital of France?").confidence
    assert on > 0.5 > off


def test_index_save_and_load(kb_dir, tmp_path):
    chunks = load_corpus(kb_dir)
    built = SearchIndex.build(chunks, LSAEmbedder(), 180, 30)
    built.save(tmp_path)
    loaded = SearchIndex.load(tmp_path, LSAEmbedder())
    q = "background tasks after returning a response"
    assert built.dense_search(q, 3) == pytest.approx(loaded.dense_search(q, 3))
    assert loaded.info.chunks == len(chunks)
