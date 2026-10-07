"""Naming conventions for generated projects (see AGENTS.md, "Naming Conventions")."""

import keyword
import re
import sys

# Import names the generated project depends on. A package with one of these
# names would shadow the dependency.
DEPENDENCY_IMPORT_NAMES = frozenset(
    {"fastapi", "starlette", "pydantic", "uvicorn", "pytest", "ruff", "httpx", "httpx2"}
)
# Not in sys.stdlib_module_names, but shadowing them still breaks imports or test discovery.
RESERVED_NAMES = frozenset({"test", "tests"})

_PROJECT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


class InvalidNameError(ValueError):
    """Raised when a name cannot be turned into a valid project or package name."""


def default_project_name(framework: str) -> str:
    return normalize_project_name(f"{framework}-app")


def normalize_project_name(raw: str) -> str:
    """Normalize a user-supplied name to kebab-case: 'My API_Service' -> 'my-api-service'."""
    name = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")
    if not name:
        raise InvalidNameError(f"Project name {raw!r} has no letters or digits.")
    if not _PROJECT_NAME_PATTERN.match(name):
        raise InvalidNameError(f"Project name {raw!r} must start with a letter (got {name!r}).")
    return name


def to_package_name(project_name: str) -> str:
    """Derive the snake_case import package name from a kebab-case project name."""
    package_name = project_name.replace("-", "_")
    check_module_name(package_name)
    return package_name


def check_module_name(name: str) -> None:
    """Raise InvalidNameError if `name` is not a safe snake_case module or package name."""
    if not name.isidentifier() or name != name.lower():
        raise InvalidNameError(f"{name!r} is not a valid snake_case Python identifier.")
    if keyword.iskeyword(name):
        raise InvalidNameError(f"{name!r} is a Python keyword.")
    if name in sys.stdlib_module_names or name in RESERVED_NAMES:
        raise InvalidNameError(f"{name!r} would shadow the standard library module {name!r}.")
    if name in DEPENDENCY_IMPORT_NAMES:
        raise InvalidNameError(f"{name!r} would shadow the dependency {name!r}.")
