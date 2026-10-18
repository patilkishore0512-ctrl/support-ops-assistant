"""
The assistant: ingest the knowledge base, and answer questions from docs or ticket data.

/ask flow
  validate -> route
    docs:    hybrid retrieve -> confidence check -> injection filter -> LLM answer -> citations
    tickets: write SQL -> guard -> read-only execute -> summarize -> SQL as the citation
  Abstains ("I don't know") when confidence is below MIN_CONFIDENCE, when every relevant
  passage was filtered, when the LLM itself says it cannot answer, or when no safe SQL exists.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field

from rag_service.config import Settings
from rag_service.documents import load_corpus
from rag_service.guardrails import detect_injection, validate_question, wrap_document
from rag_service.index import SearchIndex
from rag_service.llm import IDK, make_llm
from rag_service.models import make_embedder, make_reranker
from rag_service.retrieval import Filters, RetrievalResult, Retriever
from rag_service.router import route
from rag_service.sql_tool import SCHEMA_DOC, SQLGuardError, execute_readonly, guard_sql

log = logging.getLogger("rag.service")

PASSAGE_CHARS = 600


@dataclass
class Citation:
    source: str
    section: str
    passage: str
    url: str = ""
    last_updated: str = ""
    score: float | None = None


@dataclass
class Answer:
    question: str
    answer: str
    route: str
    abstained: bool
    confidence: float
    citations: list[Citation] = field(default_factory=list)
    sql: str | None = None
    rows: list[list] | None = None
    columns: list[str] | None = None
    warnings: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    debug: dict = field(default_factory=dict)


class NotIngestedError(RuntimeError):
    pass


class Assistant:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm = make_llm(settings.llm_provider, settings.llm_model, settings.llm_timeout_s)
        self._embedder = None
        self._reranker = None
        self.retriever: Retriever | None = None
        self._lock = threading.Lock()
        self._try_load_index()

    # ---------- components ----------
    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = make_embedder(self.settings.embedding_backend, self.settings.embedding_model)
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = make_reranker(self.settings.reranker_backend, self.settings.reranker_model)
        return self._reranker

    def _try_load_index(self) -> None:
        if not (self.settings.index_dir / "info.json").exists():
            log.info("no index at %s yet; call POST /ingest", self.settings.index_dir)
            return
        try:
            index = SearchIndex.load(self.settings.index_dir, self.embedder)
            self.retriever = Retriever(index, self.reranker, self.settings.candidate_k, self.settings.rrf_k)
            log.info("loaded index: %s chunks from %s documents", index.info.chunks, index.info.documents)
        except Exception as exc:
            log.warning("could not load index (%s); call POST /ingest", exc)

    # ---------- ingest ----------
    def ingest(self, chunk_size: int | None = None, overlap: int | None = None) -> dict:
        size = chunk_size or self.settings.chunk_size_words
        over = self.settings.chunk_overlap_words if overlap is None else overlap
        if not self.settings.kb_dir.exists():
            raise FileNotFoundError(f"knowledge base folder not found: {self.settings.kb_dir}")
        with self._lock:
            chunks = load_corpus(self.settings.kb_dir, size, over)
            flagged = [c.chunk_id for c in chunks if detect_injection(c.text)]
            index = SearchIndex.build(chunks, self.embedder, size, over)
            index.save(self.settings.index_dir)
            self.retriever = Retriever(index, self.reranker, self.settings.candidate_k, self.settings.rrf_k)
        log.info("ingested %d documents into %d chunks (%s)", index.info.documents, index.info.chunks, index.info.embedder)
        return {
            **index.info.__dict__,
            "reranker": self.reranker.name,
            "chunks_flagged_for_injection": len(flagged),
        }

    def status(self) -> dict:
        info = self.retriever.index.info.__dict__ if self.retriever else None
        return {
            "index_loaded": self.retriever is not None,
            "index": info,
            "reranker": self.retriever.reranker.name if self.retriever else None,
            "llm": self.llm.name,
            "ticket_store": str(self.settings.tickets_db_path),
            "ticket_store_exists": self.settings.tickets_db_path.exists(),
        }

    # ---------- ask ----------
    def ask(self, question: str, top_k: int | None = None, filters: Filters | None = None,
            mode: str = "hybrid", rerank: bool = True, force_route: str | None = None) -> Answer:
        start = time.perf_counter()
        question = validate_question(question, self.settings.max_question_chars)
        warnings = []
        if detect_injection(question):
            warnings.append("question contains instruction-like text; it was treated as a question only")
            log.warning("possible prompt injection in question: %s", question[:200])

        chosen = force_route or route(question)
        if chosen == "tickets":
            ans = self._ask_tickets(question)
        else:
            ans = self._ask_docs(question, top_k or self.settings.top_k, filters, mode, rerank)
        ans.warnings = warnings + ans.warnings
        ans.latency_ms = round((time.perf_counter() - start) * 1000, 1)
        log.info("ask route=%s abstained=%s confidence=%.3f latency_ms=%.0f question=%s",
                 ans.route, ans.abstained, ans.confidence, ans.latency_ms, question[:200])
        return ans

    def _ask_docs(self, question: str, top_k: int, filters: Filters | None, mode: str, rerank: bool) -> Answer:
        if self.retriever is None:
            raise NotIngestedError("knowledge base is not indexed yet; call POST /ingest first")
        result: RetrievalResult = self.retriever.search(question, top_k, filters, mode, rerank)
        citations = [
            Citation(h.chunk.source, h.chunk.section or h.chunk.title, h.chunk.text[:PASSAGE_CHARS],
                     h.chunk.url, h.chunk.last_updated,
                     round(h.rerank if h.rerank is not None else h.rrf, 4))
            for h in result.hits
        ]
        debug = {"mode": result.mode, "reranked": result.reranked, "timings_ms": result.timings_ms}

        if result.confidence < self.settings.min_confidence:
            # no citations: the weak candidates do not support any answer
            return Answer(question, f"{IDK}. The knowledge base does not contain enough evidence to answer this.",
                          "docs", True, result.confidence, [], debug=debug)

        warnings, docs, ctx_parts = [], [], []
        for i, h in enumerate(result.hits, start=1):
            if detect_injection(h.chunk.text):
                warnings.append(f"passage {i} ({h.chunk.source}) was excluded: it contains instruction-like text")
                continue
            docs.append((i, h.chunk.text))
            ctx_parts.append(wrap_document(i, h.chunk.source, h.chunk.section or h.chunk.title, h.chunk.text))
        if not docs:
            return Answer(question, f"{IDK}.", "docs", True, result.confidence, citations, warnings=warnings, debug=debug)

        text = self.llm.answer(question, docs, "\n\n".join(ctx_parts))
        abstained = text.strip().lower().startswith(IDK.lower())
        if not abstained:
            used = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
            if used:  # keep only the passages the answer actually cites, in citation order
                citations = [c for i, c in enumerate(citations, start=1) if i in used] or citations
        return Answer(question, text, "docs", abstained, result.confidence, citations, warnings=warnings, debug=debug)

    def _ask_tickets(self, question: str) -> Answer:
        sql = self.llm.write_sql(question, SCHEMA_DOC)
        if not sql:
            return Answer(question, f"{IDK}. I could not map this question to the ticket data.", "tickets", True, 0.0)
        try:
            safe_sql = guard_sql(sql, self.settings.sql_row_limit)
        except SQLGuardError as e:
            log.warning("blocked SQL (%s): %s", e, sql)
            return Answer(question, f"{IDK}. The generated query was blocked by the SQL guard.", "tickets", True, 0.0,
                          sql=sql, warnings=[f"sql blocked: {e}"])
        try:
            res = execute_readonly(self.settings.tickets_db_path, safe_sql, self.settings.sql_timeout_s,
                                   self.settings.sql_row_limit)
        except (TimeoutError, FileNotFoundError) as e:
            return Answer(question, f"{IDK}. {e}", "tickets", True, 0.0, sql=safe_sql, warnings=[str(e)])
        except Exception as e:
            log.warning("SQL failed: %s", e)
            return Answer(question, f"{IDK}. The query failed to run.", "tickets", True, 0.0, sql=safe_sql,
                          warnings=[f"sql error: {type(e).__name__}"])

        text = self.llm.summarize_rows(question, res.sql, res.columns, res.rows)
        citation = Citation("tickets.duckdb", "SQL query", res.sql)
        return Answer(question, text, "tickets", False, 1.0 if res.rows else 0.5, [citation],
                      sql=res.sql, rows=res.rows, columns=res.columns,
                      warnings=["result truncated"] if res.truncated else [])
