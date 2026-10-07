"""Bounded repair of a failed validation (see AGENTS.md, principle 8).

The LLM proposes edits; this module decides whether they are allowed:
- only files the generator created can change;
- the parts of pyproject.toml that come from ProjectSpec cannot change (name, Python
  version, dependencies, build system), so repairs never override the spec or the user;
- every attempt must change at least one file.
"""

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.builder.templates import render_files
from app.builder.validate import StepResult
from app.models.repair import RepairProposal
from app.models.spec import ProjectSpec
from app.tools.files import read_file, write_file

MAX_REPAIR_ATTEMPTS = 3
MAX_FILE_CHARS = 50_000


class RepairRejected(Exception):
    """Raised when a proposed repair cannot or may not be applied. Message is user-facing."""


@dataclass(frozen=True)
class RepairAttempt:
    failed_step: str
    diagnosis: str
    changed_files: tuple[str, ...]


def editable_files(spec: ProjectSpec) -> list[str]:
    return sorted(render_files(spec))


def _protected_pyproject(text: str) -> dict:
    data = tomllib.loads(text)
    project = data.get("project", {})
    return {
        "name": project.get("name"),
        "requires-python": project.get("requires-python"),
        "dependencies": project.get("dependencies"),
        "dependency-groups": data.get("dependency-groups"),
        "build-system": data.get("build-system"),
    }


def build_repair_context(
    spec: ProjectSpec,
    project_dir: Path,
    failed: StepResult,
    history: list[RepairAttempt],
) -> str:
    """Everything the LLM needs to diagnose the failure, as one prompt section."""
    files = "\n".join(
        f'<file path="{path}">\n{read_file(project_dir, path)}</file>'
        for path in editable_files(spec)
    )
    previous = "\n".join(
        f"- attempt {i}: failed at {a.failed_step}; diagnosis: {a.diagnosis}; "
        f"changed: {', '.join(a.changed_files)}"
        for i, a in enumerate(history, start=1)
    )
    return (
        f"<project_spec>\n{json.dumps(spec.model_dump(mode='json'), indent=2)}\n</project_spec>\n\n"
        f'<failed_step name="{failed.name}" command="{failed.command}">\n'
        f"{failed.output}\n</failed_step>\n\n"
        f"<files>\n{files}\n</files>\n\n"
        f"<previous_attempts>\n{previous or 'none'}\n</previous_attempts>"
    )


def apply_repair(spec: ProjectSpec, project_dir: Path, proposal: RepairProposal) -> list[str]:
    """Check every edit, then write them. Returns the changed paths."""
    if not proposal.can_repair:
        raise RepairRejected(f"Cannot be repaired safely: {proposal.diagnosis}")
    if not proposal.edits:
        raise RepairRejected("The repair proposed no changes.")

    allowed = set(editable_files(spec))
    changed: dict[str, str] = {}
    for edit in proposal.edits:
        path = edit.path.strip().removeprefix("./")
        if path not in allowed:
            raise RepairRejected(f"Repair tried to edit {edit.path!r}, which is not editable.")
        if len(edit.content) > MAX_FILE_CHARS:
            raise RepairRejected(f"Repair content for {path} is too large.")
        current = read_file(project_dir, path)
        if path == "pyproject.toml":
            try:
                protected_changed = _protected_pyproject(edit.content) != _protected_pyproject(
                    current
                )
            except tomllib.TOMLDecodeError as exc:
                raise RepairRejected(f"Repair made pyproject.toml invalid: {exc}") from exc
            if protected_changed:
                raise RepairRejected(
                    "Repair tried to change the project name, Python version, dependencies, "
                    "or build system in pyproject.toml. These come from the ProjectSpec."
                )
        if edit.content != current:
            changed[path] = edit.content

    if not changed:
        raise RepairRejected("The repair did not change any file.")
    for path, content in changed.items():
        write_file(project_dir, path, content)
    return sorted(changed)
