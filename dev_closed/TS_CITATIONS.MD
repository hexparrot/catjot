# TS_CITATIONS — direct entry citation (`ref:` tags) + the citation contract

This doc extends the first-class-entity discipline of OBJECT_TOOLING.md /
OBJECT_PERMANENCE.md from objects and locations to **individual note entries**: when
the engine has already positively cross-referenced a prior entry during a turn, the
derived notes written that turn carry a durable pointer to it — the tag word
`ref:{now}` — instead of paraphrasing it forever. **A citation is retrieval
provenance, engine-stamped, never model-emitted; it dereferences through machinery
that already exists; and a notefile with no refs behaves identically to today.**
The invariant table (§2) is the spine; every phase (§4) exists to make an invariant
true, and every invariant names the test that proves it (§5).

> **Anchors.** All rpjot.py line anchors reference the `object_permanence` branch
> (commits 1e8d3ec..f29c42f — Phases 0–4 landed, Tier 1 green). catjot.py is
> identical on both branches (verified: `git diff now_coworked object_permanence --
> catjot.py` is empty). Implementation branches from `object_permanence` after merge.

## 1. Problem — the engine does the work, then throws the pointer away

- Every turn, step-1 lookup tools (`get_character` rpjot.py:3217, `search_world`
  :3429, `get_object` :3258, …) select specific prior notes, render them, and hand
  the text to the model. That selection **is** a positive cross-reference — the
  strongest relevance signal the system produces. Then it is discarded: step-2
  writes (`record_event` :2651, `record_knowledge` :3496, the thirteen `rel:`
  writers :4111–4673) re-describe the source in fresh prose, and the next session
  re-derives the connection from scratch. Derived notes are synthesized, then
  re-synthesized, and the chain of custody from a bond note back to the betrayal
  event it encodes exists nowhere on disk.
- The substrate already contains everything a citation needs, unused:
  - Tags are space-separated exact-word-matched (`s_text in inst.tag.split()`,
    catjot.py:749) — `ref:1750000100` is a legal tag word today, exactly as
    `obj:iron-key` is.
  - `ContextBundle.__iadd__` (catjot.py:911) routes a bare `int` term to
    `SearchType.TIMESTAMP` (exact `now` match, catjot.py:747) — **dereference
    already exists**; `_regen_notes` (catjot.py:1021) dedupes on reload.
  - Backlinks are a TAG search for the literal word `ref:{T}` — zero new machinery.
- One premise must be corrected before it hardens into a bug: **`now` is not
  unique.** Same-second collisions are real enough that OBJECT_PERMANENCE I6
  defines a later-in-file tie-break, and `Note.delete(src, timestamp)`
  (catjot.py:471) affects *every* note sharing the second. `ref:T` is therefore a
  multi-resolving key by contract (C3), never assumed singular.
- The governing engine lesson applies unchanged (neg_navto: 0/18 with a prose
  caveat; nudge_pos_desc shipped instead): schema prose is inert on weak models,
  and the compact step-2 budget sits at 2,792/3,000 tok. Any design that asks the
  model to emit citations inherits both problems. This design asks it nothing.

**Formal definition (repeated in §7):** *a citation is engine-stamped retrieval
provenance — "a targeted step-1 lookup this turn surfaced this entry among its
top-K results" — NOT semantic quotation. It proves the entry was on the model's
desk, not that the model used it.*

## 2. The citation contract — invariant table

Anything not in this table is not guaranteed. Test names are the actual methods to
implement in `TestEntryCitation` (§5.1) unless otherwise attributed.

| ID | Invariant | Proven by |
|---|---|---|
| C1 | **Form.** A citation is the tag word `ref:{now}` — the cited note's epoch int, verbatim, no ordinal, no padding. Citations live only in the tag field, never in pwd/context/message. | `test_parse_refs_round_trip`, `test_garbage_ref_words_skipped` (`ref:`, `ref:abc` ignored) |
| C2 | **Provenance semantics.** Citations are engine-stamped only. `ref:T` on note N asserts: N was written in a turn whose *targeted* step-1 lookups surfaced a note with `now == T` among their top-K results. No tool schema exposes, accepts, or describes citations; the model never sees the mechanism. | `test_no_ref_in_any_schema` (asserts `"ref:"` absent from step1/step2/compact schema JSON), `test_ambient_tools_capture_nothing` |
| C3 | **Multi-resolution.** `ref:T` dereferences via TIMESTAMP match and may resolve to every note sharing second T. Readers tolerate multi-resolution; single-winner contexts use the existing I6 later-in-file tie-break (`_newest_by_now`, rpjot.py:2116). | `test_same_second_ref_derefs_all` |
| C4 | **Scope.** Only derived-narrative writers stamp: `record_event`, `record_knowledge` (both notes it writes), the `rel:`/`int:` family (`record_bond`…`record_longing`, rpjot.py:4111–4673), `record_conscience`, `save_yomi`. Never: canonical writers (`save_character` :2949, `save_location` :3007, `save_object` :3057), `place_object` sightings (:3135 — a sighting's meaning is its pwd/now, per OBJECT_TOOLING), structural tools (`navigate_to`, `set_people_present`, `begin_scene`), `/summaries`. | `test_canonical_and_structural_writers_stamp_nothing`, `test_record_event_stamps`, `test_record_knowledge_stamps_both_notes` |
| C5 | **Bounds + self-citation guard.** ≤ `_REF_CAP_PER_LOOKUP`(3) refs captured per lookup, newest-first; ≤ `_REF_CAP_PER_NOTE`(6) ref words stamped per note; any ref with `ts >= int(time())` at stamp time is dropped — a note can never cite itself, a same-turn sibling, or the future. | `test_per_lookup_cap`, `test_per_note_cap`, `test_same_second_ref_dropped_at_stamp` |
| C6 | **Depth-1 deref.** Dereference-on-recall follows refs exactly one hop, ≤ `_REF_DEREF_CAP`(12) timestamps per bundle; refs carried by dereferenced notes are not followed — cycle-safe by construction, no visited-set. Bundle dedupe (catjot.py:1021) guarantees no duplicate notes. | `test_deref_is_not_transitive`, `test_deref_cap_honored` |
| C7 | **Inertness.** `ref:` words are invisible to every non-citation tag consumer (`_object_registry` rpjot.py:2032 prefix-filters on `TAG_OBJ`; `_parse_residence` :2008 reads only pwd; exp:/know:/scene: match by exact term) and add zero tokens to any tool schema. A notefile with no refs behaves identically to today. | `test_ref_words_inert_to_object_registry`, `TestObjectPermanence` + `TestCompactSchemaKeepList` unchanged-green (regression tripwires) |
| C8 | **Durability + decay.** Refs ride `Note.amend`'s additive tag semantics (catjot.py:499); `~ref:T` removes one citation; deleting a cited note leaves dangling refs that dereference to an empty TIMESTAMP match — a silent no-op, rendered as-is. No automatic repair. | `test_dangling_ref_is_silent_noop`, `test_tilde_amend_removes_single_ref` |

> **Tripwire (documented limitation, not an invariant).** Capture uses
> newest-K-returned as a relevance proxy. A lookup that returns three trivial
> recent notes above the one that actually mattered stamps the trivial three.
> `test_recency_proxy_tripwire` asserts the *current* behavior (newest-first
> capture, no semantic filter) so any future ranking change is deliberate. Damage
> bound: a wrong ref is bounded by C5's caps and confined by §3.3's single deref
> site; `~ref:` (C8) is the manual escape hatch. See §8 — this proxy is the
> load-bearing bet of the whole design.

## 3. Design resolutions

### 3.1 Who stamps — engine-side deterministic provenance

During step 1, each *targeted* lookup handler records the `now` of the top-K notes
it actually returned into a per-turn set; every C4-scoped step-2 write that turn is
stamped with those refs. The model neither sees nor emits `ref:`.

1. **Bakeoff history is decisive.** A model-emitted `cite` param is the neg_navto
   failure shape again (prose ignored 0/18 on weak models) plus ~30–80 tok of a
   compact budget already at 2,792/3,000. Engine stamping costs exactly 0 schema
   tokens and works identically on every model tier.
2. **Selectivity is structural, not judgmental.** The capture set is only what
   targeted lookups returned — the model *chose* to look these up. Ambient tools
   that surface the whole room (`get_people_present` :2705, `examine_location`
   :2734, `prepare_context` :4030) and the deterministic baseline blocks
   (`[OBJECTS HERE]`, `[ROOMS KNOWN HERE]`) capture nothing; citing everything in
   context would make every ref meaningless.
3. The honest cost is C2's weaker semantics: provenance, not proof of use. That
   trade is taken knowingly and stated on the tin.

### 3.2 The key — plain `ref:{now}`, multi-match accepted

`ref:{now}.{ordinal}` is rejected: it breaks the int-typed `bundle += ts` path,
needs new parsing in catjot, and buys nothing — same-second siblings are almost
always same-turn writes, i.e. *relevant*, and single-winner contexts already have
the I6 tie-break. The real same-second hazard is **intra-turn self-citation**
(mocked-LLM tests run sub-second, so a "prior" note and the citing note can share
a second and deref would pull the citing note into its own sources). C5's
`ts >= int(time())` stamp-time guard closes it: live turns always pass (LLM
latency ≫ 1 s); tests seed past `now=` values, the established pattern
(`TestObjectPermanence` seeds at `T = 1_750_000_000`).

### 3.3 Read side — full v1, one deref site

All four readers ship, cheapest first, with deref deliberately confined:

1. **Provenance marker** — `render_context`'s parts loop (rpjot.py:2414) appends
   one line, `[refs: T1 T2]`, to any note whose tags parse to refs. Tags are
   otherwise invisible at render time (verified: parts = context + message only),
   so without this line a citation buys nothing at read time. ~6–10 tok, only
   when refs exist; gives the prose model a stable pointer vocabulary.
2. **Backlinks** — `_backlinks(ts)`: one TAG search for `ref:{ts}`. Named seam for
   tests and future tooling ("what cites this entry"), zero new semantics.
3. **Depth-1 deref** — `_deref_citations(bundle)`: collect ref ints from visible
   notes, `bundle.ts.update(first 12)`, **one** `_regen_notes()` — never per-term
   `__iadd__` (each call rescans the whole notefile; precedent for direct
   mutation: `_bundle_from_notes`). Wired into `gather_pov_context` (rpjot.py:2222)
   only — the motivating case: a `rel:` bond note citing a betrayal event pulls
   the actual event verbatim into that character's POV. No other bundle derefs.
4. **Condensation: prompt nudge, not pinning.** A verbatim-pinning path through
   `_condense_context` (:2481) would be a second rendering pipeline for marginal
   value. Instead one line in the condense prompt: *"If a fact carries a
   bracketed [refs: …] marker, keep the marker attached to that fact."*
   Engine-internal prompt, not tool schema → no bakeoff trigger.

### 3.4 Cache interplay — provenance must survive the query cache

Four capture-point tools serve from `session._query_cache` on hit (`_cache_get`
rpjot.py:1694): the rendered string comes back but the note timestamps were
destroyed at the render boundary, so a naïve implementation captures zero refs on
every cache hit. Resolution: `self._ref_cache: dict[key, list[int]]` populated
beside `_cache_put` (:1700) on miss, replayed into the turn set on hit, and
evicted in lockstep in both `_cache_drop` (:1711) and `_cache_put`'s FIFO evict
(`self._ref_cache.pop(key, None)`).

## 4. Phases — readers first, over seeded fixtures

Readers-before-writers is preserved even though no `ref:` tags exist yet: Phase 1
builds and Tier-1-proves all read machinery against *hand-seeded* fixture notes
carrying `ref:` tags, exactly how `TestObjectPermanence.PHASES` seeds `obj:`
history. Writers land only once readers are proven.

| Phase | Contents | Depends on | Ships alone? |
|---|---|---|---|
| **0 — Substrate** | `TAG_REF = "ref:"` + `_REF_CAP_PER_LOOKUP`/`_REF_CAP_PER_NOTE`/`_REF_DEREF_CAP` beside the tag constants (rpjot.py:134–154); `_parse_refs(tag_str) -> list[int]` (split, prefix-filter, int-parse, garbage skipped) + `_format_refs` static helpers beside `_newest_by_now` (:2116) | nothing | yes — invisible |
| **1 — Read side + Tier 1** | `[refs: …]` line in `render_context` (:2414); `_backlinks(ts)` beside `_object_sightings` (:2139); `_deref_citations` wired as the last step of `gather_pov_context` (:2222); condense-prompt line (:2481); the full seeded-fixture half of `TestEntryCitation` — C1, C3, C6, C7, C8 all provable here with zero writers | Phase 0 | yes — refs only exist where tests seed them |
| **2 — Turn-scoped capture** | `self._turn_refs` (engine `__init__` default; reset at top of `run_turn` :1485); `_cite(notes, cap)` — 1–2 lines in each of `_tool_search_world` :3429, `_tool_get_scene` :3641, `_tool_get_character` :3217, `_tool_get_object` :3258 (canonical node + 2 newest sightings), `_tool_get_yomi` :3932, `_tool_get_conscience` :3979, `_tool_find_character` :3334 (matched profiles only); `_ref_cache` per §3.4 | Phase 0 | yes — captures accumulate, nothing reads them yet |
| **3 — Stamp side** | `_stamp_refs(tag_str) -> str` (C5 guard, per-note cap, dedupe against words already present); one-line `tag=self._stamp_refs(…)` per C4-scoped writer: `record_event` (after the scene-tag append), both `Note.jot` calls in `record_knowledge`, the thirteen `rel:` writers, `record_conscience`, `save_yomi`. Parameters, descriptions, schemas untouched. | Phases 0+2 (hard); 1 (for meaning) | no — the ordering constraint lives here |
| **4 — Live harness** | Tier-2 scenario (§5.2), endpoint-gated. **No Tier 3 sweep** — zero model-facing schema/prompt change is a design goal, and it is met. | Phase 3 | n/a — measurement |

catjot.py: **zero changes, all phases.** The substrate stays a generic note store;
that is the compat statement. Migration is a non-event — old notes carry no refs
and every reader no-ops on absence (C7).

## 5. Testing harness

### 5.1 Tier 1 — `TestEntryCitation` (deterministic, zero LLM)

Modeled on `TestObjectPermanence`: `Note.NOTEFILE = TMP_CATNOTE`, class-level
seeded timeline at `T = 1_750_000_000` stepping by 100, fresh engine per test.
Core fixture: event note at T (`/story/events/foyer`, the "betrayal"); bond note
at T+100 tagged `rel:wound ref:{T}`; a second-cousin note at T+200 tagged
`ref:{T+100}` (for the non-transitivity test); two notes sharing second T+300
(for C3). Test roster: the "Proven by" column of §2, plus the two flagship
scenario tests —

- `test_bond_citing_event_pulls_event_into_pov` — the motivating case end-to-end
  over seeded notes: `gather_pov_context(char)` on the bond-holder includes the
  event note's message verbatim (deref) and the rendered POV contains
  `[refs: {T}]` (marker).
- `test_cache_hit_capture_parity` — `_tool_get_character` twice (miss, then hit):
  `_turn_refs` identical both times (§3.4).

Regression tripwires run unchanged: `TestObjectPermanence` (C7 against obj:
machinery) and `TestCompactSchemaKeepList` (doubles as the token-budget guard —
any accidental schema growth fails it).

### 5.2 Tier 2 — live acceptance (endpoint-gated, N-of-M)

**Preflight the :5000 endpoint first** — a dead server silently xfails and fakes
green (established gotcha). One scripted three-turn scenario:

1. Turn 1 establishes a distinctive event (unique lexical anchor in the message).
2. Turn 2's player input references it, making a targeted step-1 lookup
   (`search_world`/`get_character`) plausible. Assert, N-of-M sampled (step-1
   tool choice is stochastic): some C4-scoped write this turn carries ≥1 `ref:`
   that resolves to a turn-1 note **and** the cited note is topically related
   (word-overlap against the turn-2 write — this assertion is the canary for §8).
3. Turn 3 renders the involved character's POV; assert the cited event's anchor
   text appears (deref working end-to-end against live writes).

### 5.3 Tier 3 — none, by design

No tool schema, parameter, description, or step-1/step-2 prompt visible to the
model changes. The condense-prompt line is engine-internal; if belt-and-braces is
wanted, a 5-sample condense spot-check suffices — not a sweep.

## 6. What this does NOT do

- Does not let the model choose, emit, or copy citations into tool arguments — no
  schema surface exists at all.
- Does not prove the model *used* a cited note (C2: retrieval provenance, nothing
  stronger).
- No transitive citation graphs: depth-1 only; no walker, no cycle detection.
- No condensation pinning or verbatim protection — a prompt nudge only.
- No citations on canonical identity nodes, structural notes, `place_object`
  sightings, or `/summaries` (nothing writes summaries today; C4 pre-excludes it
  for when something does).
- No repair of dangling refs after `Note.delete`; no dedup/merge of ref sets on
  amend beyond word-level dedupe at stamp time.
- No catjot.py changes: no new SearchType, no ref-aware substrate semantics, no
  CLI surface.
- Does not change which notes any character recalls, *except* the additive
  depth-1 deref inside `gather_pov_context`.

## 7. Token budget

| Surface | Delta |
|---|---|
| Compact step-2 schema | **0** (no tools/params/descriptions; `_COMPACT_KEEP_*` untouched; guarded by `TestCompactSchemaKeepList`; budget stays 2,792/3,000) |
| Step-1 schemas | 0 |
| Rendered context | ~6–10 tok per cited note actually rendered (`[refs: …]` line) |
| Bundle size | ≤ 12 extra notes per POV build, already bounded by `render_context`'s soft/hard tiers |
| Condense prompt | +~20 tok per condense call |
| Disk | ~13 bytes per ref word |

*Definition restated:* a citation is engine-stamped retrieval provenance — the
entry was on the model's desk this turn — not semantic quotation.

## 8. The load-bearing bet — precision laundering

Engine stamping converts a heuristic (newest-K surfaced by a lookup) into a
durable, authoritative-looking pointer. When the heuristic picks wrong — a
`record_bond` about a betrayal stamped with three trivial recent events because
`get_character` returns newest-first while the betrayal was result #7 — the wrong
ref is permanent on disk, and depth-1 deref then *amplifies* the wrong source
into that character's POV every future turn. A fuzzy paraphrase at least decays;
a wrong precise pointer compounds.

Mitigations built in: small caps (C5), targeted-lookup-only capture (§3.1), deref
confined to `gather_pov_context` (§3.3), `~ref:` amend as the manual escape hatch
(C8), and the recency-proxy tripwire test (§2) making the assumption explicit.
**Recency-as-relevance is the bet.** The Tier-2 topical-relatedness assertion
(§5.2) is the first instrument allowed to falsify it — if it fails persistently,
the correct retreat is capture-side ranking (or narrowing C4's stamp scope), not
abandoning the contract: C1–C8 hold regardless of how well the proxy performs.
