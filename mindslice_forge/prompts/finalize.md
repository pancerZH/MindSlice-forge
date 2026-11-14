# Task: finish a MindSlice build

Source documents have been converted into the MindSlice workspace at `{{out}}`, chunk by chunk, by
earlier sessions that you have no memory of. So far {{coverage}} have been processed. You finish
the build: navigation, remaining problems, a retrieval check and the report.

## Read first

- The build plan: `{{plan_file}}`
- The ledgers of the processed chunks, in `{{ledger_dir}}`: which cards each chunk created or
  changed and from which sources, what it left out, and where sources disagreed.
- The source inventory, `{{profile_file}}`, and the chunk manifest, `{{manifest_file}}`: which files
  and lines went into which chunk, and what was skipped, with the reasons.

The user's conversion instructions:

> {{user_prompt}}

## What to do

1. **Overview.** For each slice under `{{slices_dir}}`, write or rewrite `overview.md`: what the
   slice covers, its topic folders, and a few good starting cards as `[[links]]`. If only part of
   the sources has been processed, say which part. `overview.md` needs no frontmatter.
2. **Remaining problems.** The engine currently reports:

{{issues}}

   Fix them, keeping the knowledge: split a long card by meaning, repair or remove a broken link.
   Then run `mindslice -w {{out}} validate` and fix what is left. Where two cards turn out to
   describe the same thing under different names, as happens when different files or chunks
   introduced it separately, keep one, move the other's facts into it, and repoint the links.
3. **Retrieval check.** From the ledgers and the plan's purpose, choose 5 to 10 questions a user of
   this workspace would ask, and note the facts a good answer needs, before you search. Include
   a question about something that changes over time and, when there are several source files, one
   whose answer draws on more than one of them. For each one run

   ```
   mindslice -w {{out}} search "<the question in a user's words>"
   mindslice -w {{out}} read <address>
   ```

   and judge whether the right card is found and gives enough to answer. Where it is not found,
   improve titles and aliases. Do not add facts the cards' sources do not support.
4. **Report.** Write `{{report_file}}`, in the language of the user's instructions: what was built
   (slices, their purpose, card counts); the coverage ({{coverage}}), with the files and ranges
   that were left out and why, including files the inventory lists as not text; disagreements
   between sources and how each is recorded; the questions you tried, with what was found and what
   you repaired; problems that remain; and 2 or 3 questions the user can try first, with the
   expected answer and the card that holds it.

## Rules

- Write only inside the slice directories under `{{slices_dir}}`, plus the report. Do not create
  new slices, do not use `mindslice write`, and do not run `git`.
- Cards state only what the sources say. This session adds no new knowledge.
- Before creating any card, including when splitting a long one, run `{{lookup}} "<name>"`.
  Search one name at a time and read matching cards before deciding to create or merge. The
  build requires a successful lookup from this session or its repairs for every new card:
  the whole query must occur in its title or an alias, or the whole title or alias in the query
  (ignoring case, respecting Latin word boundaries). Earlier chunks' lookups do not count.
  Slice overviews are exempt.

## Card format

{{card_format}}
