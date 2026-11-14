"""The MindSlice engine, used only through its ``mindslice`` command."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

from . import ForgeError

# Warnings an agent can fix in the card they are reported on. Others are left for the final report.
FIXABLE = {"card_too_long", "missing_summary", "long_summary", "missing_title", "missing_frontmatter",
           "broken_wikilink", "frontmatter_schema"}


def _run(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["mindslice", *args, "--json"], capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        raise ForgeError("the `mindslice` command is not on PATH; install the MindSlice engine first") from None


def _call(*args: str) -> dict:
    done = _run(*args)
    if done.returncode != 0:
        raise ForgeError(f"mindslice {' '.join(args)} failed:\n{done.stderr.strip() or done.stdout.strip()}")
    return json.loads(done.stdout)


def init(out: Path) -> None:
    _call("init", str(out))


def mount_slices(out: Path) -> list[str]:
    """Mount every ``slices/*.slice`` directory that is not mounted yet; returns the new aliases."""
    mounted = {(out / m["path"]).resolve() for m in _call("-w", str(out), "status")["mounts"]}
    new = [d for d in sorted((out / "slices").glob("*.slice")) if d.resolve() not in mounted]
    return [_call("-w", str(out), "mount", str(d), "--precedence", "50", "--role", "curated")["alias"] for d in new]


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
