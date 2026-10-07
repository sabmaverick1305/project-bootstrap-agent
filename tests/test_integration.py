"""End-to-end build of a real project: live version lookups, uv, Ruff, pytest, Uvicorn.

Needs network access and uv. Excluded by default; run with `uv run pytest -m integration`.
"""

import pytest

from app.builder.generate import generate_project
from app.builder.validate import validate_project
from app.models.spec import ProjectSpec
from app.tools.versions import (
    get_latest_version,
    get_python_versions,
    make_client,
    resolve_python_version,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live_spec() -> ProjectSpec:
    spec = ProjectSpec(project_name="fastapi-app")
    with make_client() as client:
        packages = [get_latest_version(dep.name, client) for dep in spec.all_packages()]
        python = resolve_python_version(packages, get_python_versions(client))
    versions = {package.name: package.version for package in packages}
    return spec.with_package_versions(versions).updated(language_version=python.minor)


def _describe(report) -> str:
    return "\n\n".join(f"[{s.name}] ok={s.ok}\n{s.output}" for s in report.steps)


def test_generated_project_passes_full_validation(live_spec, tmp_path):
    project_dir = generate_project(live_spec, tmp_path)

    report = validate_project(project_dir, live_spec)

    assert report.ok, _describe(report)
    assert (project_dir / "uv.lock").exists()


def test_broken_project_fails_with_diagnostics(live_spec, tmp_path):
    project_dir = generate_project(live_spec, tmp_path)
    main = project_dir / "src" / live_spec.package_name / "main.py"
    main.write_text(main.read_text().replace("def health()", "def health(:"))

    report = validate_project(project_dir, live_spec)

    assert not report.ok
    assert report.failed_step.name == "lint"
    assert "main.py" in report.failed_step.output
