# FEATURE_TOGGLES — make whole tool-families cheap to disable and remove

> **Status: IMPLEMENTED (core), 2026-07-06.** Increments 0–4 shipped and green
> (600 passed). What landed:
> - **Inc 0 (TIMING):** `/timing` command; per-tool `_tool_tok_map` (schema tok
>   per variant); timed `_safe_dispatch` shell (`_dispatch_guarded` +
>   `_record_tool_event`) feeding `_turn_tool_events` / `_session_tool_stats` /
>   `_last_turn_report`; bg-speculation isolation; optional API-usage capture via
>   `catjot.LAST_USAGE`; `RPJotEngine.timing_report()`.
> - **Inc 1 (families):** `RPJOT_DISABLE` denylist + registration gate at the
>   choke point; per-family `[FAMILY]` log. **Design deviation:** family membership
>   lives in a centralized class-level `_FAMILY_TOOLS` map (reverse-indexed to
>   `_TOOL_FAMILY`), NOT a per-decorator `family=` kwarg. Rationale: fewer edit
>   sites (one greppable table vs 25 multi-line decorator edits), same idiom as
>   the existing `_COMPACT_*` frozensets, and it *is* the FEATURE_FAMILIES index.
>   Unlisted tools default to `core` (never disablable) — the 12 test pins fall
>   here automatically.
> - **Inc 2 (guards):** conscience/interiority POV reads gated in
>   `gather_pov_context`; `/yomi` prints `[feature disabled]` when off.
> - **Inc 3 (YOMI repair):** `_gather_yomi_for_scene` wired into `ProseStep.run`
>   behind `family_enabled("yomi")` — the docstring's "automatically injected"
>   claim is now true rather than aspirational.
> - **Inc 4 (unify):** `_COMPACT_HIDDEN_TOOLS` deleted; the 19 granular rel/int
>   writers moved to `_GRANULAR_TOOLS`, deregistered by default (one seam across
>   compact/bare/full/dispatch). `RPJOT_GRANULAR=1` restores them. The wrappers
>   delegate to the granular methods directly, so delegation is unaffected.
>
> **Deferred (optional halves, not blocking):** the "make relationships data earn
> its keep" compact social-map read into `gather_pov_context`; `/stats` /
> `/construct` per-family section guards; the Inc 5 runbook appendix.

> **Status: upcoming plan** (filed in `dev_open/` per [[dev-doc-dirs-convention]]).
> Motivation: YOMI is dead code and the tool-schema budget is dominated by a few
> feature-families whose value doesn't justify their token weight. Today, changing a
> feature's fate is a scavenger hunt across `rpjot.py` + `play.py` + tests. This plan adds a
> single **feature-family** seam so a family can be disabled with a flag, **repaired and
> A/B'd**, slimmed, or deleted with a greppable checklist. **Pruning is the last resort:
> when an underpulling feature is genuinely wanted and the fix is small and well-understood,
> the plan repairs it instead of deleting it** — YOMI is exactly such a case.

## Context — the weight, quantified

Measured from a live engine (`register_all_tools()` → `_tool_schemas`, tokenized with the
engine's own `_tok`): **46 tools, 9527 schema tokens** in the full flat schema — the
*maintenance surface*, not what's shipped per call (see token note below). Grouped by family:

| Family | Tools | Schema tok | % budget | Fed into context? | Disposition |
|---|---:|---:|---:|---|---|
| **yomi** | 2 | 436 | 4.6% | ❌ never — `_gather_yomi_for_scene()` (rpjot.py:4047, 41 lines) has **zero callers**; docstring at :5663 falsely claims "automatically injected" | **REPAIR** (wire the existing gather in; it's a missing call, not a bad idea) → remove only if the repaired version still doesn't earn its keep |
| **relationships**† | 14 | 2940 | **30.9%** | ❌ opt-in only (`get_relationship_arc`/`get_social_map`); never auto-assembled | **UNIFY + REPAIR** — granular writers already hidden in prod (†); unify the seam (Increment 4), and/or auto-feed a compact social-map summary so the captured data is actually used |
| **interiority**† | 10 | 2134 | 22.4% | ✅ `gather_pov_context` :3931, every character POV | KEEP; the 10→1 wrapper slim is already shipped in the compact menu (†) |
| **conscience** | 2 | 581 | 6.1% | ✅ `gather_pov_context` :3928, every POV | KEEP (cheap + core) |
| core/other | 18 | 3436 | 36% | ✅ baseline every turn | KEEP (see test pin below) |

Two families (relationships + interiority) are **53% of the full-schema budget**†. Relationships
remains the worst weight-to-value case on the *write* side: its captured data is never read into
context unless the model explicitly queries it — but that's a *wiring* failure, so it's a repair
candidate, not automatically a deletion.

> † **Already hidden in prod:** `_COMPACT_HIDDEN_TOOLS` (rpjot.py:2002-2009, TOOL_UNIFY U1)
> removes the 19 granular relationship/interior writers (`record_bond`, `record_secret`, …) from
> the compact step-2 menu; the `record_relationship`/`record_interior` wrappers stand in for them
> (:2045-2054). Their tok figures above are the full variant. Residual prod cost of the hidden
> tools: ~19 bare name-only stubs (~300-400 tok) on every step-3 prose call (:1950-1961), the
> dispatch surface, and a second maintenance seam.

> Token note: the numbers above are the **full** flat schema (`_tool_schemas`, 9527 tok), which
> is **never sent to the model** — its only prod consumer is the `_guard_payload` default (:8438).
> What each step actually ships: step 1 sends the full step-1 set (:1273), step 2 sends the
> **compact** schema (~2000 tok, :1530), step 3 sends bare name-only stubs of ALL step-2 tools
> (:1778). Per-family *shares* do **not** hold across variants — the † hiding zeroes
> relationships/interiority granulars in the compact variant — so weigh savings against the
> variant a change actually touches (Increment 0's `/timing` makes this per-tool, per-variant).

## Disposition framework — prune is the last resort

Every underpulling family gets one of four dispositions. The family seam (below) supports all four,
so the choice is reversible and A/B-testable rather than a one-way door:

1. **Keep** — earns its weight already (conscience, interiority-as-fed, core).
2. **Repair** — the capability is wanted and the shortfall is a *wiring bug*, not a bad idea. Fix
   the wiring, keep it behind the family flag, A/B whether it now earns its keep. → **yomi**.
3. **Minimize** — the idea is genuinely useful but the *surface* is bloated (too many near-duplicate
   tools, too much schema). Collapse to a single parameterized wrapper and/or feed its data into
   context so it's actually used. Capability preserved, tokens reclaimed. → **relationships**.
4. **Remove** — only when the idea itself isn't worth the tokens *and* repair/minimize won't save it.
   Reserved for dead code with no salvage value (yomi *if* repair is declined).

Rule of thumb: **reach for Remove only after Repair and Minimize are ruled out.** The machinery
makes all three the same amount of work to try.

## The existing seam (already toggle-friendly)

- `@rp_tool(description, parameters, *, step=2)` (rpjot.py:733) stashes
  `fn._rp_tool_meta = (description, parameters, step)`.
- `register_all_tools()` (rpjot.py:2252) auto-discovers decorated methods via `dir()`, and
  `_register_tool` (:2145) is the **single choke point** that adds both schema and handler into
  the step1/step2 registries. It already computes per-step schema token cost (:2272–2287).
- Config idiom: `play.py:69-80` parses `RPJOT_*` envs into module constants; the play loop sets
  engine attributes post-construction (`engine.seed_enabled = _BG_SEED` at :892). **The engine
  never reads env** — the play loop configures it. New toggles must follow this idiom.
- Test pin: `test_rpjot.py:741` asserts (membership, not count) that 12 tool names exist — all
  `core` family. Removable families are not pinned, so disabling/removing them is test-safe.

## Design — one "feature family" concept, gated at the choke point

**Increment 0 — per-tool cost instrumentation + `/timing` (lands FIRST: measure before surgery).**
Zero dependency on family tags; it's the readout that makes Increment 2's `RPJOT_DISABLE` A/B
measurable — and it would have caught this doc's own token-note error. Deterministic, no LLM.
The user-facing contract: after any reply, `/timing` quantifies per tool (a) the cost it had **by
existing** — schema tok in the variant actually sent × LLM iterations that turn — and (b) the cost
it incurred **by executing** — handler wall-clock + result tok fed back into context.

- **Per-tool token map (existence cost).** After the four aggregate caches in
  `register_all_tools()` (rpjot.py:2272-2277) — NOT in `_register_tool`; compact/bare are
  properties over the finished list — build
  `self._tool_tok_map: dict[name, {"step1": int, "compact": int, "bare": int, "full": int}]` by
  tokenizing each schema entry individually (`_tok(json.dumps(entry))`) across `_step1_schemas` /
  `_compact_step2_schemas` / `_bare_tool_schemas` / `_tool_schemas`. Absent from a variant = 0
  (hidden tools: `compact=0, bare>0` — pins the † fact in code). Accept ~1 tok/tool
  list-delimiter drift vs the aggregates; the cached aggregates stay the authoritative per-call
  figures.
- **Dispatch instrumentation (execution cost).** Refactor `_safe_dispatch` (:2175-2238) into a
  timed shell: extract the current body as `_dispatch_guarded` returning `(text, ok)`, wrap with
  `perf_counter`, then `_record_tool_event(name, ms=…, result_toks=_tok(result), ok=ok)`.
  **Error-JSON text stays byte-identical** (test_rpjot.py:765-768 + the model's self-correction
  loop). One seam covers step-1 (:1306), step-2 (:1594), and background speculation. Shapes:
  `self._turn_tool_events: list[dict]` (reset with `_last_ctx_now` at run_turn start :2468);
  `self._session_tool_stats: dict[name, {calls, errors, ms, result_toks, bg_calls}]`;
  `self._last_turn_report: dict|None` — snapshot built just before the `[TIMING]` log (:2551)
  holding events, per-step iters (`last_rounds`: every iteration shipped the schemas, so it is
  exactly the existence-cost multiplier), step seconds t0–t3, seed status. Background thread:
  save/restore `_turn_tool_events` around `speculate_step1` (:2633-2645, mirroring the
  `_turn_refs` precedent) + a `self._in_bg_speculation` flag; on seed hit adopt the speculative
  events flagged `bg=True`.
- **Optional, severable — real API usage.** `call_llm` (catjot.py:1362) discards the response
  `usage` object. Add a module-level `LAST_USAGE` side channel (safe under the single-slot
  endpoint invariant, play.py:1056-1060); rpjot appends per-iteration prompt/completion tokens to
  `self._turn_usage` after the call sites (:1282, :1538, :1787), skipping bg speculation.
  Streaming step-3 reports no usage — the report says so.
- **`/timing` command.** play.py: add `"/timing"` to `_SLASH_EXACT` (:54-56), `_HELP_TEXT`
  (:96-105), and a dispatch block near `/stats` (:952-963) printing `engine.timing_report()`.
  Rendering is engine-side (precedent: `scene_debug_report`, `construct_report`) on the shared
  `_introspect_rule`/`_INTROSPECT_WIDTH` helpers (:95-129). Sections: (1) header — turn + the
  existing per-step timing/iteration/seed summary; (2) EXECUTED THIS TURN — per tool: calls,
  handler ms, result tok, err/bg flags, with a footnote that LLM latency is per-iteration-batch
  and never divided across the tools of one round (attribution honesty); (3) STANDING COST THIS
  TURN — per variant: per-call tok × iterations = turn tok, plus top tools by standing cost with
  a family column (`self._tool_families.get(name, "-")` — populated by Increment 1, degrades
  gracefully before it lands); (4) SESSION — cumulative per-tool calls/errors/ms/result tok;
  (5) API USAGE if the usage sub-item landed. No-turn case renders standing costs only, with a
  notice.
- **Tests (`TestToolTiming`).** Map covers all registered tools and per-variant sums ≈ cached
  aggregates (± len(map)+2); every `_COMPACT_HIDDEN_TOOLS` name has `compact==0, bare>0`;
  dispatching a read-only step-1 tool (`get_people_present` — avoids the tests/bellvue.jot
  append) records an event; unknown tool records `ok=False` with unchanged error JSON;
  `timing_report()` renders pre-turn. Relative assertions only (`_tok` falls back to len//4
  without `regex`).

**Increment 1 — family tagging + registration gate (the reusable machinery).**
- Extend the decorator: `rp_tool(description, parameters, *, step=2, family="core")`; store family
  in `_rp_tool_meta` (now a 4-tuple); update the unpack at rpjot.py:2265.
- Tag all 46 tools with a family (`core`, `yomi`, `conscience`, `relationships`, `interiority`,
  `objects`, `scenes`, …). Mechanical, one word per decorator. **Invariant: the 12 test-pinned
  tools are `family="core"` and `core` is never disablable.**
- Engine gains `self.enabled_families: set[str]` (default = all families). In `register_all_tools`
  skip any tool whose `family not in self.enabled_families`. Because registration is the single
  choke point, one check removes a family's schemas *and* handlers together — no post-hoc filtering.
- A module-level `FEATURE_FAMILIES` table maps each family → its `PWD_*`/`TAG_*` constants, its
  read-sites, and its slash commands. This is the self-documenting index that makes removal greppable.
- Env wiring (mirror play.py:69-80): `RPJOT_DISABLE="yomi,relationships"` (denylist; empty = all on).
  Parse to a module constant in play.py; set `engine.enabled_families = ALL - _DISABLED` **before**
  `register_all_tools()`. Engine stays env-free.
- Extend the existing registration log to print per-family tool count + token cost as a rollup of
  `self._tool_tok_map` (Increment 0) grouped by family tag — one line per family with count +
  step1/compact/bare toks; no parallel tokenization pass. "Weight" stays visible in the logs.

**Increment 2 — read-side + slash guards keyed on the same families (makes *disable* graceful).**
- Wrap the two context-assembly reads in `gather_pov_context` — conscience (:3928) and interiority
  (:3931) — in `if self.family_enabled("…")`. A disabled family just yields smaller context; old
  session notes for it are silently ignored (matches the zero-backward-compat stance in [[IDENTITY]]).
- Slash commands that depend on a family guard on enablement instead of crashing: `/yomi`
  (play.py:1034-1043) prints `[feature disabled]`; `/stats` / `/construct` skip disabled-family
  sections. `_SLASH_PREFIX` (play.py:54-61) entries for disabled families are dropped.
- Result: `RPJOT_DISABLE=relationships` is a safe, reversible A/B lever to feel the token savings
  and narrative impact before committing to deletion.

**Increment 3 — YOMI: repair first, remove only if repair is declined (proves Repair + the seam).**
YOMI's tools work; the failure is that the read half was never wired in. Two branches, decide at
review:

- **Repair (preferred).** Call the existing `_gather_yomi_for_scene()` (:4047) where per-character
  POV/prose context is assembled (`gather_pov_context` :3911 / the ProseStep synthesis at ~:1710,
  alongside the existing `_gather_attn_for_scene`/`_gather_mood_for_scene` injections), behind
  `family_enabled("yomi")`. Fix the docstring (:5663) so it matches reality. Then A/B with
  `RPJOT_DISABLE=yomi`: if the model's reads of a character's "yomi" measurably improve prose and
  justify 436 tok, keep it; otherwise fall through to remove. Small, well-understood fix.
- **Remove (fallback).** If repair is declined, delete in one pass: `_tool_save_yomi` (:5688),
  `_tool_get_yomi` (:5738), `_gather_yomi_for_scene` (:4047), `PWD_YOMI` (:407), `TAG_YOMI` (:410),
  the `yomi:` cache keys (:4060, :5699, :5742), the `/yomi` slash command + `_SLASH_PREFIX` entry,
  the `/stats` construct yomi section (~:7723-7731), and the test tool-list mention. Net ~120 lines,
  436 tok, zero context-assembly changes.

**Increment 4 — Minimize relationships (keep the capability, cut the bloat).**
Relationships is wanted but over-surfaced: 14 tools (`record_bond`, `record_history`,
`record_wound`, `record_debt`, `record_lie`, `record_leverage`, …) that are near-duplicates writing
to the same `/story/relationship/{pair}` family, plus two query tools — 2940 tok in the full
schema, and the captured data is never auto-read. Two moves, both reversible via the family flag:

- **Unify the seam (schema + maintenance win).** The 14→1 collapse itself is **already shipped**
  (†): the `record_relationship` wrapper exists (:6835) with a keep-listed selection description
  (:2045-2054), and `_COMPACT_HIDDEN_TOOLS` (:2002-2009) hides the granular writers from the prod
  compact step-2 menu. What remains is that they're hidden by a *second, parallel seam* while
  staying registered: they still ride every step-3 prose call as bare stubs (~300-400 tok,
  :1950-1961), inflate the full-schema guard default, and stay dispatchable. The move: **delete
  `_COMPACT_HIDDEN_TOOLS` and stop registering the granular tools** behind the family/`granular`
  sub-flag at the registration choke point — one seam that removes them from compact, bare, full,
  and dispatch together. Legacy/habituated calls to `record_bond` etc. become unknown-tool errors
  — accepted (zero-backward-compat stance). Savings are measured, not estimated: `/timing`'s
  standing-cost section (Increment 0) shows the before/after per variant.
- **Make the data earn its keep (value win).** Optionally feed a *compact* social-map / arc summary
  (`get_social_map` :7121 output, truncated) into `gather_pov_context` behind
  `family_enabled("relationships")`, so relationships that are recorded actually reach the narrator
  instead of sitting unread. This is the "repair" half — turns a write-only graph into a read path.

> **Cross-plan sequencing ([[IDENTITY]], rev. 2026-07-06):** IDENTITY's resolve-on-write lands at
> choke-point builders (`_person_pwd`/`_person_tag` + a free-text tag sanitizer), not per-tool, so
> wrapper-only registration here neither breaks nor is blocked by identity routing — the wrapper
> delegates to granular writers that build through the same choke points. If yomi is **removed**,
> `/yomi/{slug}` drops out of IDENTITY's durable surface automatically. Either plan can land first.
> IDENTITY also flags a sibling of the yomi wiring bug: `know:` tags are read (rpjot.py:3929/7577/
> 8179) but written nowhere — same repair-or-remove disposition applies (its KNOW DEBT).

**Increment 5 — document the disposition runbook (turn future changes into a checklist).**
Add a short runbook (appendix here): to act on family X → `grep 'family="X"'`; for **minimize**,
flip its granular/wrapper flag; for **repair**, add/remove the `gather_pov_context` read guard; for
**remove**, delete tools + `PWD_X`/`TAG_X` + cache keys + slash branch + `_SLASH_PREFIX` +
`FEATURE_FAMILIES` row + tests. The family tag + `FEATURE_FAMILIES` table make every touchpoint
greppable, so all three dispositions are a bounded, mechanical edit.

## Files
- `rpjot.py` — (Inc 0) `_tool_tok_map` after :2277, timed `_safe_dispatch` shell + accumulators,
  `_last_turn_report` snapshot at :2551, `timing_report()`; decorator gains `family` (+ optional
  `granular` sub-flag); `_rp_tool_meta` 4-tuple + unpack (:2265); `enabled_families` + skip in
  `register_all_tools`; `FEATURE_FAMILIES` table; per-family token log from `_tool_tok_map`;
  family read-guards in `gather_pov_context` (:3928/:3931); YOMI repair (wire
  `_gather_yomi_for_scene` in) or removal; granular-tool deregistration replacing
  `_COMPACT_HIDDEN_TOOLS` + compact social-map read.
- `play.py` — (Inc 0) `/timing` in `_SLASH_EXACT` + `_HELP_TEXT` + dispatch block; parse
  `RPJOT_DISABLE`; set `engine.enabled_families`; guard slash commands + `_SLASH_PREFIX`; `/yomi`
  guarded (repair) or deleted (remove).
- `catjot.py` — (Inc 0, optional/severable) `LAST_USAGE` side channel in `call_llm` (:1362).
- `test_rpjot.py` — (Inc 0) `TestToolTiming` (map coverage, dispatch event, error-JSON unchanged,
  pre-turn render); a disabled family drops exactly its tools and leaves the 12 core pins intact;
  `enabled_families` default keeps today's set; relationships-unified still exposes
  `record_relationship`; (remove branch only) drop the `save_yomi` mention.

## Verification
0. `/timing` renders both pre-turn (standing costs + notice) and post-turn (executed + standing +
   session); after `RPJOT_DISABLE=<family>` its standing section shows that family at 0 across all
   variants.
1. `python -m pytest test_rpjot.py test_context.py` green (core pins at :741 unaffected;
   `TestToolTiming` green).
2. Registration log prints per-family tool count + token cost, so "weight" is visible each boot.
3. `RPJOT_DISABLE=yomi` / `=relationships` → `_tool_schemas` omits exactly that family's tools; a
   turn still runs. Unified relationships still exposes `record_relationship`;
   `grep -n _COMPACT_HIDDEN_TOOLS rpjot.py` is empty; bare-stub + full-schema savings confirmed by
   `/timing`'s standing-cost section.
4. **YOMI repair:** a recorded yomi insight for a present character surfaces in that character's POV
   context (was invisible before); docstring at :5663 now matches behavior. **YOMI remove (if chosen):**
   `grep -ri yomi rpjot.py play.py` is empty; token log drops by 436.
5. **Relationships value check:** with the compact social-map read wired in, a recorded bond/wound is
   reflected in later narration without the model having to call `get_relationship_arc` first.
