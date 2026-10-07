"""Command execution and health checks for generated projects (see AGENTS.md, "Safety").

- Only allowlisted commands run, as argument lists with no shell.
- Secrets are removed from the environment passed to child processes.
- Every process runs in its own process group so a timeout or stop also ends its children
  (`uv run` starts the real program as a child).
"""

import os
import signal
import socket
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

# Programs that may run inside a generated project, directly or through `uv run`.
ALLOWED_PROGRAMS = frozenset({"pytest", "ruff", "uvicorn"})
SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
MAX_OUTPUT_CHARS = 20_000
STOP_GRACE_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.25


class CommandNotAllowedError(Exception):
    """Raised when a command is outside the execution policy."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int | None  # None when the command timed out or could not start
    output: str  # stdout and stderr combined, truncated to the last MAX_OUTPUT_CHARS
    duration: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class HealthResult:
    url: str
    ok: bool
    status_code: int | None
    body: str
    server_output: str
    error: str | None
    duration: float


def check_command(argv: Sequence[str]) -> None:
    """Raise CommandNotAllowedError unless argv is `uv sync`, `uv run <allowed> ...`,
    or `<allowed> ...`."""
    if not argv:
        raise CommandNotAllowedError("Empty command.")
    if argv[0] == "uv":
        if list(argv) == ["uv", "sync"]:
            return
        if len(argv) >= 3 and argv[1] == "run" and argv[2] in ALLOWED_PROGRAMS:
            return
        raise CommandNotAllowedError(
            f"Only `uv sync` and `uv run <{'|'.join(sorted(ALLOWED_PROGRAMS))}>` are allowed, "
            f"got {list(argv)}."
        )
    if argv[0] not in ALLOWED_PROGRAMS:
        raise CommandNotAllowedError(f"Program {argv[0]!r} is not allowed.")


def run_command(argv: Sequence[str], cwd: Path, timeout: float) -> CommandResult:
    """Run an allowlisted command in `cwd` and return its result. Never raises for
    a failing command; only for one the policy forbids."""
    check_command(argv)
    if not cwd.is_dir():
        raise NotADirectoryError(f"Working directory {cwd} does not exist.")
    return _execute(argv, cwd, timeout)


def check_application_health(project_dir: Path, app_import: str, timeout: float) -> HealthResult:
    """Start `uvicorn <app_import>` on a free local port, request /health, then stop it."""
    port = find_free_port()
    argv = ["uv", "run", "uvicorn", app_import, "--host", "127.0.0.1", "--port", str(port)]
    check_command(argv)
    return _run_health_check(argv, project_dir, f"http://127.0.0.1:{port}/health", timeout)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _safe_env() -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("ANTHROPIC_")
        and not any(marker in name.upper() for marker in SECRET_MARKERS)
    }
    # The agent's own virtualenv must not leak into the generated project's `uv run`.
    env.pop("VIRTUAL_ENV", None)
    env["NO_COLOR"] = "1"
    return env


def _tail(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text[-MAX_OUTPUT_CHARS:]


def _start(argv: Sequence[str], cwd: Path, stdout) -> subprocess.Popen:
    return subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=_safe_env(),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # own process group, so we can stop its children too
    )


def _kill_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        _kill_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_group(proc, signal.SIGKILL)
            proc.wait()
    else:
        _kill_group(proc, signal.SIGKILL)  # leftover children of an exited parent


def _execute(argv: Sequence[str], cwd: Path, timeout: float) -> CommandResult:
    start = time.monotonic()
    try:
        proc = _start(argv, cwd, subprocess.PIPE)
    except FileNotFoundError:
        return CommandResult(tuple(argv), None, f"Program {argv[0]!r} not found.", 0.0)

    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc, signal.SIGKILL)
        output, _ = proc.communicate()
        message = f"\n[timed out after {timeout}s]"
        return CommandResult(
            tuple(argv), None, _tail(output) + message, time.monotonic() - start, timed_out=True
        )
    return CommandResult(tuple(argv), proc.returncode, _tail(output), time.monotonic() - start)


def _run_health_check(argv: Sequence[str], cwd: Path, url: str, timeout: float) -> HealthResult:
    start = time.monotonic()
    with tempfile.TemporaryFile(mode="w+") as log:
        try:
            proc = _start(argv, cwd, log)
        except FileNotFoundError:
            return HealthResult(url, False, None, "", "", f"Program {argv[0]!r} not found.", 0.0)
        try:
            status_code, body, error = _poll_health(url, proc, start + timeout, timeout)
        finally:
            _stop(proc)
        log.seek(0)
        server_output = _tail(log.read())

    return HealthResult(
        url=url,
        ok=status_code == 200,
        status_code=status_code,
        body=body,
        server_output=server_output,
        error=error,
        duration=time.monotonic() - start,
    )


def _poll_health(
    url: str, proc: subprocess.Popen, deadline: float, timeout: float
) -> tuple[int | None, str, str | None]:
    last_error = "no response"
    # trust_env=False: a configured HTTP proxy must not intercept localhost requests.
    with httpx.Client(timeout=2.0, trust_env=False) as client:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return None, "", f"Server exited with code {proc.returncode} before responding."
            try:
                response = client.get(url)
            except httpx.TransportError as exc:
                last_error = str(exc) or type(exc).__name__
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            body = response.text[:1000]
            if response.status_code == 200:
                return 200, body, None
            return response.status_code, body, f"GET {url} returned HTTP {response.status_code}."
    return None, "", f"Server did not respond within {timeout}s (last error: {last_error})."
