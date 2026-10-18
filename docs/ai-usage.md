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

**Way of working**
- Set the workflow for every part: restate the requirements, confirm scope, then build, verify and publish. No code was written before scope was agreed.
- Broke the assignment into four independent deliverables and directed them in order (A, then B, C and D), approving each before moving on.
- Asked how the work would be submitted at the start, so the repository was shaped around the submission requirements from day one.

**Design decisions**
- Approved the Part A architecture: DuckDB as the queryable store, CSV daily files as input, and a CLI + Makefile as the orchestration entry point instead of a full orchestrator.
- Decided on one repository for all four parts rather than separate projects, so the pipeline, RAG service, evaluation and design note share one data store, one setup and one CI.

**Quality bar**
- Required the deliverable to be presentable, not just working code: a clear folder structure, a short top-level README, detailed documentation per part, sample outputs and a clean commit history.
- Required honest disclosure of AI assistance, which is why this file exists and why commits carry a `Co-Authored-By` line.

**Repository and delivery**
- Chose the repository name, created the public GitHub repository, and connected the Claude GitHub App to it so work could be pushed directly.
- Confirmed CI (tests and an end-to-end pipeline run) passes on GitHub before moving to the next part.
