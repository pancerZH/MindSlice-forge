"""Source inventory: which text files there are, how they are encoded and how large they are.

Nothing here looks at the *structure* of a document; finding chapters and sections is the agent's job.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from pathlib import Path

from . import ForgeError


@dataclass
class Source:
    path: Path
    encoding: str
    lines: int
    chars: int


def collect(paths: list[Path]) -> list[Path]:
    """The files named by ``paths``: a file is itself, a directory is every non-hidden file below it."""
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files += sorted(p for p in path.rglob("*")
                            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(path).parts))
        else:
            raise ForgeError(f"no such file or directory: {path}")
    return list(dict.fromkeys(files))


def detect_encoding(data: bytes) -> str | None:
    """The encoding that decodes ``data``, or None when it is not text."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if b"\x00" in data[:8192]:
        return None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            data.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return None


def read_lines(path: Path, encoding: str) -> list[str]:
    """The file's lines without their terminators. Line N of a source is ``read_lines(...)[N - 1]``;
    this is what Python's ``open(path, encoding=encoding)`` iterates over."""
    text = path.read_bytes().decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def scan(paths: list[Path]) -> tuple[list[Source], list[Path]]:
    """Profile every file under ``paths``: (text sources, files skipped because they are not text)."""
    sources, skipped = [], []
    for path in collect(paths):
        encoding = detect_encoding(path.read_bytes())
        if encoding is None:
            skipped.append(path)
            continue
        lines = read_lines(path, encoding)
        sources.append(Source(path, encoding, len(lines), sum(map(len, lines))))
    return sources, skipped


def render(sources: list[Source], skipped: list[Path]) -> str:
    """The inventory as Markdown, for the agent and for the user."""
    out = ["| source | encoding | lines | characters |", "| --- | --- | ---: | ---: |"]
    out += [f"| `{s.path}` | {s.encoding} | {s.lines} | {s.chars} |" for s in sources]
    out.append(f"\nTotal: {len(sources)} file(s), {sum(s.chars for s in sources)} characters.")
    if skipped:
        out.append("\nNot text, left out of the build:")
        out += [f"- `{p}`" for p in skipped]
    return "\n".join(out) + "\n"
