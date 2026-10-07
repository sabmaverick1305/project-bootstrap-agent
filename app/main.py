"""CLI entry point and the v0.1 workflow (see AGENTS.md, "Agent Workflow").

uv run python -m app.main "Setup a Python + FastAPI project for me" --output-dir ~/code
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import anthropic
from pydantic import ValidationError

from app.agent.llm import LLMError, make_client, parse_intent, propose_repair
from app.builder.generate import GenerationError, generate_project
from app.builder.plan import render_plan, render_result, run_command_for
from app.builder.repair import (
    MAX_REPAIR_ATTEMPTS,
    RepairAttempt,
    RepairRejected,
    apply_repair,
    build_repair_context,
)
from app.builder.resolve import resolve_spec
from app.builder.validate import ValidationReport, validate_project
from app.builder.workspace import (
    discard_staging,
    has_conflict,
    new_staging_root,
    promote,
    read_locked_versions,
    target_dir,
)
from app.console import Console, TerminalConsole
from app.models.intent import IntentDraft, IntentError, build_spec
from app.models.repair import RepairProposal
from app.models.spec import ProjectSpec
from app.tools.files import WorkspaceError
from app.tools.runner import CommandResult
from app.tools.versions import VersionLookupError
from app.tools.versions import make_client as make_http_client

# Errors whose message is meant for the user. Anything else is a bug and is raised.
USER_FACING_ERRORS = (IntentError, LLMError, VersionLookupError, GenerationError, WorkspaceError)
MAX_CLARIFICATIONS = 3
MAX_NAME_ATTEMPTS = 3


def _with_name(spec: ProjectSpec, name: str) -> ProjectSpec:
    """Return `spec` renamed to `name` (normalized), marked as chosen by the user."""
    try:
        return spec.updated(
            project_name=name, user_specified=spec.user_specified | {"project_name"}
        )
    except ValidationError as exc:
        reason = exc.errors()[0]["msg"].removeprefix("Value error, ")
        raise IntentError(f"Invalid project name {name!r}: {reason}") from exc


@dataclass
class Services:
    """External work the workflow depends on. Tests replace these with fakes."""

    parse_intent: Callable[[str], IntentDraft]
    resolve_spec: Callable[[ProjectSpec], ProjectSpec]
    propose_repair: Callable[[str], RepairProposal]
    generate_project: Callable[[ProjectSpec, Path], Path] = generate_project
    validate_project: Callable[[Path, ProjectSpec], ValidationReport] = validate_project
    promote: Callable[[Path, Path], CommandResult] = promote


class Workflow:
    def __init__(
        self, console: Console, output_dir: Path, services: Services, ask_for_name: bool = True
    ):
        self.console = console
        self.output_dir = output_dir
        self.services = services
        self.ask_for_name = ask_for_name
        # Name the user chose (flag, prompt, or request). Kept across edits that don't rename.
        self.project_name: str | None = None

    def run(self, request: str, project_name: str | None = None) -> int:
        try:
            spec, request = self._understand(request)
            spec = self._choose_name(spec, project_name)
        except USER_FACING_ERRORS as exc:
            self.console.show(f"Cannot continue: {exc}")
            return 1

        while True:
            target = target_dir(self.output_dir, spec)
            conflict = has_conflict(target)
            self.console.show("\n" + render_plan(spec, target, conflict))
            options = ["edit", "cancel"] if conflict else ["approve", "edit", "cancel"]
            choice = self.console.choose("Proceed with this plan?", options)
            if choice == "cancel":
                self.console.show("Cancelled. Nothing was written.")
                return 0
            if choice == "edit":
                spec, request = self._revise(spec, request)
                continue

            staging_root = new_staging_root(self.output_dir)
            try:
                outcome = self._build_and_review(spec, staging_root, target)
            finally:
                discard_staging(staging_root, self.output_dir)
            if outcome is not None:
                return outcome
            spec, request = self._revise(spec, request)

    def _understand(self, request: str) -> tuple[ProjectSpec, str]:
        """Request -> resolved ProjectSpec, asking clarifying questions if the LLM needs them."""
        self.console.show("Understanding the request...")
        result = build_spec(self.services.parse_intent(request))
        for _ in range(MAX_CLARIFICATIONS):
            if result.question is None:
                break
            answer = self.console.ask(result.question)
            if not answer:
                raise IntentError("No answer given to the clarifying question.")
            request = f"{request}\n\nClarification: {result.question} {answer}"
            result = build_spec(self.services.parse_intent(request))
        if result.spec is None:
            raise IntentError("The request is still unclear after several clarifications.")

        self.console.show("Looking up current versions...")
        spec = self.services.resolve_spec(result.spec)
        if "project_name" in spec.user_specified:
            self.project_name = spec.project_name  # the request named the project
        elif self.project_name:
            spec = _with_name(spec, self.project_name)  # keep the name chosen earlier
        return spec, request

    def _choose_name(self, spec: ProjectSpec, name_option: str | None) -> ProjectSpec:
        """Apply --name, or ask for a name if the request gave none. Enter keeps the default."""
        if name_option:
            spec = _with_name(spec, name_option)
        elif self.ask_for_name and "project_name" not in spec.user_specified:
            for _ in range(MAX_NAME_ATTEMPTS):
                answer = self.console.ask(f"Project name (press Enter for '{spec.project_name}'):")
                if not answer:
                    break
                try:
                    spec = _with_name(spec, answer)
                    break
                except IntentError as exc:
                    self.console.show(str(exc))
        if "project_name" in spec.user_specified:
            self.project_name = spec.project_name
        return spec

    def _revise(self, spec: ProjectSpec, request: str) -> tuple[ProjectSpec, str]:
        """Apply a change the user describes. On failure, keep the previous spec."""
        change = self.console.ask("What should change?")
        if not change:
            return spec, request
        try:
            return self._understand(f"{request}\n\nRequested change: {change}")
        except USER_FACING_ERRORS as exc:
            self.console.show(f"Couldn't apply that change: {exc}")
            return spec, request

    def _validate_and_repair(
        self, spec: ProjectSpec, project_dir: Path
    ) -> tuple[ValidationReport, list[RepairAttempt], str | None]:
        """Validate; on failure diagnose, repair, and re-validate, at most MAX_REPAIR_ATTEMPTS
        times. Returns the last report, the repairs applied, and why repair stopped early."""
        report = self.services.validate_project(project_dir, spec)
        repairs: list[RepairAttempt] = []
        while not report.ok and len(repairs) < MAX_REPAIR_ATTEMPTS:
            failed = report.failed_step
            self.console.show(
                f"Validation failed at '{failed.name}'. Diagnosing "
                f"(repair attempt {len(repairs) + 1} of {MAX_REPAIR_ATTEMPTS})..."
            )
            context = build_repair_context(spec, project_dir, failed, repairs)
            try:
                proposal = self.services.propose_repair(context)
                changed = apply_repair(spec, project_dir, proposal)
            except (LLMError, RepairRejected) as exc:
                return report, repairs, str(exc)
            repairs.append(RepairAttempt(failed.name, proposal.diagnosis, tuple(changed)))
            self.console.show(
                f"  Diagnosis: {proposal.diagnosis}\n  Changed: {', '.join(changed)}. "
                "Re-running validation..."
            )
            report = self.services.validate_project(project_dir, spec)

        if not report.ok and len(repairs) == MAX_REPAIR_ATTEMPTS:
            return report, repairs, f"Repair limit reached ({MAX_REPAIR_ATTEMPTS} attempts)."
        return report, repairs, None

    def _build_and_review(self, spec: ProjectSpec, staging_root: Path, target: Path) -> int | None:
        """Generate, validate, and ask for result approval. None means 'user wants changes'."""
        try:
            project_dir = self.services.generate_project(spec, staging_root)
        except USER_FACING_ERRORS as exc:
            self.console.show(f"Generation failed: {exc}")
            return 1

        self.console.show("Installing and validating (uv sync, ruff, pytest, health check)...")
        report, repairs, stop_reason = self._validate_and_repair(spec, project_dir)
        names = [dep.name for dep in spec.all_packages()]
        installed = read_locked_versions(project_dir, names) if report.ok else {}
        self.console.show("\n" + render_result(spec, report, installed, repairs, stop_reason))
        if not report.ok:
            self.console.show("\nThe project did not pass validation, so it was not kept.")
            return 1

        choice = self.console.choose("Accept this project?", ["accept", "changes", "reject"])
        if choice == "reject":
            self.console.show("Rejected. Nothing was kept.")
            return 0
        if choice == "changes":
            return None

        try:
            install = self.services.promote(project_dir, target)
        except USER_FACING_ERRORS as exc:
            self.console.show(f"Could not move the project into place: {exc}")
            return 1
        if not install.ok:
            self.console.show(
                f"The project is at {target}, but `uv sync` failed there:\n{install.output}"
            )
            return 1
        self.console.show(
            f"\nDone. Your project is at {target}\nRun it:  cd {target} && {run_command_for(spec)}"
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bootstrap-agent",
        description="Turn a project request into a runnable, validated local project.",
    )
    parser.add_argument("request", nargs="?", help='e.g. "Setup a Python + FastAPI project"')
    parser.add_argument(
        "--name",
        help="Project name, e.g. orders-api. If omitted and the request names none, "
        "you are asked (default: fastapi-app).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="Directory the project is created in (default: current directory).",
    )
    args = parser.parse_args(argv)
    console = TerminalConsole()

    output_dir = args.output_dir.expanduser().resolve()
    if not output_dir.is_dir():
        console.show(f"Output directory {output_dir} does not exist.")
        return 1
    request = args.request or console.ask("What would you like to build?")
    if not request:
        console.show("No request given.")
        return 1

    try:
        llm_client = make_client()
    except anthropic.AnthropicError as exc:
        console.show(f"Could not create the Anthropic client: {exc}")
        return 1

    with make_http_client() as http_client:
        services = Services(
            parse_intent=lambda text: parse_intent(text, llm_client),
            resolve_spec=lambda spec: resolve_spec(spec, http_client),
            propose_repair=lambda context: propose_repair(context, llm_client),
        )
        try:
            return Workflow(console, output_dir, services).run(request, args.name)
        except KeyboardInterrupt:
            console.show("\nInterrupted. Staging files were removed.")
            return 130


if __name__ == "__main__":
    sys.exit(main())
