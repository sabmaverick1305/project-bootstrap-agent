"""Target, staging, and promotion (see AGENTS.md, "Workspace and Rejection Flow").

The output directory is the workspace. Projects are built in
`<output>/.bootstrap-staging/<run id>/` and moved to `<output>/<project_name>` only after
the user accepts the result. Staging is deleted on accept, reject, cancel, or failure.
"""

import shutil
import tomllib
import uuid
from pathlib import Path

from app.builder.validate import INSTALL_TIMEOUT_SECONDS
from app.models.spec import ProjectSpec
from app.tools.files import WorkspaceError, resolve_inside
from app.tools.runner import CommandResult, run_command

STAGING_DIR_NAME = ".bootstrap-staging"


def target_dir(output_dir: Path, spec: ProjectSpec) -> Path:
    return resolve_inside(output_dir, spec.project_name)


def has_conflict(target: Path) -> bool:
    """True if `target` exists and is not an empty directory (it must never be overwritten)."""
    return target.exists() and (not target.is_dir() or any(target.iterdir()))


def new_staging_root(output_dir: Path) -> Path:
    staging_root = resolve_inside(output_dir, f"{STAGING_DIR_NAME}/{uuid.uuid4().hex[:12]}")
    staging_root.mkdir(parents=True)
    return staging_root


def discard_staging(staging_root: Path, output_dir: Path) -> None:
    """Delete one staging run directory, and the staging parent if it is now empty."""
    staging_parent = resolve_inside(output_dir, STAGING_DIR_NAME)
    staging_root = staging_root.resolve()
    if staging_root.parent != staging_parent:
        raise WorkspaceError(f"Refusing to delete {staging_root}: not a staging directory.")
    shutil.rmtree(staging_root, ignore_errors=True)
    if staging_parent.is_dir() and not any(staging_parent.iterdir()):
        staging_parent.rmdir()


def promote(project_dir: Path, target: Path) -> CommandResult:
    """Move the validated project to `target` and reinstall its environment there."""
    if has_conflict(target):
        raise WorkspaceError(f"{target} already exists and is not empty; it was not changed.")
    # A virtualenv contains absolute paths and breaks when moved, so rebuild it in place.
    shutil.rmtree(project_dir / ".venv", ignore_errors=True)
    if target.exists():
        target.rmdir()  # empty directory, checked above
    project_dir.rename(target)
    return run_command(["uv", "sync"], target, INSTALL_TIMEOUT_SECONDS)


def read_locked_versions(project_dir: Path, names: list[str]) -> dict[str, str]:
    """Exact installed versions of `names`, read from uv.lock."""
    lock_file = project_dir / "uv.lock"
    if not lock_file.is_file():
        return {}
    packages = tomllib.loads(lock_file.read_text(encoding="utf-8")).get("package", [])
    locked = {package["name"]: package["version"] for package in packages if "version" in package}
    return {name: locked[name] for name in names if name in locked}
