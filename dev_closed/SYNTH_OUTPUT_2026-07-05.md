# SYNTH_OUTPUT 2026-07-05 — consolidated plan for the current open four

Produced by applying [[SYNTH_UPGRADES]] to `dev_open/` on 2026-07-05. Batch:
**MOVEMENT_TREE, PLAYER_PHASING, SLASH_IMPROVEMENT, EXEC_DEBUG** (SYNTH_UPGRADES
self-excluded). This file is a work item: it retires to `dev_closed/` once executed, or
is regenerated if the open set changes.

## 1. Context

Four independently-authored plans, all currently doc-only. Individually coherent;
together they share code surfaces and afford emergent functionality none of them names.
This plan orders them, pulls shared refactors forward, folds in the emergent wins, and
carries a fully-hands-off execution contract.

**Decisions locked (with the user):** separate output file (this doc); all four emergent
items in-scope; soft `[BUDGET]` measure-and-warn on context growth; fully-hands-off
autonomy.

## 2. Per-plan digest

- **SLASH_IMPROVEMENT** — `/construct <subject>` recall page (present vs used-both-ways
  vs recency) + trim `/location /people /stats`. Adds `_last_ctx_now: set[int]` capture
  in context assembly; extracts `scene_debug_report` `_row`/`_note_toks` (rpjot.py:6560)
  to module level; reuses canonicalizers, `gather_pov_context`, `ContextBundle`. New:
  `/construct` in `_SLASH_PREFIX`. Doc-only.
- **EXEC_DEBUG** — `/debug <desc>` writes a self-contained `debug/*.md` report + appends
  `debug/index.jsonl`. Adds `build_debug_report` + `_scan_recent_activity` (a log-tail
  scanner keyed on `[TURN k]` blocks); resolves the log via `_h_file.baseFilename`
  (rpjot.py:97/122); reuses `scene_debug_report`/`history_report` and retained per-turn
  attrs (`_cast_warnings`/`_loc_warnings`/`_turn_stationary`/`_seed_status`). Memory
  category consumes `/construct`. New: `/debug` in `_SLASH_PREFIX`. Doc-only.
- **PLAYER_PHASING** — names per-turn work by phase (RW read-window / PS post-submit /
  PD post-display); adds `EXPECTED_READ_S` (rpjot.py:~355) + a `[PHASING]` readout.
  Composes with the seed machinery and names MOVEMENT_TREE's cartographer as a future RW
  tenant. Design-only, instrumentation — no correctness code.
- **MOVEMENT_TREE** — locale graph: `adj:` edge tags, an LLM cartographer
  (`speculate_locale`/`_consume_locale_seed` mirroring `speculate_step1`/`_consume_seed`
  rpjot.py:2230/2292), adjacency-aware `resolve_destination`/`compute_traversal`
  (rpjot.py:2639/2599), `_scan_room_drift` mirroring `_scan_cast_drift` (rpjot.py:2271),
  and a `_locale_graph_block()` injected into `_build_baseline_context`/
  `_build_seeded_message`. Phases 0–3, Tier 1–3 tests. Picks up LOCATION_MARKING's
  deferred adjacency redesign. Doc-only.

## 3. Compatibility matrix

| Surface | MOVEMENT_TREE | PLAYER_PHASING | SLASH_IMPROVEMENT | EXEC_DEBUG |
|---|---|---|---|---|
| `play.py` `_SLASH_PREFIX` / `_HELP_TEXT` | — | — | extend (`/construct`) | extend (`/debug`) |
| render helpers (`_row`/`_note_toks`, rpjot.py:6560) | — | — | **extract** | reuse |
| context assembly / `_last_ctx_now` | extend (inject graph block) | — | **add capture** | consume |
| telemetry prefixes | add `[LOCALE]`/`[CARTO]` | add `[PHASING]` | — | **scan** |
| idle-worker / seed (`_idle_worker`, `speculate_*`) | add tenant | name/measure tenant | — | — |
| drift scan (`_scan_cast_drift`, rpjot.py:2271) | mirror → `_scan_room_drift` | — | — | read `_*_warnings` |
| canonicalizers (`_canonicalize_room/_object`) | extend (edges) | — | reuse | — |

**No `conflict` cells.** All interactions are `touch`/`extend`/`reuse` — the batch is
additive.

## 4. Overlap & dedup (shared refactors pulled forward)

1. **Render helpers** — SLASH_IMPROVEMENT extracts `_row`/`_note_toks`; EXEC_DEBUG and
   `/stats` reuse them. → extract once, in Phase 0.
2. **`_last_ctx_now`** — introduced by SLASH_IMPROVEMENT for the `/construct` census;
   EXEC_DEBUG's memory forensics ("was X actually fed?") wants the identical set. → build
   once, in Phase 0; both consume.
3. **Dispatch region** — `/construct` and `/debug` edit the same `_SLASH_PREFIX` +
   `_HELP_TEXT`. → sequence Phases 1–2 adjacently.
4. **Drift scan** — `_scan_cast_drift` and MOVEMENT_TREE's `_scan_room_drift` are the
   same shape. → refactor to `_scan_drift(kind)` once, in Phase 0.

## 5. Emergent (all in-scope per user)

- **E1 — `_last_ctx_now` as shared substrate.** One capture hook serves `/construct`
  census *and* `/debug` memory forensics. (Phase 0b.)
- **E2 — telemetry-prefix registry.** Replace EXEC_DEBUG's hardcoded interesting-prefix
  /category set with a registry + `register_prefix(prefix, category, meaning)` seam;
  MOVEMENT_TREE (`[LOCALE]`/`[CARTO]`) and PLAYER_PHASING (`[PHASING]`) self-register. One
  source of truth for "what mechanisms announce." (Phase 0c; consumed in 2/3/4.)
- **E3 — unified `_scan_drift(kind)`.** Cast now, room via MOVEMENT_TREE, object later;
  `_{kind}_warnings` surfaced uniformly in `/stats`, `/construct`, `/debug`. (Phase 0d.)
- **E4 — shared introspection/render module.** `/stats`, `/construct`, `/debug` render
  fixed-width tables from one module instead of drifting copies. (Phase 0a — the render
  extraction is its seed; the module grows as 1–2 consume it.)

## 6. Ordered phases (fully hands-off · worktree-isolated per phase · D-locked)

- **Phase 0 — shared substrate (lands first; everything depends on it).**
  - **0a** extract `_row`/`_note_toks`/`HDR`/`SEP` from `scene_debug_report` into a shared
    introspection module (E4 seed).
  - **0b** add `_last_ctx_now` capture in `build_scene_context_map`/`WorldStateStep.run`
    (E1).
  - **0c** telemetry-prefix registry + `register_prefix()` seam; migrate EXEC_DEBUG's
    scanner to read it (E2).
  - **0d** refactor `_scan_cast_drift` → `_scan_drift("cast")` producing `_cast_warnings`
    unchanged (E3).
  - **0e** add the `[BUDGET]` baseline-token measure line (soft warn; no gate).
  - *Gate:* full deterministic suite green; no behavior change (pure refactor + additive
    capture).
- **Phase 1 — SLASH_IMPROVEMENT.** `/construct` + trim `/location /people /stats`, on 0a
  helpers + 0b capture. *Gate:* SLASH_IMPROVEMENT Tier-1 tests (census math, witness
  scaling) green.
- **Phase 2 — EXEC_DEBUG.** `/debug` on 0a/0b/0c/0d; memory category consumes
  `/construct`; scanner reads the registry; reads `_scan_drift` warnings. Edits the
  dispatch region adjacent to Phase 1. *Gate:* EXEC_DEBUG Tier-1 tests (scanner block-
  split, keyword routing, index.jsonl round-trip) green.
- **Phase 3 — PLAYER_PHASING.** Phase registry + `[PHASING]` readout; `register_prefix`
  for `[PHASING]`. Pure instrumentation, low risk. *Gate:* deterministic suite green;
  `[PHASING]` line emits with correct fields.
- **Phase 4 — MOVEMENT_TREE.** Locale graph; `register_prefix` for `[LOCALE]`/`[CARTO]`;
  `_scan_drift("room")`; inject `_locale_graph_block()` under the `[BUDGET]` warn.
  `/construct` place view + `/debug` location category then consume adjacency. Largest,
  most independent — lands last. *Gate:* Tier-1 deterministic (`TestLocaleGraph`) green;
  live tiers only after `:5000` preflight.

## 7. Cross-plan risks & budget

- **Combined context growth.** MOVEMENT_TREE's graph block enlarges the step-1 baseline,
  which `_last_ctx_now` then captures, atop the already-shipped compaction. Policy:
  **soft `[BUDGET]` measure-and-warn** (Phase 0e emits the baseline; Phase 4 must keep
  the delta visible). No hard gate this batch.
- **Dispatch merge churn.** `/construct` and `/debug` touch the same region — Phases 1–2
  adjacent, single reviewer pass.
- **Test-endpoint fidelity.** A dead `:5000` fake-greens live tiers — preflight before
  any live step (Phases 2/4). Set `RPJOT_MC_ALIASES` where live movement tests need it.
- **Refactor blast radius.** Phase 0 is a pure refactor + additive capture; its gate is
  "no behavior change," so downstream phases build on a stable base.

## 8. Combined verification

Per-phase gates as in §6. End-to-end: after Phase 4, the full suite is green; `/stats`,
`/construct`, `/debug` all render from the shared module; the prefix registry lists
`[LOCALE]`/`[CARTO]`/`[PHASING]`; `_scan_drift` reports cast + room; the `[BUDGET]` line
shows the cumulative baseline delta.

## 9. Autonomous execution contract (this batch)

Decision-locked (no open questions remain). One worktree-isolated agent per phase, run in
0→4 order, single launch approval, no inter-phase human gate; a failed phase gate halts
and reports. Endpoint preflight before any live tier; `[BUDGET]` warn respected;
consolidated end-of-run report (per-phase pass/fail, budget delta, any dropped work).

## 10. Status

Emitted 2026-07-05, doc-only. Retires to `dev_closed/` once executed. Regenerate via
[[SYNTH_UPGRADES]] if the `dev_open/` set changes before execution.
