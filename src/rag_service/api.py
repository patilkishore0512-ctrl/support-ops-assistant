"""
FastAPI app.

  GET  /health   liveness + what is loaded (index, models, LLM, ticket store)
  POST /ingest   parse, chunk, embed and index the knowledge base
  POST /ask      answer a question with citations and a confidence / abstention signal

Run:  uvicorn rag_service.api:app --host 0.0.0.0 --port 8000
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from rag_service.config import get_settings
from rag_service.guardrails import ValidationError, configure_logging, validate_question
from rag_service.retrieval import Filters
from rag_service.service import Assistant, NotIngestedError

configure_logging()
log = logging.getLogger("rag.api")

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["assistant"] = Assistant(get_settings())
    yield
    state.clear()


app = FastAPI(
    title="Support Ops Assistant",
    version="0.1.0",
    description="Answers questions from the knowledge base and the support-ticket store, with citations.",
    lifespan=lifespan,
)


def assistant() -> Assistant:
    return state["assistant"]


# ---------- schemas ----------
class IngestRequest(BaseModel):
    chunk_size_words: int | None = Field(None, ge=40, le=1000, description="override CHUNK_SIZE_WORDS")
    chunk_overlap_words: int | None = Field(None, ge=0, le=300, description="override CHUNK_OVERLAP_WORDS")


class FilterModel(BaseModel):
    source_prefix: str | None = Field(None, max_length=200, examples=["fastapi/deployment"])
    section_contains: str | None = Field(None, max_length=200)
    updated_after: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", examples=["2026-01-01"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000, examples=["How do I enable CORS?"])
    top_k: int | None = Field(None, ge=1, le=20)
    filters: FilterModel | None = None
    route: Literal["docs", "tickets"] | None = Field(None, description="force a route instead of auto-routing")

    @field_validator("question")
    @classmethod
    def clean(cls, v: str) -> str:
        try:
            return validate_question(v)
        except ValidationError as e:
            raise ValueError(str(e)) from e


class CitationModel(BaseModel):
    source: str
    section: str
    passage: str
    url: str = ""
    last_updated: str = ""
    score: float | None = None


class AskResponse(BaseModel):
    answer: str
    route: Literal["docs", "tickets"]
    abstained: bool = Field(description="true when the service answered \"I don't know\"")
    confidence: float = Field(ge=0, le=1)
    citations: list[CitationModel]
    sql: str | None = None
    columns: list[str] | None = None
    rows: list[list] | None = None
    warnings: list[str] = []
    latency_ms: float


# ---------- routes ----------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", **assistant().status()}


@app.post("/ingest")
def ingest(req: IngestRequest | None = None) -> dict:
    req = req or IngestRequest()
    try:
        return assistant().ingest(req.chunk_size_words, req.chunk_overlap_words)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    filters = Filters(**req.filters.model_dump()) if req.filters else None
    try:
        ans = assistant().ask(req.question, top_k=req.top_k, filters=filters, force_route=req.route)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except NotIngestedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return AskResponse(
        answer=ans.answer, route=ans.route, abstained=ans.abstained, confidence=ans.confidence,
        citations=[CitationModel(**c.__dict__) for c in ans.citations],
        sql=ans.sql, columns=ans.columns, rows=ans.rows, warnings=ans.warnings, latency_ms=ans.latency_ms,
    )
