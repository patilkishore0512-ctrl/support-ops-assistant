"""
Service configuration, read from environment variables (and a .env file if present).
Every value has a default, so the service runs with no configuration at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # optional dependency
    pass

ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parents[2]))


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    # data locations
    kb_dir: Path = field(default_factory=lambda: Path(_env("KB_DIR", str(ROOT / "data" / "kb"))))
    index_dir: Path = field(default_factory=lambda: Path(_env("INDEX_DIR", str(ROOT / "warehouse" / "index"))))
    tickets_db_path: Path = field(
        default_factory=lambda: Path(_env("TICKETS_DB_PATH", str(ROOT / "warehouse" / "tickets.duckdb")))
    )

    # chunking
    chunk_size_words: int = field(default_factory=lambda: int(_env("CHUNK_SIZE_WORDS", "180")))
    chunk_overlap_words: int = field(default_factory=lambda: int(_env("CHUNK_OVERLAP_WORDS", "30")))

    # models
    # auto: use sentence-transformers if installed and the model loads, otherwise the offline LSA backend
    embedding_backend: str = field(default_factory=lambda: _env("EMBEDDING_BACKEND", "auto"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    reranker_backend: str = field(default_factory=lambda: _env("RERANKER_BACKEND", "auto"))
    reranker_model: str = field(
        default_factory=lambda: _env("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    )

    # retrieval
    top_k: int = field(default_factory=lambda: int(_env("TOP_K", "5")))
    candidate_k: int = field(default_factory=lambda: int(_env("CANDIDATE_K", "20")))
    rrf_k: int = field(default_factory=lambda: int(_env("RRF_K", "60")))
    # below this confidence the service answers "I don't know"
    min_confidence: float = field(default_factory=lambda: float(_env("MIN_CONFIDENCE", "0.35")))

    # LLM
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "mock"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", ""))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "30")))

    # guardrails
    max_question_chars: int = field(default_factory=lambda: int(_env("MAX_QUESTION_CHARS", "1000")))
    sql_row_limit: int = field(default_factory=lambda: int(_env("SQL_ROW_LIMIT", "100")))
    sql_timeout_s: float = field(default_factory=lambda: float(_env("SQL_TIMEOUT_S", "5")))


def get_settings() -> Settings:
    return Settings()
