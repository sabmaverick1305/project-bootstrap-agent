"""Text shown at the two approval gates: the plan, and the validated result."""

from pathlib import Path

from app.builder.repair import RepairAttempt
from app.builder.templates import render_files
from app.builder.validate import COMMAND_STEPS, STEP_NAMES, ValidationReport
from app.models.spec import ProjectSpec

ALTERNATIVES = {
    "server": "Gunicorn + Uvicorn workers, Hypercorn",
    "package_manager": "pip + venv, Poetry",
}
OUTPUT_TAIL_LINES = 40


def _row(label: str, value: str, note: str = "") -> str:
    line = f"  {label:<14}{value}"
    if note:
        line = f"{line:<38}  {note}"  # always at least two spaces before the note
    return line.rstrip()


def _version_note(spec: ProjectSpec, field: str, latest_note: str = "latest stable") -> str:
    return "you asked for this version" if field in spec.user_specified else latest_note


def _choice_note(spec: ProjectSpec, field: str) -> str:
    if field in spec.user_specified:
        return "you asked for this"
    return f"default (alternatives, not supported yet: {ALTERNATIVES[field]})"


def run_command_for(spec: ProjectSpec) -> str:
    return f"uv run uvicorn {spec.package_name}.main:app --reload"


def render_plan(spec: ProjectSpec, target: Path, conflict: bool) -> str:
    dev = ", ".join(f"{d.name} {d.version}" for d in spec.dev_dependencies)
    lines = [
        f"Plan for: {spec.project_name}",
        _row("Location", str(target)),
        _row(
            "Python",
            spec.language_version or "?",
            _version_note(spec, "language_version", "newest supported by all packages"),
        ),
        _row("FastAPI", spec.framework_version or "?", _version_note(spec, "framework_version")),
        _row(
            "Server",
            f"Uvicorn {spec.server_version}",
            _choice_note(spec, "server"),
        ),
        _row("Package mgr", "uv", _choice_note(spec, "package_manager")),
        _row("Dev tools", dev, "pytest for tests, Ruff for lint/format"),
        _row("Build backend", f"uv_build {spec.build_backend_version}"),
    ]
    for item in spec.unsupported_requests:
        lines.append(_row("Not included", item))

    files = sorted(render_files(spec))
    lines.append(_row("Files", files[0]))
    lines += [_row("", path) for path in files[1:]]
    commands = [" ".join(argv) for _, argv, _ in COMMAND_STEPS]
    lines.append(_row("Validation", commands[0]))
    lines += [_row("", command) for command in commands[1:]]
    lines.append(_row("", "start the app and check GET /health returns 200"))

    if conflict:
        lines += [
            "",
            f"  ! {target} already exists and is not empty. It will not be overwritten.",
            "    Choose 'edit' to pick a different project name, or 'cancel'.",
        ]
    return "\n".join(lines)


def _tail(text: str) -> str:
    return "\n".join(text.rstrip().splitlines()[-OUTPUT_TAIL_LINES:])


def render_result(
    spec: ProjectSpec,
    report: ValidationReport,
    installed: dict[str, str],
    repairs: list[RepairAttempt] = (),
    stop_reason: str | None = None,
) -> str:
    lines = [f"Result for: {spec.project_name}", "", "  Validation (final run)"]
    for step in report.steps:
        status = "PASS" if step.ok else "FAIL"
        lines.append(f"    {status}  {step.name:<8} {step.command}  ({step.duration:.1f}s)")
    lines += [f"    SKIP  {name}" for name in STEP_NAMES[len(report.steps) :]]

    if repairs:
        lines += ["", "  Repairs applied (files differ from the templates)"]
        for i, attempt in enumerate(repairs, start=1):
            lines.append(f"    {i}. {attempt.failed_step} failed: {attempt.diagnosis}")
            lines.append(f"       changed: {', '.join(attempt.changed_files)}")

    failed = report.failed_step
    if failed:
        lines += ["", f"  Failed step: {failed.name} ({failed.command})"]
        if repairs:
            lines.append(f"  Likely cause: {repairs[-1].diagnosis}")
        if stop_reason:
            lines.append(f"  Repair stopped: {stop_reason}")
        lines += ["  Output:", _tail(failed.output)]
        return "\n".join(lines)

    if installed:
        lines += ["", "  Installed versions (from uv.lock)"]
        lines += [f"    {name} {version}" for name, version in installed.items()]
    lines += ["", "  Files"]
    lines += [f"    {path}" for path in sorted(render_files(spec))]
    lines += ["", f"  Run it:  cd {spec.project_name} && {run_command_for(spec)}"]
    return "\n".join(lines)
