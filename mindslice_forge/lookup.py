"""Search the workspace for a name, and record that the search was made.

    python lookup.py WORKSPACE NAME...

Agents use this, not ``mindslice search`` directly, to find out whether something already has a
card. The record is what lets the forge enforce "look before you write": a new card whose name
was never looked up is sent back. This file is run as a script by its path, so it imports
nothing from the package.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unicodedata
from pathlib import Path


def forge_dir(workspace: Path) -> Path:
    """The forge's own directory for a workspace: next to it, so it never ends up in a workspace backup."""
    return workspace.with_name(workspace.name + ".forge")


def log_file(workspace: Path) -> Path:
    return forge_dir(workspace) / "lookups.jsonl"


def queries(workspace: Path) -> list[str]:
    """Every lookup made so far, oldest first."""
    log = log_file(workspace)
    if not log.exists():
        return []
    return [json.loads(line)["query"] for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def covered(names: list[str], queries: list[str]) -> bool:
    """Match a whole lookup name within a title/alias, or a whole title/alias in a lookup.

    Substrings allow a base name to cover its period cards. Latin letters and numbers must
    match at word boundaries, so searching for "API" cannot cover "Capital".
    """
    def normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    def word_char(char: str) -> bool:
        return char.isdigit() or "LATIN" in unicodedata.name(char, "") or char == "_"

    def contains(text: str, name: str) -> bool:
        start = text.find(name)
        while start >= 0:
            end = start + len(name)
            if (not word_char(name[0]) or start == 0 or not word_char(text[start - 1])) and (
                    not word_char(name[-1]) or end == len(text) or not word_char(text[end])):
                return True
            start = text.find(name, start + 1)
        return False

    names = [normalized for name in names if (normalized := normalize(name))]
    for query in queries:
        query = normalize(query)
        if query and any(contains(name, query) or contains(query, name) for name in names):
            return True
    return False


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    workspace, query = Path(argv[0]).resolve(), " ".join(argv[1:]).strip()
    if not query:
        print("error: lookup needs a non-empty name", file=sys.stderr)
        return 2
    try:
        # JSON also avoids the engine's text renderer failing on unresolved lineage references.
        done = subprocess.run(["mindslice", "-w", str(workspace), "search", query, "--json"])
        if done.returncode == 0:
            log = log_file(workspace)
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"time": time.time(), "query": query}, ensure_ascii=False) + "\n")
    except OSError as error:
        print(f"error: lookup failed: {error}", file=sys.stderr)
        return 1
    return done.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
