"""Resolve every version in a ProjectSpec from authoritative sources (AGENTS.md, principle 4).

Versions the user pinned are verified, never replaced. Everything else gets the latest
stable release, and Python gets the newest supported version all packages accept.
"""

import httpx

from app.models.spec import ProjectSpec
from app.tools.versions import (
    PackageVersion,
    VersionLookupError,
    get_latest_version,
    get_python_versions,
    get_release,
    incompatible_packages,
    resolve_python_version,
)


def resolve_spec(spec: ProjectSpec, client: httpx.Client) -> ProjectSpec:
    packages: list[PackageVersion] = []
    for dep in spec.all_packages():
        if dep.version:  # pinned by the user
            packages.append(get_release(dep.name, dep.version, client))
        else:
            packages.append(get_latest_version(dep.name, client))

    releases = get_python_versions(client)
    if spec.language_version:  # pinned by the user
        release = next((r for r in releases if r.minor == spec.language_version), None)
        if release is None:
            supported = ", ".join(r.minor for r in releases)
            raise VersionLookupError(
                f"Python {spec.language_version} is not a currently supported release "
                f"(supported: {supported})."
            )
        conflicts = incompatible_packages(packages, release)
        if conflicts:
            raise VersionLookupError(
                f"Python {release.minor} is not supported by: {', '.join(conflicts)}."
            )
    else:
        release = resolve_python_version(packages, releases)

    versions = {package.name: package.version for package in packages}
    return spec.with_package_versions(versions).updated(language_version=release.minor)
