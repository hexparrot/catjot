# Tool Consolidation + Turn-Latency Reduction

## Context

A player turn currently costs 3–20 sequential LLM calls (typically 6–11): step 1 loops up to 8 iterations over 11 read tools, step 2 loops up to 10 (+1 nudge) over **31 write tools**, step 3 is one prose call. Turnaround is borderline infeasible for smooth play.

Two measured facts drive this plan:
1. **19 of the 31 step-2 tools (the rel/int suites) virtually never fire.** Across all real sessions in `sessions/`: `exp:` tags 708 (record_event), `char:` 426, `loc:` 226 — but `rel:*` tags **zero** and `int:*` tags **one**. Yet those 19 tools cost 1,471 tok = 56% of the 2,621-tok compact step-2 schema, and act as 19 distractors in every step-2 selection.
2. **Restoring one function description (navigate_to) took a 17% selection miss to 0%** (PROMPT_MOVE re-sweep). Freed schema budget can be spent on more restored descriptions — converting latency savings into accuracy gains.

User decisions: scope = both tool consolidation **and** loop-latency work; merge depth = grouped tools with kind enums (not one mega-tool, not drop-only).

Key verified fact making the merge safe: **readers never key off tool names** — they read by pwd/tag (`gather_character_knowledge` → `PWD_INTERIOR/{char}` rpjot.py:1718; `get_relationship_arc`/`get_social_map` → `PWD_REL/{pair}` rpjot.py:4248-4326). Merged tools that write the same tags/pwd leave storage byte-identical; only the LLM-facing surface shrinks.

## Changes

### S0 — Timing telemetry + baseline (first; no behavior change)
- `WorldStateStep.run` (rpjot.py:597-653) and `ComplianceStep.run` (rpjot.py:789-897): set `self.last_rounds` on every exit path; per-call `time.perf_counter()` DEBUG timing.
- `run_turn` (rpjot.py:1321-1393): one parseable INFO line before the existing `[TOOLS]` census (rpjot.py:1360-1366):
  `[TIMING] turn=7 step1=12.4s/3it step2=31.9s/6it step3=9.1s total=53.4s`
- Unit tests with mocked `call_llm` (pattern: `TestZeroCanonicalNudge`, test_rpjot.py:1625).
- Run the timing protocol (below) → record baseline table.

### S1 — Consolidation core: 31 → 14 step-2 tools
Two new `@rp_tool` methods (place after rpjot.py:4183), each a dict-based kind-switch **delegating to the existing 19 `_tool_record_*` implementations** (tags, pwd, cache-drops, logs all preserved):

- `record_relationship(kind, char_a, char_b, description, label="", detail="")` — kind enum: `bond|history|dynamic|power|wound|promise|debt|lie|leverage|impression` (note `power`, matching the stored `rel:power` tag). `char_a` = actor/source (holder/promiser/liar/observer), `char_b` = target. Per-kind delegation maps legacy params: e.g. `bond_type=label`, `stakes=detail`, `truth=detail or "(truth unrecorded)"`.
- `record_interior(kind, character, content, target="", detail="")` — kind enum: `secret|desire|longing|jealousy|mask|subtext|reputation|trigger|unspoken`. `target` = other person (concealed-from/desired/audience), `detail` = second layer (private self/actual meaning/reaction).
- Unknown kind → error JSON matching the `_safe_dispatch` contract (rpjot.py:1207-1270); model self-corrects next round.
- Accepted fidelity loss: `record_wound.known_to_inflicter` bool dropped (zero measured usage; legacy tool remains callable).

**Hiding the legacy 19 — free safety net, no unregistration:**
- Add `_COMPACT_HIDDEN_TOOLS = frozenset({...19 names...})` next to the keep-lists (rpjot.py:1086).
- In `_compact_step2_schemas` (rpjot.py:1117-1156), top of loop: `if name in self._COMPACT_HIDDEN_TOOLS: continue`.
- ComplianceStep is the only caller sending step-2 schemas to the model (rpjot.py:819-824), while dispatch uses `_step2_handlers` — so legacy names **stay dispatchable** (old resumed histories, model habit) but vanish from the menu. Token cache (rpjot.py:1307-1309) auto-corrects.

**Explicitly NOT merged** (keep tuned surface intact): record_event, navigate_to, set_people_present, save_character/location/object, record_knowledge, begin_scene, record_conscience, update_attn, save_yomi, record_mood (transient session state, rpjot.py:4207-4222, usage invisible in .jot). Step-1 get/find/search stay — step-1 sends full descriptions already, and `find_character` has load-bearing side effects (NPC tracker, rpjot.py:2605-2615).

### S2 — Spend freed budget on restored descriptions (separate commit for attribution)
Token math: 2621 − 790 (rel) − 681 (int) + ~415 (two merged tools incl. their fn descriptions) ≈ **1,565 tok**. Spend ~400 tok on `_COMPACT_KEEP_FUNCTION_DESCRIPTIONS` (rpjot.py:1108-1114) one-liners, priority-ordered/trimmable:
1. `record_knowledge` (event/knowledge boundary — core of POV engine)
2. `set_people_present` (cast drift = highest-stakes silent failure, rpjot.py:1397-1400)
3. `save_character` (new-NPC persistence miss)
4. `save_object` (minimal text; object_permanence branch owns its contract)

Post-restoration ≈ 1,950–2,050 tok — still ~600 below today and ~1,000 under the 3,000 ceiling.

### S3 — Validation: `bakeoff_consolidate.py` + re-sweeps (ship gate)
- Modeled on bakeoff_navnudge.py incl. its :5000 preflight (dead-endpoint xfail gotcha — abort, never trust silent passes).
- Arms via per-arm `_COMPACT_HIDDEN_TOOLS` override (no code forks): `legacy_control` (31-tool), `merged`, `merged_desc`.
- **Corpus A (regression gate):** the 9 navnudge phrases; stationary/mobile must not regress vs `legacy_control` in-run (forced reported, not gated).
- **Corpus B (new, ~10 phrases):** rel/int family + kind selection ("swears she'll return the locket" → relationship/promise; "aches for the MC to notice" → interior/longing; ...) plus 2 negatives (action → record_event, whispered fact → record_knowledge). Gates: family ≥95% on unambiguous phrases, kind ≥80% (kind misses only mis-sub-tag within same pwd — low blast radius).
- Re-run `bakeoff_denoise.py` (TestProductionActivation × models) after S2 and S4.
- If `merged_desc` regresses vs `merged`: trim descriptions bottom-up, re-sweep.

### S4 — Followup batching + dedupe (round-trip reduction)
`ComplianceStep.run` rpjot.py:872-893 currently interleaves a `[DIRECTIVE]` user message after **each** tool result (non-standard ordering + per-call instruction pressure extending the loop; `FOLLOWUP_*` constants repeat verbatim, e.g. rpjot.py:2154, 2197, 2545, 2673). Change to: tool-result messages contiguous; collect instructions; append **one** deduped `[DIRECTIVE]` message per round; per-turn `seen_instructions` set drops repeats. `canonical_results` collection (:881) untouched. Re-run timing protocol.

### S5 — Step-2 bound tuning (data-gated)
From S0/S4 `[TIMING]` distributions: lower `max_iterations` 10→7 (rpjot.py:794) only if p95 step-2 rounds ≤ 5. No semantic early-exit heuristics. (+1 nudge headroom logic :811-813 is bound-relative, unchanged. Zero-canonical nudge verified already conditional + single-shot — no action.)

### S6 — Step-1 cross-turn seeding (decision-gated, default OFF)
Only if baseline shows step-1 ≥ ~30% of turn wall-clock; else document deferral. Behind `RPJOT_STEP1_REUSE=1`: if location AND cast unchanged since last turn, seed the initial message (`_build_initial_message`, rpjot.py:522-552) with the previous WorldStateDoc + "update only what changed", drop `max_iter` 8→3. Invalidation is automatic (navigate_to changes `session.location` :2266; set_people_present changes cast). Flip on only after A/B timing + one denoise pass.

### S7 — Wrap-up
Final before/after timing table; note in dev_open/FIXUP.md that the deferred W9/T6 consolidation is now executed with data (the `[TOOLS]` census + sessions tag counts satisfied the "telemetry first" precondition).

## Test updates (test_rpjot.py)
- `TestCompactSchemaKeepList` (1739-1793): swap `record_bond` probe (leaves menu) for still-visible tools; add assertions: 19 legacy names absent from compact schemas; merged tools present with correct enum sets; extend fn-description expectations; budget test unchanged.
- New `TestConsolidatedDispatch`: all 19 kinds → assert written note tag prefix (`rel:<kind>`/`int:<kind>`) and pwd match legacy output; rel kinds drop `rel:{pair}`/`social_map` caches; unknown kind → error JSON; **legacy names still dispatch**; omitted optionals produce valid notes.
- `TestProductionActivation` (1949-2102): no expectation changes; re-runs live automatically exercise the new menu (uses `_compact_step2_schemas` at :2000).
- New telemetry tests (assertLogs on `[TIMING]`).

## Verification
1. Tier 1: `python3 -m pytest test_rpjot.py` green (~307+ tests) after each step.
2. Preflight :5000 before any live run (`curl` models endpoint; abort if dead).
3. Timing protocol: copy a real session .jot to scratchpad + fixed 12-line transcript; `python3 play.py <copy> < transcript.txt` ×3 per commit, pinned model; grep `[TIMING]`; compare mean/p95 total and per-step seconds/rounds across S0 → S1/S2 → S4 → S6.
4. Ship gates: Corpus A no regression; Corpus B family ≥95% / kind ≥80%; bakeoff_denoise no per-test regression.

## Expected outcome
- Step-2 menu 31 → 14 tools; compact schema 2,621 → ~2,000 tok *including* four newly restored selection descriptions (accuracy up, not just latency).
- Fewer step-2 rounds from directive batching; measured seconds/turn reduction quantified by S0-vs-final timing table.
- Zero storage-format change; legacy tool names remain dispatchable as a safety net.

## Risks
- 14-way menu changes selection dynamics for untouched tools — plausibly better (fewer distractors), but Corpus A gate is the guard, not the assumption.
- object_permanence branch collision surface is only the `_COMPACT_KEEP_*` lists + budget test (that branch ~2792 tok → ~2100 after rebase; trivial either order).
- Timing comparisons are endpoint-sensitive: always preflight and pin the model.

## Critical files
- `rpjot.py` — keep-lists/compact builder 1086-1156, dispatch 1207-1278, run_turn 1321-1393, step loops 597-653 / 789-897, rel/int tool defs 3310-4183
- `test_rpjot.py` — 1739-1793, 1949-2102, 2109+
- `bakeoff_navnudge.py` (template), `bakeoff_denoise.py` (re-validation), `play.py` (timing protocol)
