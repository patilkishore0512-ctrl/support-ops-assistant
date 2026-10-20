# Use of AI Coding Assistants

## Tool Used

**Claude (Anthropic)**, accessed through claude.ai.

## How AI Was Used

Claude was used primarily as a coding assistant to implement the technical components of the assignment based on my requirements, design decisions, and instructions. I directed the development process, reviewed the implementation, validated the results, and ensured the final solution met the assignment requirements.

| Area | My Involvement and Use of AI |
|---|---|
| Requirements Analysis | Analysed the assignment requirements, divided the work into four deliverables, and defined the scope, expected outcomes, and acceptance criteria before implementation. |
| Part A – Data Pipeline | Defined the pipeline requirements, selected the architecture, and specified the expected behaviour for data generation, processing, validation, upserts, and reporting. Claude implemented the code, tests, Makefile, and CI configuration. |
| Part B – RAG Service | Defined the service requirements, retrieval approach, SQL safety rules, guardrails, and knowledge base. Claude implemented the RAG service, retrieval logic, SQL guard, Dockerfile, and tests. |
| Part C – Evaluation | Defined the evaluation scope, golden-set coverage, metrics, and experiments needed to compare retrieval methods and chunk sizes. Claude implemented the evaluation framework, ran the experiments, and generated the initial analysis from the results. |
| Part D – Cloud Design | Selected Azure as the target cloud based on my experience with Azure Web Apps and GitHub Actions CI/CD. Defined the design-note requirements covering scalability, deployment, monitoring, risks, and next steps. Claude helped draft the document, while I reviewed the technical approach and cost assumptions. |
| Testing and Validation | Ran and reviewed end-to-end pipeline tests, idempotency checks, upsert scenarios, data-quality and schema-drift cases, SQL-guard tests, and container startup checks. Reviewed CI results and evaluation findings to confirm the solution behaved as expected. |
| Documentation and Delivery | Defined the repository structure and submission requirements, reviewed the documentation against the implemented solution, and ensured the repository, sample outputs, tests, and AI contribution attribution were ready for submission. |

## My Role: Technical Ownership and AI-Assisted Development

### 1. Requirements Analysis and Workflow Planning

- Analysed the assignment brief and divided it into four independent deliverables.
- Established a structured workflow: clarify requirements, define the scope, implement, validate, and document each deliverable before moving to the next.
- Planned the repository structure and submission approach upfront.
- Defined the expected behaviour and validation criteria for each component before asking Claude to implement it.

### 2. Architecture and Design Decisions

- Selected DuckDB for analytical storage, CSV files as input, and a CLI with a Makefile for pipeline execution.
- Decided to consolidate all four deliverables into one repository to share data, configuration, testing, and CI workflows.
- Chose an offline mock LLM provider as the default to allow reviewers and CI to run the service without API keys, while keeping real providers configurable.
- Selected FastAPI documentation as the RAG knowledge base and FastAPI as the service framework.
- Defined the evaluation strategy, including exact-term, paraphrased, filtered, unanswerable, and structured-data questions.
- Selected retrieval and chunk-size experiments to assess the impact of keyword search, semantic search, hybrid retrieval, and re-ranking.
- Chose GitHub Actions for automated evaluation and CI validation.
- Selected Azure for the cloud design note, drawing on my experience with Azure Web Apps and GitHub Actions CI/CD.
- Evaluated the orchestration approach and chose a straightforward Python workflow for validation, routing, document retrieval, and guarded SQL execution instead of introducing an agent framework for a fixed two-path flow.
- Reviewed implementation approaches suggested by Claude and made the final decisions on the technical direction.

### 3. AI-Assisted Implementation and Validation

- Used Claude to generate and modify code, implement modules, write tests, configure CI, and troubleshoot development issues.
- Guided implementation through iterative instructions, reviewing each deliverable against the assignment requirements.
- Executed and reviewed the pipeline locally on Windows, including idempotent re-runs, invalid-row quarantine, newer-version-wins upserts, stale and reopened ticket handling, schema drift, and curated reports.
- Reviewed the RAG service's SQL-guard behaviour, including attack scenarios and false positives on legitimate documentation.
- Checked container startup and reviewed CI build and smoke-test results.
- Analysed evaluation findings, including the re-ranker's tendency to refuse answerable questions and the subsequent confidence adjustment that reduced those refusals from 27% to 9%.
- Re-checked the final implementation against the assignment brief, particularly the knowledge base, structured versus unstructured data routing, and the required evaluation and design deliverables.

### 4. Repository Management and Delivery

- Set up and managed the public GitHub repository and integrated the Claude GitHub App into the development workflow.
- Defined and maintained the repository organisation, documentation requirements, and sample-output expectations.
- Kept the submission focused on the assignment by removing unnecessary tooling.
- Reviewed the final repository and deliverables for completeness and consistency.
- Maintained transparency regarding AI-generated code through documentation and `Co-Authored-By: Claude` commit attribution.

## Summary

I owned the requirements analysis, architecture and design decisions, implementation planning, technical direction, testing strategy, result review, and final delivery. Claude was used as the coding assistant to implement the agreed designs, generate tests and supporting configuration, troubleshoot issues, and assist with documentation. I directed the development process and validated the solution against the assignment requirements before submission.
