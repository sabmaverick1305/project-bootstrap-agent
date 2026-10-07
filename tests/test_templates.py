import subprocess
import sys
import tomllib

import pytest

from app.builder.templates import _build_requirement, render_files
from app.models.spec import Dependency, ProjectSpec
from app.tools.files import write_file
from tests.conftest import RESOLVED_VERSIONS


def test_renders_expected_files(resolved_spec):
    assert sorted(render_files(resolved_spec)) == [
        ".gitignore",
        ".python-version",
        "README.md",
        "pyproject.toml",
        "src/fastapi_app/__init__.py",
        "src/fastapi_app/main.py",
        "tests/__init__.py",
        "tests/test_health.py",
    ]


def test_pyproject_uses_resolved_spec(resolved_spec):
    pyproject = tomllib.loads(render_files(resolved_spec)["pyproject.toml"])

    assert pyproject["project"]["name"] == "fastapi-app"
    assert pyproject["project"]["requires-python"] == ">=3.14"
    assert pyproject["project"]["dependencies"] == ["fastapi>=0.142.3", "uvicorn>=0.40.0"]
    assert pyproject["dependency-groups"]["dev"] == [
        "pytest>=9.1.1",
        "httpx2>=2.13.1",
        "ruff>=0.16.10",
    ]
    assert pyproject["build-system"] == {
        "requires": ["uv_build>=0.9.30,<0.10"],
        "build-backend": "uv_build",
    }


@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.9.30", "uv_build>=0.9.30,<0.10"), ("1.2.3", "uv_build>=1.2.3,<2")],
)
def test_build_requirement_upper_bound(version, expected):
    assert _build_requirement(Dependency(name="uv_build", version=version)) == expected


def test_custom_project_name_flows_into_imports():
    spec = (
        ProjectSpec(project_name="Orders Service")
        .with_package_versions(RESOLVED_VERSIONS)
        .updated(language_version="3.14")
    )
    files = render_files(spec)

    assert "src/orders_service/main.py" in files
    assert "from orders_service.main import app" in files["tests/test_health.py"]
    assert 'FastAPI(title="orders-service")' in files["src/orders_service/main.py"]


def test_python_version_file(resolved_spec):
    assert render_files(resolved_spec)[".python-version"] == "3.14\n"


def test_generated_python_passes_ruff(resolved_spec, tmp_path):
    for relative, content in render_files(resolved_spec).items():
        write_file(tmp_path, relative, content)

    for args in (["check"], ["format", "--check"]):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", *args, "--no-cache", "."],
            cwd=tmp_path,  # use the generated project's config, not this repo's
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
