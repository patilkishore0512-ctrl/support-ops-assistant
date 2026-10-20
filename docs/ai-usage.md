# Use of AI Coding Assistants

The assignment asks where and how AI coding assistants were used.

## Tool

Claude (Anthropic), used through claude.ai.

## How it was used

| Area | AI involvement |
|---|---|
| Requirements breakdown | Claude restated each requirement before any code was written; I confirmed scope and design choices (DuckDB, CSV input, CLI + Makefile) first. |
| Part A code | Generator, pipeline modules, tests, Makefile and CI were written by Claude to the agreed design. |
| Part B code | RAG service, retrieval, SQL guard, guardrails, Dockerfile and tests were written by Claude to the agreed design (mock LLM default, FastAPI docs as the knowledge base). |
| Part C | Golden set, metrics, evaluation runner and GitHub Actions evaluation workflow were written by Claude; Claude ran the experiments and drafted the analysis from the actual results. |
| Part D | Design note drafted by Claude on Azure (my choice of cloud), with prices checked against public sources and estimates labelled as such. |
| Verification | Claude ran the pipeline end to end, re-ran days to check idempotency, checked upsert correctness against history, and ran the tests in a fresh virtual environment. For Part B: tested SQL-guard attacks, injection false positives on the real docs, and a clean-environment run of the container startup; CI builds and smoke-tests the Docker image. |
| Documentation | Drafted by Claude from the implemented code and actual run results. |

Commits include a `Co-Authored-By: Claude` line.

## My Role: AI-Assisted Development & Technical Ownership

### 1. Requirement Analysis and Workflow Planning

- Defined project requirements and broke the assignment into four independent deliverables.
- Established a structured workflow: clarify requirements, agree on scope, implement, validate, and document each part before proceeding.
- Planned the submission and repository structure upfront.

### 2. Architecture and Design Decisions

- Selected DuckDB for analytical data storage, CSV files as input, and a CLI with a Makefile for pipeline execution.
- Consolidated all four deliverables into a single repository to enable shared data, configuration, testing, and CI.
- Checked the assignment's LLM requirements and chose a documented offline mock as the default provider, so reviewers and CI can run the service without API keys. Real providers can be switched on through configuration.
- Approved FastAPI documentation as the knowledge base and FastAPI as the service framework.
- Verified that CI builds the Docker image with real models on a clean machine and that the end-to-end smoke tests pass.
- Defined the evaluation scope before implementation: a golden set covering exact-term, paraphrased, filtered, unanswerable and structured-data questions, with retrieval, answer-quality and abstention metrics.
- Chose the before/after experiments: retrieval method (keyword, semantic, hybrid, and re-ranking on/off) and chunk size.
- Approved running the real-model evaluation on GitHub Actions, so results are reproducible on a clean machine and the setup works without API keys.
- Chose Azure as the target cloud for the design note, based on hands-on experience deploying to Azure Web Apps and building CI/CD with GitHub Actions, so the deployment, monitoring and cost choices reflect a platform I have run in practice.
- Reviewed the design-note plan against the assignment's four questions (100× scale, deployment and monitoring, top risks, next steps) before it was written.
- Evaluated the orchestration approach and kept the question flow in plain Python (validate, route, then document search or guarded SQL) rather than an agent framework such as LangGraph, because the flow is a fixed two-path pipeline; identified LangGraph as the path for multi-source or agentic extensions.
- Used AI assistance to explore implementation approaches and refine technical decisions.

### 3. AI-Assisted Implementation and Validation

- Used Claude AI to accelerate code development, troubleshoot issues, and refine implementation through iterative prompts.
- Reviewed generated changes against requirements and validated functionality before approving each deliverable.
- Verified automated tests and end-to-end pipeline execution through GitHub Actions CI.
- Reviewed the evaluation results, including the finding that the re-ranker's raw score wrongly refused answerable questions, and the confidence fix that reduced those refusals from 27% to 9%.
- Re-checked the final solution against the assignment brief, including whether the knowledge base and the routing between structured and unstructured data meet the stated requirements.
- Reviewed the Part A pipeline design step by step: file-level idempotency, data quality checks and quarantine, the newer-version-wins upsert, schema drift handling and the curated views.

### 4. Repository Management and Delivery

- Set up and managed the public GitHub repository and integrated the Claude GitHub App into the development workflow.
- Maintained a clean project structure, README, detailed documentation, and sample outputs.
- Kept the repository focused on the brief by removing tooling that was not required for the submission.
- Ensured transparency around AI-generated contributions through appropriate documentation and commit attribution.


