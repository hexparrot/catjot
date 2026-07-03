# OBJECT_TOOLING — first-class objects with canonical identity and residence

Design note for making objects first-class: one canonical identity + description per
object, and a **residence** (room *or* holder — possession is just another residence)
read deterministically from the newest sighting. Replaces the write-only `save_object`
mechanic. Companion to LOCATION_MARKING.md; reuses its primitives where they transfer
and states plainly where they do not.

## 1. Problem

- **Write-only.** `_tool_save_object` (rpjot.py:2461-2513, step 2) writes
  `tag="obj:{name}"` at `pwd=/story/location/{loc}` and nothing can read it back through
  the LLM. Objects reach the model only as undifferentiated text inside
  `location_context = ContextBundle(f"{PWD_WORLD}/{loc}")`, which `extract_scene_context`
  (rpjot.py:2003, called from `_tool_examine_location` at 2190) re-parses **with an LLM
  call** into `noteworthy_objects` — no deterministic object block anywhere.
  `play.py:139-145` has the retrieval logic (`query_object_context`, `obj:{name}` tag
  search) but it is a manual CLI command (`/objects`, play.py:481), never a tool.
- **No identity.** `name` is a free string — "iron key", "iron-key", "the key" fork into
  three objects that are never merged. There is not even a `TAG_OBJ` constant; `"obj:"`
  is an inline literal in three places (rpjot.py:2499, 4633/4636, play.py:142).
- **No residence semantics.** `location` is required but unvalidated and never defaulted;
  an object "moves" only by the model re-calling `save_object` with a new location, which
  duplicates the description and leaves the old room note in place forever.
- **No possession.** A held object has no representation at all. Filing it under
  `/story/character/{holder}` would pollute the character profile — but
  `gather_character_knowledge` (rpjot.py:1691) and `gather_pov_context` (rpjot.py:1714)
  are `DIRECTORY`-exact on `/story/character/{name}`, so a *subpath* is invisible to
  them. That accident is the design opportunity.
- **Engine lesson (bakeoff history).** There is **no deterministic classifier** for "an
  object changed hands this turn" — the same class as the forced-movement bucket (fires
  0% on weak models; prohibitions in tool descriptions are inert). So the design must be
  newest-note-wins, never-guess, with no hard dependency on model compliance. Unlike
  LOCATION_MARKING there is no `_remark_location`-style deterministic leg here; the
  honest ceiling is *stale-but-never-wrong-place* (§6).

## 2. Decisions

- **Residence is a pwd; possession is a residence.** An object's location — room or
  holder — is encoded solely as the pwd of its newest sighting note. Room:
  `pwd=/story/location/{room}`. Holder: `pwd=/story/character/{holder}/inventory`. One
  unified mechanic.
- **Identity is a tag; canon is a separate pwd namespace.** Every object note carries
  the tag word `obj:{slug}` (formalize `TAG_OBJ = "obj:"`; new root
  `PWD_OBJECTS = "/story/object"`). The canonical description lives at
  `pwd=/story/object/{slug}` — outside every residence and context namespace, so it can
  never be shadowed by sightings and never pollutes room/character rendering. A later
  `save_object` supersedes via newest-first render (rpjot.py:1863), the `save_location`
  pattern.
- **Timeline = tag search; residence = newest non-canonical pwd.** `obj:{slug}` tag
  search (exact word match, catjot.py:749/783, pwd-independent) sorted by `now`
  descending is the newest→oldest appearance traversal; residence is parsed from the
  first hit whose pwd is **not** under `PWD_OBJECTS`. The exclusion is load-bearing: a
  description re-save must not "move" the object to `/story/object/{slug}`.
- **Two write channels — one primary, one fallback.** Primary: new `place_object`
  step-2 tool (§3.4). Fallback: `obj:{slug}` tags on `record_event` count as sightings —
  the residence parser accepts `PWD_EVENTS/{room}` pwds as room evidence. This rides the
  one tool weak models reliably fire, so residence tracking degrades instead of dying
  when `place_object` under-fires.
  **Mention≠presence caveat (verbatim-strength, do not soften):** an event note that
  merely *discusses* an object, if obj:-tagged, moves its parsed residence — the same
  failure class as the neg_navto mention≠movement miss. Mitigations: the tags-param
  wording says objects **handled**, not mentioned (§3.6); `[OBJECTS HERE]` is the
  authoritative correction layer; and never-guess means the damage is a wrong *recent*
  sighting, correctable by the next real one — never an invented residence.
- **Deterministic vocabulary defense.** An `[OBJECTS HERE]` block in
  `_build_baseline_context` (mirror of ROOMS-KNOWN-HERE, LOCATION_MARKING §3.4) lists
  canonical slugs in the current room and held by present cast, so the model reuses
  names instead of forking them.

## 3. Design

All changes in `rpjot.py` unless noted.

### 3.1 Storage model — three note kinds, one tag

| kind | pwd | tag | message |
|---|---|---|---|
| canonical | `/story/object/{slug}` | `obj:{slug}` (+extra tags) | canonical description; later `save_object` supersedes via newest-first render |
| sighting | `/story/location/{room}` or `/story/character/{holder}/inventory` | `obj:{slug}` | state line ("seen here", "now cracked") — or the full description on the genesis sighting |
| event (fallback) | `/story/events/{room}` | `obj:{slug}` among event tags | whatever `record_event` recorded |

**Where a state change files.** A change observed in a scene ("the locket is now
cracked") is a **sighting at the current residence** — `place_object(name, state=...)`
with no destination, which re-states the object where it is. The canonical node is
*identity*, touched only when identity-level facts change (the model may re-save the
description via `save_object`; allowed, not required). Degrades gracefully: even if
canon is never updated, `get_object` renders newest sightings first, so "cracked"
surfaces regardless.

**Why canon is never shadowed.** It is read by `DIRECTORY`-exact query on its own pwd,
never through the tag timeline; and the residence reader skips
`pwd.startswith(PWD_OBJECTS + "/")`. The two reads are namespace-disjoint by
construction.

**Why the holder pwd is `/{holder}/inventory`.** `gather_character_knowledge` and
`gather_pov_context` are DIRECTORY-exact on `/story/character/{name}` — the subpath is
invisible to profile rendering, so possession notes cannot pollute character context.
Deliberately do **not** add the inventory dir to `gather_pov_context`; held objects
reach the model through `[OBJECTS HERE]` (§3.6), keeping POV context clean.

**Tie-break.** `Note.now` is int epoch seconds; two writes in one second are possible in
a single turn. Residence/registry scans resolve equal timestamps by **later-in-file
order** (`>=` while scanning append order), not a bare `sorted(..., reverse=True)`.

### 3.2 Canonicalization — `_canonicalize_object(name) -> str`

Normalize (`lower`, strip `obj:`, slugify to `[a-z0-9-]` — object slugs are **flat**;
`/` is stripped because hierarchy belongs to residence, not identity). Precedence,
mirroring `_canonicalize_room` (LOCATION_MARKING §3.2):

1. Exact canonical node — DIRECTORY on `f"{PWD_OBJECTS}/{slug}"`.
2. Exact slug in the object registry (§3.5 — covers legacy objects with no canonical node).
3. Word-overlap match against registry slugs ("the iron key" → `iron-key`; token-set
   containment, same spirit as `_tool_find_character`'s matching, rpjot.py:2580-2596).
4. **Create-new — the normal path, not a failure.** Stories mint objects constantly;
   there is no fail-safe-refuse branch here. This is a deliberate asymmetry with
   LOCATION_MARKING's never-guess room canonicalization — do not "fix" it into one.

### 3.3 `save_object` revision (rpjot.py:2461-2513)

Same tool name — schema-name churn is model-facing risk. Changes:

- `location` becomes **optional**, default `self.session.location` — the exact
  `record_event` pattern (rpjot.py:2110-2113). Synergy: once LOCATION_MARKING's
  `_remark_location` ships, `session.location` is precise at step-2 time, so this
  default inherits that precision for free. Neither doc depends on the other shipping
  first.
- Canonicalize `name` via §3.2.
- **Dual write:** canonical note at `PWD_OBJECTS/{slug}` *and* a genesis sighting at the
  residence carrying the same description text — byte-for-byte what old `save_object`
  wrote, so room rendering is unchanged for models that never call `get_object`.
- Return string stays `f"Object saved: {slug} (at {residence})"` — existing tests
  (test_rpjot.py:651-666) stay green.
- `_cache_drop("obj_registry", f"obj:{slug}")` (cache methods rpjot.py:1526-1543).

### 3.4 `place_object` — the residence-change tool (step 2, new)

**One** tool for pick-up / hand-over / drop / stow / move / state-change (tool count is
the scarce resource: 31 step-2 tools against the compact budget, §3.7). Params: `name`
(required), `holder` (optional), `room` (optional), `state` (optional). Semantics:

- `holder` XOR `room`; both given → return an error *string* (house rule: tool handlers
  never raise).
- Neither → re-state at the current residence (via the §3.5 parser); no prior sighting
  anywhere → `session.location` — the only defensible default, and a *room*, never a
  guessed holder.
- Ensure the canonical node exists (auto-genesis stub mirroring `_ensure_location_node`,
  LOCATION_MARKING §3.5 — message "awaiting description", superseded by a later real
  `save_object`). This doubles as the lazy-migration hook (§4).
- Write one sighting note: `message = state or f"{slug} is here."`,
  `context = f"object sighting: {slug} at {residence}"`.
- `holder`: slugify; warn-log if not in `npc_tracker` but **don't reject** — cast
  tracking is itself model-dependent. `room`: `removeprefix(TAG_LOC)` + slugify now;
  route through `_canonicalize_room` once LOCATION_MARKING lands (one-line TODO).
- Add `"place_object": "Object placed"` to `_WRITE_TOOL_LABELS` (rpjot.py:206) — every
  step-2 tool needs an entry or fallback synthesis renders a raw dict.

### 3.5 Reads — registry, residence parser, `get_object` (step 1, new)

`_object_registry()` — one pass over the notefile via NoteContext TREE on `"/story"`.
(This design otherwise needs **no** TREE scans, so LOCATION_MARKING §3.6's
path-boundary bug is a non-issue here — `/story` as a prefix is unambiguous; say so in
the docstring.) Collect per `obj:*` tag word: the newest non-canonical note (residence
evidence, `>=`-in-file-order tie-break, §3.1) and whether a canonical node exists.
Cache under `"obj_registry"`; dropped by `save_object`, `place_object`, **and**
`record_event` (cheap, and required because event tags are sightings).

Residence parser (pwd → residence), applied to the newest non-canonical note:

```
pwd == f"{PWD_CHARS}/{h}/inventory"      → held by h
pwd.startswith(f"{PWD_WORLD}/")          → in room pwd[len(PWD_WORLD)+1:]
pwd.startswith(f"{PWD_EVENTS}/")         → in room (record_event fallback channel)
```

`_tool_get_object(name)` — step 1, mirrors `_tool_get_character` (rpjot.py:2514-2549):
cache key `f"obj:{slug}"`; returns JSON with `canonical_description` (newest note at the
canonical pwd; **legacy fallback:** newest sighting's message), `residence`
(`{"held_by": ...}` or `{"room": ...}`), `timeline` (newest→oldest sightings rendered
through `render_context` for the recency/size caps), and a `followup_instruction`. On a
canonicalization miss it returns the known-slug roster with a find_character-style "use
an existing name, do not invent a new one" followup (rpjot.py:2624-2631) — which is why
there is **no separate `find_object` tool** (tool-count discipline; `search_world`
already covers keyword search). Wire play.py's `query_object_context` to the same
helpers (§3.6).

### 3.6 `[OBJECTS HERE]` block + integration points

In `_build_baseline_context` (rpjot.py:554-595), after the character-profile blocks:

```
[OBJECTS HERE] (canonical slugs — reuse these exact names):
in this room: iron-key, silver-mirror
held — alice: locket
```

Built from the registry: slugs whose parsed residence == `session.location`, plus per
present character their held slugs. **Only the newest residence counts** — an object
moved out of this room is not listed even though its stale sighting note still sits
(undeletable, append-only store) in the room's ContextBundle blob; this block is the
authoritative correction layered on top (the §3.4 vocabulary-loop argument from
LOCATION_MARKING, applied to an open name space where it matters more).

Remaining integration points:

- `_tool_examine_location` (rpjot.py:2177-2201): add a deterministic `objects_here` key
  from the registry **alongside** the LLM-extracted `noteworthy_objects`
  (`extract_scene_context` left untouched). Contract: the deterministic key wins on
  conflict.
- `play.py:139-145` `query_object_context`: delegate to the engine helpers — `/objects`
  bare → registry residents of the current room; `/objects name` → the `get_object`
  render. Kills the last inline `"obj:"` literal in play.py.
- Constants: `PWD_OBJECTS` beside the other roots (rpjot.py:141-150), `TAG_OBJ` beside
  the other tags (rpjot.py:134-139); replace inline `"obj:"` at rpjot.py:2499 and
  4633/4636 (the `/stats` rows keep working unchanged — room sightings still live at
  room pwds).
- `record_event` `tags` param description (rpjot.py:2085-2090, already on the keep
  list): append "Add obj:slug for any significant object **handled**." — handled, not
  mentioned (§2 caveat).

### 3.7 Compaction and the 3,000-token budget

Step-2 compact schema currently measures ~2,621 tok against the 3,000 ceiling
(`test_compact_budget_under_3000`, test_rpjot.py:1772). Additions (estimates —
**re-measure at implementation**):

| item | ~tok |
|---|---|
| `place_object` compact stub incl. 2 keep-listed param descriptions (`holder`, `room` — the holder-XOR-room and possession-is-residence contracts are load-bearing) | 121 |
| `_COMPACT_KEEP_FUNCTION_DESCRIPTIONS["place_object"]` (rpjot.py:1108) — positive one-liner, justified by the shipped `nudge_pos_desc` precedent: "Log an object changing hands or rooms — picked up, handed over, dropped, stowed, left behind; also to note a lasting change to its condition." | 31 |
| `record_event` tags extension | 9 |
| `save_object` required-list change (location optional) | ~0 |

Projected ≈ **2,782 / 3,000** (~218 headroom). Bookkeeping: append
`("place_object", "holder")` and `("place_object", "room")` at the **bottom** of
`_COMPACT_KEEP_PARAM_DESCRIPTIONS` (rpjot.py:1086 — ordered trim-from-bottom list).
`save_object` stays **off** both keep lists (`test_non_keeplist_tool_has_no_param_descriptions`,
test_rpjot.py:1766, untouched); `test_function_descriptions_match_keep_list` (1781)
auto-adapts. `get_object` is step 1 — outside the compact ceiling entirely (note the
~120 tok step-1 overhead increase anyway).

## 4. Migration — a non-event, by construction

Legacy `obj:` notes (old `save_object`: description at `pwd=/story/location/{loc}`,
`tag=obj:{name}`) are *already* valid genesis sightings under §3.1: tag search finds
them regardless of pwd, so timelines include them; the residence parser reads their room
from pwd; `get_object`'s canonical-description fallback (newest sighting message)
returns exactly the old description. The first `place_object`/`save_object` touch
lazily creates the canonical node (§3.4 auto-genesis). No data rewrite, no version
flag, no dual code path. Only observable seam: a legacy object has no
`/story/object/{slug}` node until first touched — visible only in `/stats`, harmless.

## 5. Tests (extend test_rpjot.py; reuse `TMP_CATNOTE` fixture + `_make_engine`)

- `_canonicalize_object`: precedence (exact node / registry slug / word-overlap /
  create-new-is-normal — assert create-new *succeeds*, guarding the §3.2 asymmetry).
- `save_object`: dual write (canonical node at `PWD_OBJECTS/{slug}` + genesis sighting
  at the room); `location` defaults to `session.location`; existing 651-666 tests stay
  green **unmodified**.
- `place_object`: holder → `/story/character/{h}/inventory` pwd; room →
  `/story/location/{r}`; neither → restates at current residence; both → error string,
  no note written; auto-creates a missing canonical node.
- **KEY acceptance — residence traversal:** seed a room sighting, then a holder sighting
  with later `now` → residence = holder; then re-save the canonical *description* even
  later → residence **still** holder. (Guards the §2 canonical-pwd exclusion.)
- **Canon-not-shadowed:** after sightings, `get_object` still returns the canonical
  description from its own pwd, newest sighting first in the timeline.
- **Fallback channel:** `record_event(tags="exp:alice obj:locket")` at a new location
  moves the parsed residence (events-pwd sighting).
- **Mention-pollution (documented limitation):** an obj:-tagged event that only
  discusses the object also moves residence — assert the *current* behavior and mark the
  test as the §2 caveat's tripwire, so any future fix is deliberate.
- **Legacy:** seed only an old-style note → `get_object` returns its description + room;
  a subsequent `place_object` to a holder migrates it (canonical node appears).
- `[OBJECTS HERE]`: lists in-room + held-by-present; **excludes** an object whose newest
  residence is elsewhere despite the stale room note.
- Same-second tie-break: two sightings with equal `now`, later-in-file wins.
- Budget regression: `pytest test_rpjot.py -k Compact` — 1766/1772/1781 all green.

## 6. Risks / limitations

- **No deterministic possession signal — the hard ceiling.** "Object changed hands" is
  exactly the forced-movement class (0% tool-fire on weak models; description
  prohibitions inert). `place_object` **will** under-fire there. Mitigations are
  layered, none individually load-bearing: the `record_event` obj:-tag fallback
  (piggybacks on the one tool weak models do fire), `[OBJECTS HERE]` vocabulary, the
  positive one-liner (nudge_pos_desc precedent), and never-guess residence. The failure
  mode is a *stale* residence, never a *wrong-place* one. Plainly: on weak models,
  residence lags reality until any obj:-tagged write occurs.
- **Mention-pollution via the event fallback** (§2 caveat): accepted and tripwired in
  tests; a wrong recent sighting is corrected by the next real one.
- **Stale sightings still render** in the room's ContextBundle blob (append-only store,
  no deletion); the prose model may re-materialize a moved object. `[OBJECTS HERE]` is
  the authoritative layer; accepted residual.
- **Slug proliferation** for ephemeral props (mugs, doors): registry noise. Description
  guidance is inert on weak models (say so); the `/stats` object rows are the audit
  backstop.
- **extract_scene_context divergence:** the LLM extraction can contradict the registry;
  `examine_location` returns both keys and the contract says deterministic wins.

## 7. Verification

- `pytest test_rpjot.py -k "Object or Compact or Registry"`.
- Compact-budget spot-check (read-only): instantiate the engine, `register_all_tools()`,
  assert the cached compact step-2 schema ≤ 3000 tok.
- End-to-end (needs the `:5000` endpoint live — **preflight first**; a dead server
  silently x-fails live tests): save an object → have an NPC take it → `/objects locket`
  shows the holder residence and the full newest→oldest timeline.

## Critical files

- `rpjot.py` — `_tool_save_object` 2461-2513; `_tool_record_event` 2066-2128 (tags
  description 2085-2090); `_tool_get_character`/`_tool_find_character` 2514-2639
  (patterns to mirror); `_build_baseline_context` 554-595; `_tool_examine_location`
  2177-2201; `extract_scene_context` 2003; compaction 1086-1156
  (`_COMPACT_KEEP_PARAM_DESCRIPTIONS` 1086, `_COMPACT_KEEP_FUNCTION_DESCRIPTIONS` 1108);
  constants 134-154; `_WRITE_TOOL_LABELS` 206; caches 1526-1543; newest-first render
  sort 1863; `/stats` obj rows 4626-4642.
- `catjot.py` — SearchType semantics 730-733 (DIRECTORY/TREE), 749/783 (tag exact word
  match); ContextBundle term routing 911-931.
- `play.py` — `query_object_context` 139-145; `/objects` dispatch 481-484.
- `test_rpjot.py` — save_object tests 649-666; compaction suite 1740-1793;
  `TMP_CATNOTE` fixture 2480-2513; `_make_engine` 42-49.
- `LOCATION_MARKING.md` — house patterns: `_canonicalize_room` (§3.2),
  `_ensure_location_node` (§3.5), TREE boundary rule (§3.6), `session.location`
  precision contract (§3.1).
