import pytest

from app.builder.generate import GenerationError, generate_project
from app.builder.templates import render_files
from app.models.spec import ProjectSpec


def test_generates_all_files_into_staging(resolved_spec, tmp_path):
    project_dir = generate_project(resolved_spec, tmp_path / "staging")

    assert project_dir == (tmp_path / "staging" / "fastapi-app").resolve()
    for relative, content in render_files(resolved_spec).items():
        assert (project_dir / relative).read_text() == content


def test_unresolved_spec_is_rejected(tmp_path):
    with pytest.raises(GenerationError, match="unresolved"):
        generate_project(ProjectSpec(project_name="fastapi-app"), tmp_path)
    assert not (tmp_path / "fastapi-app").exists()


def test_existing_non_empty_directory_is_not_overwritten(resolved_spec, tmp_path):
    existing = tmp_path / "fastapi-app"
    existing.mkdir()
    (existing / "keep.txt").write_text("user data")

    with pytest.raises(GenerationError, match="not empty"):
        generate_project(resolved_spec, tmp_path)
    assert (existing / "keep.txt").read_text() == "user data"
    assert not (existing / "pyproject.toml").exists()
