"""The MindSlice engine, used only through its ``mindslice`` command."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

from . import ForgeError

# Warnings an agent can fix in the card they are reported on. Others are left for the final report.
FIXABLE = {"card_too_long", "missing_summary", "long_summary", "missing_title", "missing_frontmatter",
           "broken_wikilink", "frontmatter_schema"}
MINIMUM = (0, 2, 5)  # older engines are not supported (single user, no compatibility kept)


def _engine(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["mindslice", *args], capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        raise ForgeError("the `mindslice` command is not on PATH; install the MindSlice engine first") from None


def _run(*args: str) -> subprocess.CompletedProcess:
    return _engine(*args, "--json")


def version() -> str:
    """What ``mindslice --version`` prints. An engine older than ``MINIMUM`` is an error."""
    done = _engine("--version")
    line = done.stdout.strip()
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", line)
    if done.returncode != 0 or not found:
        raise ForgeError(f"`mindslice --version` did not give a version:\n{line or done.stderr.strip()}")
    if tuple(map(int, found.groups())) < MINIMUM:
        raise ForgeError(f"this forge needs MindSlice {'.'.join(map(str, MINIMUM))} or newer, and `mindslice` on PATH "
                         f"is {found.group()}; upgrade the engine, for example with `pipx upgrade mindslice`")
    return line


def _call(*args: str) -> dict:
    done = _run(*args)
    if done.returncode != 0:
        raise ForgeError(f"mindslice {' '.join(args)} failed:\n{done.stderr.strip() or done.stdout.strip()}")
    return json.loads(done.stdout)


def init(out: Path) -> None:
    _call("init", str(out))


def mounts(workspace: Path) -> list[dict]:
    """The mount table of a workspace, the overlay included; ``path`` is relative to the workspace or absolute."""
    return _call("-w", str(workspace), "status")["mounts"]


def mount(workspace: Path, directory: Path, alias: str | None = None, precedence: int = 50,
          role: str = "curated") -> str:
    """Mount ``directory`` read-only and return its alias. The workspace's overlay is left as it is."""
    args = ["-w", str(workspace), "mount", str(directory), "--precedence", str(precedence), "--role", role,
            "--keep-overlay", *(["--alias", alias] if alias else [])]
    return _call(*args)["alias"]


def mount_slices(out: Path) -> list[str]:
    """Mount every ``slices/*.slice`` directory that is not mounted yet; returns the new aliases."""
    mounted = {(out / m["path"]).resolve() for m in mounts(out)}
    return [mount(out, d) for d in sorted((out / "slices").glob("*.slice")) if d.resolve() not in mounted]


def slice_alias(directory: Path) -> str:
    """The alias a slice directory is mounted under: the one in its ``slice.toml``, or else its name."""
    try:
        alias = tomllib.loads((directory / "slice.toml").read_text(encoding="utf-8")).get("alias")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        alias = None
    name = alias.strip() if isinstance(alias, str) and alias.strip() else directory.name.removesuffix(".slice")
    return "@" + name.removeprefix("@")


def validate(out: Path, slice_dir: Path | None = None) -> dict:
    """The engine's lint report for the workspace, or for one slice directory. Errors do not raise."""
    done = _run("-w", str(out), "validate", *([str(slice_dir)] if slice_dir else []))
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        raise ForgeError(f"mindslice validate failed:\n{done.stderr.strip() or done.stdout.strip()}") from None


def card_names(card: Path) -> list[str]:
    """A card's title and aliases, or no names when its frontmatter is unreadable."""
    text = card.read_text(encoding="utf-8", errors="replace")
    try:
        lines = text.splitlines()
        meta = tomllib.loads("\n".join(lines[1:lines.index("+++", 1)])) if lines and lines[0] == "+++" else {}
    except (ValueError, tomllib.TOMLDecodeError):
        meta = {}
    aliases = meta.get("aliases") if isinstance(meta.get("aliases"), list) else []
    names = [name for name in [meta.get("title"), *aliases] if isinstance(name, str) and name.strip()]
    return names


def issues(report: dict, files: set[Path] | None = None) -> list[dict]:
    """What a repair session should fix: every error, and the fixable warnings on ``files``
    (on any file when ``files`` is None)."""
    out = list(report["errors"])
    for warning in report["warnings"]:
        file = warning.get("file")
        if warning["code"] in FIXABLE and file and (files is None or Path(file).resolve() in files):
            out.append(warning)
    return out
