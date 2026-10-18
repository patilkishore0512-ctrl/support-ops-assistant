# Evaluation run 1 (before blending re-ranker confidence)

Kept for the record: re-ranking used the raw cross-encoder score as confidence. See docs/part-c-evaluation.md, experiment 1.

Generated 2026-10-18 13:32 | golden set: 34 questions | Python 3.12.15 | LLM: mock

## Retrieval configurations

| Configuration | Hit@1 | Hit@5 | Recall@5 | MRR | Hit@5 exact | Hit@5 paraphrase | Correctness | Faithfulness | Abstain on unanswerable | False abstain | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BM25 only | 86% | 95% | 89% | 0.90 | 100% | 100% | 61% | 100% | 50% | 14% | 2 |
| Dense only (offline LSA) | 73% | 95% | 89% | 0.81 | 86% | 100% | 57% | 100% | 67% | 9% | 9 |
| Hybrid BM25 + LSA | 77% | 100% | 96% | 0.87 | 100% | 100% | 65% | 100% | 67% | 0% | 11 |
| Dense only (bge-small) | 82% | 100% | 94% | 0.89 | 100% | 100% | 56% | 100% | 67% | 0% | 24 |
| Hybrid BM25 + bge-small | 86% | 100% | 96% | 0.92 | 100% | 100% | 58% | 100% | 83% | 0% | 26 |
| Hybrid BM25 + bge-small + cross-encoder re-rank | 95% | 100% | 98% | 0.97 | 100% | 100% | 52% | 100% | 83% | 27% | 1381 |

## Chunk size (hybrid, offline LSA)

| Chunk size (words) | Chunks | Hit@1 | Hit@5 | MRR | Correctness |
|---|---|---|---|---|---|
| 80 | 1245 | 77% | 100% | 0.85 | 64% |
| 180 | 796 | 77% | 100% | 0.87 | 65% |
| 400 | 698 | 77% | 100% | 0.86 | 70% |

## Structured-data questions

Routing accuracy 100%, answer accuracy 83% (6 questions; identical across retrieval configurations). Routing accuracy over all questions: 100%.

## Abstention threshold sweep: hybrid-lsa

| Threshold | Abstain on unanswerable | False abstain on answerable | Answered with right source | Balanced accuracy |
|---|---|---|---|---|
| 0.10 | 33% | 0% | 100% | 67% |
| 0.20 | 33% | 0% | 100% | 67% |
| 0.25 | 33% | 0% | 100% | 67% |
| 0.30 | 50% | 0% | 100% | 75% |
| 0.35 | 67% | 0% | 100% | 83% |
| 0.40 | 67% | 0% | 100% | 83% |
| 0.45 | 67% | 14% | 86% | 76% |
| 0.50 | 67% | 18% | 82% | 74% |
| 0.60 | 100% | 36% | 64% | 82% |
| 0.70 | 100% | 50% | 50% | 75% |
| 0.80 | 100% | 82% | 18% | 59% |

## Abstention threshold sweep: hybrid-bge-rerank

| Threshold | Abstain on unanswerable | False abstain on answerable | Answered with right source | Balanced accuracy |
|---|---|---|---|---|
| 0.10 | 67% | 9% | 91% | 79% |
| 0.20 | 67% | 18% | 82% | 74% |
| 0.25 | 83% | 23% | 77% | 80% |
| 0.30 | 83% | 23% | 77% | 80% |
| 0.35 | 83% | 27% | 73% | 78% |
| 0.40 | 83% | 27% | 73% | 78% |
| 0.45 | 100% | 27% | 73% | 86% |
| 0.50 | 100% | 27% | 73% | 86% |
| 0.60 | 100% | 27% | 73% | 86% |
| 0.70 | 100% | 27% | 73% | 86% |
| 0.80 | 100% | 32% | 68% | 84% |

## Misses (hit@5 = 0) per configuration

- **bm25**: filter-03
- **dense-lsa**: exact-03
- **hybrid-lsa**: none
- **dense-bge**: none
- **hybrid-bge**: none
- **hybrid-bge-rerank**: none
- **chunk-80**: none
- **chunk-180**: none
- **chunk-400**: none
