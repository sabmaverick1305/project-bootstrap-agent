import pytest

from app.tools.files import WorkspaceError, read_file, resolve_inside, write_file


def test_write_and_read_inside_workspace(tmp_path):
    path = write_file(tmp_path, "src/pkg/main.py", "x = 1\n")

    assert path == (tmp_path / "src/pkg/main.py").resolve()
    assert read_file(tmp_path, "src/pkg/main.py") == "x = 1\n"


@pytest.mark.parametrize("relative", ["../outside.txt", "a/../../outside.txt", "/etc/passwd", "."])
def test_paths_outside_workspace_are_rejected(tmp_path, relative):
    with pytest.raises(WorkspaceError):
        resolve_inside(tmp_path / "root", relative)


def test_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(tmp_path)

    with pytest.raises(WorkspaceError):
        write_file(root, "link/escaped.txt", "nope")
    assert not (tmp_path / "escaped.txt").exists()
