import pytest

from app.builder import workspace
from app.builder.workspace import (
    STAGING_DIR_NAME,
    discard_staging,
    has_conflict,
    new_staging_root,
    promote,
    read_locked_versions,
    target_dir,
)
from app.tools.files import WorkspaceError
from app.tools.runner import CommandResult


def test_target_dir_and_conflicts(resolved_spec, tmp_path):
    target = target_dir(tmp_path, resolved_spec)
    assert target == (tmp_path / "fastapi-app").resolve()
    assert not has_conflict(target)  # missing

    target.mkdir()
    assert not has_conflict(target)  # empty directory

    (target / "file.txt").write_text("x")
    assert has_conflict(target)


def test_conflict_when_target_is_a_file(tmp_path):
    path = tmp_path / "fastapi-app"
    path.write_text("x")
    assert has_conflict(path)


def test_staging_lifecycle(tmp_path):
    first = new_staging_root(tmp_path)
    second = new_staging_root(tmp_path)
    (first / "project").mkdir()

    discard_staging(first, tmp_path)
    assert not first.exists()
    assert second.exists()

    discard_staging(second, tmp_path)
    assert not (tmp_path / STAGING_DIR_NAME).exists()  # parent removed once empty


def test_discard_refuses_non_staging_directory(tmp_path):
    precious = tmp_path / "precious"
    precious.mkdir()

    with pytest.raises(WorkspaceError):
        discard_staging(precious, tmp_path)
    assert precious.exists()


@pytest.fixture
def fake_uv_sync(monkeypatch):
    calls = []

    def run_command(argv, cwd, timeout):
        calls.append((list(argv), cwd))
        return CommandResult(tuple(argv), 0, "synced", 0.1)

    monkeypatch.setattr(workspace, "run_command", run_command)
    return calls


def test_promote_moves_project_and_rebuilds_venv(tmp_path, fake_uv_sync):
    project = tmp_path / "staging" / "fastapi-app"
    (project / ".venv").mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]")
    target = tmp_path / "out" / "fastapi-app"
    target.parent.mkdir()

    result = promote(project, target)

    assert result.ok
    assert (target / "pyproject.toml").read_text() == "[project]"
    assert not (target / ".venv").exists()  # the moved venv is dropped; uv sync rebuilds it
    assert not project.exists()
    assert fake_uv_sync == [(["uv", "sync"], target)]


def test_promote_into_empty_existing_directory(tmp_path, fake_uv_sync):
    project = tmp_path / "staging" / "app"
    project.mkdir(parents=True)
    target = tmp_path / "app-target"
    target.mkdir()

    promote(project, target)
    assert target.is_dir()


def test_promote_never_overwrites(tmp_path, fake_uv_sync):
    project = tmp_path / "staging" / "app"
    project.mkdir(parents=True)
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("user data")

    with pytest.raises(WorkspaceError):
        promote(project, target)
    assert (target / "keep.txt").read_text() == "user data"
    assert project.exists()
    assert fake_uv_sync == []


def test_read_locked_versions(tmp_path):
    (tmp_path / "uv.lock").write_text(
        """
version = 1

[[package]]
name = "fastapi"
version = "0.142.3"

[[package]]
name = "uvicorn"
version = "0.54.0"

[[package]]
name = "fastapi-app"
source = { editable = "." }
"""
    )
    assert read_locked_versions(tmp_path, ["fastapi", "uvicorn", "ruff"]) == {
        "fastapi": "0.142.3",
        "uvicorn": "0.54.0",
    }


def test_read_locked_versions_without_lock(tmp_path):
    assert read_locked_versions(tmp_path, ["fastapi"]) == {}
