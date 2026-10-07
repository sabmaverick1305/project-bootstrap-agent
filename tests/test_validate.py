from pathlib import Path

import pytest

from app.builder import validate
from app.builder.validate import validate_project
from app.tools.runner import CommandResult, HealthResult


class FakeTools:
    """Replaces the runner so validation logic can be tested without real commands."""

    def __init__(self, failing_step: str | None = None):
        self.failing_step = failing_step
        self.commands: list[list[str]] = []
        self.health_calls: list[str] = []

    def run_command(self, argv, cwd, timeout):
        self.commands.append(list(argv))
        name = next(name for name, step_argv, _ in validate.COMMAND_STEPS if step_argv == argv)
        returncode = 1 if name == self.failing_step else 0
        return CommandResult(tuple(argv), returncode, f"{name} output", 0.1)

    def check_application_health(self, project_dir, app_import, timeout):
        self.health_calls.append(app_import)
        ok = self.failing_step != "health"
        return HealthResult(
            url="http://127.0.0.1:1234/health",
            ok=ok,
            status_code=200 if ok else 500,
            body='{"status":"ok"}' if ok else "Internal Server Error",
            server_output="server log",
            error=None if ok else "GET returned HTTP 500.",
            duration=0.2,
        )


@pytest.fixture
def fake_tools(monkeypatch):
    def install(failing_step=None):
        tools = FakeTools(failing_step)
        monkeypatch.setattr(validate, "run_command", tools.run_command)
        monkeypatch.setattr(validate, "check_application_health", tools.check_application_health)
        return tools

    return install


def test_all_steps_pass(fake_tools, resolved_spec):
    tools = fake_tools()

    report = validate_project(Path("/project"), resolved_spec)

    assert report.ok
    assert report.failed_step is None
    assert [s.name for s in report.steps] == ["install", "lint", "format", "tests", "health"]
    assert tools.health_calls == ["fastapi_app.main:app"]


@pytest.mark.parametrize("failing", ["install", "lint", "format", "tests"])
def test_stops_at_first_failing_command(fake_tools, resolved_spec, failing):
    tools = fake_tools(failing)

    report = validate_project(Path("/project"), resolved_spec)

    assert not report.ok
    assert report.failed_step.name == failing
    assert report.failed_step.output == f"{failing} output"
    assert report.steps[-1].name == failing
    assert tools.health_calls == []


def test_health_failure_is_reported(fake_tools, resolved_spec):
    fake_tools("health")

    report = validate_project(Path("/project"), resolved_spec)

    assert not report.ok
    assert report.failed_step.name == "health"
    assert "HTTP 500" in report.failed_step.output
    assert "server log" in report.failed_step.output


def test_incomplete_report_is_not_ok():
    assert not validate.ValidationReport(steps=()).ok
