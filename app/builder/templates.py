"""File templates for a generated FastAPI project, rendered only from a resolved ProjectSpec.

Version policy: dependencies get a lower bound at the resolved version (`>=`); uv.lock,
created by `uv sync`, pins the exact installed versions. The build backend also gets an
upper bound, because its configuration can change between releases.
"""

from packaging.version import Version

from app.models.spec import Dependency, ProjectSpec


def _requirement(dep: Dependency) -> str:
    return f"{dep.name}>={dep.version}"


def _build_requirement(dep: Dependency) -> str:
    version = Version(dep.version)
    upper = f"0.{version.minor + 1}" if version.major == 0 else str(version.major + 1)
    return f"{dep.name}>={dep.version},<{upper}"


def _toml_list(items: list[str]) -> str:
    return "\n".join(f'    "{item}",' for item in items)


def pyproject_toml(spec: ProjectSpec) -> str:
    runtime = _toml_list([_requirement(d) for d in spec.runtime_packages()])
    dev = _toml_list([_requirement(d) for d in spec.dev_dependencies])
    build = _build_requirement(spec.build_package())
    return f"""\
[project]
name = "{spec.project_name}"
version = "0.1.0"
description = "A FastAPI application."
readme = "README.md"
requires-python = ">={spec.language_version}"
dependencies = [
{runtime}
]

[dependency-groups]
dev = [
{dev}
]

[build-system]
requires = ["{build}"]
build-backend = "uv_build"

[tool.pytest.ini_options]
testpaths = ["tests"]
"""


def python_version(spec: ProjectSpec) -> str:
    return f"{spec.language_version}\n"


def gitignore(spec: ProjectSpec) -> str:
    return """\
__pycache__/
*.py[oc]
build/
dist/
*.egg-info/
.venv/
.pytest_cache/
.ruff_cache/
"""


def readme(spec: ProjectSpec) -> str:
    return f"""\
# {spec.project_name}

A FastAPI application.

## Setup

```
uv sync
```

## Run

```
uv run uvicorn {spec.package_name}.main:app --reload
```

Health check: http://127.0.0.1:8000/health

## Test and lint

```
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
"""


def main_py(spec: ProjectSpec) -> str:
    return f'''\
from fastapi import FastAPI

app = FastAPI(title="{spec.project_name}")


@app.get("/health")
def health() -> dict[str, str]:
    return {{"status": "ok"}}
'''


def health_test_py(spec: ProjectSpec) -> str:
    return f"""\
from fastapi.testclient import TestClient

from {spec.package_name}.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {{"status": "ok"}}
"""


def render_files(spec: ProjectSpec) -> dict[str, str]:
    """Relative path -> file content for every file the generated project needs."""
    src = f"src/{spec.package_name}"
    return {
        "pyproject.toml": pyproject_toml(spec),
        ".python-version": python_version(spec),
        ".gitignore": gitignore(spec),
        "README.md": readme(spec),
        f"{src}/__init__.py": "",
        f"{src}/main.py": main_py(spec),
        "tests/__init__.py": "",
        "tests/test_health.py": health_test_py(spec),
    }
