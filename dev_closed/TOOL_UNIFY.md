# TOOL_UNIFY — one execution plan for TOOL_JOIN (consolidation) + TS_CITATIONS (entry citations)

Unifies `TOOL_JOIN.md` (shrink/consolidate the tool surface, cut per-turn round trips) and
`TS_CITATIONS.MD` (engine-stamped `ref:{now}` provenance that dynamically enriches prompts)
into a single ordered execution plan on branch **`true_resume`**.

## 0. Base resolution — the branch question is already answered

`true_resume` (current branch) is a superset of both parents: it contains all of
`now_coworked` (through `0674da6`, nudge_pos_desc shipped) **and** all of
`object_permanence` (through `78760df`, [KNOWN OBJECTS] reframe). So:

- TS_CITATIONS' anchors (object_permanence code, 2,792/3,000 compact budget) are valid here as written.
- TOOL_JOIN's token math is restated against the 2,792 baseline (it was designed against now_coworked's 2,621).
- Step-2 menu on this base is **32 tools** (the 31 + `place_object`); step-1 is 12 (+ `get_object`).
- Caution: uncommitted `play.py`/`test_rpjot.py` edits + `SAVING_STATE.md` (resume work) are in the tree — land or stash that work before U0 so commits stay attributable.

## 1. Why the two designs compose (and don't collide)

They pull on opposite ends of the same prompt:

| | TOOL_JOIN | TS_CITATIONS |
|---|---|---|
| Surface touched | Model-facing step-2 schema (shrinks it) | Zero model-facing surface (C2: engine-stamped only) |
| Token budget | Frees ~1,040 tok, spends ~400 on restored descriptions | Costs 0 schema tok; +6–10/cited note rendered, +~20 condense |
| Turn latency | Fewer distractors, fewer rounds (batching), fewer iterations | Neutral (≤12 extra bundle notes, already tier-bounded) |
| Prompt quality | Sharper *selection* (restored fn-descriptions) | Richer *content* (POV deref pulls cited events verbatim — prompts assemble from provenance, not re-paraphrase) |
| Validation | Needs live bakeoff gate (selection changes) | Needs NO sweep (no model-visible change; Tier 2 scenario only) |

The combined effect is the "dynamic prompts" goal: each step-2 call gets ~650–850 tok
cheaper and chooses among 15 tools instead of 32, while the read side starts carrying
`[refs: T…]` markers and depth-1 dereferenced source entries — context that updates
itself from provenance instead of decaying through repeated re-synthesis.

## 2. Integration points (the substance of this doc)

**I-1 — Token math on the unified base.** 2,792 − 790 (rel suite) − 681 (int suite)
+ ~415 (two merged kind-enum tools incl. their fn-descriptions) ≈ **1,736**; + ~200–400
of restored descriptions (record_knowledge → set_people_present → save_character,
priority-ordered, trimmable) ≈ **1,950–2,150** final. Re-measure at impl (house rule).
`test_compact_budget_under_3000` gains ~900 tok of headroom. TS_CITATIONS adds zero;
its `TestCompactSchemaKeepList` unchanged-green tripwire (C7) now guards the NEW
post-consolidation number — update the tripwire's expectation once, in U1.

**I-2 — C4 stamp scope survives the merge automatically, if stamped at the right site.**
TS_CITATIONS Phase 3 stamps inside the legacy `_tool_record_*` implementations (one-line
`tag=self._stamp_refs(…)` at each `Note.jot` call). TOOL_JOIN's merged
`record_relationship`/`record_interior` are pure delegators to those same implementations,
and its `_COMPACT_HIDDEN_TOOLS` safety net keeps legacy names dispatchable. Therefore:
**stamp at the legacy implementation bodies, never at dispatch/wrapper level keyed by
tool name** — then merged path, legacy-name path, and future callers all stamp
identically. C4's scope restated post-merge: `record_event`, `record_knowledge` (both
notes), the rel/int family (reached via either name), `record_conscience`, `save_yomi`;
never canonical/structural writers or `place_object` sightings. TOOL_JOIN's S1 does not
edit those bodies (it only adds delegators + a schema filter), so the two changes never
touch the same lines — no rebase churn in either order.

**I-3 — Test matrix cross-coverage.** `TestEntryCitation` (TS §5.1) gains one unified-path
test: dispatch `record_relationship(kind=wound, …)` after a seeded capture and assert the
written note carries both `rel:wound` and the expected `ref:{T}` — proving delegation
preserves stamping. `TestConsolidatedDispatch` (TOOL_JOIN) needs no citation awareness
(runs with empty `_turn_refs`; C7 inertness covers it).

**I-4 — Step-1 seeding (TJ-S6) starves citation capture on stable turns — accepted, by
contract.** A seeded turn that skips targeted lookups genuinely has weaker provenance;
replaying the previous turn's refs would violate C2's semantics ("surfaced THIS turn").
Policy: no replay, no exception. Since S6 is decision-gated (only if step-1 ≥ ~30% of
wall-clock) and flag-gated (`RPJOT_STEP1_REUSE`, default off), the interaction ships
disabled and is re-examined with S6's own A/B run. Documented here so it can't be
"discovered" later as a bug.

**I-5 — Scheduling around the dead :5000 endpoint.** TOOL_JOIN's ship gate needs live
sweeps; TS_CITATIONS needs none (design goal met: zero model-visible change). So all
code lands endpoint-independent with Tier-1 proof, and ONE live campaign validates
everything. Timing baseline can't be measured on old code after the fact → baseline runs
against the U0 commit via checkout, post runs against HEAD (telemetry exists at both).

## 3. Unified sequence (one commit each unless noted)

**Track A — endpoint-independent (land now, Tier 1 green at every step):**

- **U0 — Timing telemetry** (TJ-S0). `[TIMING] turn=N step1=Xs/Nit step2=Ys/Mit step3=Zs total=Ts`
  in `run_turn`; `last_rounds` on every exit path of both step classes; mocked-call unit tests.
  No behavior change. *This commit is the baseline checkout point for the live campaign.*
- **U1 — Consolidation core** (TJ-S1). `record_relationship` (kind enum ×10, `power` not
  `power_dynamic`) + `record_interior` (kind enum ×9) delegating to existing impls;
  `_COMPACT_HIDDEN_TOOLS` filter in `_compact_step2_schemas`; step-2 menu 32 → 15;
  legacy names stay dispatchable. Test updates: `TestCompactSchemaKeepList` (hidden-names
  absent, enums present, new budget number), new `TestConsolidatedDispatch` (19 kinds →
  tag/pwd parity with legacy output, cache-drops, unknown-kind error JSON, legacy-name
  dispatch, omitted-optionals).
- **U2 — Description restoration** (TJ-S2, separate commit for sweep attribution).
  Restore fn-descriptions: `record_knowledge`, `set_people_present`, `save_character`
  (+`save_object` only if not already carrying the object_permanence-shipped desc —
  check `_COMPACT_KEEP_FUNCTION_DESCRIPTIONS` current state on this base first).
- **U3 — Citation substrate + read side** (TS Phases 0+1). `TAG_REF`, caps, `_parse_refs`/
  `_format_refs`; `[refs: …]` line in `render_context`; `_backlinks`; `_deref_citations`
  wired into `gather_pov_context` only; condense-prompt line. `TestEntryCitation` seeded-
  fixture half (C1, C3, C6, C7, C8).
- **U4 — Turn-scoped capture** (TS Phase 2). `_turn_refs` reset in `run_turn`; `_cite()`
  in the 7 targeted step-1 lookups (incl. `get_object`); `_ref_cache` beside the query
  cache with lockstep eviction (§3.4). `test_cache_hit_capture_parity`.
- **U5 — Stamp side** (TS Phase 3). `_stamp_refs` with C5 guard; one line per C4-scoped
  legacy writer body (per I-2). Remaining `TestEntryCitation` tests + the I-3 merged-path
  test + `test_bond_citing_event_pulls_event_into_pov` flagship.
- **U6 — Followup batching + dedupe** (TJ-S4). One deduped `[DIRECTIVE]` per round,
  contiguous tool results, per-turn `seen_instructions`. Mocked-loop unit tests.
  (Independent of citations; placed here so the live campaign measures its timing effect
  in the same session.)

**Track B — the live campaign (one :5000 session, preflight first — the xfail gotcha):**

- **U7** in order: (1) preflight; (2) timing baseline: checkout U0 commit, run the piped-
  transcript protocol ×3; (3) `bakeoff_consolidate.py` — 3 arms (`legacy_control`/`merged`/
  `merged_desc` via per-arm `_COMPACT_HIDDEN_TOOLS` override), Corpus A = 9 navnudge
  phrases NO-REGRESSION GATE, Corpus B = rel/int family ≥95% + kind ≥80%; (4)
  `bakeoff_denoise.py` (TestProductionActivation × models — 15-way menu plausibly improves
  it; measure, don't assume); (5) TS Tier-2 three-turn citation scenario (N-of-M, topical-
  relatedness canary for the §8 recency bet); (6) the owed object_permanence live runs
  (`-k "ObjectActivationLive or ProductionActivation or Stationary"`); (7) timing @ HEAD.
  **Ship gate for U1+U2 = (3); nothing else in the unified work needs a sweep.**
  If `merged_desc` regresses vs `merged`: trim restorations bottom-up, re-sweep arm only.

**Track C — data-gated tail:**

- **U8** — step-2 `max_iterations` 10→7 only if U7 timing shows p95 rounds ≤ 5 (TJ-S5);
  step-1 seeding behind `RPJOT_STEP1_REUSE` only if step-1 ≥ ~30% of wall-clock (TJ-S6,
  with the I-4 capture policy); flip on only after its own A/B + one denoise pass.
- **U9 — Wrap.** Final before/after timing table; mark FIXUP.md W9/T6 consolidation
  executed-with-data; note in TS_CITATIONS that the C4 writer list is now reached via
  the merged delegators.

## 4. Combined budget & outcome table

| Surface | Today (true_resume) | After U1–U5 |
|---|---|---|
| Step-2 menu | 32 tools | 15 tools |
| Compact step-2 schema | 2,792 tok | ~1,950–2,150 tok (incl. restored descs) |
| Step-2 schema seen by model | names-only for 21 tools | names + 5–7 load-bearing fn-descs |
| Rendered context | paraphrase-only | + `[refs:]` markers + depth-1 POV deref (≤12 notes, ≤10 tok/marker) |
| LLM calls/turn | 3–20 (typ. 6–11) | same ceiling; fewer step-2 rounds expected (batching) — measured by U7, tuned by U8 |
| catjot.py | — | zero changes (both designs' shared compat statement) |

## 5. Risks (delta beyond each doc's own list)

- The 15-way menu changes selection dynamics for tools BOTH designs assume stable
  (record_event, record_knowledge are C4 stampers AND Corpus-A regulars) — U7's Corpus A
  gate is the guard for both designs at once.
- Landing 7 commits before any live validation concentrates gate risk in U7. Mitigant:
  every commit is independently Tier-1 green and independently revertable; the only
  live-gated *behavior* is the schema shape (U1/U2), which the 3-arm bakeoff isolates.
- `true_resume`'s uncommitted resume work must land first or the U0 baseline checkout
  becomes ambiguous.

## 6. Files

- `rpjot.py` — everything (keep-lists/compact builder, dispatch, run_turn, step loops,
  rel/int bodies, render/gather/condense, query cache)
- `test_rpjot.py` — TestCompactSchemaKeepList, new TestConsolidatedDispatch, new
  TestEntryCitation, TestProductionActivation (re-run only), telemetry tests
- new `bakeoff_consolidate.py` (template: `bakeoff_navnudge.py` incl. preflight)
- `bakeoff_denoise.py`, `play.py` (timing protocol), `dev_open/FIXUP.md` (U9 notes)
