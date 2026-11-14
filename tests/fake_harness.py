"""A stand-in for an agent harness, used by the functional tests: ``fake_harness.py PROMPT_FILE``.

It does what a well-behaved agent would do for each forge step, deterministically, and misbehaves
on request through the ``FAKE_MODES`` environment variable (comma-separated):

    bad_card     chunk 0001 also writes a card with a broken ``extends`` (a validation error)
    warn_card    chunk 0001 also writes a card without a summary (a validation warning)
    never_fix    repair sessions do nothing
    split_gap    the first splitter leaves the last line of each source uncovered
    fail_once    the first session for chunk 0002 leaves a junk file and exits with status 1
    fail_always  every extract session exits with status 1
    no_lookup    extract sessions create cards without looking their names up first
    fail_after_lookup  chunk 0002 looks up a name then fails; its retry omits the lookup
    finalize_card     finalize creates a card without a lookup
    repair_new_card   the first extract repair creates another card without a lookup

Every session is recorded in ``<forge dir>/fake-calls.log``.
"""

import os
import subprocess
import sys
from pathlib import Path

PROMPT = Path(sys.argv[1])
WORK = PROMPT.parent.parent
OUT = Path(str(WORK).removesuffix(".forge"))
SLICE = OUT / "slices" / "demo.slice"
STEP = PROMPT.stem  # plan, extract-0002, finalize, split.repair1, extract-0001.repair1, ...
MODES = set(filter(None, os.environ.get("FAKE_MODES", "").split(",")))
CALLS = WORK / "fake-calls.log"
LOOKUP = Path(__file__).resolve().parent.parent / "mindslice_forge" / "lookup.py"

SPLITTER = '''
import argparse, json
from pathlib import Path

GAP = %(gap)s
p = argparse.ArgumentParser()
p.add_argument("--chunk-size", type=int)
p.add_argument("--out", type=Path)
p.add_argument("sources", nargs="+")
args = p.parse_args()
args.out.mkdir(parents=True, exist_ok=True)

chunks, skipped = [], []
parts, text, size = [], [], 0  # the chunk being filled


def close():
    global parts, text, size
    if parts:
        ident = "%%04d" %% (len(chunks) + 1)
        title = ", ".join("%%s:%%d-%%d" %% (Path(p["source"]).name, p["start_line"], p["end_line"]) for p in parts)
        (args.out / (ident + ".txt")).write_text("\\n".join(text) + "\\n", encoding="utf-8")
        chunks.append({"id": ident, "file": ident + ".txt", "title": title, "parts": parts})
    parts, text, size = [], [], 0


for source in args.sources:
    data = Path(source).read_bytes()
    try:
        lines = data.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        lines = data.decode("gb18030").splitlines()
    last = len(lines) - 1 if GAP else len(lines)
    first = 1
    if lines and lines[0].startswith("GENERATED"):
        skipped.append({"source": source, "start_line": 1, "end_line": len(lines), "reason": "generated file"})
        continue
    if lines and lines[0].startswith("AD:"):
        skipped.append({"source": source, "start_line": 1, "end_line": 1, "reason": "advertisement"})
        first = 2
    for number in range(first, last + 1):
        if not text or number == first:
            text.append("===== " + Path(source).name + " =====")
        text.append(lines[number - 1])
        size += len(lines[number - 1])
        if size >= args.chunk_size:  # a large file is cut; the next chunk continues it
            parts.append({"source": source, "start_line": first, "end_line": number})
            close()
            first = number + 1
    if first <= last:  # what is left of this file shares the chunk with the next file
        parts.append({"source": source, "start_line": first, "end_line": last})
close()
(args.out / "manifest.json").write_text(json.dumps({"chunks": chunks, "skipped": skipped}), encoding="utf-8")
'''


def look_up(name: str) -> None:
    subprocess.run([sys.executable, str(LOOKUP), str(OUT), name], stdout=subprocess.DEVNULL, check=True)


def card(path: str, title: str, summary: str, extends: str = "") -> None:
    file = SLICE / f"{path}.md"
    file.parent.mkdir(parents=True, exist_ok=True)
    lineage = f'extends = ["{extends}"]\n' if extends else ""
    file.write_text(f'+++\ntitle = "{title}"\n{lineage}+++\n\n{summary}\n\n- {summary}\n', encoding="utf-8")


def note(folder: str, ident: str) -> None:
    file = WORK / folder / f"{ident}.md"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f"{folder} of chunk {ident}\n", encoding="utf-8")


def plan() -> None:
    (WORK / "plan.md").write_text("# Plan\n\nOne slice, @demo.\n", encoding="utf-8")
    SLICE.mkdir(parents=True, exist_ok=True)
    (SLICE / "slice.toml").write_text(
        'name = "demo"\nversion = "0.1.0"\ndescription = "Demo."\nalias = "@demo"\ndependencies = []\n', encoding="utf-8")
    (WORK / "split.py").write_text(SPLITTER % {"gap": "split_gap" in MODES}, encoding="utf-8")


def extract(ident: str, earlier_calls: int) -> None:
    if "fail_after_lookup" in MODES and ident == "0002" and earlier_calls == 0:
        look_up("Hero")
        card("story/junk", "Hero", "Left by a failed attempt.")
        sys.exit(1)
    if "fail_always" in MODES or ("fail_once" in MODES and ident == "0002" and earlier_calls == 0):
        (SLICE / "junk.md").write_text("left behind by a failed session\n", encoding="utf-8")
        sys.exit(1)
    text = (WORK / "chunks" / f"{ident}.txt").read_text(encoding="utf-8")
    content = next(line for line in text.splitlines() if line.strip() and not line.startswith("====="))
    if "no_lookup" not in MODES and not ("fail_after_lookup" in MODES and ident == "0002"):
        for name in ("Hero", "Broken", "Bare"):
            look_up(name)
    if ident == "0001":
        card("story/hero", "Hero", "The hero of the story.")
        if "bad_card" in MODES:
            card("story/broken", "Broken", "A card with a broken lineage.", extends="story/nowhere")
        if "warn_card" in MODES:
            (SLICE / "story" / "bare.md").write_text('+++\ntitle = "Bare"\n+++\n\n- no summary\n', encoding="utf-8")
    card(f"story/hero-part-{ident}", f"Hero in part {ident}", content, extends="story/hero")
    note("ledger", ident)
    note("handoff", ident)


def repair(step: str) -> None:
    if "never_fix" in MODES:
        return
    if step == "split":
        (WORK / "split.py").write_text(SPLITTER % {"gap": False}, encoding="utf-8")
    if step.startswith("extract-"):
        look_up("Hero")
        if "repair_new_card" in MODES:
            if STEP.endswith("repair1"):
                card("story/repair-note", "Repair note", "A fact preserved during repair.")
            else:
                look_up("Repair note")
    if step == "extract-0001":
        (SLICE / "story" / "broken.md").unlink(missing_ok=True)
        (SLICE / "story" / "bare.md").unlink(missing_ok=True)
    if step == "finalize" and "finalize_card" in MODES:
        look_up("Glossary")


def finalize() -> None:
    if "finalize_card" in MODES:
        card("story/glossary", "Glossary", "Names used by the story.")
    (SLICE / "overview.md").write_text("# Demo\n\nStart with [[story/hero]].\n", encoding="utf-8")
    (WORK / "report.md").write_text("# Report\n", encoding="utf-8")


earlier = CALLS.read_text(encoding="utf-8").split().count(STEP) if CALLS.exists() else 0
with open(CALLS, "a", encoding="utf-8") as fh:
    fh.write(STEP + "\n")

if ".repair" in STEP:
    repair(STEP.split(".repair")[0])
elif STEP == "plan":
    plan()
elif STEP.startswith("extract-"):
    extract(STEP.removeprefix("extract-"), earlier)
elif STEP == "finalize":
    finalize()
