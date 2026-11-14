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
    """Whether one of the lookups was for one of these names: a word of the lookup occurs in a
    name, or a name occurs in the lookup."""
    names = [name.casefold().strip() for name in names if name.strip()]
    for query in map(str.casefold, queries):
        if any(word in name or name in query for word in query.split() for name in names):
            return True
    return False


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    workspace, query = Path(argv[0]).resolve(), " ".join(argv[1:])
    done = subprocess.run(["mindslice", "-w", str(workspace), "search", query])
    with open(log_file(workspace), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"time": time.time(), "query": query}, ensure_ascii=False) + "\n")
    return done.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
