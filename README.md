# mindslice-forge

Turn documents into a [MindSlice](https://github.com/pancerZH/MindSlice) workspace.

MindSlice is the memory engine. The forge is the tool that fills it: it takes one long document, a
folder of many files, or a mixture, drives the agent harness you choose through the conversion,
and checks every result with the `mindslice` command. Sources of any size work,
because they are read in chunks and each chunk is handled by a fresh agent session that continues
the work of the ones before it.

The forge itself is small on purpose. It keeps the inventory, the state, the checkpoints and the
checks; everything that needs understanding of the text is done by the agent, following the
templates in [`mindslice_forge/prompts/`](https://github.com/pancerZH/MindSlice-forge/tree/main/mindslice_forge/prompts).

Python 3.12+, standard library only. Needs `mindslice` 0.1.0 or newer, `git`, and an agent harness
that can run a session unattended from the command line.

## When to use Forge

Use Forge when you want an agent to distill source documents into topic cards. MindSlice itself
handles the other starting points without Forge:

- `mindslice init <workspace>` creates empty memory for learning through conversation.
- `mindslice -w <workspace> ...` opens an existing workspace directly.
- `mindslice -w <workspace> mount <directory>` mounts a `.slice` or a plain Markdown notes folder
  read-only, without LLM conversion.

MindSlice's [setup skill](https://github.com/pancerZH/MindSlice/blob/main/skills/mindslice-setup/SKILL.md)
guides onboarding; its [builder skill](https://github.com/pancerZH/MindSlice/blob/main/skills/slice-builder/SKILL.md)
uses Forge for conversion and connects the result. Forge is optional for MindSlice users; the
engine does not call an LLM or require Forge as a runtime dependency.

## Install

```bash
pipx install mindslice-forge   # or: uv tool install mindslice-forge
```

Install the MindSlice engine the same way (`pipx install mindslice`): Forge and the agent sessions
it starts call the `mindslice` command by name, so both must be on `PATH`. Make sure the harness
you will use is authenticated and its model is available.

*(For local development from source: `git clone https://github.com/pancerZH/MindSlice-forge.git && cd MindSlice-forge && pipx install -e .`.)*

## Use

```bash
HARNESS='...'   # the command that runs one agent session: see "The harness command" below
mindslice-forge novel.txt \
    --harness-cmd "$HARNESS" \
    --dict wiki-summary.txt \
    --prompt "Extract the world and its background for solo role-play. Record how a thing changes over time as several cards that extend one another, not as one crowded card." \
    --sample 2 \
    -o ./novel.workspace
```

| Argument | Meaning |
| --- | --- |
| `SOURCE...` | Documents or folders to convert. Text files in UTF-8, GB18030 or UTF-16; other files are listed and left out. |
| `--dict PATH` | A reference frame: an outline, glossary or encyclopedia page about the sources. It shapes names and structure and is never a source of facts. Repeatable. |
| `--prompt TEXT_OR_FILE` | What to extract and how. |
| `-o, --output DIR` | The workspace to create. Default: `./<first source name>.workspace`. |
| `--into WORKSPACE` | An existing MindSlice workspace to add the result to; see [Add to an existing workspace](#add-to-an-existing-workspace). |
| `--sample N` | Process only the first N chunks, to try a prompt before a long run. |
| `--chunk-size CHARS` | Target size of a chunk. Default 100000 characters. |
| `--harness-cmd COMMAND` | The command line that runs one agent session; see [The harness command](#the-harness-command). Needed for everything except `--dry-run`. |
| `--timeout SECONDS` | Time limit of one agent session. Default 3600. A session that runs longer is rolled back and retried. |
| `--max-repairs N` | Repair sessions allowed per step. Default 2. |
| `--from STAGE` | Redo the build from `plan`, `split`, `extract` or `finalize`. |
| `--dry-run` | Show the inventory, the directories a session must reach and what would run. Calls no agent and writes nothing. |

```bash
mindslice-forge ./project-docs ./adr --harness-cmd "$HARNESS" \
    --prompt "Rules, architecture and operating procedures for new engineers."
```

**Running the same command again continues an unfinished build.** A typical session:

```bash
mindslice-forge novel.txt --harness-cmd "$HARNESS" --prompt prompt.md --sample 2   # try two chunks
git -C novel.workspace log -p                             # see what each chunk produced
# edit prompt.md
mindslice-forge novel.txt --harness-cmd "$HARNESS" --prompt prompt.md --sample 2 --from extract   # extract again
mindslice-forge novel.txt --harness-cmd "$HARNESS" --prompt prompt.md              # satisfied: run the rest
```

If the sources, the dict, the prompt or the chunk size change after a build has started, the forge
refuses to continue silently: say which stage to redo with `--from`, or choose another output.

## The harness command

Forge knows no agent product. You give it the command line that runs **one agent session** of
the harness you use, with its model and options, and Forge runs that command once per step: from
the forge directory, with no terminal attached, after filling in the placeholders `{message}`
(one line telling the agent to read the prompt file and do what it says), `{prompt_file}` and
`{workdir}`. Do not quote the placeholders, and keep the command on one line: inside quotes, a
shell does not join a line that ends in a backslash with the next one. The command must:

- contain `{message}` or `{prompt_file}`, so that the agent receives its task;
- name files and directories by absolute paths, as it does not run from your directory;
- end when the task is done, with exit status 0 on success;
- never wait for an answer: no questions, and no permission prompts for reading and writing
  files or running shell commands;
- let the agent write in the workspace and the forge directory and read the sources and every
  `--dict` (`--dry-run` lists these directories);
- give the agent's shell a `PATH` with the `mindslice` command.

`mindslice-forge --help` prints the same list. Take the options from your harness's own help:
look for its non-interactive mode (often called print, exec or headless), the option that
approves tool use without asking, the option for the model, and, if it limits file access, the
option that grants directories. As an illustration only, this is such a command for Antigravity's
`agy`; other harnesses, and other versions of this one, have their own options:

```bash
HARNESS='agy --print={message} --dangerously-skip-permissions --disable-slash-commands --output-format stream-json --model <model> --add-dir /abs/novel.workspace --add-dir /abs/sources'
```

**A new command is checked before it is used.** Before the first step with a command, one short
session has to run `mindslice --version`, read a source file and write a file in the workspace.
If it cannot, the build stops there, within seconds or minutes, and says which requirement
failed and how the session's log ends. The command that passed is recorded in the forge
directory; changing the command, for example to another model, checks it again.

## One document or many

Files and chunks are units of reading, nothing more. They decide neither the slices nor the cards.

- **A folder** is read recursively. Hidden files and folders are ignored; files that are not text
  (images, PDF, Word) are listed in the inventory and left out, so convert those to text first.
- **Small files** are kept whole and share a chunk; **a large file** is cut at its own chapters or
  sections. The agent decides the grouping and the reading order when it plans: related files
  together, what the rest builds on first. It can leave out a whole file, such as generated
  output or a copy, and must give the reason.
- **Everything is accounted for.** Every line of every text file ends up in exactly one chunk or
  in a skipped range with a reason; the forge checks this before any card is written.
- **Cards follow topics.** Several files can feed one card and one file can feed many. Where files
  disagree, the later one does not win by being read later: the cards record what each statement
  applies to, and unresolved disagreements are listed in the report.
- If everything fits in one chunk, no splitting happens and one session reads it all.

## How it works

| Stage | Who | What happens |
| --- | --- | --- |
| profile | forge | Lists the sources with their encoding, line count and size. |
| plan | agent, one session | Studies the structure of the documents and how the files relate, reads the dict, writes the build plan (slices, folders, card paths, reading order, how change over time is recorded), creates the slices, and writes a splitter program if the sources do not fit in one chunk. |
| split | forge | Runs the splitter and checks that every source line is in exactly one chunk or in a declared skipped range, and that no chunk is too large or has lost text. Problems go back to the agent. |
| extract | agent, one session per chunk, in order | Is told which files and lines the chunk holds, reads it in full, searches the workspace for what already exists, writes or extends cards, and leaves a ledger and a note for the next session. |
| finalize | agent, one session | Writes each slice's overview, clears remaining problems, tries retrieval questions, and writes the report. |

After every agent session the forge runs `mindslice validate`. Errors, and fixable warnings on the
cards that session touched, go to a repair session (at most `--max-repairs`). Errors that survive
stop the build; warnings do not, and are listed in the report. A session that fails or times out
is rolled back and retried.

**Search before creating cards is a required check.** The extract and finalize prompts provide a
`python /path/to/mindslice_forge/lookup.py WORKSPACE "name"` command. It searches the workspace
through `mindslice search --json` and records successful searches in the forge directory's
`lookups.jsonl`. Search each name separately. Every new card must have a title or alias covered
by a lookup from that step or its repairs: the whole query occurs in the title/alias or vice
versa, ignoring case and respecting Latin word boundaries. A base name can cover period cards.
Older steps and failed attempts do not count; edits to existing cards and slice overviews are
exempt. Missing lookups are errors that stop the build if repairs cannot resolve them.

Repairs must search the missing names, read matches, and merge duplicate cards into the one that
already existed, preserving facts and updating links. This check verifies search coverage at the
end of the step; deciding whether two cards describe the same thing remains the agent's job.

## Output

```text
novel.workspace/            a MindSlice workspace, ready for `mindslice -w novel.workspace ...`
├── mounts.toml, overlay/, staging/
├── slices/<name>.slice/    the cards, mounted
├── reference/              with --into: the read-only copy of the existing workspace's slices
└── .git/                   one commit per finished chunk: the checkpoints (slices and mounts only)
novel.workspace.forge/      build state and evidence; keep it for resume and review
├── state.json, harness.json  progress, and the harness command that passed its check
├── profile.md, plan.md, split.py, report.md
├── chunks/                 the chunk files and manifest.json
├── ledger/, handoff/       per chunk: what was recorded, and the note for the next session
├── lookups.jsonl           successful name searches
└── prompts/, logs/         every prompt as it was sent, and each session's output
```

The forge directory lives next to the workspace so that workspace backups do not carry a copy of
the sources.

## Connect the result to MindSlice

**For a new workspace**, pass the intended workspace path to Forge with `-o`. Forge initializes
it and mounts the generated slices; do not run `mindslice init` there first. After checking the
report, validation and representative facts, use `mindslice -w <output> ...` or connect your
agent's MCP server to that workspace.

A connected workspace can still be built further: run the same command to continue a sample, or
use `--from` to redo a stage. What the agent has learned in the meantime (`overlay/`, `staging/`
and the engine's `.history/`) is kept out of the checkpoints, so no rerun, retry or rollback
touches it. If the slices or the mount table were changed outside the forge, by `mindslice bake`,
`mount` or `unmount` or by hand, the forge stops and lists the changes instead of discarding
them; build into another output and mount its slices. Do not bake or change mounts while a build
is running.

**For an existing workspace**, use `--into`, described in the next section. To do it by hand
instead, build into a separate new directory (Forge cannot take over a nonempty workspace that it
did not create), inspect the resulting `slices/*.slice` aliases and dependencies, then mount the
selected individual slices. For example, if the build produced `handbook.slice` and its alias is
available in your destination:

```bash
mindslice -w ~/mindslice mount /path/to/build.workspace/slices/handbook.slice \
  --precedence 50 --role curated
mindslice -w ~/mindslice validate
```

Mount each required slice separately, preserving aliases used by cross-slice links. Resolve
collisions before mounting; do not silently replace an existing mount. The workspace root,
`slices/` parent and `.forge/` build directory are not slice mount targets.

Keep mounted paths stable. If you plan further Forge rebuilds, first copy the selected slices to
new durable library directories or the destination's `slices/` directory and mount those copies.
Otherwise a rebuild or rollback can change the files the live workspace reads. Keep the sibling
`.forge/` directory for resume and evidence; completed slice artifacts do not depend on it at runtime.

Validation checks structure, and recorded lookups check search coverage. Neither establishes that
the model preserved every fact. Treat input coverage, retrieval and source fidelity as separate
checks; a sampled build remains partial after it is connected to an agent.

## Add to an existing workspace

```bash
mindslice-forge paper.md notes/ --into ~/mindslice --harness-cmd "$HARNESS" \
    --prompt "Methods, results and open questions of the paper, for later research sessions."
```

With `--into`, the result is new slices for a workspace that already holds knowledge, such as the
memory your agent uses every day. The build still happens in its own output (`-o`, by default
`./<first source name>.workspace`), with the checkpoints, resume and rollback described above;
the existing workspace is changed once, at the end.

- **The build sees what is there.** When it starts, the forge copies the existing workspace's
  slices into `<output>/reference/` and mounts them under the same aliases, and copies its
  overlay. Searches, reads and the lookup check cover them, so sessions write only what the
  sources add: a card that `extends` an existing one, or a link `[[@alias/path]]` to it, and
  `dependencies` in `slice.toml` for the slices linked to. The copy is taken once; later changes
  to the existing workspace are not seen by this build.
- **The copy is read-only.** A session that changes a file under `reference/` has the change
  undone and is sent back to put the knowledge in its own slice. Issues the engine reports on the
  copy are not the build's to fix and do not stop it.
- **New aliases.** The plan must give each new slice an alias the existing workspace does not
  use; a clash is sent back for repair.
- **Added when complete.** After the last chunk and the finalize step, the forge copies each new
  slice into the existing workspace's `slices/` (as `<name>.slice`, or `<name>-2.slice` and so
  on if that name is taken), mounts it there with precedence 50 and role `curated`, leaving the
  overlay as it is, and reports the issues `mindslice validate` finds on the new slices. A
  `--sample` build that has not reached the last chunk is not added; run without `--sample` to
  finish it.
- **Nothing is replaced.** Running the same command again after the result was added does
  nothing. After a rebuild (`--from`), the forge stops if the alias is still mounted in the
  existing workspace and says how to unmount the earlier version; run the same command again
  then.
- A build started with `--into` continues only with the same `--into`, and one started without
  it only without.

## Safety

- Select allowed sources before invoking Forge, including `--dry-run`: recursive scanning does
  not honor project ignore files or forbidden-directory markers. Pass selected files or prepare
  an allowed snapshot with exclusions applied before traversal. Keep output, reference files and
  conversion prompts outside the source directories.
- The harness command you give runs agents with tool permissions approved automatically, and the
  splitter is a program the agent wrote. Convert sources you trust, or run the forge in a sandbox.
- A build session is an ordinary session of your harness and loads what the harness is configured
  with: MCP servers, standing rules, hooks. If your MindSlice memory is connected there, every
  build session starts that workspace's MCP server as well and could write to it. The build needs
  none of it: it works through files and the `mindslice` command in its own output. To keep your
  memory out of a build's reach, add to the harness command the options that leave that
  configuration out, taken from your harness's own help.
- With `--into`, the existing workspace's slices and overlay are copied into the build, and every
  build session can read them: all of that memory reaches the harness and its model. Use a
  workspace whose whole content you are willing to share with them.
- Forge writes its output workspace and sibling `.forge/` directory, and with `--into` it adds
  the finished slices to that workspace. It refuses to take over an
  existing nonempty output it did not create. Rollbacks happen inside its output directory and
  cover the slices and the mount table only, never `overlay/`, `staging/` or `.history/`.

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

The unit tests need nothing. The functional tests run whole builds with the real `mindslice` and
`git` commands and a fake agent ([`tests/fake_harness.py`](https://github.com/pancerZH/MindSlice-forge/blob/main/tests/fake_harness.py)); no model is
called.

### Checking a first real run

After a `--sample 2` run against a real harness, look at:

- the `[split]` line: the chunk count and the first and last titles should match the document;
  `novel.workspace.forge/chunks/manifest.json` lists what was skipped and why;
- `git -C novel.workspace log --stat`: one commit per chunk, with the cards it touched;
- `mindslice -w novel.workspace validate`, and a few searches and reads of things you know are in
  the first chunks, including one that changes over time;
- `novel.workspace.forge/report.md`.
