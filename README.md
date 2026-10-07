# project-bootstrap-agent

Turns a request like "Setup a Python + FastAPI project for me" into a runnable, validated
local project. It looks up current versions, shows a plan for approval, builds the project,
installs it, runs lint, tests, and a health check, repairs failures (up to 3 attempts), and
asks for approval again before moving the project into place.

v0.1 builds Python + FastAPI projects with uv, Uvicorn, pytest, and Ruff.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- An Anthropic API key: `export ANTHROPIC_API_KEY=...` (or `ant auth login`)

## Install

From a clone of this repository:

```
uv tool install .
```

After pulling changes, reinstall with `uv tool install --reinstall .`.
To remove it: `uv tool uninstall project-bootstrap-agent`.

## Use

```
bootstrap-agent "Setup a Python + FastAPI project for me" --name orders-api --output-dir ~/code
```

| Argument | Required | Default | Description |
|---|---|---|---|
| `"<request>"` | no | asked interactively | What to build, in plain language |
| `--name` | no | asked interactively; Enter keeps `fastapi-app` | Project name (normalized, e.g. "My Service" → `my-service`) |
| `--output-dir` | no | current directory | Where the project folder is created |

Shorter forms:

```
bootstrap-agent "Setup a Python + FastAPI project for me"   # asks for the name, builds in the current directory
bootstrap-agent                                              # asks what to build, then the name
bootstrap-agent --help                                       # all options
```

A name in the request ("... called orders api") also works and skips the name prompt.
`--name` wins if both are given.

To run from source without installing:

```
uv run python -m app.main "Setup a Python + FastAPI project for me" --name orders-api --output-dir ~/code
```

At the plan: `approve`, `edit` (describe a change, e.g. "call it orders-api"), or `cancel`.
At the result: `accept`, `changes`, or `reject`. Only an accepted project is kept; nothing
in the output directory is ever overwritten.

## Develop

```
uv sync
uv run pytest                  # unit tests (offline)
uv run pytest -m integration   # real builds and live LLM calls (needs network and API key)
uv run ruff check . && uv run ruff format --check .
```

See `AGENTS.md` for the design and rules.
