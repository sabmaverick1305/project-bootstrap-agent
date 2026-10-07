import pytest

from app.builder.generate import generate_project
from app.builder.repair import (
    RepairAttempt,
    RepairRejected,
    apply_repair,
    build_repair_context,
    editable_files,
)
from app.builder.validate import StepResult
from app.models.repair import FileEdit, RepairProposal

MAIN = "src/fastapi_app/main.py"


@pytest.fixture
def project(resolved_spec, tmp_path):
    return generate_project(resolved_spec, tmp_path)


def _proposal(*edits, can_repair=True):
    return RepairProposal(
        diagnosis="d", can_repair=can_repair, edits=[FileEdit(path=p, content=c) for p, c in edits]
    )


def test_applies_edit_to_generated_file(resolved_spec, project):
    changed = apply_repair(resolved_spec, project, _proposal((MAIN, "x = 1\n")))

    assert changed == [MAIN]
    assert (project / MAIN).read_text() == "x = 1\n"


def test_leading_dot_slash_is_accepted(resolved_spec, project):
    assert apply_repair(resolved_spec, project, _proposal((f"./{MAIN}", "x = 1\n"))) == [MAIN]


@pytest.mark.parametrize(
    "path", ["../outside.py", "/etc/hosts", "uv.lock", ".venv/bin/activate", "src/new_module.py"]
)
def test_rejects_files_that_are_not_editable(resolved_spec, project, path):
    with pytest.raises(RepairRejected, match="not editable"):
        apply_repair(resolved_spec, project, _proposal((path, "x")))


def test_nothing_is_written_if_any_edit_is_rejected(resolved_spec, project):
    original = (project / MAIN).read_text()
    with pytest.raises(RepairRejected):
        apply_repair(resolved_spec, project, _proposal((MAIN, "x = 1\n"), ("uv.lock", "x")))
    assert (project / MAIN).read_text() == original


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('name = "fastapi-app"', 'name = "other"'),
        ('requires-python = ">=3.14"', 'requires-python = ">=3.12"'),
        ('"fastapi>=0.142.3"', '"fastapi>=0.100.0"'),
        ('"ruff>=0.16.10",', '"ruff>=0.16.10",\n    "black",'),
        ('build-backend = "uv_build"', 'build-backend = "hatchling.build"'),
    ],
)
def test_rejects_changes_to_spec_owned_pyproject_sections(resolved_spec, project, old, new):
    pyproject = (project / "pyproject.toml").read_text()
    assert old in pyproject
    with pytest.raises(RepairRejected, match="come from the ProjectSpec"):
        apply_repair(
            resolved_spec, project, _proposal(("pyproject.toml", pyproject.replace(old, new)))
        )


def test_allows_tool_config_changes_in_pyproject(resolved_spec, project):
    pyproject = (project / "pyproject.toml").read_text()
    edited = pyproject + "\n[tool.ruff]\nline-length = 100\n"

    assert apply_repair(resolved_spec, project, _proposal(("pyproject.toml", edited))) == [
        "pyproject.toml"
    ]


def test_rejects_invalid_pyproject(resolved_spec, project):
    with pytest.raises(RepairRejected, match="invalid"):
        apply_repair(resolved_spec, project, _proposal(("pyproject.toml", "[project")))


def test_rejects_no_op_and_empty_and_unrepairable(resolved_spec, project):
    same = (project / MAIN).read_text()
    with pytest.raises(RepairRejected, match="did not change"):
        apply_repair(resolved_spec, project, _proposal((MAIN, same)))
    with pytest.raises(RepairRejected, match="no changes"):
        apply_repair(resolved_spec, project, _proposal())
    with pytest.raises(RepairRejected, match="Cannot be repaired safely"):
        apply_repair(resolved_spec, project, _proposal((MAIN, "x"), can_repair=False))


def test_context_contains_failure_files_and_history(resolved_spec, project):
    failed = StepResult("tests", False, "uv run pytest", "ImportError: cannot import FastAPi", 1.0)
    history = [RepairAttempt("lint", "missing colon", (MAIN,))]

    context = build_repair_context(resolved_spec, project, failed, history)

    assert '<failed_step name="tests" command="uv run pytest">' in context
    assert "ImportError: cannot import FastAPi" in context
    for path in editable_files(resolved_spec):
        assert f'<file path="{path}">' in context
    assert '@app.get("/health")' in context
    assert "attempt 1: failed at lint; diagnosis: missing colon" in context
    assert '"project_name": "fastapi-app"' in context
