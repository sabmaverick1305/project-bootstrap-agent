from pathlib import Path

from app.builder.plan import render_plan, render_result
from app.builder.validate import StepResult, ValidationReport

TARGET = Path("/work/fastapi-app")


def _step(name, ok=True, output=""):
    return StepResult(name, ok, f"cmd-{name}", output, 1.0)


def test_plan_shows_versions_choices_and_validation(resolved_spec):
    plan = render_plan(resolved_spec, TARGET, conflict=False)

    assert "Plan for: fastapi-app" in plan
    assert "/work/fastapi-app" in plan
    assert "3.14" in plan and "newest supported by all packages" in plan
    assert "0.142.3" in plan and "latest stable" in plan
    assert "Uvicorn 0.40.0" in plan and "Gunicorn" in plan
    assert "pytest 9.1.1, httpx2 2.13.1, ruff 0.16.10" in plan
    assert "src/fastapi_app/main.py" in plan
    assert "uv run pytest" in plan and "GET /health" in plan
    assert "already exists" not in plan


def test_plan_marks_user_choices_and_unsupported_items(resolved_spec):
    spec = resolved_spec.updated(
        user_specified={"framework_version", "server"},
        unsupported_requests=["database: postgresql (not supported yet; it will be left out)"],
    )
    plan = render_plan(spec, TARGET, conflict=False)

    assert "you asked for this version" in plan
    assert "you asked for this" in plan
    assert "Not included  database: postgresql" in plan


def test_plan_warns_about_conflict(resolved_spec):
    plan = render_plan(resolved_spec, TARGET, conflict=True)
    assert "already exists and is not empty" in plan


def test_result_success(resolved_spec):
    report = ValidationReport(
        tuple(_step(n) for n in ("install", "lint", "format", "tests", "health"))
    )

    text = render_result(resolved_spec, report, {"fastapi": "0.142.3"})

    assert text.count("PASS") == 5
    assert "fastapi 0.142.3" in text
    assert "uv run uvicorn fastapi_app.main:app" in text


def test_result_failure_shows_output_and_skipped_steps(resolved_spec):
    output = "\n".join(f"line {i}" for i in range(100))
    report = ValidationReport((_step("install"), _step("lint", ok=False, output=output)))

    text = render_result(resolved_spec, report, {})

    assert "FAIL  lint" in text
    assert "SKIP  format" in text and "SKIP  health" in text
    assert "line 99" in text and "line 10\n" not in text  # only the tail is shown
    assert "Run it" not in text
