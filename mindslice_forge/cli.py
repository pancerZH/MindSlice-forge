"""Command line: ``mindslice-forge SOURCE... [--dict PATH] [--prompt TEXT_OR_FILE] [-o DIR] [--sample N]``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import ForgeError, __version__, harness, pipeline
from .state import STAGES


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mindslice-forge",
        description="Turn documents into a MindSlice workspace by driving an agent harness. "
                    "Running the same command again continues an unfinished build.")
    p.add_argument("sources", nargs="+", type=Path, metavar="SOURCE", help="document or folder to convert")
    p.add_argument("--dict", dest="dicts", action="append", default=[], type=Path, metavar="PATH",
                   help="reference frame (outline, glossary, wiki page): shapes names and structure, "
                        "is never a source of facts; repeatable")
    p.add_argument("--prompt", default="", metavar="TEXT_OR_FILE",
                   help="conversion instructions, or a file that holds them")
    p.add_argument("-o", "--output", type=Path, metavar="DIR",
                   help="workspace to create (default: ./<first source name>.workspace)")
    p.add_argument("--sample", type=int, metavar="N", help="process only the first N chunks")
    p.add_argument("--chunk-size", type=int, default=100_000, metavar="CHARS",
                   help="target characters per chunk (default: %(default)s)")
    p.add_argument("--harness", choices=sorted(harness.PRESETS), default="agy",
                   help="agent harness preset (default: %(default)s)")
    p.add_argument("--harness-cmd", metavar="TEMPLATE",
                   help="custom harness command; placeholders {message}, {prompt_file}, {workdir}")
    p.add_argument("--model", help="model name passed to the harness preset")
    p.add_argument("--timeout", type=int, default=3600, metavar="SECONDS",
                   help="time limit of one agent session (default: %(default)s)")
    p.add_argument("--max-repairs", type=int, default=2, metavar="N",
                   help="repair sessions allowed per step (default: %(default)s)")
    p.add_argument("--from", dest="from_stage", choices=STAGES, metavar="STAGE",
                   help=f"redo the build from this stage on: {', '.join(STAGES)}")
    p.add_argument("--dry-run", action="store_true",
                   help="show the source inventory and what would run; call no agent, write nothing")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def read_prompt(value: str) -> str:
    """``--prompt`` is the instructions themselves, or the path of a file that holds them."""
    try:
        if value and "\n" not in value and Path(value).is_file():
            return Path(value).read_text(encoding="utf-8").strip()
    except OSError:  # too long to be a file name
        pass
    return value.strip()


def job_from(args: argparse.Namespace) -> pipeline.Job:
    if args.sample is not None and args.sample < 1:
        raise ForgeError("--sample must be at least 1")
    if args.chunk_size < 1:
        raise ForgeError("--chunk-size must be at least 1")
    sources = [p.resolve() for p in args.sources]
    dicts = [p.resolve() for p in args.dicts]
    first = sources[0]
    out = (args.output or Path(f"{first.name if first.is_dir() else first.stem}.workspace")).resolve()
    readable = list(dict.fromkeys([out, *(p if p.is_dir() else p.parent for p in [*sources, *dicts])]))
    return pipeline.Job(
        sources=sources, dicts=dicts, prompt=read_prompt(args.prompt), out=out,
        harness=harness.template(args.harness, args.harness_cmd, args.model, readable),
        sample=args.sample, chunk_size=args.chunk_size, timeout=args.timeout, max_repairs=args.max_repairs,
        from_stage=args.from_stage, dry_run=args.dry_run)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        pipeline.run(job_from(args))
    except ForgeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted; run the same command again to continue", file=sys.stderr)
        return 130
    return 0
