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
    return {path for _, path in _status(out)}


def created(out: Path) -> set[Path]:
    """Files created since the last commit."""
    return {path for code, path in _status(out) if code == "??"}


def _status(out: Path) -> list[tuple[str, Path]]:
    entries = git(out, "status", "--porcelain", "-z", "--untracked-files=all").split("\0")
    return [(entry[:2], (out / entry[3:]).resolve()) for entry in entries if entry]
