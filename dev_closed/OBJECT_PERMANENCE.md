# OBJECT_PERMANENCE — unification plan + the permanence contract

This doc unifies LOCATION_MARKING.md and OBJECT_TOOLING.md into one dependency-ordered
execution plan, and defines *accurate object permanence* as a formal, tested contract:
**multiple entries per object are history, not corruption; the engine deterministically
recalls the newest entry; a stale answer is possible, a wrong-place answer is not.**
The invariant table (§2) is the spine — every implementation phase (§3) exists to make
an invariant true, and every invariant names the test that proves it (§4).

## 1. Problem — two docs, one store, no proof

- Both docs are design-only; neither is implemented. Each has a §Tests list, but the
  *permanence property itself* — "entry A then entry B for the same object → recall B" —
  is tested nowhere today. Verified: no such test exists in test_rpjot.py, and no
  multi-turn scenario runner exists anywhere (play.py is a REPL;
  create_canonical_seed.py appends notes directly and never runs turns).
- The two designs share seams that neither owns: `place_object.room` routing through
  `_canonicalize_room` is an explicit TODO in OBJECT_TOOLING §3.4; `save_object`'s
  `session.location` default is only as precise as LOCATION_MARKING's
  `_remark_location` makes it; both add a vocabulary block to
  `_build_baseline_context`; both lean on `record_event` as the weak-model fallback
  channel. §3 resolves all four.
- The governing engine lesson (neg_navto bakeoff history): mention≠movement and
  mention≠presence are the same failure class; the shipped fix shape is a
  classification-gated nudge + positive fn-description (`nudge_pos_desc`), never a hard
  gate. This doc's harness applies that diagnostic to objects (§4.2/§4.3).

**Formal definition (repeated in §5):** *accurate object permanence = deterministic
recall of whatever was written (newest-wins, full history retained) +
stale-never-wrong-place. It is NOT deterministic capture of every narrative event — no
deterministic possession classifier exists ("object changed hands" is the
forced-movement class: 0% tool-fire on weak models, description prohibitions inert).*

## 2. The permanence contract — invariant table

Anything not in this table is not guaranteed. Test names are the actual methods to
implement in `TestObjectPermanence` (§4.1) unless otherwise attributed.

| ID | Invariant | Proven by |
|---|---|---|
| I1 | **History, not corruption.** N obj:-tagged notes for one slug are a valid timeline; every entry is retained; `get_object.timeline` renders all sightings newest→oldest (render sort rpjot.py:1863). | `test_timeline_all_entries_newest_first`, `test_restate_extends_history_not_forks` |
| I2 | **Newest-wins residence.** Residence = pwd of the newest **non-canonical** `obj:{slug}` note, parsed as: `/story/character/{h}/inventory` → held by h; `/story/location/{r}` → room r; `/story/events/{r}` → room r. | `test_residence_follows_newest_sighting`, `test_room_to_holder_transition` |
| I3 | **Canon never shadows, never shadowed.** Notes under `/story/object/` are excluded from the residence parse; the canonical description is read DIRECTORY-exact on its own pwd, never via the tag timeline. A canonical re-save *later* than a holder sighting must NOT move residence. | `test_canonical_resave_does_not_move_residence` (flagship), `test_canonical_description_survives_sightings` |
| I4 | **True-room stamping.** Object writes with no explicit room stamp `session.location`, which LOCATION_MARKING §3.1 commits pre-step-2 — so the stamp is the true room on stationary/led turns. | `test_save_object_defaults_to_session_location`; LOCATION_MARKING's KEY acceptance test (cross-referenced, not duplicated) |
| I5 | **Stale-never-wrong-place.** No sighting ⇒ no residence invented (`get_object` miss returns the known-slug roster, never a guess); residence changes only when a sighting is written. | `test_unknown_object_no_invented_residence`, `test_residence_static_without_new_notes` |
| I6 | **Same-second tie-break = later-in-file.** Equal `Note.now` resolves by append order (`>=` scan), never by unstable sort. | `test_equal_now_later_in_file_wins` |
| I7 | **Authoritative correction layer.** `[OBJECTS HERE]` and the registry reflect newest residence *even while stale sighting notes still render* inside the room's ContextBundle blob (append-only store, no deletion). | `test_objects_here_excludes_moved_object` (asserts BOTH: block excludes it AND the stale note is still in the room blob) |
| I8 | **Fallback-channel parity.** An obj:-tagged `record_event` note at `/story/events/{room}` is a first-class room sighting — residence tracking degrades to the one tool weak models reliably fire, instead of dying. | `test_event_tag_is_room_sighting` |
| I9 | **Identity stability.** Name variants ("the iron key", "Iron Key", "iron-key") canonicalize to one flat slug; history never forks on spelling. | `test_canonicalize_variants_one_slug` (absorbs OBJECT_TOOLING §5's precedence tests) |
| I10 | **Legacy continuity.** Pre-migration `obj:` notes (old write-only `save_object`) are valid genesis sightings: timeline includes them, residence parses their room, description falls back to the newest sighting message. | `test_legacy_note_is_genesis_sighting` |

> **Tripwire (documented limitation, not an invariant).** An obj:-tagged event that
> merely *discusses* an object moves its parsed residence — mention-pollution through
> I8's channel, the mention≠presence twin of neg_navto.
> `test_mention_pollution_tripwire` asserts the *current* behavior so any future change
> is deliberate. Damage bound: a wrong **recent** sighting, corrected by the next real
> one — I5 still holds (residence is never invented, only mis-evidenced).

## 3. Unification — seams and execution order

### 3.1 The four seams

| Seam | Owner today | Resolution |
|---|---|---|
| `place_object.room` → `_canonicalize_room` (TODO, OBJECT_TOOLING §3.4) | neither doc | `_canonicalize_room` is Phase-0 substrate; `place_object` (Phase 3) ships **with** canonicalization on day one. The TODO dies here. |
| `save_object` location default inherits `_remark_location` precision (OBJECT_TOOLING §3.3 "synergy") | soft coupling | Made a **hard ordering**: Phase 2 (location precision) lands before Phase 3 (object writers), so no object sighting is ever stamped with a coarse room. Rationale §3.2. |
| Both vocabulary blocks in `_build_baseline_context` (rpjot.py:554-595) | conflict-free but unbudgeted | Fixed order: shared lore → `[ROOMS KNOWN HERE]` → character profiles → `[OBJECTS HERE]`. Slugs only, one line per category; combined cap ~30 slugs / ~150 tok, **measured when both land** (end of Phase 3). |
| `record_event` as shared fallback (location stamp via `session.location` default rpjot.py:2110-2113; object sightings via obj: tags) | both docs touch it | Single edit, Phase 3: tags-param description gets the "objects **handled**, not mentioned" wording (OBJECT_TOOLING §3.6) and the handler drops the `obj_registry` cache. The location default needs no edit — it inherits Phase-2 precision automatically. |

### 3.2 Execution order — readers before writers; location precision before object writers

Two arguments, both load-bearing:

1. **Permanence is a property of readers over an append-only store.** Every §2
   invariant is provable from (notefile, reader functions) alone — writers only improve
   *capture rate*, the model-dependent channel Tier 1 explicitly does not depend on.
   Migration being a non-event (OBJECT_TOOLING §4) means the read side is useful over
   existing data with zero write-side change.
2. **Imprecision is forever on an append-only store.** Shipping
   `place_object`/`save_object` before `_remark_location` would mint sightings stamped
   with coarse/stale rooms that can never be deleted, only superseded. History is a
   feature only if it is *accurate* history — writers land after the room key is
   precise.

| Phase | Contents | Depends on | Ships alone? |
|---|---|---|---|
| **0 — Substrate** | `TAG_OBJ`/`PWD_OBJECTS` constants (rpjot.py:134-154); `resolve_destination` G4 fix + `known_roots` (LM §3.3, incl. the test_rpjot.py:835-837 split); `_canonicalize_room` (LM §3.2); `_child_room_slugs` + TREE boundary filter (LM §3.4/§3.6); `_ensure_location_node` (LM §3.5); `_canonicalize_object` (OT §3.2) | nothing | yes — all invisible or strictly safer |
| **1 — Object read side + Tier 1** | `_object_registry` + residence parser + tie-break (OT §3.5); `_tool_get_object`; `[OBJECTS HERE]` (OT §3.6); `examine_location.objects_here`; play.py `/objects` wiring; the full `TestObjectPermanence` suite (§4.1) — I1–I3 and I5–I10 all provable here over seeded notes | Phase 0 | yes — independent of Phase 2 |
| **2 — Location precision** | `_remark_location` + step-1 `CURRENT ROOM:` line (LM §3.1); `[ROOMS KNOWN HERE]` (LM §3.4); `gather_location_events` (LM §3.6); LM §4 tests | Phase 0 | yes — independent of Phase 1 |
| **3 — Object write side** | `save_object` revision (OT §3.3); `place_object` (OT §3.4, room param through `_canonicalize_room`); `record_event` tags edit + registry cache-drop; compaction keep-lists + **budget re-measure** (OT §3.7, projected 2,782/3,000); the I4 test | Phases 0+2 (hard); 1 (for the registry cache-drop) | no — the ordering constraint lives here |
| **4 — Live harness** | Tier-2 class + `_LLM_CLASSES` registration (conftest.py:24); `bakeoff_objperm.py`; ship/hold decision on the `place_object` fn-desc one-liner per the sweep (§4.3) | Phase 3 | n/a — measurement, not feature |

**Phases 1 and 2 have no dependency on each other** — either order, or parallel
branches. Phase 0 can ship silently at any time. Only Phase 3 is order-constrained.

## 4. Testing harness — three tiers

Tier 1 is the guarantee (zero LLM, every invariant). Tier 2 is acceptance sampling of
the model-dependent capture channel. Tier 3 is the sweep that tunes that channel per
model. **A green Tier 1 with a red Tier 3 is a working system with a lagging capture
rate — that is the designed degradation, not a failure.**

### 4.1 Tier 1 — `TestObjectPermanence` (deterministic, zero LLM)

Modeled on `TestKnowledgeGapScenario` (test_rpjot.py:2632-2800): docstring narrates the
world; class constants hold fixture data; `Note.NOTEFILE = TMP_CATNOTE` + empty-file
creation in setUp; teardown removes it. **One deliberate extension of the house
pattern:** assertions are needed *at each step* of the timeline (KnowledgeGap asserts
only the final state), so seeding moves from setUp into a class-level `PHASES` list of
`(now, jot_kwargs)` tuples plus a `_seed_through(k)` helper that appends phases 0..k —
each test seeds exactly the history it asserts against. `Note.jot(..., now=...)`
(catjot.py:436, `now or int(time())`) makes the timeline exact.

The main scenario:

```
SLUG   = "tarnished-locket"
ROOM_A = "ravenwood-manor/foyer"
ROOM_B = "ravenwood-manor/secret-garden"
HOLDER = "evie"
T      = 1_750_000_000          # base epoch; phases step by 100

PHASES = [
  # P0 t=T+0    genesis (save_object dual-write shape)
  (T+0,   dict(message="A tarnished silver locket, clasp worn, holding a faded portrait.",
               tag="obj:tarnished-locket", context="object canon: tarnished-locket",
               pwd="/story/object/tarnished-locket")),
  (T+0,   dict(message="A tarnished silver locket, clasp worn, holding a faded portrait.",
               tag="obj:tarnished-locket",
               context="object sighting: tarnished-locket at ravenwood-manor/foyer",
               pwd="/story/location/ravenwood-manor/foyer")),
  # P1 t=T+100  restate in place (place_object, no destination)
  (T+100, dict(message="tarnished-locket is here.", tag="obj:tarnished-locket",
               pwd="/story/location/ravenwood-manor/foyer")),
  # P2 t=T+200  moved to room B
  (T+200, dict(message="The locket lies on the garden bench.", tag="obj:tarnished-locket",
               pwd="/story/location/ravenwood-manor/secret-garden")),
  # P3 t=T+300  picked up by NPC (possession is a residence)
  (T+300, dict(message="Evie pockets the locket.", tag="obj:tarnished-locket",
               pwd="/story/character/evie/inventory")),
  # P4 t=T+400  state change files AT the current residence
  (T+400, dict(message="The locket is now cracked across the portrait glass.",
               tag="obj:tarnished-locket", pwd="/story/character/evie/inventory")),
  # P5 t=T+500  canonical re-save — the GLOBALLY NEWEST note
  (T+500, dict(message="A tarnished silver locket, now cracked across the portrait glass.",
               tag="obj:tarnished-locket", pwd="/story/object/tarnished-locket")),
  # P6 t=T+600  mention-pollution tripwire (event that only DISCUSSES it)
  (T+600, dict(message="Evie talks about the locket she lost years ago.",
               tag="exp:evie obj:tarnished-locket",
               pwd="/story/events/ravenwood-manor/foyer")),
]
```

Per-step assertions (each a test method; invariant IDs in parentheses):

- `_seed_through(P0)`: residence == room `ravenwood-manor/foyer`; `get_object`
  canonical_description contains "faded portrait"; `[OBJECTS HERE]` at the foyer lists
  `tarnished-locket`. (I2)
- `_seed_through(P1)`: residence unchanged; timeline has 2 non-canonical entries,
  newest first. (I1)
- `_seed_through(P2)`: residence == `secret-garden`; `[OBJECTS HERE]` at the foyer
  **excludes** it while the foyer ContextBundle blob still contains both stale notes;
  the secret-garden block lists it. (I2, I7)
- `_seed_through(P3)`: residence == `{"held_by": "evie"}`; `[OBJECTS HERE]` shows
  `held — evie: tarnished-locket`; `gather_pov_context("evie")` does NOT surface the
  inventory note — the profile-pollution guard (OT §3.1). (I2)
- `_seed_through(P4)`: residence still evie; timeline's newest entry is the "cracked"
  line while canon still says "clasp worn" — graceful degradation without a canon
  update. (I1, I3)
- `_seed_through(P5)`: **residence STILL evie** despite the canonical note being the
  globally newest — the flagship I3 assertion; canonical_description is now the P5 text
  (newest-first at the canonical pwd).
- `_seed_through(P6)`: residence becomes room `foyer` — assert this *current* behavior
  as the tripwire, with a comment linking to §2's caveat block.

Side fixtures (separate slugs, separate tests):

- `iron-key` — two sightings, both `now=T+50`, appended foyer-then-garden → residence
  == garden. (I6, later-in-file)
- `sealed-letter` — only ever an obj:-tagged note at
  `pwd=/story/events/ravenwood-manor/foyer` → residence == foyer. (I8)
- `silver-mirror` — one legacy old-style note (description at a room pwd, no canonical
  node) → `get_object` returns description + room; a subsequent `place_object` to a
  holder creates the canonical node. (I10, migration)
- `phantom-dagger` — zero notes → roster miss, no residence. (I5)
- I4 tests the engine directly (the existing save_object test pattern,
  test_rpjot.py:651-666): set `session.location`, call the handler with no location,
  assert the sighting pwd.

**Multi-turn scenario runner: not built (decided).** The store is turn-agnostic —
every §2 invariant is a property of the notefile plus reader functions, and
`Note.jot(now=...)` produces exact timelines a live multi-turn run could only
approximate nondeterministically. Turn *mechanics* (that `session.location` commits
before step-2 writes) are covered single-turn with a stubbed `world_doc` by
LOCATION_MARKING's KEY acceptance test. What a runner would add is model-dependent
event capture — which is exactly Tiers 2/3, in single-turn production-shape calls (the
`TestProductionActivation` precedent). A runner would be a third copy of that
measurement with worse determinism.

### 4.2 Tier 2 — `TestObjectActivationLive` (endpoint-gated acceptance)

New class, **added to `_LLM_CLASSES` (conftest.py:24) — a required implementation
step**: unregistered LLM classes fail hard when the endpoint is absent instead of
skipping. Reuses the `TestProductionActivation` shape: `_step2_tools_for` routed
through `ComplianceStep._compose_step2_user_content` (test_rpjot.py:1979-2003), N-of-M
sampling via `_passes(3, need=2)` (test_rpjot.py:1963). One extension: the helper must
also return tool-call **arguments**, because the fallback assertion inspects
`record_event.tags` for an `obj:` word.

Tests — including the **object minimal pair**, the neg_navto diagnostic applied to
mention≠presence (twin phrases share the object noun with opposite ground truth):

- POSITIVE: `"Evie pockets the locket."` → `place_object` fires (2-of-3).
- NEGATIVE twin: `"Evie talks about the locket she lost."` → `place_object` must NOT
  fire (2-of-3).
- Handover: `"[MC action]: I press the iron key into Evie's palm."` → `place_object`.
- Genesis: an examine-and-describe phrase → `save_object`.
- Fallback: `"The guards confiscate my satchel and toss it into the cell corner."` →
  pass iff `place_object` fired OR `record_event` fired with an `obj:` tag word —
  asserting the layered-mitigation contract, not a single tool.

Discipline: conftest.py:46-62 skips the class when `openai_api_url` is unset;
conftest.py:86-124 converts 5xx/ConnectionError/Timeout to xfail-skip. **The known
gotcha: a dead or half-up `:5000` silently x-fails these tests, and an
HTTP-200-with-malformed-body produces a KeyError that is NOT converted — always run the
§6 preflight before trusting a green or a skip.**

### 4.3 Tier 3 — `bakeoff_objperm.py` (the sweep)

Mirrors `bakeoff_navnudge.py` structurally: preflight reused verbatim (navnudge
200-229), `ROUNDS`/`CALL_TIMEOUT` envs, per-bucket scorecard, `bakeoff_models.sh`-style
sweep via `openai_api_model`, `FORCE=1` bypass. One deliberate simplification: **no
`CLASSIFIER` knob** — the arms here are schema/context variants applied
unconditionally, not injections gated on a per-turn classification.

**Arms — a 2×2 factorial, four arms:**

| arm | `place_object` fn-desc one-liner (OT §3.7 text) | `[OBJECTS HERE]` block in context |
|---|---|---|
| `baseline` | no | no |
| `fn_desc` | yes (local schema copy, the `_compact_schemas_with_nav_desc` pattern, navnudge 157-166) | no |
| `objects_here` | no | yes |
| `fn_desc+oh` | yes | yes — the proposed production config |

This answers both open questions at once: does the `nudge_pos_desc` precedent transfer
to `place_object`, and does the vocabulary block have a tool-selection side-effect
beyond its naming purpose.

**Corpus buckets** (ground-truth field `expects_place`; every positive bucket's noun
reappears in a mention-negative twin — the minimal-pair discipline):

| bucket | example | expects_place | correct iff |
|---|---|---|---|
| pickup | "Evie plucks the locket from the bench and pockets it." | True | place_object fired |
| handover | "[MC action]: I press the iron key into Evie's palm." | True | place_object fired |
| drop | "[MC action]: I leave the lantern by the garden gate." | True | place_object fired |
| state-change | "[MC action]: I wind the music box until the spring snaps." | True | place_object fired |
| mention-negative | "Evie talks about the locket she lost." / "[MC speaks aloud]: 'Bring the iron key tomorrow.'" | False | place_object did NOT fire |
| fallback (weak-model) | "The guards confiscate my satchel and drag me to the cells." | True | place_object OR record_event-with-obj:-tag |

**Decision rule:** ship the fn-desc one-liner iff it wins mention-negative without
losing pickup/handover — the exact bar `nudge_pos_desc` cleared for `navigate_to`;
scope = `place_object` only. `[OBJECTS HERE]` ships regardless (its primary job is I9
vocabulary, Phase 1); the sweep only measures its selection side-effect. Tier 3
measures the channel Tier 1 does not depend on — a poor sweep score lowers capture
rate, never correctness.

## 5. Risks / honest ceiling (unified)

- **The formal ceiling, restated.** Accurate object permanence = deterministic recall
  of whatever was written + stale-never-wrong-place. It is not, and cannot be,
  deterministic capture of every narrative event: "object changed hands" is the
  forced-movement class (0% weak-model tool-fire; description prohibitions inert; hard
  gates rejected — they break forced movement). On weak models, residence lags reality
  until any obj:-tagged write occurs. Unlike location, there is no
  `_remark_location`-style deterministic leg for objects.
- **Mention-pollution** through the I8 channel: accepted, tripwired
  (`test_mention_pollution_tripwire`), bounded by I5 — a wrong recent sighting,
  corrected by the next real one, never an invented residence.
- **Inherited from LOCATION_MARKING:** the one-turn lore lag and the transition-turn
  filing lag apply to I4's precision — an object sighting written on a
  prose-materialized led-move turn files under the departure room and self-heals next
  turn.
- **Stale renders:** moved objects still appear in room blobs (append-only store, no
  deletion); `[OBJECTS HERE]` is the authoritative layer (I7); prose re-materialization
  remains possible.
- **Budget:** compact step-2 projected 2,782/3,000 — re-measure at Phase 3; combined
  baseline-context vocabulary blocks capped per §3.1.
- **Cache coherence:** `record_event` must drop `obj_registry`, or I8 sightings go
  invisible until an unrelated invalidation — called out because it is the one
  cross-phase wiring that is easy to forget.

## 6. Verification

- Tier 1: `pytest test_rpjot.py -k ObjectPermanence`, then the umbrella
  `pytest test_rpjot.py -k "Object or Location or Compact or Registry or Canonical or Remark"`.
- Budget regression: `pytest test_rpjot.py -k Compact` (test_rpjot.py:1766/1772/1781
  all green).
- **Preflight discipline (before ANY live tier):** a dead `:5000` silently x-fails
  Tier 2 and fake-greens nothing visibly; an HTTP-200 malformed body KeyErrors. Run the
  bakeoff preflight (validates `choices[0].message` — navnudge 200-229 / denoise
  55-93) or its curl equivalent first; `FORCE=1` only knowingly.
- Tier 2: `openai_api_url=... pytest test_rpjot.py -k ObjectActivationLive`.
- Tier 3: `ROUNDS=3 python bakeoff_objperm.py [models...]`; full sweep via the
  `bakeoff_models.sh` pattern; per-model pytest matrix via the `bakeoff_denoise.py`
  subprocess/junitxml pattern if needed.
- Invariant audit: every §2 row's test names exist and pass — the `def test_` delta in
  test_rpjot.py matches the table.

## Critical files

- `LOCATION_MARKING.md` / `OBJECT_TOOLING.md` — the two source designs this doc
  sequences; all mechanism detail lives there, all guarantees live here.
- `rpjot.py` — merged anchors from both docs' critical-files sections; plus
  `_build_baseline_context` 554-595 (block ordering, seam 3) and `record_event`
  2066-2128 (seam 4).
- `catjot.py` — `Note.jot` `now` param 436 (timeline control); SearchType 730-733; tag
  word-match 749/783.
- `test_rpjot.py` — `TestKnowledgeGapScenario` 2632 (Tier-1 ancestor);
  `TestProductionActivation` helpers 1963-2003 (Tier-2 ancestor); compaction suite
  1740-1793; `_make_engine` 42; save_object tests 651-666.
- `conftest.py` — `_LLM_CLASSES` 24 (Tier-2 registration); xfail conversion 86-124.
- `bakeoff_navnudge.py` — Tier-3 template (preflight 200-229, schema-variant pattern
  157-166); `bakeoff_models.sh` / `bakeoff_denoise.py` — sweep mechanics.
