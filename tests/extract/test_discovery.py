from __future__ import annotations

import os
import subprocess

from codecards.extract.discovery import find_python_files


def write(root, rel, text="x = 1\n"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def names(files, root):
    return sorted(str(f.relative_to(root)) for f in files)


def test_finds_python_files_recursively(tmp_path):
    write(tmp_path, "a.py")
    write(tmp_path, "pkg/b.py")
    write(tmp_path, "pkg/notes.txt")
    files, skipped = find_python_files([tmp_path])
    assert names(files, tmp_path) == ["a.py", "pkg/b.py"]
    assert skipped == []


def test_noise_directories_are_skipped(tmp_path):
    write(tmp_path, "a.py")
    write(tmp_path, "__pycache__/junk.py")
    write(tmp_path, ".venv/lib/dep.py")
    write(tmp_path, "node_modules/thing.py")
    files, _ = find_python_files([tmp_path])
    assert names(files, tmp_path) == ["a.py"]


def test_exclude_patterns_are_applied(tmp_path):
    write(tmp_path, "a.py")
    write(tmp_path, "tests/test_a.py")
    files, _ = find_python_files([tmp_path], excludes=["tests/*"])
    assert names(files, tmp_path) == ["a.py"]


def test_symlink_loop_does_not_hang(tmp_path):
    write(tmp_path, "a.py")
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    files, _ = find_python_files([tmp_path])
    assert names(files, tmp_path) == ["a.py"]


def test_results_are_sorted_and_deduplicated(tmp_path):
    write(tmp_path, "b.py")
    write(tmp_path, "a.py")
    files, _ = find_python_files([tmp_path, tmp_path])
    assert names(files, tmp_path) == ["a.py", "b.py"]


def test_missing_root_is_reported_not_raised(tmp_path):
    files, skipped = find_python_files([tmp_path / "nope"])
    assert files == []
    assert len(skipped) == 1
    assert "does not exist" in skipped[0].reason


def test_a_single_file_root_is_accepted(tmp_path):
    path = write(tmp_path, "a.py")
    files, _ = find_python_files([path])
    assert files == [path.resolve()]


def test_gitignored_files_are_skipped_in_a_repo(tmp_path):
    write(tmp_path, "a.py")
    write(tmp_path, "generated/gen.py")
    (tmp_path / ".gitignore").write_text("generated/\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    files, _ = find_python_files([tmp_path])
    assert names(files, tmp_path) == ["a.py"]
