"""Workflow tests: scripted user answers, fake LLM/lookups/validation, real files on disk."""

from pathlib import Path

from app.agent.llm import LLMError
from app.builder.repair import MAX_REPAIR_ATTEMPTS
from app.builder.validate import StepResult, ValidationReport
from app.builder.workspace import STAGING_DIR_NAME
from app.main import Services, Workflow
from app.models.intent import IntentDraft, RequestedItem
from app.models.repair import FileEdit, RepairProposal
from app.tools.runner import CommandResult
from tests.conftest import RESOLVED_VERSIONS


class ScriptedConsole:
    def __init__(self, answers: list[str]):
        self.answers = list(answers)
        self.shown: list[str] = []
        self.prompts: list[tuple[str, list[str] | None]] = []

    def show(self, text):
        self.shown.append(text)

    def choose(self, prompt, options):
        self.prompts.append((prompt, options))
        answer = self.answers.pop(0)
        assert answer in options, f"{answer!r} not offered in {options}"
        return answer

    def ask(self, prompt):
        self.prompts.append((prompt, None))
        return self.answers.pop(0)

    @property
    def text(self) -> str:
        return "\n".join(self.shown)


def _draft(*items, project_name=None, question=None) -> IntentDraft:
    return IntentDraft(
        is_project_request=True,
        project_name=project_name,
        requested=[RequestedItem(category=c, value=v) for c, v in items],
        clarification_question=question,
    )


FASTAPI = _draft(("language", "python"), ("framework", "fastapi"))


class FakeServices:
    def __init__(self, drafts, report_ok=True, validations=None, repairs=()):
        self.drafts = list(drafts)
        self.requests: list[str] = []
        self.report_ok = report_ok
        # Results of successive validation runs; when None every run returns report_ok.
        self.validations = list(validations) if validations is not None else None
        self.repairs = list(repairs)
        self.repair_contexts: list[str] = []
        self.built: list[Path] = []
        self.promoted: list[tuple[Path, Path]] = []

    def parse_intent(self, request):
        self.requests.append(request)
        return self.drafts.pop(0)

    def resolve_spec(self, spec):
        return spec.with_package_versions(RESOLVED_VERSIONS).updated(language_version="3.14")

    def validate_project(self, project_dir, spec):
        self.built.append(project_dir)
        names = ("install", "lint", "format", "tests", "health")
        ok = self.validations.pop(0) if self.validations is not None else self.report_ok
        if ok:
            steps = tuple(StepResult(n, True, n, "", 0.1) for n in names)
        else:
            steps = (
                StepResult("install", True, "uv sync", "", 0.1),
                StepResult("lint", False, "ruff", "E999 SyntaxError in main.py", 0.1),
            )
        return ValidationReport(steps)

    def propose_repair(self, context):
        self.repair_contexts.append(context)
        if not self.repairs:
            raise LLMError("no repair scripted")
        return self.repairs.pop(0)

    def promote(self, project_dir, target):
        assert (project_dir / "pyproject.toml").exists()  # still in staging when promoted
        target.mkdir(parents=True, exist_ok=True)
        self.promoted.append((project_dir, target))
        return CommandResult(("uv", "sync"), 0, "", 0.1)

    def services(self) -> Services:
        return Services(
            parse_intent=self.parse_intent,
            resolve_spec=self.resolve_spec,
            propose_repair=self.propose_repair,
            validate_project=self.validate_project,
            promote=self.promote,
        )


def _run(
    tmp_path,
    answers,
    drafts,
    request="Setup a Python + FastAPI project",
    ask_for_name=False,  # most tests are not about naming; see the name tests below
    name=None,
    **kwargs,
):
    console = ScriptedConsole(answers)
    fakes = FakeServices(drafts, **kwargs)
    workflow = Workflow(console, tmp_path, fakes.services(), ask_for_name=ask_for_name)
    code = workflow.run(request, name)
    return code, console, fakes


def _no_staging_left(tmp_path) -> bool:
    return not (tmp_path / STAGING_DIR_NAME).exists()


def test_approve_and_accept(tmp_path):
    code, console, fakes = _run(tmp_path, ["approve", "accept"], [FASTAPI])

    assert code == 0
    assert fakes.promoted[0][1] == (tmp_path / "fastapi-app").resolve()
    assert "Plan for: fastapi-app" in console.text
    assert "Done. Your project is at" in console.text
    assert _no_staging_left(tmp_path)


def test_cancel_at_plan_writes_nothing(tmp_path):
    code, console, fakes = _run(tmp_path, ["cancel"], [FASTAPI])

    assert code == 0
    assert fakes.built == []
    assert list(tmp_path.iterdir()) == []


def test_edit_reparses_with_the_change(tmp_path):
    renamed = _draft(("framework", "fastapi"), project_name="billing-api")
    code, console, fakes = _run(
        tmp_path, ["edit", "call it billing-api", "approve", "accept"], [FASTAPI, renamed]
    )

    assert code == 0
    assert "Requested change: call it billing-api" in fakes.requests[1]
    assert "Plan for: billing-api" in console.text
    assert fakes.promoted[0][1].name == "billing-api"


def test_rejected_edit_keeps_previous_plan(tmp_path):
    flask = _draft(("framework", "flask"))
    code, console, fakes = _run(tmp_path, ["edit", "use flask", "cancel"], [FASTAPI, flask])

    assert code == 0
    assert "Couldn't apply that change: Only Python + FastAPI" in console.text
    assert console.text.count("Plan for: fastapi-app") == 2


def test_reject_result_keeps_nothing(tmp_path):
    code, console, fakes = _run(tmp_path, ["approve", "reject"], [FASTAPI])

    assert code == 0
    assert len(fakes.built) == 1
    assert fakes.promoted == []
    assert list(tmp_path.iterdir()) == []


def test_changes_after_result_rebuilds(tmp_path):
    renamed = _draft(("framework", "fastapi"), project_name="orders")
    code, console, fakes = _run(
        tmp_path,
        ["approve", "changes", "call it orders", "approve", "accept"],
        [FASTAPI, renamed],
    )

    assert code == 0
    assert len(fakes.built) == 2
    assert fakes.promoted[0][1].name == "orders"
    assert _no_staging_left(tmp_path)


def test_failed_validation_reports_and_keeps_nothing(tmp_path):
    code, console, fakes = _run(tmp_path, ["approve"], [FASTAPI], report_ok=False)

    assert code == 1
    assert "FAIL  lint" in console.text
    assert "E999 SyntaxError in main.py" in console.text
    assert "did not pass validation" in console.text
    assert all(options != ["accept", "changes", "reject"] for _, options in console.prompts)
    assert list(tmp_path.iterdir()) == []


def test_existing_target_cannot_be_approved(tmp_path):
    (tmp_path / "fastapi-app").mkdir()
    (tmp_path / "fastapi-app" / "keep.txt").write_text("user data")

    code, console, fakes = _run(tmp_path, ["cancel"], [FASTAPI])

    assert code == 0
    assert console.prompts[0][1] == ["edit", "cancel"]
    assert "already exists and is not empty" in console.text
    assert (tmp_path / "fastapi-app" / "keep.txt").read_text() == "user data"


def test_clarifying_question(tmp_path):
    unclear = _draft(question="What kind of project do you want?")
    code, console, fakes = _run(
        tmp_path, ["A FastAPI web API", "cancel"], [unclear, FASTAPI], request="build me something"
    )

    assert code == 0
    assert console.prompts[0] == ("What kind of project do you want?", None)
    assert "Clarification: What kind of project do you want? A FastAPI web API" in fakes.requests[1]


def test_unsupported_request_exits_with_message(tmp_path):
    code, console, fakes = _run(tmp_path, [], [_draft(("framework", "django"))])

    assert code == 1
    assert "Cannot continue: Only Python + FastAPI" in console.text


def test_project_is_promoted_from_staging(tmp_path):
    _, _, fakes = _run(tmp_path, ["approve", "accept"], [FASTAPI])
    staged_project = fakes.promoted[0][0]
    assert staged_project.name == "fastapi-app"
    assert staged_project.parent.parent.name == STAGING_DIR_NAME


def _readme_fix(n: int) -> RepairProposal:
    return RepairProposal(
        diagnosis=f"cause {n}",
        can_repair=True,
        edits=[FileEdit(path="README.md", content=f"# fixed {n}\n")],
    )


def test_failure_is_repaired_then_accepted(tmp_path):
    code, console, fakes = _run(
        tmp_path,
        ["approve", "accept"],
        [FASTAPI],
        validations=[False, True],
        repairs=[_readme_fix(1)],
    )

    assert code == 0
    assert len(fakes.built) == 2
    assert "E999 SyntaxError in main.py" in fakes.repair_contexts[0]
    assert "Diagnosis: cause 1" in console.text
    assert "Repairs applied" in console.text  # shown even though the final run passed
    staged = fakes.promoted[0][0]
    assert fakes.promoted and staged.name == "fastapi-app"


def test_repair_limit_is_enforced(tmp_path):
    code, console, fakes = _run(
        tmp_path,
        ["approve"],
        [FASTAPI],
        validations=[False] * (MAX_REPAIR_ATTEMPTS + 1),
        repairs=[_readme_fix(n) for n in range(1, MAX_REPAIR_ATTEMPTS + 2)],
    )

    assert code == 1
    assert len(fakes.repair_contexts) == MAX_REPAIR_ATTEMPTS
    assert len(fakes.built) == MAX_REPAIR_ATTEMPTS + 1
    assert f"Repair limit reached ({MAX_REPAIR_ATTEMPTS} attempts)" in console.text
    assert f"Likely cause: cause {MAX_REPAIR_ATTEMPTS}" in console.text
    assert "previous_attempts" in fakes.repair_contexts[1]
    assert "cause 1" in fakes.repair_contexts[1]  # history is passed to the next attempt
    assert fakes.promoted == []
    assert list(tmp_path.iterdir()) == []


def test_unrepairable_failure_stops_early(tmp_path):
    refusal = RepairProposal(diagnosis="needs a different FastAPI version", can_repair=False)
    code, console, fakes = _run(
        tmp_path, ["approve"], [FASTAPI], validations=[False], repairs=[refusal]
    )

    assert code == 1
    assert len(fakes.built) == 1
    assert "Repair stopped: Cannot be repaired safely: needs a different FastAPI" in console.text


def test_disallowed_repair_is_not_applied(tmp_path):
    escape = RepairProposal(
        diagnosis="x", can_repair=True, edits=[FileEdit(path="../outside.txt", content="x")]
    )
    code, console, fakes = _run(
        tmp_path, ["approve"], [FASTAPI], validations=[False], repairs=[escape]
    )

    assert code == 1
    assert "not editable" in console.text
    assert not (tmp_path / "outside.txt").exists()


NAME_PROMPT = "Project name (press Enter for 'fastapi-app'):"


def test_name_prompt_enter_keeps_default(tmp_path):
    code, console, fakes = _run(tmp_path, ["", "approve", "accept"], [FASTAPI], ask_for_name=True)

    assert code == 0
    assert console.prompts[0] == (NAME_PROMPT, None)
    assert "Plan for: fastapi-app" in console.text
    assert fakes.promoted[0][1].name == "fastapi-app"


def test_name_prompt_uses_entered_name(tmp_path):
    code, console, fakes = _run(
        tmp_path, ["Orders API", "approve", "accept"], [FASTAPI], ask_for_name=True
    )

    assert code == 0
    assert "Plan for: orders-api" in console.text
    assert fakes.promoted[0][1].name == "orders-api"


def test_invalid_name_is_asked_again(tmp_path):
    code, console, fakes = _run(
        tmp_path, ["fastapi", "billing", "cancel"], [FASTAPI], ask_for_name=True
    )

    assert "Invalid project name 'fastapi'" in console.text
    assert [p for p, _ in console.prompts].count(NAME_PROMPT) == 2
    assert "Plan for: billing" in console.text


def test_no_prompt_when_request_names_the_project(tmp_path):
    named = _draft(("framework", "fastapi"), project_name="inventory")
    code, console, fakes = _run(tmp_path, ["cancel"], [named], ask_for_name=True)

    assert NAME_PROMPT not in [p for p, _ in console.prompts]
    assert "Plan for: inventory" in console.text


def test_name_option_skips_prompt_and_wins(tmp_path):
    named = _draft(("framework", "fastapi"), project_name="inventory")
    code, console, fakes = _run(
        tmp_path, ["cancel"], [named], ask_for_name=True, name="Payments Service"
    )

    assert console.prompts[0][0] == "Proceed with this plan?"
    assert "Plan for: payments-service" in console.text


def test_invalid_name_option_exits(tmp_path):
    code, console, _ = _run(tmp_path, [], [FASTAPI], name="test")

    assert code == 1
    assert "Cannot continue: Invalid project name 'test'" in console.text


def test_chosen_name_survives_edits(tmp_path):
    py313 = _draft(("framework", "fastapi"), ("language", "python"))
    code, console, fakes = _run(
        tmp_path,
        ["orders", "edit", "use python 3.13", "cancel"],
        [FASTAPI, py313],
        ask_for_name=True,
    )

    assert console.text.count("Plan for: orders") == 2
    assert [p for p, _ in console.prompts].count(NAME_PROMPT) == 1  # asked only once


def test_edit_can_rename(tmp_path):
    renamed = _draft(("framework", "fastapi"), project_name="billing")
    code, console, fakes = _run(
        tmp_path,
        ["orders", "edit", "call it billing", "cancel"],
        [FASTAPI, renamed],
        ask_for_name=True,
    )

    assert "Plan for: orders" in console.text
    assert "Plan for: billing" in console.text
