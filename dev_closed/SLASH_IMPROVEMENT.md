# SLASH_IMPROVEMENT — dense, low-noise introspection commands

Design note for turning the in-game slash commands from note-dumps and scene-wide
token tables into **subject-focused recall pages**. The engine already *logs* how it
assembles context (`[CTX]`/`[SEED]`/`[TIMING]`) and quantifies token spend
(`scene_debug_report`, rpjot.py:6546); what the player cannot yet do is ask one
question about one entity and get a deterministic, information-dense answer:

> Of everything the story has canonicalized about *this* person / place / object, how
> much is actually **present**, how much is currently **used** (fed to the LLM), and
> **how recent** is it?

This note specifies a new `/construct <name>` command that answers exactly that, plus
a noise-reduction pass on the existing `/location`, `/people`, `/stats`.

## 1. Problem

The introspection commands (`play.py:825-918`) fall into two failure modes, neither
of which lets the player judge memory sufficiency:

- **Note-dumps (G1).** `/location` and `/people` render full note bodies via
  `ContextBundle.__str__` (`query_location_context` / `query_people_context`,
  play.py:146-168). Output scales with the canon and drowns the reader; there is no
  count, no proportion, no recency.
- **Scene-wide + token-centric (G2).** `/stats` (`scene_debug_report` +
  `history_report`, rpjot.py:6546/6768) is comprehensive but oriented around the
  whole scene's token budget. It answers "how many tokens is the scene spending",
  not "how much of what we know about *Bartholomew* is actually in play right now".
- **No present-vs-used ratio (G3).** Nothing exposes the gap between the *canon*
  (every note ever written about a subject) and the *active* subset (the notes that
  survived POV selection and truncation into the turn the LLM actually saw). Without
  that ratio the player cannot tell whether a thin, generic reply is the model's
  fault or a starved context.
- **No faithful "as-fed" record (G4).** The engine retains only
  `_last_payload_toks` (a token count, rpjot.py:1429) and `_turn_refs` (a
  citation-captured proxy, capped 3 per lookup / 6 per note, rpjot.py:1439). Neither
  is the set of notes actually fed last turn, so "used" cannot be measured today.

**Out of scope:** any change to what context the engine *selects* or *feeds* (this is
a read-only lens over existing behavior); LLM-generated summaries (every page here is
non-LLM and deterministic); a room-graph or object-model redesign.

## 2. Decisions

- **One subject-focused page.** `/construct <name>` resolves a single person / place /
  object and prints a compact fixed-width page — non-LLM, truncation-safe, same
  string-returning contract as `scene_debug_report`.
- **Present vs used, both ways, side by side.** The headline block reports canon
  total against **two** active counts — *as fed last turn* and *fresh POV recompute* —
  each as an absolute and a proportion. Showing both makes drift visible: a large gap
  means selection/truncation is dropping canon the player expected to be live.
- **Faithful as-fed capture.** Add `self._last_ctx_now: set[int]` populated during
  context assembly with the `now` of every note included in the turn's context. The
  as-fed count is the intersection of that set with the subject's census. This is the
  only new engine state; it is additive and changes no selection behavior.
- **Recency is first-class.** Every count is paired with recency derived from
  `note.now` — newest/oldest entry, and per-witness last-seen — expressed as
  "N turns ago" plus wall-clock age.
- **Noise reduction by default, full on demand.** `/location`, `/people`, `/stats`
  default to dense summaries; today's full output moves behind an explicit `full`
  sub-arg so nothing is lost.

## 3. Design

All engine code in `rpjot.py`; dispatch in `play.py`. This is mostly *aggregation
over data the engine already exposes* — reuse is called out per piece.

### 3.1 `RpjotEngine.construct_report(name) -> str`
Returns a preformatted multi-section string (contract identical to
`scene_debug_report`, rpjot.py:6546). Four sections, described 3.3–3.6.

### 3.2 Name resolution — object → place → person precedence
The same never-guess canonicalizers the engine already trusts:

1. **Object.** `_canonicalize_object(name)` (rpjot.py:2878); accept if the slug is a
   key in `_object_registry()` (rpjot.py:2832) or has a canonical node.
2. **Place.** `_canonicalize_room(name, session.location)` (rpjot.py:2696); accept if
   the resulting `/story/location/{path}` node exists.
3. **Person.** lower/slug; accept if any `char:{name}` note exists.
4. **Miss.** Print `[no subject matching '<name>']` plus a nearest-known roster line,
   mirroring the `/objects` miss path (play.py:179-181). Never guess a subject.

### 3.3 Section 1 — header + presence/residence (one line)
```
CONSTRUCT: bartholomew   (person · in scene)
CONSTRUCT: library       (place · current room's sibling)
CONSTRUCT: iron-key      (object · held by evie)
```
Residence for objects from `_parse_residence` (rpjot.py:2808) via the registry;
presence for people from `session.people_present`.

### 3.4 Section 2 — RECALL CENSUS (the headline block)
```
RECALL CENSUS
  canon entries (total)                 190
  active — as fed last turn              22   11.6%
  active — fresh POV recompute           27   14.2%
  newest entry                     3 turns ago   (age: 12m)
  oldest entry                    41 turns ago
```
- **total** — union of the subject's census sources (3.7), deduplicated by note
  identity (`now` + file order) so a note carrying multiple matching tags counts once
  here (unlike `scene_debug_report`, which counts per category by design,
  rpjot.py:6551-6553).
- **as fed last turn** — `|census ∩ self._last_ctx_now|`. Percentage is of total.
  Before turn 1 (`_last_ctx_now` empty) print `—  (no turn yet)`.
- **fresh POV recompute** — re-run selection now and count survivors of selection +
  `render_context` truncation (rpjot.py:3256). For a person this is
  `gather_pov_context(name)` (rpjot.py:3078); for a place the
  `query_location_context` gather; for an object the registry/timeline gather.
  Deterministic across repeated calls (no LLM, no clock-dependent selection).
- **recency** — newest/oldest from `note.now`, via the `_newest_by_now` idiom
  (rpjot.py:2916), rendered "N turns ago" (3.8) + wall-clock age.

### 3.5 Section 3 — WITNESSED BY (ASCII bars + recency)
Count `exp:{char}` co-occurrences across the subject's census notes; one bar per
character scaled to a fixed inner width, with a last-seen column from the max
`note.now` of that witness's co-occurring notes:
```
WITNESSED BY   (exp: co-occurrence)
  evie          ████████████  12   · last 3 turns ago
  bartholomew   ████           4   · last 9 turns ago
  mara          ██             2   · last 22 turns ago
```
Sorted by count desc. `exp:` word matching is exact-word (catjot.py:749), same as the
POV gather, so counts reflect real witness tags rather than substrings.

### 3.6 Section 4 — subject-type detail
- **Person.** yomi count + freshest yomi `now` (`_tool_get_yomi`, rpjot.py:4886);
  per-partner relationship arcs — `rel:*` kind counts and the current (newest) arc
  value via `_tool_get_relationship_arc` / `_rel_key` (rpjot.py:6224); and the
  profile / conscience / know: / exp: note+token four-row block lifted from
  `scene_debug_report` (rpjot.py:6647-6650).
- **Place.** ancestor/descendant hierarchy depth; description-note vs `obj:` split
  (the split `scene_debug_report` already computes, rpjot.py:6609-6621); event count;
  `[ROOMS KNOWN HERE]` children + siblings (`_child_room_slugs` /
  `_sibling_room_slugs`).
- **Object.** canonical description present?; residence; sighting-timeline depth;
  canonical-vs-sighting note split (canonical never shadows, OBJECT_TOOLING §3.1).

### 3.7 Census sources per subject type
Union of `ContextBundle` queries (deduplicated for the total; per-source for the
category rows):
- **object:** `/story/object/{slug}` (canonical) + every `obj:{slug}` sighting.
- **place:** `/story/location/{path}` subtree + `/story/events/{path}` + `loc:{path}`.
- **person:** `/story/character/{name}`, `/story/conscience/{name}`,
  `/story/interior/{name}`, `know:{name}`, `exp:{name}`, `/yomi/{name}`, and the
  `rel:` pair notes.

### 3.8 Recency → "N turns ago"
Map `note.now` to a turn count. Prefer an existing turn↔timestamp source if one
exists; otherwise convert the wall-clock delta and fall back to `self._turn_count`,
labeling any approximation honestly rather than implying turn-exactness the data
can't support.

### 3.9 Shared formatting
Lift `_row` / `_note_toks` / `HDR` / `SEP` out of `scene_debug_report`
(rpjot.py:6560-6595) to module-level helpers so `construct_report` and the debug
report render identically. No visual divergence between the two surfaces.

## 4. Dispatch + noise reduction (`play.py`)

- **`/construct <name>`** — add `"/construct"` to `_SLASH_PREFIX` (play.py, takes a
  name arg like `/yomi`/`/objects`); handler prints `engine.construct_report(name)`,
  usage line when bare; list it in `_HELP_TEXT` (play.py:90-98).
- **`/location full`, `/people full`** — default to a census-style summary (counts +
  newest one-line snippet per entity) via a shared `_summarize_bundle()` helper;
  `full` preserves today's full `ContextBundle` dump verbatim (current
  `query_*_context` bodies, play.py:146-168).
- **`/stats full`** — default shows top-line budget + per-character totals; the long
  sub-sections (OTHER KNOWN CHARACTERS, per-note breakdowns) move behind `full`.
- `full` is a suffix on existing prefixes, so the unknown-command guard
  (`_SLASH_EXACT` / `_SLASH_PREFIX`, play.py:911-918) needs only minor adjustment.

## 5. Invariants

- **V1 — non-LLM.** No section calls the model; every number is deterministic and
  reproducible across repeated calls in the same state.
- **V2 — as-fed ⊆ census.** The as-fed count never exceeds total; it is a strict set
  intersection with `_last_ctx_now`.
- **V3 — read-only.** `construct_report` writes no notes and mutates no session
  state; `_last_ctx_now` is populated only by the assembly path, not by the lens.
- **V4 — never-guess.** Resolution reuses the existing canonicalizers; a miss reports
  the nearest roster and resolves nothing.
- **V5 — lossless trim.** `full` reproduces today's output byte-for-byte; the default
  only summarizes, never invents.
- **V6 — dedup honesty.** The census total dedups by note identity; category rows may
  double-count (a note with two `exp:` tags), and that asymmetry is stated in-page.

## 6. Verification

1. **Offline census math (no :5000).** Seed a scratch notefile with a known character
   (profile + several `exp:`/`know:`/`yomi:` notes across distinct `now` values) and
   an object (canonical + sightings); call `construct_report` directly and assert
   total, witness bar counts, and newest/oldest against hand-computed values.
2. **As-fed correctness.** Run 2–3 turns (mock/replay LLM if :5000 down), then
   `/construct <in-scene subject>`; assert as-fed ≤ total and equals
   `census ∩ _last_ctx_now`; assert fresh-recompute is stable across repeats.
3. **Noise reduction.** `/location` vs `/location full` (and `/people`, `/stats`):
   default materially shorter; `full` byte-identical to today.
4. **Miss path.** `/construct <nonsense>` prints not-found + roster, no traceback.
5. **Regression.** Full suite stays green (~449 passing); add focused tests for
   census dedup and witness-bar scaling.

## 7. Status

Designed 2026-07-05 (doc only). Not yet implemented. Anchored to the existing
introspection surface (`scene_debug_report`, `ContextBundle`, the object/location
canonicalizers) and OBJECT_PERMANENCE's residence model; the only new engine state is
the additive `_last_ctx_now` capture.
