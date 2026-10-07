import pytest
from pydantic import ValidationError

from app.models.spec import ProjectSpec
from tests.conftest import RESOLVED_VERSIONS


def test_defaults_and_derived_package_name():
    spec = ProjectSpec(project_name="My API")

    assert spec.project_name == "my-api"
    assert spec.package_name == "my_api"
    assert spec.server == "uvicorn"
    assert spec.package_manager == "uv"
    assert [d.name for d in spec.dev_dependencies] == ["pytest", "httpx2", "ruff"]


def test_package_name_input_is_overridden():
    spec = ProjectSpec(project_name="orders", package_name="something_else")
    assert spec.package_name == "orders"


def test_round_trip():
    spec = ProjectSpec(project_name="fastapi-app", user_specified={"framework"})
    assert ProjectSpec.model_validate(spec.model_dump()) == spec


@pytest.mark.parametrize(
    "changes",
    [
        {"framework": "flask"},
        {"package_manager": "poetry"},
        {"database": "postgres"},
        {"docker": True},
        {"language_version": "3.14.1"},
        {"framework_version": "1.0.0rc1"},
        {"user_specified": {"not_a_field"}},
        {"unknown_field": 1},
    ],
)
def test_out_of_scope_or_invalid_values_are_rejected(changes):
    with pytest.raises(ValidationError):
        ProjectSpec(project_name="fastapi-app", **changes)


def test_shadowing_project_name_is_rejected():
    with pytest.raises(ValidationError):
        ProjectSpec(project_name="fastapi")


def test_unresolved_until_versions_applied():
    spec = ProjectSpec(project_name="fastapi-app")
    assert not spec.is_resolved
    assert spec.unresolved_fields() == [
        "language_version",
        "fastapi",
        "uvicorn",
        "pytest",
        "httpx2",
        "ruff",
        "uv_build",
    ]

    resolved = spec.with_package_versions(RESOLVED_VERSIONS).updated(language_version="3.14")

    assert resolved.is_resolved
    assert resolved.framework_version == "0.142.3"
    assert resolved.server_version == "0.40.0"
    assert resolved.build_backend_version == "0.9.30"
    assert spec.framework_version is None  # original is unchanged
