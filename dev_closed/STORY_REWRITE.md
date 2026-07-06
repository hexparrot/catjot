# Enrich the Bellvue story in `tests/bellvue.jot`

## Context

`tests/bellvue.jot` holds the raw lore for the Ravenswood Manor / Bellvue-family roleplay
(the actual story lives in **lines 1–248**; everything after is unrelated test-fixture noise —
nav logs, elara/theron, crypt/cellar events — and is left untouched).

The story reads as one-dimensional because **every character note is only `Personality:` +
`Backstory:` with no physical appearance, attire, voice, or mannerisms** — even though the
narrator rules explicitly demand "describe their attire and one or two distinctive physical
traits." Worse, five of the six women share a nearly identical "cynical / betrayed-once /
distrustful-of-outsiders" arc, so they blur together. There is also no lore for the manor's
staff, despite the world rule that *all* staff are women.

**Goal:** flesh out the six women and the locations with vivid, *distinct* sensory/behavioral
prose (personalities and backstories kept as-is — we **append**, never rewrite), and seed a
roster of important house-staff roles who each already have a name in the lore but stay
"nameless" to the player (referred to by role) until they're introduced.

Per the user's choice this touches **only `tests/bellvue.jot`** (raw lore). It does **not**
regenerate `tests/bellvue_canonical.jot` and therefore does **not** change live in-game prose
(the game plays from the generated canonical seed via `play.py:41`). This is a
worldbuilding/lore-authoring pass on the source document.

## Hard constraints (must stay green — verified against `test_context.py` / `test_rpjot.py`)

Editing an existing note's `Message:` body is always safe. When **adding** notes, obey all of:

- **Never reuse or alter** these locked timestamps or their tag sets: `1725989783`
  (`uncensored_ai system_role`), `1725989938` (`story_premise bellvue_family bartholomew`),
  and `1726003498`, `1726003872`, `1726004390`, `1726006467`, `1726006979`, `1726007167`,
  `1726007326`, `1726007622`, `1726007812`, `1726008604`.
- **Do not change counted tags.** New notes must NOT carry `system_role` (locked at 7 notes),
  `character-backstory` (locked at 6), `story_premise`, or `bartholomew` (locked at 2).
- **Do not add notes** to directories `/system/rules/roleplaying` (locked at 3) or
  `/story/premises/main_plot` (locked at 2).
- Keep querying `luna` surfacing `luna_bellvue`, i.e. don't touch existing `Tag:` lines.
- Preserve `.jot` format exactly: each note starts with `^-^` on its own line, then
  `Directory:` / `Date:` / `Tag:` / `Context:` / `Message:` (body runs until the next `^-^`).

## Work item A — Enrich the six women (edit existing `Message:` bodies, lines ~71–127)

For each of the 6 existing character notes (Aurora `1725999543`, Evie `1725999624`,
Luna `1725999706`, Cassidy `1725999780`, Winnie `1725999840`, Sam `1725999875`), **keep the
existing Personality/Backstory text verbatim** and append two labeled paragraphs:

- **Appearance:** a distinct visual signature — build, hair, eyes, attire, one memorable detail.
- **Manner:** voice/cadence + habitual mannerisms + a "tell" that betrays the guarded surface,
  plus one note of texture *beyond* cynicism so they stop being interchangeable.

Distinct signatures (differentiation is the whole point — no two share a register):

| Char | Appearance hook | Manner / voice | Tell + hidden texture |
|---|---|---|---|
| **Aurora** | flawless dark chignon, pale grey auditor's eyes, severe charcoal tailoring, antique signet ring | low, clipped, never raised; steeples fingers, works ledgers, stays standing | jaw-muscle flickers when control slips; a buried, exhausted tenderness |
| **Evie** | silver-streaked auburn, jewel-tone silks, heirloom jewelry, bergamot-and-old-roses scent | honeyed, musical, slow; touches your forearm, pours the tea herself | warmth blinks off for a half-second when she calculates; real grief & loneliness underneath |
| **Luna** | honey waves with a paint smudge, kohl-smudged eyes, rings, bohemian layers, barefoot, linseed-oil scent | bright, quick, laughs loudly as deflection; drapes over furniture, changes subject | laugh goes brittle when called shallow; genuine talent + terror of being seen as empty |
| **Cassidy** | razor-sharp dark bob, understated makeup, impeccable tailoring (power, not wealth), tablet in hand | dry, fast, sardonic, last word is hers; eyebrow raise, works the room from the walls/exits | goes very still and very polite once she's marked you a threat; fierce, near-sole loyalty to Evie |
| **Winnie** | once-radiant, now pale and thin, still in widow's greys 4 yrs on, unremoved ring, long sleeves | soft, rare speech; flinches at raised voices; hovers at thresholds, hugs her elbows | bright false hostess-smile to deflect scrutiny; a dry startling wit when she forgets to be afraid |
| **Sam** | deliberately forgettable — plain practical clothes, hair tied back, chapped working hands, eye slides off her | soft, deferential, agreeable ("of course"); makes herself small, listens at the edges | face goes cold-and-adult for a half-second when slighted; genuinely competent, knows the house best |

## Work item B — Enrich the locations (edit existing `Message:` bodies)

Expand the sensory prose (light, sound, smell, texture, mood) of the four location notes while
**preserving every load-bearing fact** (single door / single entryway, the one second-floor room
that overlooks the "secret" garden, three garage doors, etc.):

- Cottage `1726003498`, Secret Garden `1726003872`, Car Garage `1726004390`,
  Front Entrance of Ravenswood `1726006467`.

Body-only edits — timestamps/tags/dirs untouched, so the timestamp-lookup and count tests hold.

## Work item C — Seed the house-staff roster (NEW notes, "nameless until named")

Insert a contiguous block of new notes **at the end of the curated story region** — physically
after the "all staff are women" note (`1726008604`, ends ~line 248) and *before* the
test-fixture noise at line 249. Use fresh ascending timestamps `1726008700`, `1726008710`, …
(all unused; the next existing note jumps to `1779243245`).

**Naming convention** (one lore note, `Directory:/story/character/staff`,
`Tag:staff-convention naming`, NOT `system_role`): document the rule that each staff member
already has a canonical name in the lore but the player only ever sees her **role label**
("the butler", "the housekeeper", …) until she introduces herself or another character names
her. She is not a blank — she is a fully realized woman whose name is simply not yet *known* to
the player. (Kept as lore, not a live `system_role` rule, to preserve the 7-note count; if the
user later wants it injected at play time it belongs in `create_canonical_seed.py`, out of scope.)

**Staff notes** — one per role, `Directory:/story/character/staff/<role>`,
`Tag:staff-profile <role> <seeded-name-token>` (no `character-backstory`/`_bellvue`). Each body
gives: the role & what she runs, the **provisional label** the player sees, the **seeded real
name** (narrator-known), and the same Appearance/Manner enrichment treatment as the women. All
women, per the world rule. Proposed roster (names are proposals — easy to rename at review):

| Role (player sees) | Seeded name | Domain |
|---|---|---|
| the butler / majordomo | Mrs. Prewitt | runs the household staff, answers the oak doors |
| the housekeeper | Dorothy "Dot" Alder | rooms, linens, the daughters' floors |
| the cook | Bess Farrow | the kitchens |
| the resident mechanic | Frankie Voss | the car garage (already referenced in the garage note) |
| the driver / chauffeur | Nadia Roan | the vintage cars, arrivals & departures |
| the gardener / groundskeeper | Iris Quill | flower walls, the secret garden, the grounds |

## Files

- `tests/bellvue.jot` — the only file changed: body edits to the 6 character notes and 4
  location notes; ~7 new notes (1 convention + 6 staff) inserted before line 249.

## Verification

1. `python -m pytest test_context.py test_rpjot.py` — must stay fully green (this is the real
   guard: it pins the tag counts `system_role=7` / `character-backstory=6` / `bartholomew=2`,
   the dir counts, and the timestamp→tag lookups listed above).
2. Sanity-parse the file so no `^-^`/field formatting slipped:
   `python -c "from catjot import Note; print(len(list(Note.iterate('tests/bellvue.jot'))))"`
   (count rises by exactly the number of new staff/convention notes; no parse error).
3. Eyeball the six enriched character notes + staff block to confirm personalities/backstories
   are unchanged (append-only) and each character now has a *distinct* appearance/manner.
