# mindslice-forge

Turn documents into a [MindSlice](https://github.com/pancerZH/MindSlice) workspace.

MindSlice is the memory engine. The forge is the tool that fills it: it takes one long document, a
folder of many files, or a mixture, drives an agent harness (such as Antigravity's `agy`) through
the conversion, and checks every result with the `mindslice` command. Sources of any size work,
because they are read in chunks and each chunk is handled by a fresh agent session that continues
the work of the ones before it.

The forge itself is small on purpose. It keeps the inventory, the state, the checkpoints and the
checks; everything that needs understanding of the text is done by the agent, following the
templates in [`mindslice_forge/prompts/`](mindslice_forge/prompts/).

Python 3.12+, standard library only. Needs `mindslice`, `git` and an agent harness on `PATH`.

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
git clone https://github.com/pancerZH/MindSlice-forge.git
cd MindSlice-forge
pipx install -e .        # or: pip install -e . inside a virtual environment
```

Reuse an existing checkout or installation when available. Install MindSlice separately and make
sure the chosen harness is authenticated and its model is available.

## Use

```bash
mindslice-forge novel.txt \
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
| `--sample N` | Process only the first N chunks, to try a prompt before a long run. |
| `--chunk-size CHARS` | Target size of a chunk. Default 100000 characters. |
| `--harness agy\|claude`, `--model M` | The agent harness preset and its model. |
| `--harness-cmd TEMPLATE` | Any other harness, as a command with `{message}`, `{prompt_file}` or `{workdir}`. |
| `--from STAGE` | Redo the build from `plan`, `split`, `extract` or `finalize`. |
| `--dry-run` | Show the inventory and what would run. Calls no agent and writes nothing. |

```bash
mindslice-forge ./project-docs ./adr --prompt "Rules, architecture and operating procedures for new engineers."
```

**Running the same command again continues an unfinished build.** A typical session:

```bash
mindslice-forge novel.txt --prompt prompt.md --sample 2   # try two chunks
git -C novel.workspace log -p                             # see what each chunk produced
# edit prompt.md
mindslice-forge novel.txt --prompt prompt.md --sample 2 --from extract   # extract again
mindslice-forge novel.txt --prompt prompt.md              # satisfied: run the rest
```

If the sources, the dict, the prompt or the chunk size change after a build has started, the forge
refuses to continue silently: say which stage to redo with `--from`, or choose another output.

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
└── .git/                   one commit per finished chunk: the checkpoints
novel.workspace.forge/      build state and evidence; keep it for resume and review
├── state.json              progress
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

**For an existing workspace**, build into a separate new directory. Forge cannot take over a
nonempty workspace that it did not create. Inspect the resulting `slices/*.slice` aliases and
dependencies, then mount the selected individual slices. For example, if the build produced
`handbook.slice` and its alias is available in your destination:

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

## Safety

- Select allowed sources before invoking Forge, including `--dry-run`: recursive scanning does
  not honor project ignore files or forbidden-directory markers. Pass selected files or prepare
  an allowed snapshot with exclusions applied before traversal. Keep output, reference files and
  conversion prompts outside the source directories.
- The agent runs with tool permissions approved automatically, and the splitter is a program the
  agent wrote. Convert sources you trust, or run the forge in a sandbox.
- Forge writes its output workspace and sibling `.forge/` directory. It refuses to take over an
  existing nonempty output it did not create. Workspace rollbacks happen inside its output directory.

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

The unit tests need nothing. The functional tests run whole builds with the real `mindslice` and
`git` commands and a fake agent ([`tests/fake_harness.py`](tests/fake_harness.py)); no model is
called.

### Checking a first real run

After a `--sample 2` run against a real harness, look at:

- the `[split]` line: the chunk count and the first and last titles should match the document;
  `novel.workspace.forge/chunks/manifest.json` lists what was skipped and why;
- `git -C novel.workspace log --stat`: one commit per chunk, with the cards it touched;
- `mindslice -w novel.workspace validate`, and a few searches and reads of things you know are in
  the first chunks, including one that changes over time;
- `novel.workspace.forge/report.md`.
