## The existing workspace

The slices of this build are for the MindSlice workspace `{{into}}`, which already holds other
knowledge. When the build is complete, the forge copies them there and mounts them next to it.

So that you can see what is known already, this build's workspace `{{out}}` holds a copy of it,
taken when the build started: these slices, mounted under the same aliases as there,

{{reference}}

and, in the overlay, {{overlay_cards}} recent card(s) that are not in a slice yet. Searches and reads
with `mindslice -w {{out}}`, and the lookup command, find these cards together with the build's own.
Each slice's `slice.toml` describes it; its `overview.md`, if it has one, says what is where. Use
the copy, never the existing workspace itself or a memory server this session may offer.

- **The copy is reference, not material.** Cards still state only what the sources say. An
  existing card is no evidence for a card of this build: it shows what need not be written again.
- **Do not repeat it.** Where a thing already has a card, write in your own slice only what the
  sources add: a card that `extends` the existing one (by its `@alias/path`) for a later period,
  another aspect or more detail, or a link `[[@alias/path]]` to it. Where the sources disagree
  with an existing card, record that in your own slice, by the rule for disagreements, and link
  to the card.
- **Link to and extend only cards in these slices**, never cards in `@overlay`: those move into
  other slices later, and a link to them would break.
- **The copy is read-only.** Do not change, add or delete anything under `{{reference_dir}}` or in
  `{{out}}/overlay`. The build undoes changes to the slices and sends them back.
- **Plan against it.** The plan looks at these slices before deciding its own: it gives new
  slices, folders and cards to what the sources add, uses the names the existing cards use for the
  same things, and says which existing slices and cards the new ones build on.
- **Aliases and dependencies.** A new slice takes an alias that none of these use, and not
  `@overlay`. It lists in the `dependencies` of its `slice.toml` the existing slices its cards
  extend or link to: the plan declares the ones it expects, and the final session adds any others
  the cards came to use.
- The slice overviews and the report say how the new slices relate to the existing ones.
