"""Write a generated project into the staging directory.

See AGENTS.md, "Workspace and Rejection Flow".
"""

from pathlib import Path

from app.builder.templates import render_files
from app.models.spec import ProjectSpec
from app.tools.files import resolve_inside, write_file


class GenerationError(Exception):
    """Raised when the project cannot be generated from the given spec."""


def generate_project(spec: ProjectSpec, staging_root: Path) -> Path:
    """Render every file for `spec` into `staging_root/<project_name>` and return that path."""
    if not spec.is_resolved:
        raise GenerationError(f"ProjectSpec has unresolved values: {spec.unresolved_fields()}")

    staging_root.mkdir(parents=True, exist_ok=True)
    project_dir = resolve_inside(staging_root, spec.project_name)
    if project_dir.exists() and any(project_dir.iterdir()):
        raise GenerationError(f"Staging directory {project_dir} already exists and is not empty.")

    for relative_path, content in render_files(spec).items():
        write_file(project_dir, relative_path, content)
    return project_dir
