"""Chunks: run the agent-written splitter and check that its chunks account for every source line.

The splitter contract (also given to the agent in ``prompts/plan.md``):

    python split.py --chunk-size CHARS --out DIR SOURCE...

writes ``DIR/<id>.txt`` (UTF-8) and ``DIR/manifest.json``::

    {"chunks":  [{"id": "0001", "file": "0001.txt", "title": "...",
                  "parts": [{"source": "/abs/path", "start_line": 1, "end_line": 120}]}],
     "skipped": [{"source": "/abs/path", "start_line": 121, "end_line": 125, "reason": "..."}]}

Line numbers are 1-based and inclusive, counted as ``profile.read_lines`` counts them. A chunk can
hold parts of several sources (small files sharing a chunk), and a source can be spread over
several chunks (a large file cut at its own boundaries).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .profile import Source, read_lines

HARD_LIMIT = 1.5  # a chunk may exceed the target size by this factor, no more
TIMEOUT = 600


def run_splitter(script: Path, chunks_dir: Path, sources: list[Source], chunk_size: int) -> str | None:
    """Run the splitter into a fresh ``chunks_dir``. Returns what went wrong, or None."""
    shutil.rmtree(chunks_dir, ignore_errors=True)
    if not script.exists():
        return f"{script} does not exist"
    cmd = [sys.executable, str(script), "--chunk-size", str(chunk_size), "--out", str(chunks_dir),
           *(str(s.path) for s in sources)]
    try:
        done = subprocess.run(cmd, cwd=script.parent, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"{script.name} did not finish within {TIMEOUT}s"
    if done.returncode != 0:
        return f"{script.name} exited with status {done.returncode}:\n{done.stderr[-2000:]}"
    return None


def single_chunk(chunks_dir: Path, sources: list[Source]) -> None:
    """Sources small enough to read at once need no splitter: they are one chunk."""
    shutil.rmtree(chunks_dir, ignore_errors=True)
    chunks_dir.mkdir(parents=True)
    texts, parts = [], []
    for s in sources:
        texts.append(f"===== {s.path} =====\n" + "\n".join(read_lines(s.path, s.encoding)) + "\n")
        if s.lines:
            parts.append({"source": str(s.path), "start_line": 1, "end_line": s.lines})
    (chunks_dir / "0001.txt").write_text("\n".join(texts), encoding="utf-8")
    manifest = {"chunks": [{"id": "0001", "file": "0001.txt", "title": "all sources", "parts": parts}],
                "skipped": []}
    (chunks_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def load(chunks_dir: Path) -> list[dict]:
    """The chunks of a verified manifest, in reading order."""
    return json.loads((chunks_dir / "manifest.json").read_text(encoding="utf-8"))["chunks"]


def verify(chunks_dir: Path, sources: list[Source], chunk_size: int) -> list[str]:
    """Everything wrong with the manifest and chunk files; empty when the split is sound."""
    try:
        manifest = json.loads((chunks_dir / "manifest.json").read_text(encoding="utf-8"))
        return _check(manifest, chunks_dir, sources, chunk_size)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"manifest.json is missing or does not follow the contract: {exc!r}"]


def _check(manifest: dict, chunks_dir: Path, sources: list[Source], chunk_size: int) -> list[str]:
    chunks = manifest["chunks"]
    if not chunks:
        return ["the manifest lists no chunks"]
    ranges = [(Path(p["source"]).resolve(), int(p["start_line"]), int(p["end_line"]))
              for parts in [*(c["parts"] for c in chunks), manifest.get("skipped", [])] for p in parts]
    ids = [c["id"] for c in chunks]

    errors = []
    if len(set(ids)) != len(ids) or ids != sorted(ids):
        errors.append("chunk ids must be unique and in ascending reading order")
    by_path = {s.path.resolve(): s for s in sources}
    for path in sorted({r[0] for r in ranges} - set(by_path)):
        errors.append(f"{path} is not one of the sources")
    for path, source in by_path.items():
        errors += _coverage(source, sorted((start, end) for p, start, end in ranges if p == path))
    lines = {path: read_lines(s.path, s.encoding) for path, s in by_path.items()}
    for chunk in chunks:
        errors += _chunk_file(chunks_dir, chunk, lines, chunk_size)
    return errors


def _coverage(source: Source, ranges: list[tuple[int, int]]) -> list[str]:
    """Chunk parts and skipped ranges together must cover lines 1..N exactly once."""
    errors, expected = [], 1
    for start, end in ranges:
        if start > end:
            errors.append(f"{source.path}: range {start}-{end} is reversed")
        elif start > expected:
            errors.append(f"{source.path}: lines {expected}-{start - 1} are in no chunk and not listed as skipped")
        elif start < expected:
            errors.append(f"{source.path}: lines {start}-{min(end, expected - 1)} are covered more than once")
        expected = max(expected, end + 1)
    if expected <= source.lines:
        errors.append(f"{source.path}: lines {expected}-{source.lines} are in no chunk and not listed as skipped")
    elif expected > source.lines + 1:
        errors.append(f"{source.path}: ranges run past the last line ({source.lines})")
    return errors


def _chunk_file(chunks_dir: Path, chunk: dict, lines: dict[Path, list[str]], chunk_size: int) -> list[str]:
    """The chunk file must be readable, within the size limit, and hold the text of its source lines."""
    name = f"chunk {chunk['id']} ({chunk['file']})"
    try:
        text = (chunks_dir / chunk["file"]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{name}: cannot be read as UTF-8: {exc!r}"]
    errors = []
    if len(text) > chunk_size * HARD_LIMIT:
        errors.append(f"{name}: {len(text)} characters, over the limit of {int(chunk_size * HARD_LIMIT)}")
    claimed = [line for part in chunk["parts"]
               for line in lines.get(Path(part["source"]).resolve(), [])[part["start_line"] - 1:part["end_line"]]]
    if _ink(text) < sum(map(_ink, claimed)):
        errors.append(f"{name}: holds less text than the source lines it claims to contain")
    return errors


def _ink(text: str) -> int:
    """Characters that are not whitespace: a splitter may reflow text but must not drop it."""
    return len("".join(text.split()))
