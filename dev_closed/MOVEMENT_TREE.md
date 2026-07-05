# movement_tree — the locale graph: LLM-assisted node creation, connectivity, and adjacency-aware movement

Design note for turning the location system from a **pure containment tree** into a
**navigable graph**. An LLM cartographer canonicalizes rooms the moment they are mentioned,
records how they connect (`adj:` edges), exposes that graph back to the model, and lets
movement follow real doors instead of walking up and down the hierarchy. This picks up the
work `LOCATION_MARKING.md` §1 explicitly deferred:

> **Out of scope** (not the goal): … any **room-graph / adjacency redesign**.

That deferral is now claimed here.

## 1. Problem

Location is a hierarchical slug path (`manor/foyer/drawing_room`) and **nothing else**. Two
gaps follow:

- **Node creation is passive and lossy.** A room exists only once someone `navigate_to`s
  into it or `save_location` names it. `_ensure_location_node` (rpjot.py:2804) mints a
  single bare stub at the arrival path; it creates no ancestors and no relationships. A
  room merely *mentioned* ("the drawing room, just off the entryway") leaves no trace —
  next turn the model has no canonical slug for it and re-invents or mis-nests it. The
  `_canonicalize_room` create-new leg (rpjot.py:2801) keeps only the leaf, so an unrooted
  multi-segment mention collapses and the intermediary is dropped.
- **Connectivity does not exist.** `save_location` (rpjot.py:4018) stores only prose
  (name / description / tags). There are **no** exits, doors, connections, or neighbors
  anywhere (grep-confirmed). `compute_traversal` (rpjot.py:2599) is a pure tree walk —
  ascend to the common ancestor, descend to the target — so two rooms that share a real
  door but sit in different branches can only be reached by routing up through their
  common ancestor. The drawing room *off* the entryway is, to the engine, reachable only
  by leaving the entryway's subtree entirely.

The result is a world the model cannot reason about spatially: it knows *containment*
(`foyer` is inside `manor`) but never *adjacency* (`foyer` connects to `drawing_room`).

**Out of scope** (not the goal): a player-facing minimap; pathfinding cost/weights beyond
hop-count; NPC autonomous navigation; retroactively re-parenting the existing tree.

## 2. Decisions

- **Edges are tags, not schema.** A connection is a bidirectional `adj:` tag on each room's
  node (§3.0). No note-store change; greppable, dedupable, newest-wins — the same
  tag-as-relation discipline as `obj:`/`exp:`.
- **The cartographer is a background, gated sub-step.** It runs in the idle window
  (`speculate_step1`'s neighbor), only when a *new room was mentioned* this turn, and only
  ever **stages** a proposal — the foreground commits it next turn. Zero added latency,
  zero idle-window disk writes (§3.2, I7).
- **Generate a little ahead, bounded.** The model may canonicalize named rooms **and** up
  to **2** strongly-implied connective rooms per turn (a corridor, a landing). Never a
  comprehensive map; never a distant invention (I4, I5).
- **Movement follows edges.** `resolve_destination`/`compute_traversal` become
  adjacency-aware: a recorded door is a 1-hop move; a multi-room trip is the shortest path
  over known edges; with no edges the behavior is byte-identical to today (§3.3, I8).

## 3. Design

All changes in `rpjot.py` unless noted. The four subsections map to the four rollout phases
(§5): §3.0 → Phase 0, §3.1 → Phase 1, §3.2 → Phase 2, §3.3 → Phase 3.

### 3.0 Edge substrate — `adj:` tags and the adjacency note

New constant beside `TAG_OBJ` (rpjot.py:140):

```
TAG_ADJ = "adj:"   # adj:manor.foyer.drawing-room  (bidirectional room connection)
```

Edge endpoints are **full canonical paths**, `/`→`.` encoded so the value is a single
whitespace-safe, `/`-free tag word (catjot tags split on whitespace; `/` is avoided to keep
tag parsing and any future pwd-shaped matcher unambiguous):

```
_edge_tag(path)   = TAG_ADJ + path.replace("/", ".")      # adj:manor.foyer.drawing-room
_edge_decode(tag) = tag[len(TAG_ADJ):].replace(".", "/")  # manor/foyer/drawing-room
```

Edges live on a **dedicated newest-wins "adjacency note"** at the room's own pwd
(`context="adjacency: {room}"`, `message` empty), **separate** from description notes. This
is the `OBJECT_TOOLING.md` canonical-vs-sighting separation applied to rooms: an edge write
never clobbers a `save_location` description, and a re-description never drops edges. Read
takes the newest adjacency note's `adj:` tags; write appends a fresh adjacency note carrying
the union.

```
_locale_edges(room) -> set[str]
    # newest adjacency note at PWD_WORLD/{room}, DIRECTORY-exact; parse its adj: tags,
    # _edge_decode each → set of connected full-path slugs. {} when none.

_add_edge(a, b) -> None
    # idempotent + bidirectional. _ensure_location_node(a) and (b) first (an edge implies
    # both rooms exist). For each endpoint, append an adjacency note whose tag set is the
    # existing _locale_edges ∪ {the other endpoint}. Re-adding an existing edge is a no-op
    # (union unchanged → skip the write). Logs [LOCALE] edge a <-> b.
```

`_locale_graph_block() -> str` — the read surface, extending `_rooms_vocab_block`
(rpjot.py:639). It renders the **neighborhood** of `session.location` — ancestors, children,
siblings (already computed by `_child_room_slugs`/`_sibling_room_slugs`) **plus** each
room's `adj:` edges — as a compact listing the model can reuse verbatim:

```
[LOCALE GRAPH] (canonical rooms near you and how they connect):
  manor/foyer  ⇄  manor/foyer/drawing-room, manor/cellar
  manor/foyer/drawing-room  ⇄  manor/foyer, manor/foyer/library
  (children: drawing-room, closet | siblings: cellar)
```

Returns `""` when nothing is known (parity with `_rooms_vocab_block`).

### 3.1 Detection + context surface (read side)

`_scan_room_drift(classified_input, narrative) -> list` — mirrors `_scan_cast_drift`
(rpjot.py:2323), inverted: cast-drift searches for *known* names that are absent; room-drift
searches for *place references* that are **not yet canonical**. A cheap `_ROOM_LEXICON`
(hall, foyer, cellar, kitchen, library, garden, chamber, study, bedroom, corridor, gallery,
attic, landing, crypt, drawing room, …) regex-scans `classified_input + narrative`;
matches that do not resolve via `_canonicalize_room` to an existing node are collected. The
result is stashed on `self._room_drift` and logged `[LOCALE] mentioned-unmapped: …`.
Detection only — it never writes and never guesses a slug; it only decides **whether to
wake the cartographer**. Called in `run_turn` right after `_scan_cast_drift`.

Injection: add `_locale_graph_block()` beside the existing `rooms_vocab` append in **both**
step-1 shapes — `_build_baseline_context` (rpjot.py:780) and `_build_seeded_message`
(rpjot.py:723) — so step-1's `CURRENT ROOM:` line and the cartographer both see the same
canonical graph. `_rooms_vocab_block` stays (it is the flat child/sibling vocabulary); the
graph block adds the edges.

### 3.2 The Cartographer — background-staged, foreground-committed

A single-purpose LLM call (the `refresh_system_message` pattern, play.py:391), **not** a
tool loop.

`_CARTOGRAPHER_SYSTEM` (sketch): *"You are the cartographer for a text RPG. Given the
current room, the recent narrative, and the known locale graph, return JSON describing the
rooms that now provably exist and how they connect. For each new room give a kebab `slug`,
a `parent` canonical path, and a one-line `description`. List `edges` as pairs of canonical
paths that share a direct door or passage. You MAY add at most 2 strongly-implied
connective rooms (a corridor, a landing, a stair) needed to join what was described — never
invent distant or unmentioned places, and never move the player. Reuse exact slugs from the
locale graph when a room already exists."* Output schema:

```json
{ "nodes": [ { "slug": "...", "parent": "...", "description": "..." } ],
  "edges": [ [ "manor/foyer", "manor/foyer/drawing-room" ] ] }
```

`speculate_locale()` — scheduled in `_idle_worker` (play.py:480) **after**
`engine.speculate_step1()` (play.py:497), and only when `self._room_drift` is non-empty. It
builds the prompt (current room + recent narrative + `_locale_graph_block()`), calls
`call_llm`, parses/validates the JSON, and **stages** it on `self._locale_seed` alongside a
`_seed_state_snapshot()` and the turn counter. **It writes nothing to disk** — preserving
the idle worker's zero-write contract (play.py: *"the worker performs zero disk writes"*),
which is invariant I7. Never raises; on any failure the seed is simply absent.

`_consume_locale_seed()` — called at the top of `run_turn` beside `_consume_seed`
(rpjot.py:2292). Validates the seed (turn counter matches, state snapshot still holds — the
exact discipline `_consume_seed` uses) and **commits in the foreground** (single writer):
each canonicalized node via `save_location`/`_ensure_location_node`, each edge via
`_add_edge`. Enforces the generate-ahead cap — at most **2** nodes not present in that
turn's `_room_drift` list survive; overflow is logged `[CARTO] dropped N speculative
nodes` and discarded. Nodes/edges are then live for this turn's step-1 graph block.

### 3.3 Adjacency-aware traversal (movement rewire)

`resolve_destination` (rpjot.py:2639) gains a leg **before** the hierarchical checks: if
the resolved destination is a direct member of `_locale_edges(from_path)`, return
`(destination, "adjacent")`. `_tool_navigate_to` (rpjot.py:3773) maps `"adjacent"` to a
1-hop `traversal = [from_loc, to_loc]` — you step through the door, not up to the parent and
back down.

`compute_traversal` (rpjot.py:2599) gains an edge-aware front path: if a **BFS over the
recorded `adj:` graph** connects `from_path` and `to_path`, return that shortest path
(tie-break by tree distance for determinism); otherwise fall back to today's exact
ascend/descend tree walk. `_tool_navigate_to` is otherwise unchanged — it still fetches
`intermediate_contexts` for each stop and emits the same follow-up narration instruction;
it simply receives a truer path.

**Backward compatibility (I8):** when a room has no `adj:` edges, `_locale_edges` is empty,
the BFS finds nothing, and both functions return byte-identical output to the pre-graph
engine. The graph *augments*; it never overrides a tree walk it cannot improve.

## 4. Invariants — the locale-graph contract

| ID | Invariant | Proven by (Tier 1 unless noted) |
|----|-----------|--------------------------------|
| I1 | **Bidirectional & idempotent edges.** `_add_edge(a,b)` ⇒ `b ∈ edges(a)` ∧ `a ∈ edges(b)`; re-adding writes nothing new. | `test_edge_bidirectional`, `test_edge_idempotent` |
| I2 | **Description/adjacency independence.** Adding an edge never drops a description; `save_location` never drops edges. | `test_edge_preserves_description`, `test_resave_preserves_edges` |
| I3 | **Cartographer never moves the session.** `speculate_locale`/`_consume_locale_seed` leave `session.location` untouched. | `test_cartographer_does_not_move_session` |
| I4 | **Bounded generate-ahead.** ≤2 nodes beyond that turn's mentioned set survive; overflow logged + dropped. | `test_generate_ahead_capped_at_two` |
| I5 | **Never-guess connectivity.** An edge is recorded only between two resolved (existing-or-just-created) nodes; never to an unresolved name. | `test_edge_requires_resolved_endpoints` |
| I6 | **Graceful degradation.** Endpoint down ⇒ no seed, no nodes/edges written; traversal falls back to the tree walk; nothing corrupts. | Tier 2 preflight + `test_no_seed_no_write` |
| I7 | **Idle worker stays zero-disk-write.** `speculate_locale` only stages `_locale_seed`; all writes happen in the foreground `_consume_locale_seed`. | `test_speculate_writes_nothing` |
| I8 | **Empty-graph traversal identity.** With no edges, `compute_traversal`/`resolve_destination` output equals the pre-graph engine exactly. | `test_traversal_identity_without_edges` |

> **Tripwire (documented limitation, not an invariant).** Generate-ahead can mint a
> plausible connective room the story never revisits (a corridor that stays a stub). It is
> bounded (I4), newest-wins-supersedable, and never wrong about *containment* — only
> speculative about *existence*. `test_generate_ahead_stub_is_inert` pins that a
> never-revisited speculative node adds no edges it was not given and never moves anyone.

## 5. Rollout — phased, readers before the writer, movement last

| Phase | Contents | Depends on | Ships alone? |
|-------|----------|------------|--------------|
| **0 — Substrate** | `TAG_ADJ`, `_edge_tag`/`_edge_decode`, `_locale_edges`/`_add_edge`, `_locale_graph_block` | nothing | yes |
| **1 — Detect + surface** | `_scan_room_drift` + `_ROOM_LEXICON`; inject `_locale_graph_block` into both step-1 shapes | Phase 0 | yes |
| **2 — Cartographer** | `_CARTOGRAPHER_SYSTEM`, `speculate_locale` (stage), `_consume_locale_seed` (commit), `_idle_worker` wiring | Phases 0+1 | no |
| **3 — Adjacency traversal** | `"adjacent"` nav_type, BFS `compute_traversal`, `resolve_destination` leg | Phase 0 (hard); 2 (soft — needs edges to matter) | yes (inert without edges) |

Phase 3 is safe to land any time after Phase 0 (it is a no-op until edges exist), but is
only *useful* once Phase 2 is populating the graph. Land 0→1→2 green before enabling 3 in
play.

## 6. Testing harness — three tiers

Tier 1 is the guarantee (zero LLM, every deterministic invariant). Tier 2 is acceptance
sampling of the model-dependent capture channel (the cartographer). Tier 3 tunes that
channel per model. **A green Tier 1 with a lagging Tier 3 is a working graph with a
lower capture rate — the designed degradation, not a failure.**

### 6.1 Tier 1 — `TestLocaleGraph` (deterministic, zero LLM)
Seeded-note class over `TMP_CATNOTE` + `_make_engine`. Covers: `_edge_tag`/`_edge_decode`
round-trip; `_add_edge` bidirectionality + idempotence (I1); description/edge independence
(I2); `_locale_edges` read; `_locale_graph_block` render; `_scan_room_drift` detection
(mention of an unmapped room fires; a known slug does not); `_consume_locale_seed` commit +
cap (I4) with a hand-built seed (no LLM); adjacency-aware traversal — 1-hop `adjacent`, BFS
shortest path, and tree-walk fallback / identity (I8); I3, I5, I7.

### 6.2 Tier 2 — `TestCartographerLive` (endpoint-gated acceptance)
The drawing-room-off-entryway scenario, minimal-pair discipline: MC in `manor/entryway`,
narrative names "the drawing room just off the entry hall" → run `speculate_locale` +
`_consume_locale_seed` → assert a `drawing-room` node exists with a sane parent, an
`entryway ↔ drawing-room` edge is present in `_locale_edges`, and ≤2 speculative nodes were
added. A negative pair (a room only *wished for*, not shown to exist) adds nothing. Skips
cleanly (xfail) without `openai_api_url`.

### 6.3 Tier 3 — `bakeoff_locale.py` (the sweep)
Per-model quality across a corpus of narrative snippets, reusing the `preflight`/`_capped`
scaffolding from `bakeoff_locacc.py`. Scores: **node precision** (no junk/duplicate rooms),
**mention recall** (every explicitly-named room canonicalized), **edge correctness** (the
described connection recorded, no phantom edges), **generate-ahead discipline** (≤2, and the
extras are genuinely connective). Preflight is mandatory (the endpoint-xfail gotcha:
a dead `:5000` silently fakes a green matrix).

## 7. Risks / limitations

- **Speculative stubs (I4 tripwire).** Bounded and supersedable; accepted.
- **Lexicon blind spots.** `_scan_room_drift` uses a curated `_ROOM_LEXICON`; an
  unlisted place-word (e.g. "the scriptorium") won't wake the cartographer that turn. It
  self-heals the next time the room is named after it becomes canonical via any path, and
  the lexicon is cheap to extend. Detection is a *gate*, not the authority — a missed gate
  costs one turn of lag, never a wrong node.
- **Edge staleness on re-description.** Edges and descriptions are independent notes; a
  room re-described in a way that *removes* a passage does not auto-prune the old edge
  (there is no `_remove_edge` in this pass). Pruning is deferred; edges are additive and
  newest-wins on the *set*, so a stale edge over-connects (a harmless extra 1-hop option)
  rather than mis-locating anything.
- **Graph/tree disagreement.** If the model records an edge that contradicts containment
  (a door between distant branches), traversal will honor it (BFS) — intended, but it means
  a bad edge can shortcut the map. Bounded by I5 (both endpoints must resolve) and visible
  in the `[LOCALE GRAPH]` block for audit.

## 8. Verification

- **Tier 1:** `pytest test_rpjot.py -k LocaleGraph`, then the umbrella
  `pytest test_rpjot.py -k "Locale or Location or Remark or Traversal"` (adjacency changes
  must not regress the existing traversal/remark suites).
- **Preflight discipline (before ANY live tier):** a dead `:5000` silently x-fails Tier 2
  and fake-greens the sweep — confirm a real completion first.
- **Tier 2:** `openai_api_url=… pytest test_rpjot.py -k CartographerLive`.
- **Tier 3:** `ROUNDS=3 python bakeoff_locale.py [models…]`.
- **Manual end-to-end:** start in `manor/entryway`; a turn's prose mentions the drawing
  room → next turn the step-1 `[LOCALE GRAPH]` block shows `drawing-room` and the
  `entryway ⇄ drawing-room` edge; `navigate_to drawing-room` resolves `nav_type="adjacent"`
  with a 1-hop traversal instead of routing up through `manor`.

## Critical files

| Anchor | File / symbol |
|--------|---------------|
| Edge substrate | `rpjot.py` `TAG_ADJ` (near :140), `_edge_tag`/`_edge_decode`, `_locale_edges`/`_add_edge`, `_locale_graph_block` (by `_rooms_vocab_block` :639) |
| Detection | `rpjot.py` `_scan_room_drift` + `_ROOM_LEXICON` (mirror `_scan_cast_drift` :2323) |
| Step-1 surface | `rpjot.py` `_build_baseline_context` :780, `_build_seeded_message` :723 |
| Cartographer | `rpjot.py` `_CARTOGRAPHER_SYSTEM`, `speculate_locale`, `_consume_locale_seed` (mirror `speculate_step1` :2230 / `_consume_seed` :2292); `play.py` `_idle_worker` :480 (after `speculate_step1` :497) |
| Traversal | `rpjot.py` `resolve_destination` :2639, `compute_traversal` :2599, `_tool_navigate_to` :3773 |
| Persistence reuse | `rpjot.py` `_tool_save_location` :4018, `_ensure_location_node` :2804 |
| Tests | `test_rpjot.py` `TestLocaleGraph`, `TestCartographerLive`; new `bakeoff_locale.py` |
| Deferral hand-off | `LOCATION_MARKING.md` §1 — repoint "room-graph / adjacency redesign" here |
