"""User intent: what the LLM extracts from a request, and how it becomes a ProjectSpec.

The LLM only reports what the user asked for (IntentDraft). This module decides, in code,
what v0.1 supports: supported choices become user-specified ProjectSpec fields, other
choices are recorded in `unsupported_requests`, and a request for a different language or
framework is rejected outright because v0.1 cannot build it.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.models.naming import default_project_name
from app.models.spec import ProjectSpec

Category = Literal[
    "language",
    "framework",
    "package_manager",
    "server",
    "testing",
    "linting",
    "database",
    "containerization",
    "deployment",
    "other",
]


class RequestedItem(BaseModel):
    category: Category
    value: str = Field(description="Canonical lowercase name, e.g. 'fastapi', 'postgresql'.")
    version: str | None = Field(
        default=None,
        description="Only a version the user named explicitly. Null for 'latest' or unspecified.",
    )


class IntentDraft(BaseModel):
    """Structured output of the intent step. Reports the request; makes no decisions."""

    is_project_request: bool = Field(
        description="False if the user is not asking to create a software project."
    )
    project_name: str | None = Field(
        default=None, description="Only a name the user gave explicitly."
    )
    requested: list[RequestedItem] = Field(
        default_factory=list,
        description="Technologies the user asked for or clearly implied. No defaults.",
    )
    clarification_question: str | None = Field(
        default=None,
        description="One short question, only if the request is too ambiguous to proceed.",
    )


class IntentError(Exception):
    """Raised when a request cannot become a ProjectSpec. The message is shown to the user."""


@dataclass(frozen=True)
class IntentResult:
    spec: ProjectSpec | None = None
    question: str | None = None  # set when the user must clarify before anything else


# category -> (ProjectSpec field, supported value)
SUPPORTED: dict[str, tuple[str, str]] = {
    "language": ("language", "python"),
    "framework": ("framework", "fastapi"),
    "package_manager": ("package_manager", "uv"),
    "server": ("server", "uvicorn"),
    "testing": ("testing", "pytest"),
    "linting": ("linting", "ruff"),
}
# A different choice in these categories means v0.1 cannot build the project at all.
CORE_CATEGORIES = frozenset({"language", "framework"})
VERSION_FIELDS = {
    "language": "language_version",
    "framework": "framework_version",
    "server": "server_version",
}
DEV_DEPENDENCY_CATEGORIES = frozenset({"testing", "linting"})

_PYTHON_VERSION = re.compile(r"^(3\.\d+)(\.\d+)?$")


def _canonical(value: str) -> str:
    """'Fast API' / 'fast-api' / 'FastAPI' -> 'fastapi'."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _language_version(version: str) -> str:
    match = _PYTHON_VERSION.match(version.strip())
    if not match:
        raise IntentError(f"Python version {version!r} is not a valid version like '3.13'.")
    return match.group(1)


def build_spec(draft: IntentDraft) -> IntentResult:
    if not draft.is_project_request:
        raise IntentError("This doesn't look like a request to create a software project.")
    if draft.clarification_question:
        return IntentResult(question=draft.clarification_question)

    fields: dict[str, Any] = {}
    user_specified: set[str] = set()
    unsupported: list[str] = []
    dev_versions: dict[str, str] = {}

    for item in draft.requested:
        value = _canonical(item.value)
        supported = SUPPORTED.get(item.category)

        if supported is None or value != supported[1]:
            if item.category in CORE_CATEGORIES:
                raise IntentError(
                    f"Only Python + FastAPI projects are supported for now; "
                    f"the request asks for {item.category} '{item.value}'."
                )
            note = "not supported yet; " + (
                f"{supported[1]} will be used" if supported else "it will be left out"
            )
            unsupported.append(f"{item.category}: {item.value} ({note})")
            continue

        field_name = supported[0]
        user_specified.add(field_name)
        if item.version:
            if item.category == "language":
                fields["language_version"] = _language_version(item.version)
                user_specified.add("language_version")
            elif item.category in VERSION_FIELDS:
                fields[VERSION_FIELDS[item.category]] = item.version
                user_specified.add(VERSION_FIELDS[item.category])
            elif item.category in DEV_DEPENDENCY_CATEGORIES:
                dev_versions[value] = item.version

    if draft.project_name:
        user_specified.add("project_name")
    project_name = draft.project_name or default_project_name("fastapi")

    try:
        spec = ProjectSpec(
            project_name=project_name,
            user_specified=user_specified,
            unsupported_requests=unsupported,
            **fields,
        )
        if dev_versions:
            spec = spec.with_package_versions(dev_versions)
    except ValidationError as exc:
        messages = "; ".join(error["msg"] for error in exc.errors())
        raise IntentError(f"The request can't be turned into a valid project: {messages}") from exc
    return IntentResult(spec=spec)
