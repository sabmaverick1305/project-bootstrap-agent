# AGENTS.md

## Project: AI Project Bootstrap Agent

### Mission

Build an AI agent that can take a high-level software project request and turn it into a runnable, validated local project.

Example:

> "Setup a Python + FastAPI project for me."

The agent must understand the request, research current technical information through tools, recommend a complete plan, get the user's approval, create the project, run it, validate it, repair failures, and get the user's final approval.

The goal is NOT simply to generate source code.

The goal is:

User Intent
→ Requirements
→ Current Dependency Research
→ Engineering Recommendations
→ Plan Approval
→ Project Specification (final)
→ Project Creation
→ Execution
→ Validation
→ Repair
→ Result Approval
→ Runnable Project


### Related Files

- `AGENTS.md` (this file) defines the product behavior and development rules. It is the source of truth.
- `app/agent/system_prompt.md` is the runtime prompt given to the bootstrap agent's LLM. It must stay consistent with this file. When behavior changes here, update the system prompt in the same change.


## Core Principles

### 1. Preserve User Intent

The user's requested stack and constraints are authoritative.

Do not silently replace explicitly requested technologies.

Example:

User:
> "Build a Python + FastAPI project."

The agent must not offer Flask, Django, or any other framework as an alternative. Flask is a framework, not a server, and is never an alternative to Uvicorn.

The agent may recommend choices for things the user did not specify, such as the application server, package manager, test tool, and linter.


### 2. Classify Unresolved Information

Classify every unresolved ProjectSpec field into:

1. USER_DECISION
2. ENGINEERING_DEFAULT
3. TOOL_LOOKUP

USER_DECISION:
A choice that materially changes architecture or represents a genuine preference. Present it as a recommendation with alternatives in the plan.

ENGINEERING_DEFAULT:
A conventional, low-risk, reversible choice. Apply the default and show it in the plan so the user can change it.

TOOL_LOOKUP:
Information that is current or authoritative. Resolve it with a tool, never from model memory.

Examples:

| Field | Classification | Default / Source |
|---|---|---|
| Python version | TOOL_LOOKUP | newest Python supported by all selected packages |
| FastAPI version | TOOL_LOOKUP | `get_latest_version("fastapi")` |
| Application server | ENGINEERING_DEFAULT | Uvicorn (alternatives: Gunicorn + Uvicorn workers, Hypercorn) |
| Package manager | ENGINEERING_DEFAULT | uv (alternatives: pip + venv, Poetry) |
| Testing | ENGINEERING_DEFAULT | pytest + httpx2 (for FastAPI's TestClient; Starlette deprecates httpx for it) |
| Linting / formatting | ENGINEERING_DEFAULT | Ruff |
| Database | USER_DECISION | only if the user's request needs persistence |

Note: `pyproject.toml` is the project configuration file used by every option above. It is not an alternative to pip; the choice is which tool installs and manages packages.

In v0.1 the builder supports only the defaults (Uvicorn, uv, pytest, Ruff). Alternatives are listed in the plan for transparency. If the user selects one, record it in `unsupported_requests` and say it is not supported yet.


### 3. One Plan, Not a Questionnaire

Do not ask about each decision separately.

Gather all lookups, defaults, and recommendations, then present them to the user as ONE consolidated plan. The user approves, edits, or cancels the plan as a whole.

Ask a separate question before the plan only when a decision blocks the lookups themselves (for example, the request is too ambiguous to know which packages are needed).


### 4. Do Not Hallucinate Current Information

The LLM must not rely on model knowledge for information that can become stale.

Examples:

- latest package version
- Python version compatibility
- deprecated APIs
- package installation instructions

Always fetch versions through tools such as `get_latest_version` before recommending them. Lookups happen before the plan is shown, never after.

"Latest" means the latest stable release: exclude pre-releases (alpha, beta, rc, dev) and yanked versions.

"Latest Python" means the newest stable Python that every selected package supports (according to the package's `requires_python` metadata), not simply the newest Python release.

The LLM decides WHAT information is needed.

Tools determine the authoritative VALUE.


### 5. ProjectSpec Is the Source of Truth

Convert the user's requirements into a structured ProjectSpec before generation.

Example:

```json
{
  "project_name": "fastapi-app",
  "package_name": "fastapi_app",
  "language": "python",
  "language_version": "resolved-version",
  "framework": "fastapi",
  "framework_version": "resolved-version",
  "package_manager": "uv",
  "build_backend": "uv_build",
  "build_backend_version": "resolved-version",
  "server": "uvicorn",
  "server_version": "resolved-version",
  "database": null,
  "docker": false,
  "testing": "pytest",
  "linting": "ruff",
  "dependencies": [],
  "dev_dependencies": [
    {"name": "pytest", "version": "resolved-version"},
    {"name": "httpx2", "version": "resolved-version"},
    {"name": "ruff", "version": "resolved-version"}
  ],
  "user_specified": ["language", "framework"],
  "unsupported_requests": []
}
```

The model is defined in `app/models/spec.py`. It only accepts v0.1 values (for example, `framework` must be `fastapi`), so out-of-scope choices fail validation instead of reaching the builder. `package_name` is always derived from `project_name`.

Version policy for generated `pyproject.toml`: dependencies get a lower bound at the resolved version (`fastapi>=X`), and `uv.lock` pins the exact installed versions. The build backend also gets an upper bound (`uv_build>=X,<next minor`) because its configuration can change between releases.

Project generation must operate from ProjectSpec rather than directly from conversational text.

Changes requested by the user (at either approval gate) must update ProjectSpec first, re-run any affected lookups, and then change the generated project.

Fields the user stated explicitly are marked as user-specified. The agent must not change them, even during repair, without asking the user.


### 6. Two Approval Gates

The user approves twice:

1. **Plan approval** — after lookups and recommendations, before any file is written.
2. **Result approval** — after the project has been generated, run, and validated.

No files are written before plan approval.

No project is delivered to its final location before result approval.


### 7. Generated Projects Must Run

Creating files is not sufficient. Before result approval, the agent must at least prove that the project installs and runs.

Minimum required check (always):

1. Dependencies install successfully (`uv sync`)
2. Application starts (`uv run uvicorn <package_name>.main:app`)
3. Health endpoint `GET /health` returns HTTP 200

Full validation (required for v0.1 Definition of Done):

4. Ruff check passes (`uv run ruff check .`)
5. Ruff format check passes (`uv run ruff format --check .`)
6. Tests pass (`uv run pytest`)

Never present a project for result approval as working unless these checks passed. If they did not pass, present it as failed with diagnostics.


### 8. Failures Trigger Diagnosis and Repair

Do not immediately return execution errors to the user.

Use the loop:

Execute
→ Observe
→ Diagnose
→ Repair
→ Re-execute
→ Validate

Rules:

- Maximum repair attempts: **3** per bootstrap (configurable in application code, not in the prompt).
- Each attempt must change something. Never retry the same operation with the same inputs.
- Repairs may only edit files the generator created (full-file replacements; no new files).
- Repairs must not change the parts of `pyproject.toml` that come from ProjectSpec: project name, `requires-python`, dependencies, dependency groups, build system. If a fix needs that, the LLM sets `can_repair: false` and the failure is reported. (Changing a version means changing the ProjectSpec, which needs the user.)
- Repairs fix the application, not the tests, unless a test is itself wrong. Tests are never weakened to pass.
- Every repair is listed in the result shown at result approval, even when validation finally passes.
- Repairs must not change user-specified fields (see principle 5). If a fix requires that, stop and ask the user.

If the problem is not resolved within the limit, stop and report the failure with useful diagnostic information.


## Naming Conventions for Generated Projects

Generated Python projects follow PEP 8 and these defaults:

| Item | Convention | Example |
|---|---|---|
| Project / distribution name (`pyproject.toml` `name`, directory name) | kebab-case | `fastapi-app` |
| Import package name | snake_case, derived from project name | `fastapi_app` |
| Module (file) names | snake_case, lowercase, `.py` | `main.py`, `health_check.py` |
| Test files | `test_<module>.py` | `test_health.py` |
| Test functions | `test_<behavior>` | `test_health_returns_ok` |
| Functions, variables | snake_case | `get_settings`, `app_name` |
| Classes | PascalCase | `HealthResponse` |
| Constants | UPPER_SNAKE_CASE | `DEFAULT_PORT` |
| Directories inside the package | snake_case | `api/`, `core/` |

Rules:

- No hyphens, spaces, or uppercase letters in `.py` file or package names.
- Names must be valid Python identifiers and must not shadow standard library or dependency names (for example, do not name a module `fastapi.py`, `typing.py`, or `test.py`).
- If neither the request nor `--name` gives a project name, ask for one before the plan (one prompt; Enter accepts the default `<framework>-app`, for example `fastapi-app`). This is the only question asked by default, at the user's request. The chosen name is kept across later edits unless an edit renames the project.
- If the user gives a name, normalize it: lowercase, spaces and underscores become hyphens for the project name, hyphens become underscores for the package name.

Default project layout (src layout):

```
fastapi-app/
├── pyproject.toml
├── uv.lock
├── .python-version
├── .gitignore
├── README.md
├── src/
│   └── fastapi_app/
│       ├── __init__.py
│       └── main.py          # FastAPI app with GET /health
└── tests/
    ├── __init__.py
    └── test_health.py
```

Generate only files the ProjectSpec requires. Do not add extra layers (routers, services, config modules) unless the request needs them.


## Initial Scope

Version 0.1 supports:

- Python
- FastAPI
- uv
- Uvicorn
- pytest
- Ruff
- basic project structure
- package/version lookup
- local filesystem creation
- local command execution
- application validation
- bounded repair loop
- plan and result approval gates

Do NOT expand scope prematurely.

If the user requests something outside v0.1 scope (for example, a database or Docker), say it is not supported yet, leave it out of the ProjectSpec, and note it in the plan. Do not silently ignore it.


## Explicitly Out of Scope for v0.1

Do not add unless explicitly required by the current milestone:

- multi-language support
- Kubernetes
- AWS deployment
- Terraform
- CI/CD generation
- GitHub repository creation
- complex multi-agent architecture
- LangChain
- LangGraph
- vector databases
- RAG
- autonomous cloud provisioning

These may be added later.

Do not introduce them simply because they are common in AI-agent projects.


## Agent Workflow

```
USER_REQUEST
      |
      v
PARSE_INTENT ──────────────> draft ProjectSpec (explicit fields marked user-specified)
      |
      v
LOOKUP ────────────────────> get_latest_version for each package,
      |                      resolve compatible Python version
      v
RECOMMEND ─────────────────> apply ENGINEERING_DEFAULTs, list alternatives,
      |                      add test + lint packages
      v
PRESENT_PLAN
      |
      v
APPROVAL #1 (plan) ───┬── approve ──> FINALIZE_PROJECT_SPEC
                      ├── edit ─────> UPDATE_PROJECT_SPEC ──> back to LOOKUP
                      └── cancel ───> EXIT (nothing written)
      |
      v
GENERATE_PROJECT ──────────> write files into a staging directory
      |
      v
EXECUTE ───────────────────> uv sync
      |
      v
VALIDATE ──────────────────> ruff check, ruff format --check, pytest,
      |                      start app, GET /health == 200
      |
      +── FAIL ──> DIAGNOSE ──> REPAIR ──> back to EXECUTE   (max 3 attempts)
      |                                    limit reached ──> REPORT_FAILURE
      v PASS
PRESENT_RESULT ────────────> file tree, resolved versions, validation results,
      |                      run command
      v
APPROVAL #2 (result) ─┬── accept ──> move project to target directory ──> DONE
                      ├── changes ─> UPDATE_PROJECT_SPEC ──> back to LOOKUP
                      └── reject ──> discard staging directory ──> EXIT
      |
      v
EXIT
```

Example plan presented at APPROVAL #1:

```
Plan for: fastapi-app
  Python        <resolved>   newest version supported by all packages   [lookup]
  FastAPI       <resolved>                                               [lookup]
  Server        Uvicorn <resolved>   (alternatives: Gunicorn + Uvicorn, Hypercorn)
  Package mgr   uv                   (alternatives: pip + venv, Poetry)
  Tests         pytest <resolved>, httpx2 <resolved>
  Lint/format   Ruff <resolved>
  Files         pyproject.toml, src/fastapi_app/main.py, tests/test_health.py, ...
  Commands      uv sync, uv run ruff check ., uv run pytest, uv run uvicorn ...
Approve? [approve / edit / cancel]
```


## Workspace and Rejection Flow

- The project is generated and validated in a staging directory inside the agent's workspace.
- On result approval, the project is moved to the target directory.
- On rejection or cancel, the staging directory is deleted. Nothing is left in the target directory.
- If the target directory already exists and is not empty, the agent must not overwrite it. Report it in the plan and ask for a different name or location.


## Tool Philosophy

Prefer small, explicit tools over unrestricted agent behavior.

Expected tools:

| Tool | Purpose |
|---|---|
| `get_latest_version(package)` | Latest stable, non-yanked version from the PyPI JSON API, plus `requires_python` |
| `get_python_versions()` | Released, non-end-of-life Python versions from endoflife.date |
| `resolve_python_version(packages, releases)` | Newest supported Python that satisfies every package's `requires_python` |
| `create_directory(path)` | Create a directory inside the workspace |
| `write_file(path, content)` | Write a file inside the workspace |
| `read_file(path)` | Read a file inside the workspace |
| `run_command(argv)` | Run an allowlisted command inside the workspace |
| `run_tests()` | Run pytest and return structured results |
| `check_application_health(url)` | Call the running app; return HTTP status and body |
| `ask_user(plan)` | Present a plan or result and return the approval decision |

Tools return structured results (status, data, error), not free text.

After installation, read resolved versions back from `uv.lock` into the ProjectSpec so the reported versions match what was actually installed.


## Safety

Shell and filesystem access must be constrained.

The agent must not:

- delete arbitrary user directories
- modify files outside the project workspace
- expose environment secrets
- execute destructive shell commands
- install unrelated system packages
- upload project contents externally without permission

Enforced in application code:

- **Command allowlist:** exactly `uv sync`, or `uv run <program> ...` / `<program> ...` where program is `pytest`, `ruff`, or `uvicorn`. No other uv subcommands and no uv flags (for example `--with` or `--directory`). Anything else is rejected before it runs.
- **Environment:** child processes do not receive variables starting with `ANTHROPIC_` or containing `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, or `CREDENTIAL`, nor the agent's own `VIRTUAL_ENV`.
- **Process groups:** every command runs in its own process group, so a timeout or stop also ends the children that `uv run` starts.
- **No shell:** commands run as argument lists, without shell expansion, pipes, or redirection.
- **Working directory:** always the staging project directory.
- **Timeouts:** 300 seconds for `uv sync`, 120 seconds for tests and lint, 30 seconds for the application to become healthy.
- **Server lifecycle:** start the application on a free local port (bind to `127.0.0.1`), run the health check, then always stop the process, including on failure.
- **Paths:** every file path is resolved and checked to be inside the workspace before any read or write.


## Architecture Principle

Keep three concerns separate:

### Reasoning

The LLM determines:

- user intent
- missing information
- engineering recommendations
- when tools are required
- failure diagnosis

### Tools

Tools provide:

- current external information
- filesystem operations
- command execution
- validation results

### Deterministic Application Logic

Application code controls:

- ProjectSpec validation
- state transitions and approval gates
- repair limits
- workspace boundaries
- tool permissions
- execution policy
- naming normalization


## Definition of Done

For v0.1, the following request:

> "Setup a Python + FastAPI project for me."

should result in:

1. Valid ProjectSpec
2. Required versions resolved through tools
3. Plan presented and approved by the user
4. Project directory created following the naming conventions
5. pyproject.toml created
6. FastAPI application created with a `/health` endpoint
7. Dependencies installed
8. Tests created
9. Tests passing
10. Ruff validation passing
11. Application successfully started
12. Health endpoint returning HTTP 200
13. Result presented and approved by the user
14. Project moved to the target directory

Only then should the agent report the project as successfully bootstrapped.


## Developing This Repo

### Stack (the agent itself)

- Python 3.12+, managed with uv
- Anthropic Python SDK (`anthropic`), model `claude-opus-5-5`
- `pydantic` for ProjectSpec and LLM structured output
- `httpx` for PyPI and endoflife.date lookups
- `packaging` for version and `requires_python` handling
- pytest and Ruff for the agent's own tests and linting

No LangChain or LangGraph in v0.1. Orchestration is a plain Python loop in `app/main.py`; approvals are terminal prompts. The whole session runs in one process with no checkpointing or resume. Resume is planned for v0.2.

### Where the LLM is used

Only in two places. Everything else is deterministic code.

1. **Parse intent:** the LLM reports what the user asked for as an `IntentDraft` (structured output via `client.beta.messages.parse()`). It makes no decisions. `build_spec()` in `app/models/intent.py` then decides in code: supported choices become user-specified ProjectSpec fields, other extras go to `unsupported_requests`, and another language or framework is rejected.
2. **Diagnose and repair:** read the failed step's output, the project files, and earlier attempts, and return a `RepairProposal` (diagnosis + full new file contents). `apply_repair()` in `app/builder/repair.py` checks every edit against the repair rules (principle 8) before writing anything. Effort is `high` for this call.

Recommendations are fixed defaults, version lookups are HTTP calls, and project files come from templates. The LLM does not write the base project from scratch.

LLM call settings (`app/agent/llm.py`): model `claude-opus-5-5`, effort `medium` (set explicitly), system prompt `app/agent/system_prompt.md`, and server-side refusal fallback (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`). Credentials come from `ANTHROPIC_API_KEY` or an `ant auth login` profile. Every API, refusal, and validation failure becomes an `LLMError` with a user-facing message.

### Layout

```
app/
  main.py               # CLI entry point and the workflow loop (Workflow, Services)
  console.py            # terminal prompts; tests swap in a scripted console
  models/spec.py        # ProjectSpec
  models/naming.py      # naming normalization and checks
  models/intent.py      # IntentDraft (LLM output) and build_spec() (deterministic mapping)
  models/repair.py      # RepairProposal (LLM output)
  tools/versions.py     # PyPI and Python version lookups
  tools/files.py        # workspace-confined file reads and writes
  tools/runner.py       # allowlisted subprocess runner and health check
  builder/templates.py  # file templates for the generated project
  builder/generate.py   # write templates into the staging directory
  builder/validate.py   # install -> lint -> format -> tests -> health, stops at first failure
  builder/resolve.py    # resolve all versions; pinned versions are verified, not replaced
  builder/plan.py       # plan and result text for the two approval gates
  builder/workspace.py  # target, staging, promotion, uv.lock version read-back
  builder/repair.py     # repair context, guardrails, apply_repair()
  agent/llm.py          # parse_intent, diagnose_and_repair
  agent/system_prompt.md
tests/
```

### Commands

```
uv sync                   # install dependencies
uv run python -m app.main "Setup a Python + FastAPI project" --output-dir ~/code   # run from source
uv tool install --reinstall .   # install/update the `bootstrap-agent` command (distribution: local CLI)
uv run pytest             # run unit tests (offline, fast)
uv run pytest -m integration  # real build (network + uv) and live LLM tests (skipped without credentials)
uv run ruff check .       # lint
uv run ruff format .      # format
```

Unit tests must not call the network or the Anthropic API. Mock HTTP with `httpx.MockTransport`. Tests that need the network or uv go in `tests/test_integration.py` with the `integration` marker; run them before marking a build step done.

### v0.1 Build Order

Each step must work and be tested before the next one starts.

1. [x] ProjectSpec model and naming normalization
2. [x] PyPI and Python version lookups
3. [x] Templates and generation into the staging directory
4. [x] Runner: install, lint, test, start, health check (a fixed spec now builds end to end without an LLM)
5. [x] LLM step: request → ProjectSpec
6. [x] Approval prompts and promotion to the target directory. Pinned versions are verified on PyPI, never replaced. Edits and "request changes" append the change to the request and re-run intent parsing, so ProjectSpec is always rebuilt from the full request. On promotion the staging `.venv` is dropped (virtualenvs hold absolute paths) and `uv sync` rebuilds it in the target.
7. [x] LLM repair loop (live tests break `main.py` three ways: syntax error, bad import, wrong route)


## Development Rule

Every implementation decision should be evaluated against one question:

> Does this help the agent reliably turn user intent into a runnable, validated software project?

If not, it is probably outside the current scope.
