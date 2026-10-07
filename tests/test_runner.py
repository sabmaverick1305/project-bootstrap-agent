import sys
import time

import pytest

from app.tools import runner
from app.tools.runner import (
    CommandNotAllowedError,
    _execute,
    _run_health_check,
    check_command,
    find_free_port,
    run_command,
)

PY = sys.executable


@pytest.mark.parametrize(
    "argv",
    [
        ["uv", "sync"],
        ["uv", "run", "pytest"],
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "uvicorn", "pkg.main:app", "--port", "8000"],
        ["ruff", "format", "--check", "."],
    ],
)
def test_allowed_commands(argv):
    check_command(argv)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["rm", "-rf", "/"],
        ["bash", "-c", "echo hi"],
        ["uv sync"],  # a shell string, not an argument list
        ["uv", "pip", "install", "requests"],
        ["uv", "sync", "--directory", "/"],
        ["uv", "run", "bash"],
        ["uv", "run", "--with", "requests", "pytest"],
        ["python", "-c", "print(1)"],
        ["uv", "run", "python", "-c", "print(1)"],
        ["uv", "run"],
        ["curl", "https://example.com"],
    ],
)
def test_disallowed_commands(argv):
    with pytest.raises(CommandNotAllowedError):
        check_command(argv)


def test_run_command_rejects_before_running(tmp_path):
    marker = tmp_path / "ran"
    with pytest.raises(CommandNotAllowedError):
        run_command(["touch", str(marker)], tmp_path, timeout=5)
    assert not marker.exists()


def test_run_command_requires_existing_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        run_command(["ruff", "--version"], tmp_path / "missing", timeout=5)


def test_execute_captures_output_and_exit_code(tmp_path):
    result = _execute(
        [PY, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
        tmp_path,
        timeout=10,
    )

    assert result.returncode == 3
    assert not result.ok
    assert "out" in result.output and "err" in result.output


def test_execute_success(tmp_path):
    result = _execute([PY, "-c", "print('hi')"], tmp_path, timeout=10)
    assert result.ok
    assert result.output.strip() == "hi"


def test_execute_timeout_kills_process(tmp_path):
    start = time.monotonic()
    result = _execute([PY, "-c", "import time; time.sleep(30)"], tmp_path, timeout=0.5)

    assert result.timed_out
    assert not result.ok
    assert "timed out" in result.output
    assert time.monotonic() - start < 10


def test_execute_missing_program(tmp_path):
    result = _execute(["definitely-not-a-program-xyz"], tmp_path, timeout=5)
    assert result.returncode is None
    assert "not found" in result.output


def test_execute_truncates_long_output(tmp_path):
    result = _execute([PY, "-c", "print('x' * 50000)"], tmp_path, timeout=10)
    assert len(result.output) == runner.MAX_OUTPUT_CHARS


def test_secrets_are_not_passed_to_children(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-secret")
    monkeypatch.setenv("DB_PASSWORD", "hunter2")
    monkeypatch.setenv("VIRTUAL_ENV", "/agent/.venv")
    monkeypatch.setenv("HARMLESS_SETTING", "visible")

    script = "import os; print(sorted(os.environ))"
    output = _execute([PY, "-c", script], tmp_path, timeout=10).output

    for name in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "VIRTUAL_ENV"):
        assert name not in output
    assert "HARMLESS_SETTING" in output
    assert "PATH" in output


def _http_server(directory, port):
    return [PY, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", directory]


def test_health_check_ok(tmp_path):
    (tmp_path / "health").write_text("ok")
    port = find_free_port()

    result = _run_health_check(
        _http_server(str(tmp_path), port), tmp_path, f"http://127.0.0.1:{port}/health", timeout=15
    )

    assert result.ok, result
    assert result.status_code == 200
    assert result.body == "ok"
    assert result.error is None


def test_health_check_non_200(tmp_path):
    port = find_free_port()  # no "health" file, so the server answers 404

    result = _run_health_check(
        _http_server(str(tmp_path), port), tmp_path, f"http://127.0.0.1:{port}/health", timeout=15
    )

    assert not result.ok
    assert result.status_code == 404
    assert "404" in result.error


def test_health_check_server_crashes(tmp_path):
    argv = [PY, "-c", "import sys; print('boom: import failed'); sys.exit(1)"]

    result = _run_health_check(argv, tmp_path, "http://127.0.0.1:9/health", timeout=15)

    assert not result.ok
    assert "exited with code 1" in result.error
    assert "boom: import failed" in result.server_output


def test_health_check_times_out_and_stops_server(tmp_path):
    pid_file = tmp_path / "pid"
    script = (
        f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    )
    start = time.monotonic()

    result = _run_health_check(
        [PY, "-c", script], tmp_path, f"http://127.0.0.1:{find_free_port()}/health", timeout=1
    )

    assert not result.ok
    assert "did not respond" in result.error
    assert time.monotonic() - start < 10
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        runner.os.kill(pid, 0)  # the server process no longer exists
