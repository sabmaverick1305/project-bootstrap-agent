from datetime import date

import httpx
import pytest

from app.tools.versions import (
    PackageVersion,
    PythonRelease,
    VersionLookupError,
    get_latest_version,
    get_python_versions,
    resolve_python_version,
)


def _client(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler))


def _file(requires_python=">=3.10", yanked=False) -> dict:
    return {"requires_python": requires_python, "yanked": yanked}


PYPI_FASTAPI = "https://pypi.org/pypi/fastapi/json"
EOL_PYTHON = "https://endoflife.date/api/python.json"


def test_latest_version_skips_prereleases_yanked_and_empty():
    client = _client(
        {
            PYPI_FASTAPI: httpx.Response(
                200,
                json={
                    "releases": {
                        "0.9.0": [_file(">=3.8")],
                        "0.10.0": [_file(">=3.9")],
                        "0.11.0": [_file(yanked=True)],
                        "0.12.0rc1": [_file()],
                        "0.12.0.dev1": [_file()],
                        "0.13.0": [],
                        "not-a-version": [_file()],
                    }
                },
            ),
        }
    )

    result = get_latest_version("fastapi", client)

    assert result == PackageVersion(name="fastapi", version="0.10.0", requires_python=">=3.9")


def test_latest_version_unknown_package():
    with pytest.raises(VersionLookupError, match="Not found"):
        get_latest_version("fastapi", _client({}))


def test_latest_version_server_error():
    client = _client({PYPI_FASTAPI: httpx.Response(503)})
    with pytest.raises(VersionLookupError, match="503"):
        get_latest_version("fastapi", client)


def test_python_versions_filters_unreleased_and_eol():
    client = _client(
        {
            EOL_PYTHON: httpx.Response(
                200,
                json=[
                    {
                        "cycle": "3.15",
                        "releaseDate": "2026-10-20",
                        "eol": "2031-10-31",
                        "latest": "3.15.0",
                    },
                    {
                        "cycle": "3.14",
                        "releaseDate": "2025-10-07",
                        "eol": "2030-10-31",
                        "latest": "3.14.8",
                    },
                    {
                        "cycle": "3.13",
                        "releaseDate": "2024-10-07",
                        "eol": "2029-10-31",
                        "latest": "3.13.16",
                    },
                    {
                        "cycle": "3.9",
                        "releaseDate": "2020-10-05",
                        "eol": "2025-10-31",
                        "latest": "3.9.25",
                    },
                ],
            ),
        }
    )

    releases = get_python_versions(client, today=date(2026, 10, 8))

    assert releases == [PythonRelease("3.14", "3.14.8"), PythonRelease("3.13", "3.13.16")]


RELEASES = [PythonRelease("3.14", "3.14.8"), PythonRelease("3.13", "3.13.16")]


def test_resolve_python_picks_newest_compatible():
    packages = [
        PackageVersion("fastapi", "0.142.3", ">=3.10"),
        PackageVersion("legacy", "1.0.0", ">=3.9,<3.14"),
        PackageVersion("nometa", "1.0.0", None),
    ]
    assert resolve_python_version(packages, RELEASES) == PythonRelease("3.13", "3.13.16")


def test_resolve_python_no_compatible_version():
    packages = [PackageVersion("old", "1.0.0", "<3.10")]
    with pytest.raises(VersionLookupError, match="old"):
        resolve_python_version(packages, RELEASES)
