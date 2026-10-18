# Part C: Evaluation

A 34-question golden set, an evaluation script for retrieval and answer quality, and two before/after experiments: retrieval method (with re-ranking on and off) and chunk size. All numbers below come from [`evaluation/results/`](../evaluation/results/), produced on GitHub Actions with the real models (run `39132100249`).

## Contents

- [Summary](#summary)
- [How to run](#how-to-run)
- [Golden set](#golden-set)
- [Metrics](#metrics)
- [Experiment 1: retrieval method and re-ranking](#experiment-1-retrieval-method-and-re-ranking)
- [Experiment 2: chunk size](#experiment-2-chunk-size)
- [Choosing the "I don't know" threshold](#choosing-the-i-dont-know-threshold)
- [Structured-data questions](#structured-data-questions)
- [Error analysis](#error-analysis)
- [Limitations of this evaluation](#limitations-of-this-evaluation)

## Summary

Production configuration (Docker default): hybrid BM25 + bge-small, cross-encoder re-ranking, abstain below 0.35.

| Metric | Result |
|---|---|
| Retrieval hit@1 / hit@5 | 95% / 100% (22 answerable docs questions) |
| MRR | 0.97 |
| Unanswerable questions correctly refused | 100% (6 of 6) |
| Answerable questions wrongly refused | 9% (2 of 22) |
| Answer correctness (heuristic) | 63% |
| Answer faithfulness (heuristic) | 100% (by construction with the mock LLM, see [Metrics](#metrics)) |
| Ticket questions: routing / correct answer | 100% / 83% (5 of 6) |
| Latency p50 / p95, docs route | 732 ms / 981 ms on a 2-vCPU GitHub runner, almost all from re-ranking |

What we learned, in short:

1. **Re-ranking gives the best ranking** (hit@1 77-86% → 95%), but **its raw score is a poor confidence signal** for questions in everyday words. Used directly, it wrongly refused 27% of answerable questions. Blending it with the retrieval evidence fixed most of that (27% → 9%) while still refusing every unanswerable question.
2. **Hybrid beats either method alone** with both embedders: 100% hit@5, and the "both methods agree" signal makes abstention much safer.
3. **Chunk size barely matters for retrieval here** (MRR 0.85-0.87). The docs are already split by headings, so the heading-aware chunker does most of the work.
4. **Re-ranking costs latency.** It is the only stage above 20 ms. That is the main trade-off to manage in production.

## How to run

```bash
python -m pipeline run                                   # ticket store (needed for ticket questions)
python evaluation/run_eval.py --configs offline          # BM25 + LSA configs, no downloads, ~20 s
python evaluation/run_eval.py --configs all              # + bge-small and re-ranking (needs models)
python evaluation/run_eval.py --judge                    # + LLM judge (needs LLM_PROVIDER and a key)
```

- CI runs the offline evaluation on every push.
- The real-model evaluation runs on demand with the **Evaluate (real models)** GitHub Actions workflow ([`.github/workflows/evaluate.yml`](../.github/workflows/evaluate.yml)). It publishes its results to the `eval-results` branch, from which they were copied into `evaluation/results/`.

| Output | Contents |
|---|---|
| [`report.md`](../evaluation/results/report.md) | All tables |
| [`summary.json`](../evaluation/results/summary.json) | Every metric for every configuration |
| `per_question_<config>.jsonl` | Per question: retrieved sources, confidence, answer, scores |
| `threshold_sweep_<config>.csv` | Abstention trade-off per threshold |
| [`history/`](../evaluation/results/history/) | Run 1, before the confidence fix (experiment 1b) |

## Golden set

[`evaluation/golden_set.yaml`](../evaluation/golden_set.yaml): 34 questions, written against the knowledge-base text **before** any evaluation was run, and not changed afterwards.

| Category | Count | Purpose | Expected answer stored as |
|---|---|---|---|
| `docs_exact` | 7 | Uses the exact identifiers from the docs (`max_age`, `dependency_overrides`) | Source file(s) + key-fact groups |
| `docs_paraphrase` | 12 | Describes the problem in the user's own words ("my React app on port 3000 is blocked by the browser") | Source file(s) + key-fact groups |
| `docs_filtered` | 3 | Uses a metadata filter (`source_prefix`) | Source file(s) + key-fact groups |
| `unanswerable` | 6 | Easy (capital of France) and hard near-misses (Django CSRF settings, MongoDB with Motor, GraphQL with Strawberry) | Correct behaviour is "I don't know" |
| `tickets` | 6 | Needs the Part A ticket store | Expected values from the store (seed 42) |

A key-fact group is a list of acceptable strings, for example `[[cookies], ["false"]]` for "what does `allow_credentials` do and what is its default?". Several questions accept more than one source file when the docs cover the topic in more than one place.

## Metrics

| Metric | Definition | Applies to |
|---|---|---|
| hit@k | 1 if any expected source file is among the top k retrieved chunks | Answerable docs questions |
| recall@5 | Share of the expected source files found in the top 5 | Answerable docs questions |
| MRR | 1 / rank of the first chunk from an expected source | Answerable docs questions |
| Correctness | Share of key-fact groups present in the answer (0 if the service refused) | Answerable docs questions |
| Faithfulness | Share of answer sentences supported by a retrieved passage (≥70% of the sentence's content words in one passage) | Answered docs questions |
| Correct abstain | Share of unanswerable questions answered "I don't know" | Unanswerable |
| False abstain | Share of answerable questions answered "I don't know" | Answerable docs questions |
| Routing / answer accuracy | Routed to the ticket store; the expected values appear in the result | Ticket questions |
| LLM judge (optional) | 1-5 score from a real LLM given the question, expected facts and answer | Docs questions |

How to read the answer metrics with the default mock LLM:

- **Faithfulness is 100% by construction.** The mock builds answers from retrieved sentences, so it cannot invent anything. The metric becomes meaningful with a real LLM (`LLM_PROVIDER=openai|anthropic`).
- **Correctness has a ceiling.** The mock returns the three sentences with the most word overlap, which sometimes omits the key fact even when the right passage was retrieved. Correctness therefore reflects retrieval plus the mock's sentence choice, so retrieval metrics are the cleaner comparison between configurations.
- The LLM judge was not run for these results because no API key was used. The script supports it with `--judge`.

## Experiment 1: retrieval method and re-ranking

Same golden set, same chunking (180 words), mock LLM, abstain threshold 0.35.

| Configuration | Hit@1 | Hit@5 | MRR | MRR paraphrase | Correctness | Abstain on unanswerable | False abstain | p50 latency |
|---|---|---|---|---|---|---|---|---|
| BM25 only | 86% | 95% | 0.90 | 0.90 | 61% | 50% | 14% | 1 ms |
| Dense only, offline LSA | 73% | 95% | 0.81 | 0.79 | 57% | 67% | 9% | 7 ms |
| Hybrid BM25 + LSA | 77% | **100%** | 0.87 | 0.86 | 65% | 67% | **0%** | 8 ms |
| Dense only, bge-small | 82% | **100%** | 0.89 | 0.86 | 56% | 67% | **0%** | 13 ms |
| Hybrid BM25 + bge-small | 86% | **100%** | 0.92 | 0.92 | 58% | 83% | **0%** | 14 ms |
| **Hybrid + bge-small + re-rank** | **95%** | **100%** | **0.97** | **0.94** | 63% | **100%** | 9% | 732 ms |

**What we learned**

- **Hybrid fixes each method's blind spot.** BM25 missed `filter-03` ("detect that a client has disconnected": no shared keywords with `WebSocketDisconnect`). Dense LSA missed `exact-03` (`OAuth2PasswordRequestForm`, an identifier that LSA blurs). Hybrid retrieved both: hit@5 went from 95% to 100% with the offline embedder.
- **Abstention benefits most from hybrid.** The "both methods agree" signal is strong evidence. BM25 alone refused 14% of answerable questions and answered half of the unanswerable ones; hybrid LSA refused none of the answerable ones.
- **bge-small ranks better than LSA**, especially for paraphrases (dense MRR 0.89 vs 0.81). Combined with BM25, it lifts hit@1 from 77% to 86% and correctly refuses 5 of 6 unanswerable questions.
- **The cross-encoder is the biggest single gain in ranking**: hit@1 86% → 95%, MRR 0.92 → 0.97, and 11 of 12 paraphrased questions have the right source at rank 1 (`para-03` is at rank 3).
- **Hit@5 is saturated** (100% for every hybrid setup). With 74 articles and questions that each map to a clear topic, hit@1 and MRR are the metrics that separate configurations. A harder golden set would make hit@5 informative again.
- **The paraphrased questions were not as hard as intended.** BM25 still reached 100% hit@5 on them, because everyday phrasing still shares words like "upload", "file" and "static" with the docs. MRR shows the gap better (0.90 BM25 vs 0.94 with re-ranking).

### Experiment 1b: re-ranker confidence, before and after

The first real-model run (run 1, [`history/`](../evaluation/results/history/run1_raw_reranker_confidence.md)) used the cross-encoder's score directly as confidence: `sigmoid(top logit)`.

| Hybrid + bge-small + re-rank | Run 1: raw cross-encoder confidence | Run 2: blended confidence |
|---|---|---|
| Hit@1 / MRR | 95% / 0.97 | 95% / 0.97 (ranking unchanged) |
| Unanswerable correctly refused | 83% | **100%** |
| Answerable wrongly refused | 27% (6 of 22) | **9% (2 of 22)** |
| Correctness | 52% | **63%** |

- In run 1, for all 6 wrongly refused questions the re-ranker's **top passage was correct** (hit@1 = 1), yet it scored them low, e.g. 0.07 for "write tests that call my endpoints without starting a real server" against the `TestClient` passage. The model was trained on web search queries, so it is confident on keyword-like questions and unsure on conversational ones.
- **Fix**: when re-ranking is on, confidence = 0.5 × cross-encoder score + 0.5 × the retrieval-evidence score (word coverage, dense similarity, BM25/dense agreement). The re-ranker still decides the order.
- **Caveat**: this fix was designed after looking at run 1 on the same 34 questions, so run 2's numbers are optimistic. A held-out question set would be needed to confirm it generalizes. Its margin is also thin (see the [threshold sweep](#choosing-the-i-dont-know-threshold)).

## Experiment 2: chunk size

Hybrid BM25 + offline LSA. Overlap scaled with size.

| Chunk size (words) | Overlap | Chunks | Hit@1 | Hit@5 | MRR | MRR paraphrase | Correctness |
|---|---|---|---|---|---|---|---|
| 80 | 15 | 1,245 | 77% | 100% | 0.85 | 0.83 | 64% |
| **180 (default)** | 30 | 796 | 77% | 100% | **0.87** | **0.86** | 65% |
| 400 | 60 | 698 | 77% | 100% | 0.86 | 0.81 | **70%** |

**What we learned**

- **Retrieval is almost insensitive to chunk size here.** The chunker splits at headings first, and most FastAPI sections are shorter than 180 words, so going from 180 to 400 only merges a few long sections (796 → 698 chunks).
- **Smaller chunks help ranking slightly less** (MRR 0.85): an 80-word chunk often lacks the context words a paraphrased question uses.
- **Larger chunks raise correctness** (70%) because the mock has more sentences to pick from in one passage, but they lower paraphrase MRR (0.81) and send more text to a real LLM per question (cost and latency). 180 words is the balanced default.

## Choosing the "I don't know" threshold

Abstention trade-off as the threshold moves (production configuration, from `threshold_sweep_hybrid-bge-rerank.csv`):

| Threshold | Unanswerable refused | Answerable wrongly refused | Balanced accuracy |
|---|---|---|---|
| 0.20 | 67% | 0% | 83% |
| 0.25 | 67% | 4% | 81% |
| **0.30 - 0.35** | **100%** | **9%** | **96%** |
| 0.40 - 0.70 | 100% | 27% | 86% |

For hybrid + LSA (offline mode), 0.35-0.40 is also the best point (83% balanced: refuses 67% of unanswerable questions, refuses no answerable ones). **0.35 is the default for both modes.**

The separation is narrow. The hardest unanswerable question (`unans-02`, Django CSRF middleware settings) scores 0.28, while the two wrongly refused answerable questions score 0.28 and 0.22. With 34 questions, a few new questions could move the best threshold. In production I would tune it on a larger set built from real user questions, and track the refusal rate as a metric (Part D).

## Structured-data questions

| Question | Routed | Correct | Note |
|---|---|---|---|
| Which product has the slowest average resolution time? | ✅ | ✅ | `mobile_app`, 152.94 h |
| How long does it take to resolve billing tickets on average? | ✅ | ✅ | 135.53 h |
| What is the current backlog of urgent tickets? | ✅ | ✅ | 233 |
| How many open high priority billing tickets are there? | ✅ | ✅ | 33 |
| What is the status of ticket TKT-000123? | ✅ | ✅ | resolved |
| Which product's tickets take the longest to close? | ✅ | ❌ | "I don't know": no SQL rule matches this phrasing |

Routing is 100% over all 34 questions. The one failure is the rule-based SQL writer used with the mock: it covers common phrasings, and "take the longest to close" is not one of them. It fails safely ("I don't know" rather than a wrong number). A real LLM writing the SQL, still behind the same guard, would cover this. I did not add a rule for it, to keep the golden set a fair test.

## Error analysis

| Question | Problem | Cause |
|---|---|---|
| `unans-02` Django CSRF settings | Answered (offline modes and hybrid bge); refused only with re-ranking, at 0.28 vs threshold 0.35 | Near-miss: "middleware" and "settings" are real FastAPI topics |
| `para-08` file upload, `filter-03` client disconnect | Wrongly refused with re-ranking (0.28, 0.22) even though the right passage ranks first | Cross-encoder unsure on short conversational questions; blended confidence just below threshold |
| `para-05`, `para-10`, `para-11`, `para-12` | Right source retrieved, correctness 0 | The mock picked sentences without the key fact (e.g. "starts up" instead of "lifespan"/"startup"). The heuristic matcher is strict; an LLM judge would score these higher |
| `para-03` password storage | Right source only at rank 3 with re-ranking | `extra-models.md` also discusses hashed passwords and ranks first; arguably a reasonable answer |
| `tickets-06` | No SQL | Rule coverage (above) |

## Limitations of this evaluation

- **Small and self-written.** 34 questions written by the same team that built the system. They were written before any run and not changed after, but they will still share blind spots with the system. A real evaluation would use questions from real support tickets.
- **Tuned on the test set.** The confidence blend and the 0.35 threshold were chosen by looking at these results. The reported abstention numbers are therefore optimistic.
- **Heuristic answer metrics.** String matching for correctness misses paraphrased facts; token overlap for faithfulness cannot judge reasoning. Both are conservative proxies. The optional LLM judge is the next step.
- **Mock LLM.** Answer metrics measure retrieval plus extractive sentence selection, not a real model's generation. Retrieval and abstention metrics do not depend on the LLM.
- **Latency on shared CI runners varies.** Re-ranking p50 was 1,381 ms in run 1 and 732 ms in run 2 on the same configuration. The relative cost (re-ranking ≫ everything else) is the reliable finding.
