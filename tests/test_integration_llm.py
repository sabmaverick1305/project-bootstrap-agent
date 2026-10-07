"""Live LLM tests against the Anthropic API: intent parsing and repairs (costs cents).

Excluded by default; run with `uv run pytest -m integration`. Skipped without credentials.
"""

import os
from pathlib import Path

import pytest

from app.agent.llm import make_client, parse_intent, propose_repair
from app.builder.generate import generate_project
from app.builder.repair import MAX_REPAIR_ATTEMPTS
from app.builder.resolve import resolve_spec
from app.main import Services, Workflow
from app.models.intent import IntentError, build_spec
from app.models.spec import ProjectSpec
from app.tools.versions import make_client as versions_client

HAS_CREDENTIALS = bool(
    os.environ.get("ANTHROPIC_API_KEY")
    or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    or (Path.home() / ".config" / "anthropic").is_dir()
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not HAS_CREDENTIALS, reason="no Anthropic credentials configured"),
]


@pytest.fixture(scope="module")
def client():
    return make_client()


def test_basic_request(client):
    spec = build_spec(parse_intent("Setup a Python + FastAPI project for me", client)).spec

    assert spec.project_name == "fastapi-app"
    assert {"language", "framework"} <= spec.user_specified
    assert spec.unsupported_requests == []
    assert spec.framework_version is None  # versions come from lookups, not the LLM


def test_name_versions_and_unsupported_extras(client):
    request = (
        "Create a FastAPI service called Orders API on Python 3.13, "
        "with a Postgres database and a Dockerfile."
    )
    spec = build_spec(parse_intent(request, client)).spec

    assert spec.project_name == "orders-api"
    assert spec.language_version == "3.13"
    unsupported = " ".join(spec.unsupported_requests).lower()
    assert "postgres" in unsupported and "docker" in unsupported


def test_other_framework_is_rejected(client):
    with pytest.raises(IntentError, match="FastAPI"):
        build_spec(parse_intent("Build me a Django blog", client))


class _QuietConsole:
    def __init__(self):
        self.shown = []

    def show(self, text):
        self.shown.append(text)


@pytest.fixture(scope="module")
def live_spec():
    with versions_client() as http:
        return resolve_spec(ProjectSpec(project_name="fastapi-app"), http)


@pytest.mark.parametrize(
    ("description", "path", "old", "new"),
    [
        ("syntax error", "main.py", "def health()", "def health(:"),
        ("bad import", "main.py", "from fastapi import FastAPI", "from fastapi import FastAPi"),
        ("wrong route", "main.py", '@app.get("/health")', '@app.get("/healthz")'),
    ],
)
def test_repair_loop_fixes_broken_project(client, live_spec, tmp_path, description, path, old, new):
    project_dir = generate_project(live_spec, tmp_path)
    source = project_dir / "src" / live_spec.package_name / path
    source.write_text(source.read_text().replace(old, new))
    test_file = project_dir / "tests" / "test_health.py"
    original_test = test_file.read_text()

    services = Services(
        parse_intent=lambda _: None,
        resolve_spec=lambda spec: spec,
        propose_repair=lambda context: propose_repair(context, client),
    )
    console = _QuietConsole()
    report, repairs, stop_reason = Workflow(console, tmp_path, services)._validate_and_repair(
        live_spec, project_dir
    )

    log = "\n".join(console.shown)
    assert report.ok, f"{description}: {stop_reason}\n{log}"
    assert 1 <= len(repairs) <= MAX_REPAIR_ATTEMPTS
    assert old in source.read_text()
    assert test_file.read_text() == original_test  # fixed the app, not the test
