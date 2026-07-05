# SYNTH_UPGRADES — the plan-of-plans synthesizer (permanent dev_open resident)

<!-- permanent: true -->

A **reusable template**, not a feature. It reads every *other* open plan in `dev_open/`
and emits a single consolidated, ordered, conflict-checked, autonomous execution plan —
a "plan of plans." It exists because independently-authored design docs share code
surfaces, imply an order, hide conflicts, and afford functionality no single doc
mentions. `dev_closed/FIXUP.md` was the hand-rolled precedent (a one-off unified plan
for PROMPTING + TOOL_CYCLE); this generalizes that act so it never has to be improvised
again.

## 0. Standing status (read first)

- **Permanent.** This doc lives in `dev_open/` forever. It is a recurring *process*, not
  a unit of work, so the [[dev-doc-dirs-convention]] "graduate to `dev_closed` once the
  machinery ships" rule **does not apply to it**. The `<!-- permanent: true -->` marker
  above exempts it.
- **Self-excluding.** When run, the input batch is every `dev_open/*.md` **except** this
  file and any other `permanent:`-marked meta-plan. It never synthesizes itself.
- **Non-destructive.** It implements nothing and moves nothing. Each *source* plan still
  ships its own code and graduates to `dev_closed/` individually. Only the **emitted
  output file** is a work item — it retires to `dev_closed/` once executed, or is simply
  regenerated when the open set changes.

## 1. When to run · inputs

- **Trigger:** ≥2 open plans worth building together, or any time you want the current
  open set ordered and de-conflicted before starting implementation.
- **Input set:** `dev_open/*.md` minus this file minus other `permanent:` docs.
- **Output:** one new file `dev_open/SYNTH_OUTPUT_{stamp}.md` (schema in §4).

## 2. The synthesis procedure

Run these steps in order. Steps P1–P2 are pure extraction (parallelizable, e.g. one
Explore agent per source doc); P3–P8 are analysis and emission.

- **P1 — Extract.** For each source plan, produce a structured digest with this fixed
  schema (so every run is comparable):
  1. title & one-line goal
  2. status (design-only / partially shipped / …) — quote the self-report
  3. files touched (every source file it proposes to modify)
  4. key functions / classes / constants touched or added, **with file:line anchors**
  5. mechanisms / subsystems involved
  6. explicit dependencies & cross-references (build-on / after / supersedes / picks-up)
  7. new tools / commands / flags (slash commands, LLM tools, env vars, log prefixes)
  8. self-flagged risks & open questions
- **P2 — Shared-surface matrix.** Build a `plan × surface` grid over the recurring
  catjot surfaces: `play.py` dispatch/`_SLASH_*`/`_HELP_TEXT`; context assembly
  (`_build_baseline_context`/`_build_seeded_message`/`build_scene_context_map`); render
  helpers (`scene_debug_report` `_row`/`_note_toks`); telemetry/logging prefixes;
  idle-worker/seed (`speculate_step1`/`_consume_seed`/`_idle_worker`); canonicalizers
  (`_canonicalize_room`/`_canonicalize_object`); drift scanners (`_scan_cast_drift`);
  session state; tool menu/schema. Mark each cell **touch / extend / conflict**.
- **P3 — Overlap, ordering, conflict.** Any surface two plans both *extend* becomes a
  shared refactor that **must land first**. Any *conflict* cell is a blocker to resolve
  in P5. Additive-only sets have no hard conflicts but still carry ordering from shared
  refactors and read-after-write data dependencies (e.g. a capture hook a later plan
  consumes).
- **P4 — Emergent scan.** Ask what the *combination* affords that no single doc states:
  shared substrate that dedups two plans' work; a registry that turns N hardcoded lists
  into one seam; a scanner/renderer several commands could share; a data structure built
  once and consumed many times. Tag each **in-scope** or **note-and-defer**.
- **P5 — Cross-plan questions & decision-lock.** Surface whole-plan risks each doc
  misses (combined token budget, dispatch-region merge churn, test-endpoint fidelity,
  ordering hazards). Put the genuinely user-facing ones to the user; resolve *every*
  open question to a **locked decision** (FIXUP `D1..Dn` discipline) so the output plan
  is decision-complete.
- **P6 — Order into phases.** Shared substrate (P3 refactors + P4 in-scope substrate)
  → dependent features → largest / most mechanically-independent last. Give each phase a
  one-line dependency rationale. Keep plans that edit the same region adjacent to
  minimize churn.
- **P7 — Combined verification.** Per phase: its own deterministic (Tier-1) suite must
  stay green; **live-endpoint preflight before any live tier** (a dead `:5000` silently
  x-fails and fake-greens — always confirm a real completion first); a `[BUDGET]`
  measure-and-warn on combined context growth.
- **P8 — Emit.** Write `dev_open/SYNTH_OUTPUT_{stamp}.md` per §4.

## 3. Autonomous execution contract (default: fully hands-off)

The emitted plan is built to run end-to-end without human checkpoints between phases:

- **Decision-locked.** No open questions survive into the output; nothing blocks mid-run
  on a clarification.
- **Worktree-isolated per phase.** One agent per phase with `isolation: worktree`, so
  phases that touch overlapping files never corrupt each other's tree.
- **Dependency-ordered, no inter-phase gate.** Phases run in the P6 order; a single
  approval launches the whole run. (A phase whose gate fails halts the run and reports —
  it does not silently proceed.)
- **Gated on truth, not hope.** Each phase: Tier-1 green + endpoint preflight before any
  live step; `[BUDGET]` warn respected.
- **End-of-run report.** Consolidated pass/fail per phase, budget deltas, and any dropped
  work, surfaced at the end.

Downgrade to per-phase human gates or advisory-ordering-only is a per-run choice, but the
template's default is fully hands-off.

## 4. Output-plan schema (`SYNTH_OUTPUT_{stamp}.md`)

1. **Context** — the batch, why now, decisions locked.
2. **Per-plan digest** — the P1 schema for each source plan.
3. **Compatibility matrix** — the P2 grid.
4. **Overlap & dedup** — P3 findings; the shared refactors pulled forward.
5. **Emergent (in-scope / deferred)** — P4.
6. **Ordered phases with D1..Dn** — P6, decision-locked.
7. **Cross-plan risks & budget** — P5 risks + the `[BUDGET]` policy.
8. **Combined verification** — P7 per-phase gates.
9. **Autonomous execution contract** — §3, instantiated for this batch.

## 5. Guardrails

- Self-exclude; never retire this doc.
- Source plans graduate to `dev_closed/` individually as they ship — the synthesis does
  not move them.
- The output file is regenerable; when the open set changes, re-run rather than
  hand-patch.
- Prefer additive integration; a real conflict is resolved by a locked decision in P5,
  not deferred into the output.
- Endpoint preflight and soft budget warn are non-negotiable defaults.

## 6. Status

Template established 2026-07-05. Permanent. First application emitted as
`dev_open/SYNTH_OUTPUT_2026-07-05.md` (the current four: MOVEMENT_TREE, PLAYER_PHASING,
[[slash-improvement-doc]], [[exec-debug-doc]]).
