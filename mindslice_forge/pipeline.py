"""The forge pipeline: profile -> plan -> split -> extract (chunk by chunk) -> finalize.

Every stage has the same shape: an agent session does the work, a check decides whether it is
sound, and repair sessions fix what the check reports. A finished stage is committed in the
workspace and recorded in ``state.json``, so running the same command again continues from there.
"""

from __future__ import annotations

import re
import shlex
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import ForgeError, checkpoint, engine, harness, lookup, profile, split
from . import state as progress

PROMPTS = Path(__file__).parent / "prompts"
NO_PROMPT = "(none given: build a faithful, general-purpose knowledge base of the sources)"
SPLIT_NEEDED = "The sources are too large to read in one session, so you must also write the splitter."
SPLIT_NOT_NEEDED = "The sources fit in one session and will be read as a single chunk: do NOT write a splitter."
MAX_ISSUES_SHOWN = 50

Check = Callable[[], list[dict]]


@dataclass
class Job:
    sources: list[Path]
    dicts: list[Path]
    prompt: str
    out: Path
    harness: list[str]  # command template, see harness.template()
    sample: int | None = None
    chunk_size: int = 100_000
    timeout: int = 3600
    max_repairs: int = 2
    from_stage: str | None = None
    dry_run: bool = False

    @property
    def work(self) -> Path:
        return lookup.forge_dir(self.out)

    @property
    def chunks(self) -> Path:
        return self.work / "chunks"

    @property
    def state_file(self) -> Path:
        return self.work / "state.json"


def log(message: str) -> None:
    print(message, flush=True)


def run(job: Job) -> None:
    sources, not_text = profile.scan(job.sources)
    if not sources:
        raise ForgeError("no text files found in the sources")
    inventory = profile.render(sources, not_text)
    needs_split = sum(s.chars for s in sources) > job.chunk_size
    if job.dry_run:
        log(inventory)
        log(f"Reference frame:\n{bullets(job.dicts)}\n")
        log(f"Workspace: {job.out}\nForge directory: {job.work}")
        log(f"Splitting: {'needed' if needs_split else 'not needed'} at {job.chunk_size} characters per chunk")
        log(f"Harness: {' '.join(job.harness)}")
        return

    state = start(job, sources)
    (job.work / "profile.md").write_text(inventory, encoding="utf-8")
    if not state["plan"]:
        plan(job, state, inventory, needs_split)
    if not state["split"]:
        split_sources(job, state, sources, needs_split)

    chunks = split.load(job.chunks)
    selected = chunks[:job.sample] if job.sample else chunks
    for index, chunk in enumerate(selected):
        if chunk["id"] not in state["chunks_done"]:
            previous = selected[index - 1]["id"] if index else None
            extract(job, state, chunk, f"{index + 1}/{len(chunks)}", previous)
    if not state["finalized"]:
        finalize(job, state, len(chunks))

    report = engine.validate(job.out)
    if report["errors"]:
        raise ForgeError("the finished workspace does not validate:\n" + format_issues(report["errors"]))
    log(f"\nWorkspace ready: {job.out}")
    log(f"  {report['stats']['cards']} cards, {len(state['chunks_done'])} of {len(chunks)} chunks processed, "
        f"{len(report['warnings'])} validation warning(s)")
    log(f"  Report: {job.work / 'report.md'}")
    log(f"  Per-chunk changes: git -C {job.out} log --stat")
    log(f"  Next: mindslice -w {job.out} doctor")


# ---------------------------------------------------------------------------
# Starting and resuming
# ---------------------------------------------------------------------------


def start(job: Job, sources: list[profile.Source]) -> dict:
    """Load the state of an earlier run, or create the workspace for a new one."""
    files = [s.path for s in sources] + profile.collect(job.dicts)
    fingerprint = progress.fingerprint(files, job.prompt, job.chunk_size)
    state = progress.load(job.state_file)
    if state is None:
        if job.from_stage:
            raise ForgeError(f"--from needs an earlier run, and {job.work} has none")
        if job.out.exists() and any(job.out.iterdir()):
            raise ForgeError(f"{job.out} already exists and was not created by the forge; choose another --output")
        job.work.mkdir(parents=True, exist_ok=True)
        engine.init(job.out)
        checkpoint.init(job.out)
        checkpoint.commit(job.out, "empty workspace", tag="forge-init")
        state = progress.new(fingerprint)
    elif job.from_stage:
        rewind(job, state)
        state["fingerprint"] = fingerprint
    elif state["fingerprint"] != fingerprint:
        raise ForgeError("the sources, dict, prompt or chunk size changed since this output was started.\n"
                         "Redo the affected stages with --from plan|split|extract, or choose another --output.")
    else:
        checkpoint.rollback(job.out)  # drop whatever an interrupted session left behind
    progress.save(job.state_file, state)
    return state


def rewind(job: Job, state: dict) -> None:
    """--from STAGE: return the workspace and the forge directory to how they were before that stage."""
    stage = job.from_stage if state["plan"] else "plan"  # nothing exists yet after an unfinished plan
    progress.rewind(state, stage)
    if stage == "finalize":
        checkpoint.rollback(job.out)
        return
    checkpoint.rollback(job.out, "forge-init" if stage == "plan" else "forge-plan")
    stale = {"plan": ("plan.md", "split.py", "chunks", "ledger", "handoff", "lookups.jsonl"),
             "split": ("chunks", "ledger", "handoff", "lookups.jsonl"),
             "extract": ("ledger", "handoff", "lookups.jsonl")}[stage]
    for name in stale:
        path = job.work / name
        shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def plan(job: Job, state: dict, inventory: str, needs_split: bool) -> None:
    log("[plan] the agent studies the sources and writes the build plan")
    prompt = render("plan", profile=inventory, dicts=bullets(job.dicts), user_prompt=job.prompt or NO_PROMPT,
                    plan_file=job.work / "plan.md", slices_dir=job.out / "slices",
                    split_note=SPLIT_NEEDED if needs_split else SPLIT_NOT_NEEDED,
                    split_file=job.work / "split.py", chunk_size=job.chunk_size)
    run_step(job, "plan", prompt, lambda: check_plan(job, needs_split))
    aliases = engine.mount_slices(job.out)
    checkpoint.commit(job.out, "plan", tag="forge-plan")
    state["plan"] = True
    progress.save(job.state_file, state)
    log(f"[plan] done; slices: {', '.join(aliases)}")


def check_plan(job: Job, needs_split: bool) -> list[dict]:
    issues = []
    if not has_text(job.work / "plan.md"):
        issues.append(problem("plan.md was not written", job.work / "plan.md"))
    if needs_split and not has_text(job.work / "split.py"):
        issues.append(problem("split.py was not written", job.work / "split.py"))
    slices = sorted((job.out / "slices").glob("*.slice"))
    if not slices:
        issues.append(problem("no <name>.slice directory was created", job.out / "slices"))
    for directory in slices:
        if not has_text(directory / "slice.toml"):
            issues.append(problem("slice.toml is missing", directory / "slice.toml"))
        issues += engine.issues(engine.validate(job.out, directory))
    return issues


def split_sources(job: Job, state: dict, sources: list[profile.Source], needs_split: bool) -> None:
    if needs_split:
        log("[split] running the agent-written splitter")
        settle(job, "split", lambda: check_split(job, sources), task="plan")
    else:
        split.single_chunk(job.chunks, sources)
    chunks = split.load(job.chunks)
    state["split"] = True
    progress.save(job.state_file, state)
    log(f"[split] {len(chunks)} chunk(s): {chunks[0].get('title', '')} ... {chunks[-1].get('title', '')}")


def check_split(job: Job, sources: list[profile.Source]) -> list[dict]:
    script = job.work / "split.py"
    failure = split.run_splitter(script, job.chunks, sources, job.chunk_size)
    errors = [failure] if failure else split.verify(job.chunks, sources, job.chunk_size)
    return [problem(error, script) for error in errors]


def extract(job: Job, state: dict, chunk: dict, position: str, previous: str | None) -> None:
    step, title = f"extract-{chunk['id']}", chunk.get("title", "")
    log(f"[{step}] chunk {position}: {title}")
    handoff = job.work / "handoff"
    prompt = render("extract", plan_file=job.work / "plan.md", user_prompt=job.prompt or NO_PROMPT,
                    dicts=bullets(job.dicts), chunk_file=job.chunks / chunk["file"], chunk_title=title,
                    chunk_parts=parts_list(chunk), lookup=lookup_command(job),
                    position=position, out=job.out, slices_dir=job.out / "slices",
                    previous_handoff=handoff / f"{previous}.md" if previous else "(none: this is the first chunk)",
                    handoff_file=handoff / f"{chunk['id']}.md", ledger_file=job.work / "ledger" / f"{chunk['id']}.md")
    earlier = len(lookup.queries(job.out))  # only lookups made for this chunk count for it
    run_step(job, step, prompt, lambda: check_chunk(job, chunk, earlier))
    checkpoint.commit(job.out, f"chunk {chunk['id']}: {title}")
    state["chunks_done"].append(chunk["id"])
    state["finalized"] = False
    progress.save(job.state_file, state)


def check_chunk(job: Job, chunk: dict, earlier_lookups: int) -> list[dict]:
    notes = [job.work / "ledger" / f"{chunk['id']}.md", job.work / "handoff" / f"{chunk['id']}.md"]
    issues = [problem(f"{path.parent.name}/{path.name} was not written", path) for path in notes if not has_text(path)]
    issues += not_looked_up(job, lookup.queries(job.out)[earlier_lookups:])
    return issues + engine.issues(engine.validate(job.out), checkpoint.changed(job.out))


def not_looked_up(job: Job, queries: list[str]) -> list[dict]:
    """Look before you write, enforced: every card this chunk created must have had its name looked up."""
    slices = (job.out / "slices").resolve()
    new_cards = sorted(path for path in checkpoint.created(job.out)
                       if slices in path.parents and path.suffix == ".md" and path.name != "overview.md")
    return [{"severity": "error", "code": "not_looked_up", "file": str(card),
             "message": "this card was created without looking up its title or an alias. Look it up now, with the "
                        "lookup command of the task; if the workspace already has a card for the same thing, "
                        "merge the two into the one that existed first"}
            for card in new_cards if not lookup.covered(engine.card_names(card), queries)]


def finalize(job: Job, state: dict, total: int) -> None:
    log("[finalize] overviews, remaining problems, retrieval checks and the report")
    done = len(state["chunks_done"])
    coverage = f"{done} of {total} chunks" + ("" if done == total else " (a partial, sample build)")
    prompt = render("finalize", plan_file=job.work / "plan.md", user_prompt=job.prompt or NO_PROMPT,
                    out=job.out, slices_dir=job.out / "slices", ledger_dir=job.work / "ledger",
                    report_file=job.work / "report.md", coverage=coverage,
                    profile_file=job.work / "profile.md", manifest_file=job.chunks / "manifest.json",
                    issues=format_issues(engine.issues(engine.validate(job.out))) or "(none)")
    run_step(job, "finalize", prompt, lambda: check_final(job))
    checkpoint.commit(job.out, "finalize")
    state["finalized"] = True
    progress.save(job.state_file, state)


def check_final(job: Job) -> list[dict]:
    required = [d / "overview.md" for d in sorted((job.out / "slices").glob("*.slice"))] + [job.work / "report.md"]
    issues = [problem(f"{path.name} was not written", path) for path in required if not has_text(path)]
    return issues + engine.issues(engine.validate(job.out))


# ---------------------------------------------------------------------------
# Sessions, checks and repairs
# ---------------------------------------------------------------------------


def run_step(job: Job, step: str, prompt: str, check: Check) -> None:
    """Run the step's agent session, retrying from a clean workspace if it fails, then settle its result."""
    for delay in (*harness.BACKOFF, None):
        if session(job, step, prompt):
            break
        checkpoint.rollback(job.out)
        if delay is None:
            raise ForgeError(f"{step}: the agent session failed {len(harness.BACKOFF) + 1} times; "
                             f"see {job.work / 'logs' / (step + '.log')}")
        log(f"[{step}] the session failed; retrying in {delay}s")
        time.sleep(delay)
    settle(job, step, check, task=step)


def settle(job: Job, step: str, check: Check, task: str) -> None:
    """Run repair sessions until ``check`` is satisfied. Errors left after the last repair are fatal;
    warnings are not, and end up in the final report. ``task`` names the prompt the work was done under."""
    for attempt in range(job.max_repairs + 1):
        issues = check()
        if not issues:
            return
        if attempt < job.max_repairs:
            log(f"[{step}] {len(issues)} problem(s) found; repair session {attempt + 1}")
            session(job, f"{step}.repair{attempt + 1}",
                    render("repair", task_file=job.work / "prompts" / f"{task}.md", issues=format_issues(issues)))
    errors = [issue for issue in issues if issue["severity"] == "error"]
    if errors:
        raise ForgeError(f"{step}: problems remain after {job.max_repairs} repair session(s):\n{format_issues(errors)}\n"
                         f"The failed attempt is left in place for inspection; "
                         f"running the same command again discards it and retries this step.")
    log(f"[{step}] {len(issues)} warning(s) left for the final report")


def session(job: Job, name: str, prompt: str) -> bool:
    """One fresh agent session. The rendered prompt and the session log are kept in the forge directory."""
    prompt_file = job.work / "prompts" / f"{name}.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")
    cmd = harness.command(job.harness, prompt_file, job.work)
    return harness.run(cmd, job.work, job.work / "logs" / f"{name}.log", job.timeout)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def render(name: str, **values: object) -> str:
    """Fill the ``{{placeholders}}`` of a prompt template; ``{{card_format}}`` is always available."""
    values.setdefault("card_format", (PROMPTS / "card-format.md").read_text(encoding="utf-8"))
    template = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda match: str(values[match.group(1)]), template)


def problem(message: str, file: Path) -> dict:
    """A forge-detected error, in the shape of an engine validation issue."""
    return {"severity": "error", "code": "forge", "message": message, "file": str(file)}


def format_issues(issues: list[dict]) -> str:
    lines = [f"- {i['severity']} [{i['code']}] {i.get('file') or i.get('address', '')}: {i['message']}"
             for i in issues[:MAX_ISSUES_SHOWN]]
    if len(issues) > MAX_ISSUES_SHOWN:
        lines.append(f"- ... and {len(issues) - MAX_ISSUES_SHOWN} more of the same kinds")
    return "\n".join(lines)


def lookup_command(job: Job) -> str:
    """The command line an agent uses to search the workspace, with the search recorded."""
    return shlex.join([sys.executable, lookup.__file__, str(job.out)])


def parts_list(chunk: dict) -> str:
    """Which source lines a chunk holds: a chunk can span several files, and a file several chunks."""
    return "\n".join(f"- `{p['source']}`, lines {p['start_line']}-{p['end_line']}" for p in chunk["parts"]) or "(none)"


def bullets(paths: list[Path]) -> str:
    return "\n".join(f"- `{p}`" for p in paths) or "(none)"


def has_text(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0
