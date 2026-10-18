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

## My review

<!-- Kishore: describe what you reviewed, changed or decided yourself before submitting. -->
