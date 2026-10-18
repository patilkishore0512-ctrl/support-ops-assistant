# Use of AI Coding Assistants

The assignment asks where and how AI coding assistants were used.

## Tool

Claude (Anthropic), used through claude.ai.

## How it was used

| Area | AI involvement |
|---|---|
| Requirements breakdown | Claude restated each requirement before any code was written; I confirmed scope and design choices (DuckDB, CSV input, CLI + Makefile) first. |
| Part A code | Generator, pipeline modules, tests, Makefile and CI were written by Claude to the agreed design. |
| Verification | Claude ran the pipeline end to end, re-ran days to check idempotency, checked upsert correctness against history, and ran the tests in a fresh virtual environment. |
| Documentation | Drafted by Claude from the implemented code and actual run results. |

Commits include a `Co-Authored-By: Claude` line.

## My role

- Asked for each part's requirements to be restated and confirmed scope before any code was written.
- Chose the design direction for Part A: DuckDB as the store, CSV daily files, and a CLI + Makefile for orchestration.
- Decided on a single repository for all four parts with a documented, presentable structure, and set up the GitHub repository.
- Directed the work part by part (A, then B, C and D), approving each step before the next.
