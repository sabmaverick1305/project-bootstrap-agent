"""Filesystem tools confined to a workspace root (see AGENTS.md, "Safety")."""

from pathlib import Path


class WorkspaceError(Exception):
    """Raised when a path would escape the workspace or the target is not usable."""


def resolve_inside(root: Path, relative: str | Path) -> Path:
    """Resolve `relative` against `root`, refusing anything outside `root` (or `root` itself)."""
    root = root.resolve()
    target = (root / relative).resolve()
    if target == root or not target.is_relative_to(root):
        raise WorkspaceError(f"Path {str(relative)!r} is outside the workspace {root}.")
    return target


def write_file(root: Path, relative: str | Path, content: str) -> Path:
    path = resolve_inside(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def read_file(root: Path, relative: str | Path) -> str:
    return resolve_inside(root, relative).read_text(encoding="utf-8")
