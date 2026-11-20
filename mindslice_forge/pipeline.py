"""The forge pipeline: profile -> plan -> split -> extract (chunk by chunk) -> finalize.

Every stage has the same shape: an agent session does the work, a check decides whether it is
sound, and repair sessions fix what the check reports. A finished stage is committed in the
workspace and recorded in ``state.json``, so running the same command again continues from there.

With ``--into``, the build is for an existing workspace: it starts with read-only copies of what that
workspace holds, so that sessions find and link to it, and a complete build is then added to it.
"""

from __future__ import annotations

import filecmp
import json
import re
import secrets
import shlex
import shutil
import sys
import tempfile
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
REFERENCE_CHANGED = ("this file is part of the copy of the existing workspace, which the build must not change, "
                     "and the forge has undone the change. If it added knowledge, put that in a card of your own "
                     "slice instead, one that extends or links to the existing card")
OUTSIDE_CARDS = ("a step that writes cards changes only card files in the slices of the plan, and the forge has "
                 "restored this file. Put what the change was for into a card instead")
NEW_OUTSIDE = ("cards go only into the slices of the plan, as files: move what this file holds into a card file of the "
               "slice it belongs to, then delete this file")
ENGINE_WRITE = ("this card was written with `mindslice write` (or another engine command) and is not part of any slice: "
                "put what it holds into a card file of the slice it belongs to, then delete this file")
MAX_ISSUES_SHOWN = 50
PROBE_TIMEOUT = 300  # seconds: the harness check is a task of a few tool calls

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
    into: Path | None = None  # the existing workspace the result is for

    @property
    def reference(self) -> Path:
        """With ``into``, where the copies of its slices are kept and mounted."""
        return self.out / "reference"

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
        log(f"An agent session must reach:\n{reach(job)}")
        log(f"Harness: {shlex.join(job.harness) if job.harness else '(no --harness-cmd given)'}")
        if job.into:
            log(f"Adds the result to: {job.into}, which mounts "
                + ", ".join(m["alias"] for m in engine.mounts(job.into)))
        return

    engine.version()
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

    report = validate(job)
    if report["errors"]:
        raise ForgeError("the finished workspace does not validate:\n" + format_issues(report["errors"]))
    cards = sum(m.get("card_count", 0) for m in built(job).values())
    log(f"\nWorkspace ready: {job.out}")
    log(f"  {cards} cards, {len(state['chunks_done'])} of {len(chunks)} chunks processed, "
        f"{len(report['warnings'])} validation warning(s)")
    log(f"  Report: {job.work / 'report.md'}")
    log(f"  Per-chunk changes: git -C {job.out} log --stat")
    if job.into:
        connect(job, state, len(chunks))
    log(f"  Next: mindslice -w {job.into if state.get('connected') else job.out} doctor")


# ---------------------------------------------------------------------------
# Starting and resuming
# ---------------------------------------------------------------------------


def start(job: Job, sources: list[profile.Source]) -> dict:
    """Load the state of an earlier run, or create the workspace for a new one."""
    files = [s.path for s in sources] + profile.collect(job.dicts)
    fingerprint = progress.fingerprint(files, job.prompt, job.chunk_size)
    into = str(job.into) if job.into else None
    state = progress.load(job.state_file)
    if state is None:
        if job.from_stage:
            raise ForgeError(f"--from needs an earlier run, and {job.work} has none")
        if job.out.exists() and any(job.out.iterdir()):
            raise ForgeError(f"{job.out} already exists and was not created by the forge; choose another --output")
        job.work.mkdir(parents=True, exist_ok=True)
        engine.init(job.out)
        if job.into:
            copy_reference(job)
        checkpoint.init(job.out)
        checkpoint.commit(job.out, "empty workspace", tag="forge-init")
        state = progress.new(fingerprint, into)
    elif state.get("into") != into:
        started = f"for {state['into']}" if state.get("into") else "without --into"
        raise ForgeError(f"this build was started {started}; run it the same way, or choose another --output")
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


def copy_reference(job: Job) -> None:
    """--into: copy what the existing workspace holds into the new one, mounted as it is there, so that the
    build's sessions find, read and link to it without being able to change it. Taken once, at the start."""
    for mount in engine.mounts(job.into):
        source = (job.into / mount["path"]).resolve()
        if not source.is_dir():
            continue
        if mount.get("kind") == "overlay":  # cards not yet in a slice: searched and read, never linked to
            shutil.copytree(source, job.out / "overlay", dirs_exist_ok=True)
            continue
        copy = job.reference / (mount["alias"].removeprefix("@") + ".slice")
        shutil.copytree(source, copy)
        engine.mount(job.out, copy, mount["alias"], mount["precedence"], mount["role"])


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
                    split_file=job.work / "split.py", chunk_size=job.chunk_size, existing=existing(job))
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
    taken = {m["alias"] for m in engine.mounts(job.out)}  # the overlay, and with --into the existing slices
    for directory in slices:
        if not has_text(directory / "slice.toml"):
            issues.append(problem("slice.toml is missing", directory / "slice.toml"))
        elif (alias := engine.slice_alias(directory)) in taken:
            issues.append(problem(f"the alias {alias} is taken: {', '.join(sorted(taken))} are mounted already. "
                                  "Give this slice an alias of its own", directory / "slice.toml"))
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
                    handoff_file=handoff / f"{chunk['id']}.md", ledger_file=job.work / "ledger" / f"{chunk['id']}.md",
                    existing=existing(job))
    run_step(job, step, prompt, lambda: check_chunk(job, chunk), cards=True)
    checkpoint.commit(job.out, f"chunk {chunk['id']}: {title}")
    state["chunks_done"].append(chunk["id"])
    state["finalized"] = False
    progress.save(job.state_file, state)


def check_chunk(job: Job, chunk: dict) -> list[dict]:
    notes = [job.work / "ledger" / f"{chunk['id']}.md", job.work / "handoff" / f"{chunk['id']}.md"]
    issues = [problem(f"{path.parent.name}/{path.name} was not written", path) for path in notes if not has_text(path)]
    return issues + engine.issues(validate(job), checkpoint.changed(job.out))


def not_looked_up(job: Job, queries: list[str]) -> list[dict]:
    """Every new card in this step needs a successful lookup naming it."""
    slices = (job.out / "slices").resolve()
    overviews = {directory / "overview.md" for directory in slices.glob("*.slice")}
    new_cards = sorted(path for path in checkpoint.created(job.out)
                       if slices in path.parents and path.suffix == ".md" and path not in overviews)
    return [{"severity": "error", "code": "not_looked_up", "file": str(card),
             "message": "this card was created without looking up its title or an alias. Look it up now, with the "
                        f"command {lookup_command(job)}; if the workspace already has a card for the same thing, "
                        "merge the two into the one that existed first (a read-only card stays as it is: "
                        "then yours extends it and keeps only what is new)"}
            for card in new_cards if not lookup.covered(engine.card_names(card), queries)]


def finalize(job: Job, state: dict, total: int) -> None:
    log("[finalize] overviews, remaining problems, retrieval checks and the report")
    done = len(state["chunks_done"])
    coverage = f"{done} of {total} chunks" + ("" if done == total else " (a partial, sample build)")
    prompt = render("finalize", plan_file=job.work / "plan.md", user_prompt=job.prompt or NO_PROMPT,
                    out=job.out, lookup=lookup_command(job), slices_dir=job.out / "slices", ledger_dir=job.work / "ledger",
                    report_file=job.work / "report.md", coverage=coverage,
                    profile_file=job.work / "profile.md", manifest_file=job.chunks / "manifest.json",
                    issues=format_issues(engine.issues(validate(job))) or "(none)", existing=existing(job))
    run_step(job, "finalize", prompt, lambda: check_final(job), cards=True)
    checkpoint.commit(job.out, "finalize")
    state["finalized"] = True
    progress.save(job.state_file, state)


def check_final(job: Job) -> list[dict]:
    required = [d / "overview.md" for d in sorted((job.out / "slices").glob("*.slice"))] + [job.work / "report.md"]
    issues = [problem(f"{path.name} was not written", path) for path in required if not has_text(path)]
    return issues + engine.issues(validate(job))


def same_copy(source: Path, copy: Path) -> bool:
    """A reserved destination may have been filled or edited by someone else after interruption."""
    if copy.is_symlink() or not copy.is_dir():
        return False
    diff = filecmp.dircmp(source, copy, ignore=[])
    if diff.left_only or diff.right_only or diff.common_funny:
        return False
    return (all(not (copy / name).is_symlink() and filecmp.cmp(source / name, copy / name, shallow=False)
                for name in diff.common_files)
            and all(same_copy(source / name, copy / name) for name in diff.common_dirs))


def connect(job: Job, state: dict, total: int) -> None:
    """--into: copy the slices of a complete build into the existing workspace and mount them there.
    An alias that is mounted there already is never replaced."""
    if state.get("connected"):
        return
    if len(state["chunks_done"]) < total:
        log(f"  Not added to {job.into}: {len(state['chunks_done'])} of {total} chunks are processed. "
            "Run without --sample to finish the build, and it is added then.")
        return
    slices = built(job)
    mounted = {m["alias"]: (job.into / m["path"]).resolve() for m in engine.mounts(job.into)}
    destinations = state.setdefault("connecting", {})
    clash = sorted(a for a in slices if a in mounted and str(mounted[a]) != destinations.get(a))
    if clash:
        raise ForgeError(f"{', '.join(clash)} is mounted in {job.into} already, and the forge does not replace a mount. "
                         f"If it is an earlier version of this build, unmount it (`mindslice -w {job.into} unmount "
                         f"{clash[0]}`) and run the same command again.")
    (job.into / "slices").mkdir(exist_ok=True)
    copies = []
    for alias, mount in slices.items():
        source = (job.out / mount["path"]).resolve()
        if alias not in destinations:
            destinations[alias] = str(free_path(job.into / "slices", source.name))
            progress.save(job.state_file, state)
        copy = Path(destinations[alias])
        copies.append(copy)
        if copy.exists() or copy.is_symlink():
            if not same_copy(source, copy):
                raise ForgeError(f"{copy} changed since this build reserved it; inspect the destination before resuming")
        else:
            with tempfile.TemporaryDirectory(dir=copy.parent, prefix=".forge-copy-") as temporary:
                ready = Path(temporary) / "slice"
                shutil.copytree(source, ready)
                ready.rename(copy)
        if alias not in mounted:
            engine.mount(job.into, copy, alias=alias)
        log(f"  Added {alias} to {job.into}: {copy}")
    del state["connecting"]
    state["connected"] = True
    progress.save(job.state_file, state)
    report = engine.validate(job.into)
    problems = [issue for issue in report["errors"] + report["warnings"]
                if issue.get("address", "").split("/")[0] in slices
                or any(copy in Path(issue.get("file", "/")).parents for copy in copies)]
    if problems:
        log(f"  {len(problems)} problem(s) in {job.into} concern the new slices:\n{format_issues(problems)}")


# ---------------------------------------------------------------------------
# Sessions, checks and repairs
# ---------------------------------------------------------------------------


def run_step(job: Job, step: str, prompt: str, check: Check, *, cards: bool = False) -> None:
    """Run the step's agent session, retrying from a clean workspace if it fails, then settle its result.
    In a step that writes ``cards``, every new card needs a lookup, and only card files of the planned slices change."""
    live = checkpoint.live(job.out)
    for delay in (*harness.BACKOFF, None):
        earlier = len(lookup.queries(job.out)) if cards else 0
        if session(job, step, prompt):
            break
        checkpoint.record_failed_attempt(job.out)
        checkpoint.rollback(job.out)
        if delay is None:
            raise ForgeError(f"{step}: the agent session failed {len(harness.BACKOFF) + 1} times; "
                             f"see {job.work / 'logs' / (step + '.log')}")
        log(f"[{step}] the session failed; retrying in {delay}s")
        time.sleep(delay)

    def checked() -> list[dict]:
        issues = check() + reference_changes(job)
        if cards:
            issues += not_looked_up(job, lookup.queries(job.out)[earlier:]) + outside_cards(job, live)
        return issues

    settle(job, step, checked, task=step)


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
        checkpoint.record_failed_attempt(job.out)
        raise ForgeError(f"{step}: problems remain after {job.max_repairs} repair session(s):\n{format_issues(errors)}\n"
                         f"The failed attempt is left in place for inspection; "
                         f"running the same command again discards it and retries this step.")
    log(f"[{step}] {len(issues)} warning(s) left for the final report")


def run_harness(job: Job, cmd: list[str], log_file: Path, timeout: int) -> bool:
    """harness.run; after Ctrl-C the session's files are recorded as its own, so a rerun discards them."""
    try:
        return harness.run(cmd, job.work, log_file, timeout)
    except KeyboardInterrupt:
        checkpoint.record_failed_attempt(job.out)
        raise


def session(job: Job, name: str, prompt: str) -> bool:
    """One fresh agent session. The rendered prompt and the session log are kept in the forge directory."""
    check_harness(job)
    prompt_file = job.work / "prompts" / f"{name}.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")
    cmd = harness.command(job.harness, prompt_file, job.work)
    checkpoint.begin(job.out)
    head = checkpoint.head(job.out)
    finished = run_harness(job, cmd, job.work / "logs" / f"{name}.log", job.timeout)
    if checkpoint.head(job.out) != head:  # a commit would hide the session's work from the checks and the rollback
        checkpoint.uncommit(job.out, head)
        log(f"[{name}] the session committed in the workspace; the forge undid the commit and kept its changes")
    return finished


def outside_cards(job: Job, live: dict[Path, int]) -> list[dict]:
    """What a card step changed besides card files of the planned slices. A file of the last checkpoint is restored;
    a new file or a card written with the engine may hold the session's work, so the repair session moves it."""
    out = job.out.resolve()
    planned = [out / name for name in checkpoint.git(job.out, "ls-tree", "-z", "--name-only", "HEAD", "slices/").split("\0")
               if name]
    for directory in (out / "slices").glob("*.slice"):  # a slice of its own, emptied by a repair session
        if directory not in planned and not any(path.is_file() for path in directory.rglob("*")):
            shutil.rmtree(directory)
    new, issues = checkpoint.created(job.out), []
    for path in sorted(checkpoint.changed(job.out)):
        if (path.suffix == ".md" and any(d in path.parents for d in planned)) or job.reference.resolve() in path.parents:
            continue  # a card, or the copy of the existing workspace (see reference_changes)
        if path in new:
            issues.append(problem(NEW_OUTSIDE, path))
        else:
            checkpoint.restore(job.out, path.relative_to(out).as_posix())
            issues.append(problem(OUTSIDE_CARDS, path))
    current = checkpoint.live(job.out)
    return issues + [problem(ENGINE_WRITE, path) for path in sorted(live.keys() | current.keys())
                     if live.get(path) != current.get(path)]


def reference_changes(job: Job) -> list[dict]:
    """--into: the copies of the existing workspace are read-only. A change to them is undone and reported."""
    if not job.reference.is_dir():
        return []
    return [problem(REFERENCE_CHANGED, job.out / name) for name in checkpoint.restore(job.out, job.reference.name)]


def check_harness(job: Job) -> None:
    """Before the first session with a harness command, prove with a short one that it works.

    The forge does not know the harness, so a command that cannot do the work must fail here, in
    seconds and with the reason, not after a long step and its retries. The command that passed
    is recorded in the forge directory, and is not checked again."""
    record = job.work / "harness.json"
    if record.exists() and json.loads(record.read_text(encoding="utf-8")) == job.harness:
        return
    log("[harness] checking the harness command with one short session")
    engine_version, source = engine.version(), profile.scan(job.sources)[0][0].path
    token, answer_file = secrets.token_hex(4), job.out / "forge-probe.txt"
    prompt_file, log_file = job.work / "prompts" / "probe.md", job.work / "logs" / "probe.log"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(render("probe", source=source, probe_file=answer_file, token=token), encoding="utf-8")
    cmd = harness.command(job.harness, prompt_file, job.work)
    timeout = min(job.timeout, PROBE_TIMEOUT)
    checkpoint.begin(job.out)
    finished = run_harness(job, cmd, log_file, timeout)
    answer = answer_file.read_text(encoding="utf-8", errors="replace") if answer_file.is_file() else None
    if checkpoint.changed(job.out) <= {answer_file}:
        checkpoint.record_failed_attempt(job.out)
    checkpoint.rollback(job.out)

    problems = []
    if not finished:
        problems.append(f"the command did not end with exit status 0 within {timeout}s")
    if answer is None:
        problems.append(f"the session did not write {answer_file}: the agent must receive its task, use its tools "
                        "without asking for permission, and be able to write in the workspace")
    else:
        if token not in answer:
            problems.append(f"the session wrote {answer_file} without the code from its prompt")
        if engine_version not in answer:
            problems.append(f"`mindslice --version` did not print `{engine_version}` in the session: "
                            "the agent's shell needs the same `mindslice` on its PATH")
        if str(source.stat().st_size) not in answer.replace(",", ""):
            problems.append(f"the session did not find the size of {source}: the agent must be able to read the sources")
    if problems:
        tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        raise ForgeError("the harness command cannot run this build:\n" + "\n".join(f"- {p}" for p in problems)
                         + f"\nCommand: {shlex.join(cmd)}\nThe end of the session log, {log_file}:\n"
                         + "\n".join(f"  {line[:300]}" for line in tail)
                         + "\n`mindslice-forge --help` says what the command must do.")
    record.write_text(json.dumps(job.harness, ensure_ascii=False) + "\n", encoding="utf-8")
    log("[harness] the command works")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def render(name: str, **values: object) -> str:
    """Fill the ``{{placeholders}}`` of a prompt template; ``{{card_format}}`` is always available."""
    values.setdefault("card_format", (PROMPTS / "card-format.md").read_text(encoding="utf-8"))
    template = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda match: str(values[match.group(1)]), template)


def built(job: Job) -> dict[str, dict]:
    """The slices of this build that are mounted in its workspace, by alias."""
    slices = job.out / "slices"
    return {m["alias"]: m for m in engine.mounts(job.out) if slices in (job.out / m["path"]).resolve().parents}


def validate(job: Job) -> dict:
    """The engine's report on the slices this build makes. The other mounts (the overlay, and with --into the
    copies of the existing workspace) are not the build's to fix, so their issues are left out."""
    report = engine.validate(job.out)
    theirs = {m["alias"] for m in engine.mounts(job.out)} - set(built(job))

    def ours(issue: dict) -> bool:
        file = Path(issue.get("file") or job.out)
        return issue.get("address", "").split("/")[0] not in theirs and job.reference not in file.parents

    return {**report, "errors": list(filter(ours, report["errors"])), "warnings": list(filter(ours, report["warnings"]))}


def existing(job: Job) -> str:
    """The prompt section on the existing workspace of --into, or nothing."""
    if not job.into:
        return ""
    mounts = engine.mounts(job.out)
    slices = [m for m in mounts if job.reference in (job.out / m["path"]).resolve().parents]
    listing = "\n".join(f"- `{m['alias']}`, {m.get('card_count', '?')} cards: `{(job.out / m['path']).resolve()}`"
                        for m in slices)
    overlay = sum(m.get("card_count", 0) for m in mounts if m.get("kind") == "overlay")
    return render("existing", into=job.into, out=job.out, reference=listing or "(none: it holds no slices yet)",
                  overlay_cards=overlay, reference_dir=job.reference).strip()


def free_path(directory: Path, name: str) -> Path:
    """``directory/name``, or, as bake names versions, the first free ``<stem>-2.slice``, ``<stem>-3.slice``, ..."""
    stem, path, number = name.removesuffix(".slice"), directory / name, 1
    while path.exists():
        number += 1
        path = directory / f"{stem}-{number}.slice"
    return path


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


def reach(job: Job) -> str:
    """The directories a session of the harness command works in, for whoever composes that command."""
    read = dict.fromkeys(p if p.is_dir() else p.parent for p in [*job.sources, *job.dicts])
    return "\n".join([f"- `{job.work}` (its working directory; read and write)", f"- `{job.out}` (read and write)",
                      *(f"- `{directory}` (read)" for directory in read)])


def bullets(paths: list[Path]) -> str:
    return "\n".join(f"- `{p}`" for p in paths) or "(none)"


def has_text(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0
