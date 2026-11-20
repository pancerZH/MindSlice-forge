"""Git checkpoints of the output workspace: one commit per finished step.

They give a clean rollback when a session fails, and ``git log -p`` in the workspace shows what
each chunk added or changed.

The checkpoints cover what the forge builds: the slices and the mount table. Once the workspace
is connected to an agent it also holds what that agent learns (``LIVE``), which is never
committed and never rolled back. The engine changes the slices and the mount table as well, when
the user bakes or mounts; a rollback that would remove such a change is refused.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from . import ForgeError

# The workspace needs no git configuration of its own, and paths are reported unescaped.
OPTIONS = ["-c", "user.name=mindslice-forge", "-c", "user.email=forge@localhost", "-c", "core.quotepath=off"]
WORKSPACE_DIRS = ("overlay", "staging", "slices")  # empty ones are not tracked by git
LIVE = ("overlay", "staging", ".history")  # written by the engine while the workspace is in use as memory
PENDING = "forge-pending"  # in .git, from the start of an agent session until the next checkpoint
RUNNING = "running\n"  # its content while the session runs; a failed attempt replaces it with a fingerprint


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
    """Mark the session as running. A hard interruption leaves its dirty files for inspection."""
    (out / ".git" / PENDING).write_text(RUNNING, encoding="ascii")


def _fingerprint(out: Path) -> str:
    digest = hashlib.sha256()
    for args in (("diff", "--cached"), ("diff",)):
        digest.update(git(out, *args, "--binary", "--no-ext-diff", "--").encode())
    for name in git(out, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if not name:
            continue
        path = out / name
        digest.update(os.fsencode(name) + b"\0")
        digest.update(str(path.lstat().st_mode).encode() + b"\0")
        if path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
        else:
            raise ForgeError(f"cannot checkpoint special file {path}; preserve it before resuming")
    return digest.hexdigest()


def record_failed_attempt(out: Path) -> None:
    (out / ".git" / PENDING).write_text(_fingerprint(out), encoding="ascii")


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
    foreign = git(out, "status", "--porcelain").splitlines()
    if foreign and pending.exists() and pending.read_text(encoding="ascii") == _fingerprint(out):
        foreign = []
    if foreign:
        interrupted = pending.exists() and pending.read_text(encoding="ascii") == RUNNING
        raise ForgeError(f"{out} has changes that the forge did not make or cannot identify after an interruption:\n"
                         + "\n".join(f"  {line}" for line in foreign)
                         + "\nContinuing this build would discard them. Undo them, or build into another "
                           "--output and mount its slices where they are needed."
                         + (f"\nAn agent session was killed before it ended. If these are its leftovers, discard them "
                            f"with `git -C {out} reset --hard && git -C {out} clean -fd` and run again."
                            if interrupted else ""))
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


def head(out: Path) -> str:
    return git(out, "rev-parse", "HEAD").strip()


def uncommit(out: Path, ref: str) -> None:
    """Go back to the checkpoint ``ref``, keeping what later commits changed as uncommitted work."""
    git(out, "reset", "-q", "--soft", ref)


def live(out: Path) -> dict[Path, int]:
    """The cards of live memory with their modification times: what the engine's writes would change."""
    return {path.resolve(): path.stat().st_mtime_ns for name in ("overlay", "staging")
            for path in (out / name).rglob("*.md")}


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
    exclude = info / "exclude"
    old = exclude.read_bytes() if exclude.exists() else b""
    missing = [f"/{name}/".encode() for name in LIVE if f"/{name}/".encode() not in old.splitlines()]
    if missing:
        with exclude.open("ab") as stream:
            if old and not old.endswith(b"\n"):
                stream.write(b"\n")
            stream.write(b"\n".join(missing) + b"\n")


def _paths(out: Path, names: str) -> set[Path]:
    return {out / name for name in names.split("\0") if name}
