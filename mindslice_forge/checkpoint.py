"""Git checkpoints of the output workspace: one commit per finished step.

They give a clean rollback when a session fails, and ``git log -p`` in the workspace shows what
each chunk added or changed.

The checkpoints cover what the forge builds: the slices and the mount table. Once the workspace
is connected to an agent it also holds what that agent learns (``LIVE``), which is never
committed and never rolled back. The engine changes the slices and the mount table as well, when
the user bakes or mounts; a rollback that would remove such a change is refused.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import ForgeError

# The workspace needs no git configuration of its own, and paths are reported unescaped.
OPTIONS = ["-c", "user.name=mindslice-forge", "-c", "user.email=forge@localhost", "-c", "core.quotepath=off"]
WORKSPACE_DIRS = ("overlay", "staging", "slices")  # empty ones are not tracked by git
LIVE = ("overlay", "staging", ".history")  # written by the engine while the workspace is in use as memory
PENDING = "forge-pending"  # in .git, from the start of an agent session until the next checkpoint


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
    _exclude_live(out)


def begin(out: Path) -> None:
    """An agent session is about to work in ``out``: until the next checkpoint, what differs from
    the last one is the forge's own unfinished work, and a rollback may discard it."""
    (out / ".git" / PENDING).touch()


def commit(out: Path, message: str, tag: str | None = None) -> None:
    git(out, "add", "-A")
    git(out, "commit", "-q", "--allow-empty", "-m", message)
    if tag:
        git(out, "tag", "-f", tag)
    (out / ".git" / PENDING).unlink(missing_ok=True)


def rollback(out: Path, ref: str = "HEAD") -> None:
    """Discard the forge's work after ``ref``: an unfinished step, and later commits when ``ref`` is a tag.

    Live memory stays as it is. Changes found while no step is unfinished were made by something
    else (a bake, a mount, an edit by hand), so they are reported and nothing is discarded."""
    if not (out / ".git").is_dir():  # git would act on a repository that contains ``out``
        raise ForgeError(f"{out} has no forge checkpoints (its .git is missing); choose another --output")
    _exclude_live(out)  # also for a workspace that an earlier forge version created
    pending = out / ".git" / PENDING
    foreign = [] if pending.exists() else git(out, "status", "--porcelain").splitlines()
    if foreign:
        raise ForgeError(f"{out} has changes that the forge did not make:\n"
                         + "\n".join(f"  {line}" for line in foreign)
                         + "\nContinuing this build would discard them. Undo them, or build into another "
                           "--output and mount its slices where they are needed.")
    git(out, "reset", "-q", "--hard", ref)
    git(out, "clean", "-fdq")
    pending.unlink(missing_ok=True)
    for name in WORKSPACE_DIRS:  # `git clean` removes them when they are empty
        (out / name).mkdir(exist_ok=True)


def restore(out: Path, name: str) -> list[str]:
    """Undo every change below the directory ``name`` since the last commit; returns the paths that had changed."""
    entries = git(out, "status", "--porcelain", "-z", "--untracked-files=all", "--", name).split("\0")
    changed = [entry[3:] for entry in entries if entry]
    if changed:
        git(out, "checkout", "-q", "HEAD", "--", name)
        git(out, "clean", "-fdq", "--", name)
    return changed


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


def _exclude_live(out: Path) -> None:
    """Keep live memory out of every commit, comparison and rollback."""
    info = out / ".git" / "info"
    info.mkdir(exist_ok=True)
    (info / "exclude").write_text("".join(f"/{name}/\n" for name in LIVE), encoding="utf-8")


def _paths(out: Path, names: str) -> set[Path]:
    return {path for name in names.split("\0") if name and (path := (out / name).resolve()).is_file()}
