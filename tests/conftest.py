import pytest

from app.models.spec import ProjectSpec

# Fixed versions for offline tests. Real runs resolve these through app.tools.versions.
RESOLVED_VERSIONS = {
    "fastapi": "0.142.3",
    "uvicorn": "0.40.0",
    "pytest": "9.1.1",
    "httpx2": "2.13.1",
    "ruff": "0.16.10",
    "uv_build": "0.9.30",
}


@pytest.fixture
def resolved_spec() -> ProjectSpec:
    spec = ProjectSpec(project_name="fastapi-app")
    return spec.with_package_versions(RESOLVED_VERSIONS).updated(language_version="3.14")
