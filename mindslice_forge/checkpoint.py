"""Git checkpoints of the output workspace: one commit per finished step.

They give a clean rollback when a session fails, and ``git log -p`` in the workspace shows what
each chunk added or changed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import ForgeError

# The workspace needs no git configuration of its own, and paths are reported unescaped.
OPTIONS = ["-c", "user.name=mindslice-forge", "-c", "user.email=forge@localhost", "-c", "core.quotepath=off"]
WORKSPACE_DIRS = ("overlay", "staging", "slices")  # empty ones are not tracked by git


def git(out: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(out), *OPTIONS, *args], capture_output=True, text=True,
                              encoding="utf-8")
    except FileNotFoundError:
        raise ForgeError("the `git` command is not on PATH; the forge uses it for checkpoints") from None
    if done.returncode != 0:
        raise ForgeError(f"git {' '.join(args)} failed in {out}:\n{done.stderr.strip()}")
    return done.stdout


def init(out: Path) -> None:
    git(out, "init", "-q")


def commit(out: Path, message: str, tag: str | None = None) -> None:
    git(out, "add", "-A")
    git(out, "commit", "-q", "--allow-empty", "-m", message)
    if tag:
        git(out, "tag", "-f", tag)


def rollback(out: Path, ref: str = "HEAD") -> None:
    """Discard everything after ``ref``: uncommitted work, and later commits when ``ref`` is a tag."""
    git(out, "reset", "-q", "--hard", ref)
    git(out, "clean", "-fdq")
    for name in WORKSPACE_DIRS:  # `git clean` removes them when they are empty
        (out / name).mkdir(exist_ok=True)


def changed(out: Path) -> set[Path]:
    """Files created or modified since the last commit."""
    return _diff(out) | _untracked(out)


def created(out: Path) -> set[Path]:
    """Files created since the last commit."""
    return _diff(out, "--diff-filter=A") | _untracked(out)


def _diff(out: Path, *args: str) -> set[Path]:
    # Compare both the index and working tree to HEAD; staged additions also count as new.
    # Treat renames as delete/add so their destination cannot evade the lookup check.
    return _paths(out, git(out, "diff", "--name-only", "--no-renames", "-z", *args, "HEAD", "--"))


def _untracked(out: Path) -> set[Path]:
    return _paths(out, git(out, "ls-files", "--others", "--exclude-standard", "-z"))


def _paths(out: Path, names: str) -> set[Path]:
    return {path for name in names.split("\0") if name and (path := (out / name).resolve()).is_file()}
