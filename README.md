# NITPICKERS

An AI-Native Code Development Environment with Red Teaming built to deliver robust software through isolated parallel development phases and deterministic AI conflict resolution.
![Build Status](https://img.shields.io/badge/build-passing-brightgreen) ![License](https://img.shields.io/badge/license-MIT-blue)

## Key Features

- **Automated Mechanical Blockade:** Zero-trust validation. Pull requests are explicitly blocked until all static (Ruff, Mypy) and dynamic (Pytest) structural checks pass with a zero exit code, eliminating assumed success.
- **5-Phase Parallel & Sequential Architecture:** Seamlessly orchestrates requirement decomposition, parallel feature implementation, 3-Way Diff integration, and full-system E2E UI testing.
- **3-Way Git Merge Conflict System:** Intelligent conflict resolution system utilizing an AI Master Integrator. Automatically detects git conflicts, extracts Base, Local, and Remote file versions, and synthesizes a unified code block to guarantee seamless parallel branch integration without manual intervention.
- **Multi-Modal Diagnostic Capture:** Automatically capture rich UI failure context, including high-resolution screenshots and DOM traces via Playwright, providing undeniable evidence of frontend regressions.
- **Self-Healing Loop with Stateless Auditor:** Utilize advanced Vision LLMs (via OpenRouter) strictly as outer-loop diagnosticians. They analyze error artifacts without project context fatigue and return structured JSON fix plans to the Worker agent.
- **Total Observability:** Fully integrated LangSmith tracing visualizes complex LangGraph node transitions, internal state mutations, and multi-modal API payloads.

## Architecture Overview

NITPICKERS operates on a strict 5-Phase pipeline managed by LangGraph. Following initialization, the system architectures the workload into independent cycles. Each cycle runs concurrently in an isolated environment, creating, evaluating, and refactoring code. Once all parallel threads conclude successfully, a central integration phase kicks in, merging all outcomes natively or via AI-driven conflict resolution. Finally, the system executes E2E QA checks in an integrated environment to secure deployment stability.

```mermaid
flowchart TD
    InitCmd([CLI: nitpick init]) --> GenTemplates[".env.sample, ruff, mypy settings"]
    GenTemplates --> PrepareSpec["ALL_SPEC.md"]

    PrepareSpec --> InitCmd2([CLI: nitpick gen-cycles])
    InitCmd2 --> ArchSession["Architect Phase"]
    ArchSession --> OutputSpecs[/"SPEC.md, UAT.md"/]

    %% Phase2: Coder Graph (Parallel: Cycle 1...N)
    subgraph Phase2 ["Phase 2: Coder Graph (Parallel: Cycle 1...N)"]
        direction TB
        CoderSession["JULES: coder_session\n(Implementation & PR)"]
        SelfCritic["JULES: self_critic\n(Pre-Sandbox Polish)"]
        SandboxEval{"LOCAL: sandbox_evaluate\n(Linter / Unit Test)"}
        AuditorNode{"OpenRouter: auditor_node\n(Serial: Auditor 1→2→3)"}
        RefactorNode["JULES: refactor_node\n(Post-Audit Refactor)"]
        FinalCritic["JULES: final_critic\n(Final Logic Verification)"]

    MergeTry -- "Conflict" --> MasterIntegrator["Master Integrator (3-Way Diff)"]
    MasterIntegrator --> MergeTry
    MergeTry -- "Success" --> GlobalSandbox{"Global Linter"}
    GlobalSandbox -- "Pass" --> UatEval{"UAT Phase"}
    UatEval -- "Pass" --> EndNode(((END)))

    classDef conditional fill:#fff3cd,stroke:#ffeeba,stroke-width:2px;
    classDef success fill:#d4edda,stroke:#c3e6cb,stroke-width:2px;
    class SandboxEval,AuditorNode,FinalCritic,MergeTry,GlobalSandbox,UatEval conditional;
    class EndNode success;
```

## Prerequisites

- Python >= 3.12, < 3.14
- `uv` Package Manager
- Git
- API Keys (e.g., OPENROUTER_API_KEY, E2B_API_KEY, JULES_API_KEY)

> **Docker is optional.** NITPICKERS runs natively via ``uvx`` or ``uv run``. Docker is only needed if you require a fully isolated execution environment.

## Installation & Setup

Ensure you have `uv` installed, then synchronize the environment:

```bash
git clone <repository_url>
cd nitpickers
uv sync
cp .env.example .env
# Edit .env to add required API Keys
```

## Usage

### Option 1: uvx (recommended)

Run directly from PyPI without cloning the repository — works in any environment (local, CI/CD, DevContainer, remote):

```bash
# Initialize a project
uvx nitpickers nitpick init

# Generate development cycles
uvx nitpickers nitpick gen-cycles

# Run the full pipeline
uvx nitpickers nitpick run-pipeline
```

*``uvx`` creates an isolated temporary environment automatically — no dependency conflicts with your project.*

### Option 2: uv run (local development)

If you have the repository cloned:

```bash
# Initialize project
uv run nitpick init
```
*Note: This generates boilerplate definitions. You must customize `dev_documents/ALL_SPEC.md` prior to the next step.*

```bash
# Generate development cycles automatically based on the specification
uv run nitpick gen-cycles

# Execute the full orchestration pipeline covering all 5 phases
uv run nitpick run-pipeline
```

### Option 3: Docker (isolated execution)

If you need a fully isolated environment (e.g., clean-room testing):

```bash
# Build the image (once)
docker compose build

# Run commands
docker compose run --rm nitpick nitpick run-pipeline
```

Or use the setup script to register a convenient alias:
```bash
bash setup.sh
nitpick run-pipeline
```

## Development Workflow

This codebase enforces strict code quality checks.

To format and lint your code:
```bash
uv run ruff check --fix
uv run ruff format
uv run mypy src
```

To run unit and integration testing (incorporating DB transaction rollbacks where applicable):
```bash
uv run pytest --cov=src --cov=dev_src
```

## Project Structure

```text
nitpickers/
├── dev_documents/
│   ├── system_prompts/   # Architectural design and Phase specifications
│   ├── ALL_SPEC.md       # Target project specifications
│   └── required_envs.json
├── src/
│   ├── cli.py            # Typer command line entries
│   ├── graph.py          # Phase definitions for Coder, Integration, and QA graphs
│   ├── state.py          # Typed definitions for CycleState & IntegrationState
│   ├── services/         # Core application services (e.g. Workflow, Conflict Manager)
│   └── nodes/            # Isolated LangGraph components
├── tests/                # Unit and Integration test modules
└── tutorials/            # Marimo UAT scenarios
```

## License

MIT License
