# LOCATION_MARKING — precise location metadata on room entry

Design note for guaranteeing that memories are filed under the **correct, precise
room** at write time. Location is a primary retrieval key for memories; this closes
the gap where notes are stamped with a coarse, wrong, or stale location.

## 1. Problem

Memories are notes stamped with `session.location` at the moment they are written:

- `_tool_record_knowledge` (the `exp:`/`know:` "conversation" memories) files at
  `pwd=/story/events/{session.location}` with **no location override** (rpjot.py:2752).
- `_tool_record_event` defaults `location` to `session.location` (rpjot.py:2110-2113).
- `_tool_navigate_to` is the **only** writer of `session.location` (rpjot.py:2266).
- Retrieval keys on the path: `location_ancestors` (rpjot.py:479) drives
  `gather_pov_context` (rpjot.py:1712), `build_scene_context_map` (rpjot.py:1726),
  and `query_location_context` (play.py:132).

The stamped key is corrupted three independent ways, each mis-filing notes:

- **Coarse (G1).** The step-1 world doc shows only `location: {leaf}` (rpjot.py:539)
  and gathers *ancestors*, never *children* (rpjot.py:568). The model is never given a
  vocabulary of sub-rooms, so a library conversation is filed under `ravenwood-manor`.
- **Wrong (G4).** `resolve_destination` (rpjot.py:1663-1670): from a single-component
  root, a bare `foyer` fails the `len(from_parts) > 1` guard and returns
  `("foyer", "direct")` — a *detached top-level* location outside the manor ancestry,
  so `location_ancestors` recall breaks.
- **Stale (G3).** Being *led/brought* to a room (passive movement) usually does not fire
  `navigate_to` — it is the `forced` bucket, which fires ~58% (0% on weak models). Notes
  recorded in the new room are then stamped with the **old** room.

**Tension with the shipped over-fire fix.** The stationary nudge (`_STATIONARY_NUDGE`,
`ComplianceStep._is_stationary_turn`, rpjot.py:707/744) deliberately *suppresses*
`navigate_to` on led/passive turns — exactly the entries where the location key still
must update. The fix therefore **decouples the location re-mark (a metadata operation)
from scene-movement (a narrative operation).**

**Out of scope** (not the goal): prose-context freshness at the prose step, a
player-facing arrival line, and any room-graph / adjacency redesign.

## 2. Decisions

- **Early commit + canonicalize.** Resolve the current room at turn start, canonicalize
  it to a known slug, and commit `session.location` **before** step-2 recording — so
  every `record_*` in the turn stamps the true room. `navigate_to` still owns scene-move
  narration.
- **Auto-node + location recall.** On entry, ensure a canonical `/story/location/{path}`
  node exists (deterministic, no model reliance), and add location-scoped memory recall
  via `SearchType.TREE` over `/story/events/{room}` (this room **and** its sub-rooms).

## 3. Design

All changes in `rpjot.py` unless noted.

### 3.1 Early re-mark — `_remark_location(classified_input, world_doc)`
Insertion: in `run_turn`, between step 1 (line 1349) and step 2 (line 1352) — after the
world doc is built (so it can be read) and before any recording tool runs.

Gating (the decoupling): defer to `navigate_to` on mobile self-moves, own the rest.
```
if not ComplianceStep._is_stationary_turn(classified_input):
    return   # mobile [MC action]+move-verb: navigate_to moves session.location
             # itself in step 2; pre-committing would collapse compute_traversal
             # to from==to and erase multi-room journey narration.
```
This reads the *same* classifier the nudge uses, never the nudge's suppression state, so
the two cannot deadlock. The re-mark exists precisely to cover the stationary/led/passive
bucket where `navigate_to` is suppressed.

Sourcing the destination room, in precedence order:
1. **Step-1 structured line (primary)** — add to `_STEP1_SYSTEM` (rpjot.py:286) an
   instruction to emit `CURRENT ROOM: <canonical path>` (or `UNCHANGED`) as the first
   line of the world doc; parse `^CURRENT ROOM:\s*(.+)$`. Step 1 sees the full scene
   context; bare tokens see none — so scene understanding, not lexical matching, decides.
   After parsing, **strip the line from `world_doc`** before it flows into step-2/step-3
   context.
2. **Gated deterministic extraction (fallback)** — slugify input tokens/bigrams and test
   each via `_canonicalize_room`, but **only** when the input carries a passive-arrival
   cue: a new `_LED_VERBS` frozenset (lead(s), bring(s), pull(s), carr(y/ies), drag(s),
   escort(s), arrive(s), take(s)) co-occurring with a directional preposition
   (into/to/inside). Mirrors the `_MOVE_VERBS` pattern (rpjot.py:728) for third-person/
   passive movement. Covers led moves that name the room ("the car pulls into the
   garage") when step 1 omits the line.
3. **Fail-safe** — nothing confident ⇒ return without touching `session.location` (keep
   the last precise location; **never guess**).

**Why this order is load-bearing (do not "simplify" it back).** The re-mark runs on
*every* stationary turn — which includes all dialogue turns. Ungated token extraction
would re-mark on mere *mention*: "[MC speaks aloud]: 'Meet me in the garage at dusk'"
confidently matches child `garage` and moves the location key while nobody moved. That
is the neg_navto 0/18 mention≠movement failure re-entering through the metadata
side-door; the `_LED_VERBS` gate and step-1-first ordering exist to keep it out.

On a confident, canonicalized `path != session.location`:
```
self._ensure_location_node(path)          # §3.5
self.session.location = path
self.session.location_context = ContextBundle(f"{PWD_WORLD}/{path}")
self._cache_drop("social_map")            # mirror navigate_to's invalidation
```
Deliberately does **not** reset `attention`/`mood` — that is scene-move semantics owned
by `navigate_to`; the re-mark is metadata-only.

**Chicken-and-egg.** `_build_baseline_context` and step 1 run against the *old* location,
so the lore shown this turn is the departure room's. That is acceptable: the contract is
*where notes are filed*, governed solely by `session.location` when the step-2 record
tools execute — which is after the commit. The one-turn lore lag for a just-entered room
is a documented limitation.

**Transition-turn filing lag.** A led move often materializes only in **step-3 prose**
("she pulls you into the garden") in response to a stationary input. At re-mark time
(pre-step-2) neither source can know it: the destination is not in the input and step 1
runs before the prose exists. Notes recorded during that transition turn file under the
departure room; the system **self-heals the next turn**, when step 1 sees the updated
history and emits the new `CURRENT ROOM:`. Same class as the lore lag — a one-turn
*note-filing* lag on prose-materialized moves. Accepted, not silent (see §5).

### 3.2 Canonicalization — `_canonicalize_room(proposed, current) -> str | None`
Normalize (`lower`, strip `loc:`, slugify to `[a-z0-9/-]`). Precedence:
1. Exact existing full-path node — `NoteContext(NOTEFILE, (SearchType.DIRECTORY, f"{PWD_WORLD}/{slug}"))` non-empty → `slug`.
2. Known child of `current` — via shared helper `_child_room_slugs(current)` (§3.4); match on last component.
3. Known top-level root — depth-1 roots under `PWD_WORLD`; match on first component.
4. `resolve_destination` fallback (§3.3) with `known_roots`.
5. Create-new — no match ⇒ nest as `f"{current}/{slug-leaf}"` (never a far-away guess);
   triggers auto-node genesis (§3.5).
Returns `None` on empty/undeterminable input (feeds the fail-safe).

### 3.3 `resolve_destination` G4 fix (rpjot.py:1663-1670)
Keep `@staticmethod`; add optional `known_roots=None`.
```
if len(dest_parts) == 1:
    if len(from_parts) > 1:
        return f"{from_parts[0]}/{destination}", "inferred"      # unchanged
    if known_roots is not None and destination in known_roots:
        return destination, "direct"                              # saved sibling root
    return f"{from_path}/{destination}", "inferred"              # nest as child (G4)
```
`_tool_navigate_to` (rpjot.py:2241) passes `known_roots=self._known_location_roots()` — a
new helper that TREE-scans `PWD_WORLD` for depth-1 root slugs with a saved node. When
`known_roots is None` (pure unit-test callers) the safe default nests as a child.
**One existing test changes:** `test_resolve_direct_top_level_to_bare_name`
(test_rpjot.py:835-837) — split into unknown-root→inferred and known-root→direct cases.
All other resolve/navigate tests hit untouched branches (multi-segment dest, or the
deep-location branch).

### 3.4 ROOMS-KNOWN-HERE block (canonical vocabulary)
In `_build_baseline_context` (rpjot.py:554), after the shared-lore block, append the
current location's one-level child slugs from `_child_room_slugs(sess.location)`:
```
[ROOMS KNOWN HERE] (canonical child slugs — reuse these exact names):
cottage, car-garage, secret-garden
```
`_child_room_slugs(parent)`: TREE over `f"{PWD_WORLD}/{parent}"`; **post-filter each hit
with `pwd == prefix or pwd.startswith(prefix + "/")`** (see boundary caveat, §3.6); for
each strictly-deeper note take `pwd[len(prefix)+1:].split("/")[0]`; dedup. Without the
filter, the `[len(prefix)+1:]` arithmetic silently assumes the next char is `/` and emits
garbage slugs for prefix-sharing siblings (`garden` vs `garden-east`). This makes the
step-1 `CURRENT ROOM:` line emit canonical slugs instead of prose, closing the loop
between §3.1/§3.2/§3.4.

### 3.5 Auto-node genesis — `_ensure_location_node(path) -> bool`
Idempotent. Existence check uses `SearchType.DIRECTORY` (exact) — TREE would false-positive
whenever a child already has a node:
```
with NoteContext(NOTEFILE, (SearchType.DIRECTORY, f"{PWD_WORLD}/{path}")) as nc:
    if len(nc): return False
Note.append(NOTEFILE, Note.jot(
    message=f"{leaf.capitalize()}. (Auto-created location node; awaiting description.)",
    tag=f"{TAG_LOC}{path}", context=f"location node (auto): {path}",
    pwd=f"{PWD_WORLD}/{path}"))
return True
```
Reuses the `_tool_save_location` jot shape (rpjot.py:2450). A later real `save_location`
supersedes the stub (render sorts newest-first, rpjot.py:1863). Also call it from
`_tool_navigate_to` after line 2266 for symmetry (low-risk).

### 3.6 Location-scoped recall — `gather_location_events(room, focus_hint="")`
`ContextBundle` cannot do prefix recall — `_regen_notes` routes dir terms through
`SearchType.DIRECTORY` (exact, catjot.py:1043 → 730-731). TREE (prefix, catjot.py:732-733)
is only reachable via a direct `NoteContext`/`Note.match` call (established pattern:
`_preload_npc_tracker_from_notes`, rpjot.py:1029). So:
```
prefix = f"{PWD_EVENTS}/{room}"
with NoteContext(NOTEFILE, (SearchType.TREE, prefix)) as nc:
    notes = [n for n in nc
             if n.pwd == prefix or n.pwd.startswith(prefix + "/")]
# transient bundle purely for render_context recency/size handling
return self.render_context(_bundle_from(notes), focus_hint=focus_hint)
```
**Boundary caveat.** `SearchType.TREE` is a raw `pwd.startswith(s_text)` (catjot.py:733)
with **no path-boundary check**: `/story/events/garden` TREE-matches
`/story/events/garden-east`. Both new helpers (here and `_child_room_slugs`, §3.4)
therefore post-filter with `pwd == prefix or pwd.startswith(prefix + "/")`. The fix
lives in the rpjot helpers, **not** in catjot's TREE branch — changing global TREE
semantics would affect existing callers (e.g. `_preload_npc_tracker_from_notes`,
rpjot.py:1029).

Plug-in points: `build_scene_context_map` (add `"location_events"`), `_build_baseline_context`
(an `[EVENTS IN THIS ROOM & SUB-ROOMS]` block), and `query_location_context` (play.py:132,
which today only walks *up* via ancestors). Contract to document in the docstrings:
DIRECTORY (exact) = this room's own notes; TREE (prefix, boundary-filtered) = this room
+ all sub-rooms; ancestor recall walks *up*, this walks *down*.

## 4. Tests (extend test_rpjot.py; reuse `TMP_CATNOTE` fixture + `_make_engine`)

- `resolve_destination`: root→child nest; separate-place guard; **update** the 835-837 test.
- `_canonicalize_room`: precedence (exact / child-of-current / root / create-new / `None`).
- ROOMS-KNOWN-HERE: listed from a seeded subtree; absent on a leaf.
- **KEY acceptance test** — enter-then-record, both paths, `world_doc` stubbed to avoid a
  live LLM:
  - *led move (easy half — destination named in input)*: `_remark_location("[MC action]:
    Evie leads me into the cottage", "CURRENT ROOM: ravenwood-manor/cottage")` →
    `record_event`/`record_knowledge` notes land at `pwd=/story/events/ravenwood-manor/cottage`,
    not the coarse `ravenwood-manor`.
  - *self move*: `_remark_location("[MC action]: I walk into the cottage", ...)` leaves
    location unchanged (deferred); then `_tool_navigate_to("cottage")` moves and the note
    stamps the precise room — proving no double-move / no lost journey.
  - *fail-safe*: `CURRENT ROOM: UNCHANGED` + no room in input → location unchanged.
- **Mention-without-movement (negative, guards §3.1 gating)**: stationary dialogue turn
  naming a known child room — `_remark_location("[MC speaks aloud]: 'Meet me in the
  garage at dusk'", "CURRENT ROOM: UNCHANGED")` → no re-mark (no `_LED_VERBS` cue, step-1
  says UNCHANGED; the lexical path must not fire on mere mention).
- **Deferred/self-heal (hard half — destination only in prose)**: unnamed led move
  (`"[MC action]: she takes my hand and we go somewhere"` with `CURRENT ROOM: UNCHANGED`)
  → fail-safe this turn, location unchanged; next turn's `_remark_location` with
  `"CURRENT ROOM: ravenwood-manor/secret-garden"` → re-mark lands. Proves the one-turn
  filing lag self-heals.
- `_ensure_location_node`: second call writes nothing (idempotent).
- `gather_location_events`: TREE returns sub-room events; DIRECTORY-exact returns only the parent.
- **TREE boundary**: seed events under `garden` and `garden-east`;
  `gather_location_events(".../garden")` excludes `garden-east`; `_child_room_slugs`
  emits clean slugs (no prefix-sharing sibling garbage).
- Regression: shipped nudge suite green — `pytest test_rpjot.py -k "Stationary or navigate or Compact"`.

## 5. Risks / limitations

- **Undeterminable destination** (unnamed led move step 1 can't infer): fail-safe keeps
  the last precise location — coarse but never wrong-place. Accepted residual gap.
- **Step-1 compliance — the residual is model-tiered.** The `CURRENT ROOM:` line depends
  on step-1 compliance from exactly the weak models that fail `navigate_to` (forced
  bucket fires 0% there). On those models, coverage comes almost entirely from the gated
  lexical path + fail-safe; the improvement is real but smallest where the problem is
  worst. Parsing tolerates absence/`UNCHANGED`/free-text and falls through to fail-safe
  (never raises).
- **One-turn lore lag**: baseline context for a freshly re-marked room lags one turn;
  acceptable for the metadata contract.
- **Transition-turn filing lag** (§3.1): when a led move materializes only in step-3
  prose, that turn's notes file under the departure room and the re-mark self-heals next
  turn. One-turn note-filing lag on prose-materialized moves — accepted, not silent.
- **Auto-node proliferation**: create-new could spawn stubs; mitigated by canonicalization
  matching existing rooms first + DIRECTORY-exact idempotency. A periodic stub audit is advisable.

## 6. Verification

- `pytest test_rpjot.py -k "Location or resolve or navigate or Canonical or Remark or Stationary or Compact"`.
- End-to-end (needs the `:5000` endpoint live — preflight first; a dead server silently
  x-fails live tests): play a led move → record a conversation → `/location` and confirm
  the note filed under the precise room.

## Critical files

- `rpjot.py` — `run_turn` 1321-1393; `resolve_destination` 1640-1670; `_build_baseline_context`
  554-595; `_tool_record_event` 2100; `_tool_record_knowledge` 2729; `_tool_navigate_to` 2234;
  `_tool_save_location` 2442; `_STEP1_SYSTEM` 286; `build_scene_context_map` 1726;
  NoteContext/TREE pattern 1029.
- `catjot.py` — `SearchType.DIRECTORY` vs `TREE` 730-733; `Note.jot` 406-441; ContextBundle
  dir-term routing 1042-1044.
- `play.py` — `query_location_context` 132-136; `classify_input` 78-115.
- `test_rpjot.py` — `TestLocationHierarchy` 756-907; `TMP_CATNOTE` fixture 2480-2513;
  `_make_engine` 42-49.
- `create_canonical_seed.py` — canonical room hierarchy 377-418 (reference for test seed data).
