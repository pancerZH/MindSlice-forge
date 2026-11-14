# Task: repair the result of an earlier session

An earlier agent session worked on the task described in `{{task_file}}`. Read that file first: it
says what was to be produced, where, and under which rules. Those rules still apply to you.

Automated checks then found these problems:

{{issues}}

Fix every one of them, and nothing else.

- Keep the knowledge. A card that is too long is split by meaning into cards that each answer one
  question, with links between them; nothing is deleted to make it fit. A broken link or lineage
  reference is pointed at the card that was meant, or removed if no such card exists.
- A missing file is written as the original task describes it.
- A problem in a program is fixed in the program. Do not hand-edit its output.
- If the original task names a check command, run it again when you are done.

## Card format

{{card_format}}
