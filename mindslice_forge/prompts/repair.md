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
- For `not_looked_up`, run the recorded lookup command in the issue with the card's title or an
  alias. Read the matching cards, ignoring the new card's own search hit. If an older card covers
  the same thing and scope, merge the new facts and aliases into it, repoint links, and remove
  the duplicate; if that older card is read-only (the task says which are), keep the new card
  with only what the older one lacks, and make it extend or link to the older one. Keep distinct
  periods or scopes separate as the plan requires. A successful
  search with no existing match permits keeping the new card. Do not rename a card merely to
  match an unrelated lookup. Any new cards you create while repairing need lookups too.
- A problem in a program is fixed in the program. Do not hand-edit its output.
- If the original task names a check command, run it again when you are done.

## Card format

{{card_format}}
