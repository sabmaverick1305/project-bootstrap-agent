"""Install and validate a generated project (see AGENTS.md, principle 7).

Steps run in order and stop at the first failure, since later steps depend on earlier
ones (nothing can be linted or tested if installation failed).
"""

from dataclasses import dataclass
from pathlib import Path

from app.models.spec import ProjectSpec
from app.tools.runner import check_application_health, run_command

INSTALL_TIMEOUT_SECONDS = 300.0
CHECK_TIMEOUT_SECONDS = 120.0
HEALTH_TIMEOUT_SECONDS = 30.0

COMMAND_STEPS: tuple[tuple[str, list[str], float], ...] = (
    ("install", ["uv", "sync"], INSTALL_TIMEOUT_SECONDS),
    ("lint", ["uv", "run", "ruff", "check", "."], CHECK_TIMEOUT_SECONDS),
    ("format", ["uv", "run", "ruff", "format", "--check", "."], CHECK_TIMEOUT_SECONDS),
    ("tests", ["uv", "run", "pytest"], CHECK_TIMEOUT_SECONDS),
)
STEP_NAMES = (*(name for name, _, _ in COMMAND_STEPS), "health")


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    command: str
    output: str
    duration: float


@dataclass(frozen=True)
class ValidationReport:
    steps: tuple[StepResult, ...]

    @property
    def failed_step(self) -> StepResult | None:
        return next((step for step in self.steps if not step.ok), None)

    @property
    def ok(self) -> bool:
        return len(self.steps) == len(STEP_NAMES) and self.failed_step is None


def validate_project(project_dir: Path, spec: ProjectSpec) -> ValidationReport:
    steps: list[StepResult] = []

    for name, argv, timeout in COMMAND_STEPS:
        result = run_command(argv, project_dir, timeout)
        steps.append(StepResult(name, result.ok, " ".join(argv), result.output, result.duration))
        if not result.ok:
            return ValidationReport(tuple(steps))

    app_import = f"{spec.package_name}.main:app"
    health = check_application_health(project_dir, app_import, HEALTH_TIMEOUT_SECONDS)
    detail = f"HTTP {health.status_code} {health.body}" if health.status_code else ""
    output = "\n".join(part for part in (health.error, detail, health.server_output) if part)
    steps.append(
        StepResult(
            "health", health.ok, f"uvicorn {app_import} + GET {health.url}", output, health.duration
        )
    )
    return ValidationReport(tuple(steps))
