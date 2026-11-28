"""Tests de scripts/check_additive_changes.py sur un dépôt Git temporaire."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_additive_changes.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_additive_changes", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("one\ntwo\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "tag", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_added_lines_and_new_files_are_accepted(repo: Path) -> None:
    (repo / "core" / "a.py").write_text("one\ntwo\nthree\n")
    (repo / "core" / "new.py").write_text("x\n")
    assert load_script().main(["--base", "base", "core"]) == 0


def test_removed_line_is_refused(repo: Path) -> None:
    (repo / "core" / "a.py").write_text("one\n")
    assert load_script().main(["--base", "base", "core"]) == 1


def test_modified_line_is_refused(repo: Path) -> None:
    (repo / "core" / "a.py").write_text("one\nTWO\n")
    assert load_script().main(["--base", "base", "core"]) == 1


def test_deleted_or_renamed_file_is_refused(repo: Path) -> None:
    git(repo, "mv", "core/a.py", "core/b.py")
    assert load_script().main(["--base", "base", "core"]) == 1


def test_unknown_base_is_an_error(repo: Path) -> None:
    assert load_script().main(["--base", "inexistante", "core"]) == 2
