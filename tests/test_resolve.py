import httpx
import pytest

from app.builder.resolve import resolve_spec
from app.models.spec import ProjectSpec
from app.tools.versions import VersionLookupError, get_release

LATEST = {
    "fastapi": ("0.142.3", ">=3.10"),
    "uvicorn": ("0.54.0", ">=3.10"),
    "pytest": ("9.1.1", ">=3.10"),
    "httpx2": ("2.13.1", ">=3.10"),
    "ruff": ("0.16.10", ">=3.7"),
    "uv_build": ("0.12.23", ">=3.8"),
}
RELEASES = {
    ("fastapi", "0.115"): ("0.115.0", ">=3.8", False),
    ("fastapi", "0.99.0"): ("0.99.0", ">=3.7,<3.13", False),
    ("fastapi", "0.1.0"): ("0.1.0", ">=3.6", True),
}
PYTHON = [
    {"cycle": "3.14", "releaseDate": "2025-10-07", "eol": "2030-10-31", "latest": "3.14.8"},
    {"cycle": "3.13", "releaseDate": "2024-10-07", "eol": "2029-10-31", "latest": "3.13.16"},
    {"cycle": "3.12", "releaseDate": "2023-10-02", "eol": "2028-10-31", "latest": "3.12.15"},
]


def _handler(request: httpx.Request) -> httpx.Response:
    parts = request.url.path.strip("/").split("/")  # pypi/<name>/json or pypi/<name>/<v>/json
    if request.url.host == "endoflife.date":
        return httpx.Response(200, json=PYTHON)
    if len(parts) == 3 and parts[1] in LATEST:
        version, requires = LATEST[parts[1]]
        files = [{"requires_python": requires, "yanked": False}]
        return httpx.Response(200, json={"releases": {version: files}})
    if len(parts) == 4 and (parts[1], parts[2]) in RELEASES:
        version, requires, yanked = RELEASES[(parts[1], parts[2])]
        return httpx.Response(
            200,
            json={
                "info": {"version": version, "requires_python": requires},
                "urls": [{"yanked": yanked}],
            },
        )
    return httpx.Response(404)


@pytest.fixture
def client():
    return httpx.Client(transport=httpx.MockTransport(_handler))


def test_unpinned_spec_gets_latest_versions(client):
    spec = resolve_spec(ProjectSpec(project_name="fastapi-app"), client)

    assert spec.is_resolved
    assert spec.language_version == "3.14"
    assert spec.framework_version == "0.142.3"
    assert spec.build_backend_version == "0.12.23"
    assert {d.name: d.version for d in spec.dev_dependencies}["httpx2"] == "2.13.1"


def test_pinned_version_is_kept_and_normalized(client):
    spec = ProjectSpec(project_name="fastapi-app", framework_version="0.115")

    resolved = resolve_spec(spec, client)

    assert resolved.framework_version == "0.115.0"  # not replaced with the latest
    assert resolved.server_version == "0.54.0"


def test_pinned_package_limits_python(client):
    resolved = resolve_spec(ProjectSpec(project_name="x-app", framework_version="0.99.0"), client)
    assert resolved.language_version == "3.12"


def test_missing_pinned_version(client):
    spec = ProjectSpec(project_name="fastapi-app", framework_version="9.9.9")
    with pytest.raises(VersionLookupError, match="fastapi 9.9.9 does not exist on PyPI"):
        resolve_spec(spec, client)


def test_yanked_pinned_version(client):
    with pytest.raises(VersionLookupError, match="yanked"):
        get_release("fastapi", "0.1.0", client)


def test_pinned_python_is_kept(client):
    resolved = resolve_spec(ProjectSpec(project_name="x-app", language_version="3.13"), client)
    assert resolved.language_version == "3.13"


def test_pinned_python_not_supported(client):
    spec = ProjectSpec(project_name="x-app", language_version="3.9")
    with pytest.raises(VersionLookupError, match="3.9 is not a currently supported release"):
        resolve_spec(spec, client)


def test_pinned_python_conflicts_with_pinned_package(client):
    spec = ProjectSpec(project_name="x-app", language_version="3.14", framework_version="0.99.0")
    with pytest.raises(VersionLookupError, match=r"fastapi 0\.99\.0 \(requires Python"):
        resolve_spec(spec, client)
