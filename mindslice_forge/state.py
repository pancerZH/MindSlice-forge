"""Resumable progress: one flat ``state.json`` in the forge directory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

STAGES = ("plan", "split", "extract", "finalize")


def fingerprint(files: list[Path], prompt: str, chunk_size: int) -> str:
    """A hash of everything the result depends on: source and dict contents, the prompt, the chunk size."""
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path).encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    digest.update(prompt.encode() + b"\0" + str(chunk_size).encode())
    return digest.hexdigest()


def new(fingerprint: str, into: str | None = None) -> dict:
    """``into``: the existing workspace the result is for (``--into``); ``connected``: whether it was added there."""
    return {"fingerprint": fingerprint, "into": into, "plan": False, "split": False, "chunks_done": [],
            "finalized": False, "connected": False}


def load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def rewind(state: dict, stage: str) -> None:
    """Forget ``stage`` and every stage after it, so the next run does them again."""
    redo = STAGES[STAGES.index(stage):]
    if "plan" in redo:
        state["plan"] = False
    if "split" in redo:
        state["split"] = False
    if "extract" in redo:
        state["chunks_done"] = []
    state["finalized"] = False
    state["connected"] = False
    state.pop("connecting", None)
