"""ProjectSpec: the source of truth for project generation (see AGENTS.md, principle 5).

Fields are restricted to what v0.1 supports. Anything else fails validation, so
out-of-scope choices cannot reach the builder.
"""

import re
from typing import Any, Literal

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.naming import normalize_project_name, to_package_name

_PYTHON_MINOR_PATTERN = re.compile(r"^3\.\d+$")


def _check_stable_version(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        version = Version(value)
    except InvalidVersion as exc:
        raise ValueError(f"{value!r} is not a valid version.") from exc
    if version.is_prerelease or version.is_devrelease:
        raise ValueError(f"{value!r} is a pre-release; only stable versions are allowed.")
    return value


class Dependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str | None = None  # None until resolved by a version lookup

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str | None) -> str | None:
        return _check_stable_version(value)


def _default_dev_dependencies() -> list[Dependency]:
    return [Dependency(name="pytest"), Dependency(name="httpx2"), Dependency(name="ruff")]


class ProjectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str
    package_name: str = ""  # always derived from project_name
    language: Literal["python"] = "python"
    language_version: str | None = None  # Python minor version, e.g. "3.14"
    framework: Literal["fastapi"] = "fastapi"
    framework_version: str | None = None
    package_manager: Literal["uv"] = "uv"
    build_backend: Literal["uv_build"] = "uv_build"  # needed to install the src/ package
    build_backend_version: str | None = None
    server: Literal["uvicorn"] = "uvicorn"
    server_version: str | None = None
    database: None = None
    docker: Literal[False] = False
    testing: Literal["pytest"] = "pytest"
    linting: Literal["ruff"] = "ruff"
    dependencies: list[Dependency] = Field(default_factory=list)  # extra runtime packages
    dev_dependencies: list[Dependency] = Field(default_factory=_default_dev_dependencies)
    # Fields the user stated explicitly. Repairs must not change these without asking.
    user_specified: set[str] = Field(default_factory=set)
    # Requests outside v0.1 scope, reported in the plan instead of silently dropped.
    unsupported_requests: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _derive_names(cls, data: Any) -> Any:
        if isinstance(data, dict) and "project_name" in data:
            project_name = normalize_project_name(data["project_name"])
            data = {**data, "project_name": project_name}
            data["package_name"] = to_package_name(project_name)
        return data

    @field_validator("language_version")
    @classmethod
    def _check_language_version(cls, value: str | None) -> str | None:
        if value is not None and not _PYTHON_MINOR_PATTERN.match(value):
            raise ValueError(f"language_version must look like '3.14', got {value!r}.")
        return value

    @field_validator("framework_version", "server_version", "build_backend_version")
    @classmethod
    def _check_versions(cls, value: str | None) -> str | None:
        return _check_stable_version(value)

    @field_validator("user_specified")
    @classmethod
    def _check_user_specified(cls, value: set[str]) -> set[str]:
        unknown = value - set(cls.model_fields)
        if unknown:
            raise ValueError(f"Unknown ProjectSpec fields in user_specified: {sorted(unknown)}")
        return value

    def updated(self, **changes: Any) -> "ProjectSpec":
        """Return a re-validated copy with `changes` applied."""
        return ProjectSpec.model_validate({**self.model_dump(), **changes})

    def runtime_packages(self) -> list[Dependency]:
        return [
            Dependency(name=self.framework, version=self.framework_version),
            Dependency(name=self.server, version=self.server_version),
            *self.dependencies,
        ]

    def build_package(self) -> Dependency:
        return Dependency(name=self.build_backend, version=self.build_backend_version)

    def all_packages(self) -> list[Dependency]:
        return [*self.runtime_packages(), *self.dev_dependencies, self.build_package()]

    def unresolved_fields(self) -> list[str]:
        """Names of values still waiting on a lookup ('language_version' or package names)."""
        missing = [] if self.language_version else ["language_version"]
        missing += [dep.name for dep in self.all_packages() if dep.version is None]
        return missing

    @property
    def is_resolved(self) -> bool:
        return not self.unresolved_fields()

    def with_package_versions(self, versions: dict[str, str]) -> "ProjectSpec":
        """Return a copy with package versions filled in from a {name: version} mapping."""

        def apply(deps: list[Dependency]) -> list[dict[str, Any]]:
            return [
                {"name": dep.name, "version": versions.get(dep.name, dep.version)} for dep in deps
            ]

        return self.updated(
            framework_version=versions.get(self.framework, self.framework_version),
            server_version=versions.get(self.server, self.server_version),
            build_backend_version=versions.get(self.build_backend, self.build_backend_version),
            dependencies=apply(self.dependencies),
            dev_dependencies=apply(self.dev_dependencies),
        )
