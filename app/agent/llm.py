"""LLM calls (Anthropic SDK). Used only to read intent and, later, to diagnose failures."""

from pathlib import Path

import anthropic
import pydantic

from app.models.intent import IntentDraft
from app.models.repair import RepairProposal

MODEL = "claude-opus-5-5"
INTENT_EFFORT = "medium"
REPAIR_EFFORT = "high"  # diagnosis needs more reasoning than extraction
MAX_TOKENS = 16000
# If a safety classifier declines a request, Anthropic retries it on its recommended
# fallback model instead of returning the refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.md").read_text(encoding="utf-8")

INTENT_TASK = """\
Current step: PARSE INTENT. Extract what the user asked for into the output schema.
Report the request only. Do not choose defaults, look anything up, or recommend anything;
later steps do that.

- requested: every technology the user asked for or clearly implied (a FastAPI project
  implies language "python"). Include ones that are not supported (for example a
  database or Docker) so they can be reported. Do not add anything the user did not ask for.
- value: the canonical lowercase name, e.g. python, fastapi, flask, django, uv, pip,
  poetry, uvicorn, gunicorn, pytest, ruff, postgresql, sqlite, docker.
- version: only a version the user named, e.g. "3.13". Null for "latest" or unspecified.
- project_name: only if the user gave a name.
- clarification_question: only if you cannot tell what kind of project the user wants.
  A request naming a language or framework is never ambiguous.
- is_project_request: false if the user is not asking to create a software project."""

REPAIR_TASK = """\
Current step: DIAGNOSE AND REPAIR. A validation step failed for the generated project above.

- Find the root cause from the actual output in <failed_step>.
- Propose the smallest change that fixes it, as the complete new content of each file you
  change. Only files listed in <files> can be edited; no new files.
- Do not change the project name, requires-python, dependencies, dependency-groups, or
  build-system in pyproject.toml. They come from the ProjectSpec. If the fix needs that,
  set can_repair to false and explain why in diagnosis.
- Fix the application, not the tests, unless a test is itself wrong. Never delete or
  weaken a test to make it pass.
- Do not repeat a change from <previous_attempts>. No unrelated changes or refactors."""


class LLMError(Exception):
    """Raised when the LLM call fails or returns no usable result. Message is user-facing."""


def make_client() -> anthropic.Anthropic:
    """Credentials come from ANTHROPIC_API_KEY or an `ant auth login` profile."""
    return anthropic.Anthropic()


def parse_intent(request: str, client: anthropic.Anthropic) -> IntentDraft:
    request = request.strip()
    if not request:
        raise LLMError("The request is empty.")
    content = f"<request>\n{request}\n</request>\n\n{INTENT_TASK}"
    return _structured_call(client, content, IntentDraft, INTENT_EFFORT)


def propose_repair(context: str, client: anthropic.Anthropic) -> RepairProposal:
    """`context` comes from app.builder.repair.build_repair_context."""
    return _structured_call(client, f"{context}\n\n{REPAIR_TASK}", RepairProposal, REPAIR_EFFORT)


def _structured_call[T: pydantic.BaseModel](
    client: anthropic.Anthropic, content: str, output_format: type[T], effort: str
) -> T:
    try:
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_format=output_format,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as exc:
        raise LLMError(
            "Anthropic credentials are missing or invalid. Set ANTHROPIC_API_KEY or run "
            "`ant auth login`."
        ) from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Anthropic API rate limit reached. Try again shortly.") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Anthropic API error (HTTP {exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Could not reach the Anthropic API. Check the network connection.") from exc
    except pydantic.ValidationError as exc:
        # parse() validates inside the call, so an empty or truncated reply (for example a
        # refusal or a max_tokens cut-off) surfaces here rather than through stop_reason.
        raise LLMError(
            "The model did not return a valid structured result (it may have declined the "
            "request or been cut off)."
        ) from exc

    if response.stop_reason == "refusal":
        raise LLMError("The model declined to process this request.")
    if response.stop_reason == "max_tokens":
        raise LLMError("The model's response was cut off before it finished.")
    if response.parsed_output is None:
        raise LLMError("The model did not return a structured result.")
    return response.parsed_output
