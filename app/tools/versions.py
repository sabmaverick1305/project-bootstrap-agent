"""Version lookup tools: current versions from authoritative sources (AGENTS.md, principle 4).

- Package versions come from the PyPI JSON API.
- Supported Python versions come from endoflife.date.
"""

from dataclasses import dataclass
from datetime import date

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

PYPI_URL = "https://pypi.org/pypi/{name}/json"
PYPI_RELEASE_URL = "https://pypi.org/pypi/{name}/{version}/json"
PYTHON_RELEASES_URL = "https://endoflife.date/api/python.json"
TIMEOUT_SECONDS = 15.0


class VersionLookupError(Exception):
    """Raised when a version cannot be resolved from the authoritative source."""


@dataclass(frozen=True)
class PackageVersion:
    name: str
    version: str
    requires_python: str | None


@dataclass(frozen=True)
class PythonRelease:
    minor: str  # e.g. "3.14"
    latest: str  # e.g. "3.14.8"


def make_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)


def _get_json(client: httpx.Client, url: str) -> object:
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise VersionLookupError(f"Request to {url} failed: {exc}") from exc
    if response.status_code == 404:
        raise VersionLookupError(f"Not found: {url}")
    if response.is_error:
        raise VersionLookupError(f"Request to {url} returned HTTP {response.status_code}")
    return response.json()


def get_latest_version(name: str, client: httpx.Client) -> PackageVersion:
    """Latest stable, non-yanked release of `name` on PyPI, with its requires_python."""
    data = _get_json(client, PYPI_URL.format(name=name))

    candidates: list[tuple[Version, str, list[dict]]] = []
    for version_str, files in data.get("releases", {}).items():
        try:
            version = Version(version_str)
        except InvalidVersion:
            continue
        if version.is_prerelease or version.is_devrelease:
            continue
        live_files = [f for f in files if not f.get("yanked")]
        if live_files:
            candidates.append((version, version_str, live_files))

    if not candidates:
        raise VersionLookupError(f"No stable, non-yanked release of {name!r} found on PyPI.")

    _, version_str, files = max(candidates, key=lambda candidate: candidate[0])
    requires_python = next((f["requires_python"] for f in files if f.get("requires_python")), None)
    return PackageVersion(name=name, version=version_str, requires_python=requires_python)


def get_release(name: str, version: str, client: httpx.Client) -> PackageVersion:
    """A specific release of `name` (one the user pinned). It must exist and not be yanked."""
    try:
        data = _get_json(client, PYPI_RELEASE_URL.format(name=name, version=version))
    except VersionLookupError as exc:
        if str(exc).startswith("Not found"):
            raise VersionLookupError(f"{name} {version} does not exist on PyPI.") from exc
        raise
    if not [f for f in data.get("urls", []) if not f.get("yanked")]:
        raise VersionLookupError(f"{name} {version} has been yanked from PyPI.")
    info = data.get("info", {})
    # PyPI normalizes the version ("0.115" -> "0.115.0"); keep the exact release it reports.
    return PackageVersion(
        name=name,
        version=info.get("version") or version,
        requires_python=info.get("requires_python") or None,
    )


def get_python_versions(client: httpx.Client, today: date | None = None) -> list[PythonRelease]:
    """Released, not end-of-life Python versions, newest first."""
    today = today or date.today()
    cycles = _get_json(client, PYTHON_RELEASES_URL)

    releases: list[tuple[Version, PythonRelease]] = []
    for cycle in cycles:
        try:
            released = date.fromisoformat(cycle["releaseDate"]) <= today
            eol = cycle["eol"]
            supported = not eol if isinstance(eol, bool) else date.fromisoformat(eol) > today
            latest = Version(cycle["latest"])
        except (KeyError, TypeError, ValueError, InvalidVersion):
            continue
        if released and supported and not latest.is_prerelease:
            releases.append((Version(cycle["cycle"]), PythonRelease(cycle["cycle"], str(latest))))

    if not releases:
        raise VersionLookupError("No supported Python release found.")
    return [release for _, release in sorted(releases, key=lambda r: r[0], reverse=True)]


def _python_specifiers(packages: list[PackageVersion]) -> dict[str, SpecifierSet]:
    try:
        return {p.name: SpecifierSet(p.requires_python) for p in packages if p.requires_python}
    except InvalidSpecifier as exc:
        raise VersionLookupError(f"Invalid requires_python metadata: {exc}") from exc


def incompatible_packages(packages: list[PackageVersion], release: PythonRelease) -> list[str]:
    """'name version (requires_python)' for each package that does not support `release`."""
    specifiers = _python_specifiers(packages)
    versions = {p.name: p.version for p in packages}
    return [
        f"{name} {versions[name]} (requires Python {spec})"
        for name, spec in specifiers.items()
        if not spec.contains(release.latest)
    ]


def resolve_python_version(
    packages: list[PackageVersion], releases: list[PythonRelease]
) -> PythonRelease:
    """Newest supported Python that satisfies every package's requires_python."""
    specifiers = _python_specifiers(packages)
    for release in releases:  # newest first
        if all(spec.contains(release.latest) for spec in specifiers.values()):
            return release

    constraints = ", ".join(f"{name} {spec}" for name, spec in specifiers.items())
    raise VersionLookupError(f"No supported Python satisfies all packages: {constraints}")
