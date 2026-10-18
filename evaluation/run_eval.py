"""
Evaluation runner.

    python evaluation/run_eval.py                      # every configuration that can run here
    python evaluation/run_eval.py --configs offline    # LSA + BM25 only (no model downloads)
    python evaluation/run_eval.py --configs models     # needs sentence-transformers + model access
    python evaluation/run_eval.py --judge              # add an LLM judge (needs LLM_PROVIDER + key)

Outputs (evaluation/results/ by default)
    summary.json                 all metrics for every configuration
    per_question_<config>.jsonl  one line per question: retrieval, answer, scores
    threshold_sweep.csv          abstention trade-off per confidence threshold
    report.md                    tables used in docs/part-c-evaluation.md

Requires the Part A ticket store (python -m pipeline run).
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import logging
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evaluation"))

from metrics import (  # noqa: E402
    contains_all, correctness, faithfulness, hit_at_k, mean, percentile, recall_at_k, reciprocal_rank,
)
from rag_service.config import get_settings  # noqa: E402
from rag_service.documents import load_corpus  # noqa: E402
from rag_service.index import SearchIndex  # noqa: E402
from rag_service.llm import make_llm  # noqa: E402
from rag_service.models import LSAEmbedder, Reranker, make_embedder, make_reranker  # noqa: E402
from rag_service.retrieval import Filters, Retriever  # noqa: E402
from rag_service.router import route  # noqa: E402
from rag_service.service import Assistant  # noqa: E402

log = logging.getLogger("eval")

K = 5
THRESHOLDS = [round(0.05 * i, 2) for i in range(1, 19)]


@dataclass(frozen=True)
class Config:
    name: str
    embedder: str          # lsa | bge
    mode: str              # bm25 | dense | hybrid
    rerank: bool
    chunk_size: int = 180
    chunk_overlap: int = 30
    group: str = "retrieval"   # retrieval | chunking
    label: str = ""


RETRIEVAL_CONFIGS = [
    Config("bm25", "lsa", "bm25", False, label="BM25 only"),
    Config("dense-lsa", "lsa", "dense", False, label="Dense only (offline LSA)"),
    Config("hybrid-lsa", "lsa", "hybrid", False, label="Hybrid BM25 + LSA"),
    Config("dense-bge", "bge", "dense", False, label="Dense only (bge-small)"),
    Config("hybrid-bge", "bge", "hybrid", False, label="Hybrid BM25 + bge-small"),
    Config("hybrid-bge-rerank", "bge", "hybrid", True, label="Hybrid BM25 + bge-small + cross-encoder re-rank"),
]
CHUNK_CONFIGS = [
    Config(f"chunk-{size}", "lsa", "hybrid", False, size, overlap, "chunking", f"Hybrid LSA, chunk {size} words")
    for size, overlap in ((80, 15), (180, 30), (400, 60))
]


def needs_models(cfg: Config) -> bool:
    return cfg.embedder == "bge" or cfg.rerank


def load_golden(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["questions"]


class Runner:
    def __init__(self, out_dir: Path, judge: bool):
        self.base = get_settings()
        self.out_dir = out_dir
        self.indexes: dict[tuple, SearchIndex] = {}
        self._bge = None
        self._reranker = None
        self.judge = make_llm(self.base.llm_provider, self.base.llm_model) if judge else None
        if self.judge is not None and self.judge.name == "mock":
            log.warning("--judge needs a real LLM_PROVIDER; skipping the LLM judge")
            self.judge = None

    # ---------- components ----------
    def bge(self):
        if self._bge is None:
            self._bge = make_embedder("sentence-transformers", self.base.embedding_model)
        return self._bge

    def reranker(self):
        if self._reranker is None:
            self._reranker = make_reranker("cross-encoder", self.base.reranker_model)
        return self._reranker

    def models_available(self) -> bool:
        try:
            self.bge()
            self.reranker()
            return True
        except Exception as exc:
            log.warning("models unavailable (%s: %s); model configurations will be skipped", type(exc).__name__, exc)
            return False

    def index_for(self, cfg: Config) -> SearchIndex:
        key = (cfg.embedder, cfg.chunk_size, cfg.chunk_overlap)
        if key not in self.indexes:
            chunks = load_corpus(self.base.kb_dir, cfg.chunk_size, cfg.chunk_overlap)
            embedder = self.bge() if cfg.embedder == "bge" else LSAEmbedder()
            self.indexes[key] = SearchIndex.build(chunks, embedder, cfg.chunk_size, cfg.chunk_overlap)
            log.info("built index %s: %d chunks in %.1fs", key, len(chunks), self.indexes[key].info.build_seconds)
        return self.indexes[key]

    def assistant_for(self, cfg: Config) -> Assistant:
        settings = dataclasses.replace(self.base, llm_provider="mock", index_dir=self.out_dir / ".no-index")
        a = Assistant(settings)
        a.retriever = Retriever(self.index_for(cfg), self.reranker() if cfg.rerank else Reranker(),
                                settings.candidate_k, settings.rrf_k)
        return a

    # ---------- evaluation ----------
    def evaluate(self, cfg: Config, golden: list[dict]) -> dict:
        assistant = self.assistant_for(cfg)
        rows = []
        for q in golden:
            filters = Filters(**q["filters"]) if q.get("filters") else None
            answerable = q.get("answerable", True)
            row = {"id": q["id"], "category": q["category"], "question": q["question"],
                   "expected_route": q["expected_route"], "routed": route(q["question"])}

            # retrieval is measured on its own, independent of answer post-processing
            if q["expected_route"] == "docs":
                res = assistant.retriever.search(q["question"], top_k=10, filters=filters,
                                                 mode=cfg.mode, rerank=cfg.rerank)
                sources = [h.chunk.source for h in res.hits]
                row.update(retrieved=sources[:K], confidence=res.confidence)
                if answerable:
                    exp = q["expected_sources"]
                    row.update(hit1=hit_at_k(sources, exp, 1), hit5=hit_at_k(sources, exp, K),
                               recall5=recall_at_k(sources, exp, K), rr=reciprocal_rank(sources, exp))
                passages = [h.chunk.text for h in res.hits[:K]]
            else:
                passages = []

            t = time.perf_counter()
            ans = assistant.ask(q["question"], top_k=K, filters=filters, mode=cfg.mode, rerank=cfg.rerank,
                                force_route=q["expected_route"])
            row["latency_ms"] = round((time.perf_counter() - t) * 1000, 1)
            row.update(answer=ans.answer, abstained=ans.abstained, answer_confidence=ans.confidence)

            if q["category"] == "tickets":
                haystack = ans.answer + " " + json.dumps(ans.rows or [])
                row.update(sql=ans.sql, ticket_correct=float(not ans.abstained and contains_all(haystack, q["expected_answer"])))
            elif answerable:
                row.update(correct=0.0 if ans.abstained else correctness(ans.answer, q["key_facts"]),
                           faithful=None if ans.abstained else faithfulness(ans.answer, passages))
            if self.judge is not None and q["category"] != "tickets":
                row["judge"] = self.llm_judge(q, ans.answer)
            rows.append(row)

        self.write_jsonl(cfg, rows)
        return {"config": dataclasses.asdict(cfg), "metrics": summarize(rows),
                "index": {"chunks": len(assistant.retriever.index.chunks)}, "rows": rows}

    def llm_judge(self, q: dict, answer: str) -> float | None:
        if q.get("answerable", True):
            facts = "; ".join(" or ".join(map(str, g)) for g in q["key_facts"])
            prompt = (f"Question: {q['question']}\nExpected facts: {facts}\nAnswer: {answer}\n\n"
                      "Score the answer from 1 (wrong or missing the expected facts) to 5 (correct and complete). "
                      "Reply with the number only.")
        else:
            prompt = (f"Question: {q['question']}\nThe knowledge base cannot answer this question.\nAnswer: {answer}\n\n"
                      "Score 5 if the answer declines or says it does not know, 1 if it invents an answer. "
                      "Reply with the number only.")
        try:
            out = self.judge.complete("You are a strict evaluator.", prompt)
            return float(next(ch for ch in out if ch.isdigit()))
        except Exception as exc:
            log.warning("judge failed: %s", exc)
            return None

    def write_jsonl(self, cfg: Config, rows: list[dict]) -> None:
        path = self.out_dir / f"per_question_{cfg.name}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def summarize(rows: list[dict]) -> dict:
    ans_docs = [r for r in rows if r["expected_route"] == "docs" and "hit5" in r]
    unans = [r for r in rows if r["category"] == "unanswerable"]
    tickets = [r for r in rows if r["category"] == "tickets"]
    by_cat = {}
    for cat in ("docs_exact", "docs_paraphrase", "docs_filtered"):
        sub = [r for r in ans_docs if r["category"] == cat]
        by_cat[cat] = {"n": len(sub), "hit5": mean([r["hit5"] for r in sub]), "mrr": mean([r["rr"] for r in sub])}
    latencies = [r["latency_ms"] for r in rows if r["expected_route"] == "docs"]
    judged = [r["judge"] for r in rows if r.get("judge") is not None]
    return {
        "n_questions": len(rows),
        "retrieval": {
            "n": len(ans_docs),
            "hit@1": mean([r["hit1"] for r in ans_docs]),
            "hit@5": mean([r["hit5"] for r in ans_docs]),
            "recall@5": mean([r["recall5"] for r in ans_docs]),
            "mrr": mean([r["rr"] for r in ans_docs]),
            "by_category": by_cat,
        },
        "answers": {
            "correctness": mean([r["correct"] for r in ans_docs]),
            "faithfulness": mean([r["faithful"] for r in ans_docs if r.get("faithful") is not None]),
            "false_abstain_rate": mean([float(r["abstained"]) for r in ans_docs]),
            "llm_judge_mean": mean(judged) if judged else None,
        },
        "abstention": {
            "n": len(unans),
            "correct_abstain_rate": mean([float(r["abstained"]) for r in unans]),
        },
        "tickets": {
            "n": len(tickets),
            "routing_accuracy": mean([float(r["routed"] == "tickets") for r in tickets]),
            "answer_accuracy": mean([r["ticket_correct"] for r in tickets]),
        },
        "routing_accuracy_all": mean([float(r["routed"] == r["expected_route"]) for r in rows]),
        "latency_ms_docs": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95)},
    }


def threshold_sweep(rows: list[dict]) -> list[dict]:
    """Abstention trade-off if the threshold were t (confidence only; same retrieval)."""
    answerable = [r for r in rows if r["expected_route"] == "docs" and "hit5" in r]
    unans = [r for r in rows if r["category"] == "unanswerable"]
    out = []
    for t in THRESHOLDS:
        correct_abstain = mean([float(r["confidence"] < t) for r in unans])
        false_abstain = mean([float(r["confidence"] < t) for r in answerable])
        answered_right = mean([float(r["confidence"] >= t and r["hit5"] == 1.0) for r in answerable])
        out.append({"threshold": t, "correct_abstain_rate": round(correct_abstain, 3),
                    "false_abstain_rate": round(false_abstain, 3),
                    "answered_with_right_source": round(answered_right, 3),
                    "balanced_accuracy": round((correct_abstain + (1 - false_abstain)) / 2, 3)})
    return out


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def write_report(results: list[dict], sweeps: dict[str, list[dict]], out_dir: Path, meta: dict) -> str:
    lines = ["# Evaluation results", "",
             f"Generated {meta['generated_at']} | golden set: {meta['n_questions']} questions | "
             f"Python {meta['python']} | LLM: mock", ""]

    retr = [r for r in results if r["config"]["group"] == "retrieval"]
    if retr:
        lines += ["## Retrieval configurations", "",
                  "| Configuration | Hit@1 | Hit@5 | Recall@5 | MRR | Hit@5 exact | Hit@5 paraphrase | Correctness | Faithfulness | Abstain on unanswerable | False abstain | p50 ms |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in retr:
            m, c = r["metrics"], r["config"]
            bc = m["retrieval"]["by_category"]
            lines.append(
                f"| {c['label']} | {pct(m['retrieval']['hit@1'])} | {pct(m['retrieval']['hit@5'])} | "
                f"{pct(m['retrieval']['recall@5'])} | {m['retrieval']['mrr']:.2f} | {pct(bc['docs_exact']['hit5'])} | "
                f"{pct(bc['docs_paraphrase']['hit5'])} | {pct(m['answers']['correctness'])} | "
                f"{pct(m['answers']['faithfulness'])} | {pct(m['abstention']['correct_abstain_rate'])} | "
                f"{pct(m['answers']['false_abstain_rate'])} | {m['latency_ms_docs']['p50']:.0f} |")
        lines.append("")

    chunk = [r for r in results if r["config"]["group"] == "chunking"]
    if chunk:
        lines += ["## Chunk size (hybrid, offline LSA)", "",
                  "| Chunk size (words) | Chunks | Hit@1 | Hit@5 | MRR | Correctness |", "|---|---|---|---|---|---|"]
        for r in chunk:
            m = r["metrics"]
            lines.append(f"| {r['config']['chunk_size']} | {r['index']['chunks']} | {pct(m['retrieval']['hit@1'])} | "
                         f"{pct(m['retrieval']['hit@5'])} | {m['retrieval']['mrr']:.2f} | {pct(m['answers']['correctness'])} |")
        lines.append("")

    any_r = results[0]["metrics"]
    lines += ["## Structured-data questions", "",
              f"Routing accuracy {pct(any_r['tickets']['routing_accuracy'])}, "
              f"answer accuracy {pct(any_r['tickets']['answer_accuracy'])} "
              f"({any_r['tickets']['n']} questions; identical across retrieval configurations). "
              f"Routing accuracy over all questions: {pct(any_r['routing_accuracy_all'])}.", ""]

    for name, sweep in sweeps.items():
        lines += [f"## Abstention threshold sweep: {name}", "",
                  "| Threshold | Abstain on unanswerable | False abstain on answerable | Answered with right source | Balanced accuracy |",
                  "|---|---|---|---|---|"]
        for s in sweep:
            if s["threshold"] in (0.1, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.8):
                lines.append(f"| {s['threshold']:.2f} | {pct(s['correct_abstain_rate'])} | {pct(s['false_abstain_rate'])} | "
                             f"{pct(s['answered_with_right_source'])} | {pct(s['balanced_accuracy'])} |")
        lines.append("")

    lines += ["## Misses (hit@5 = 0) per configuration", ""]
    for r in results:
        misses = [row["id"] for row in r["rows"] if row.get("hit5") == 0.0]
        lines.append(f"- **{r['config']['name']}**: {', '.join(misses) if misses else 'none'}")
    lines.append("")
    text = "\n".join(lines)
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Evaluate the RAG service on the golden set")
    p.add_argument("--golden", default=str(ROOT / "evaluation" / "golden_set.yaml"))
    p.add_argument("--out", default=str(ROOT / "evaluation" / "results"))
    p.add_argument("--configs", default="all", choices=["all", "offline", "models"])
    p.add_argument("--no-chunking", action="store_true", help="skip the chunk-size experiment")
    p.add_argument("--judge", action="store_true", help="add an LLM judge (needs a real LLM_PROVIDER)")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("rag.service", "rag.models", "httpx", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    golden = load_golden(Path(args.golden))
    runner = Runner(out_dir, args.judge)
    if not runner.base.tickets_db_path.exists():
        raise SystemExit("ticket store missing: run `python -m pipeline run` first")

    configs = list(RETRIEVAL_CONFIGS)
    if not args.no_chunking:
        configs += CHUNK_CONFIGS
    if args.configs == "offline":
        configs = [c for c in configs if not needs_models(c)]
    elif args.configs == "models":
        configs = [c for c in configs if needs_models(c)]
    if any(needs_models(c) for c in configs) and not runner.models_available():
        configs = [c for c in configs if not needs_models(c)]

    results = []
    for cfg in configs:
        log.info("evaluating %s", cfg.name)
        res = runner.evaluate(cfg, golden)
        m = res["metrics"]
        log.info("  hit@5=%.2f mrr=%.2f correctness=%.2f abstain=%.2f false_abstain=%.2f",
                 m["retrieval"]["hit@5"], m["retrieval"]["mrr"], m["answers"]["correctness"],
                 m["abstention"]["correct_abstain_rate"], m["answers"]["false_abstain_rate"])
        results.append(res)

    sweeps = {r["config"]["name"]: threshold_sweep(r["rows"]) for r in results
              if r["config"]["name"] in ("hybrid-lsa", "hybrid-bge-rerank")}
    for name, sweep in sweeps.items():
        with (out_dir / f"threshold_sweep_{name}.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(sweep[0]))
            w.writeheader()
            w.writerows(sweep)

    meta = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "n_questions": len(golden),
            "python": platform.python_version(), "min_confidence": runner.base.min_confidence}
    summary = {"meta": meta, "results": [{k: v for k, v in r.items() if k != "rows"} for r in results],
               "threshold_sweeps": sweeps}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(write_report(results, sweeps, out_dir, meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
