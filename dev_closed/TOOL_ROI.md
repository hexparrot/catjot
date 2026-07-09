# RPJOT Tool ROI Audit — 2026-07-07

> **Resolution log (2026-07-07, branch `tool_roi_exec`):** EXECUTED.
> §1–3 dead families: play.py's `RPJOT_DISABLE` default is now
> `"yomi,conscience,interiority"` (explicit `RPJOT_DISABLE=""` re-enables); the
> `^` sigil directive now points at core `record_knowledge` instead of the
> unregistered `record_conscience`/`record_secret`. §4: `get_social_map` retired
> outright — method deleted, family entry removed, dead `social_map` cache-drops
> trimmed. §A: new `_CANON_FIRST_DIRECTIVE` rides the initial step2 user message
> for the same action/speech prefixes the reactive nudge covers (nudge kept as
> fallback); common path collapses from 3 LLM calls to write-then-terminate.
> §B: subsumed by §A (the zero-write round after a call-1 write IS the
> terminator). §C: speculation untouched, as recommended. §D: step1 loop now
> strips/dedupes/batches followup_instructions per turn (byte-shape identical to
> step2's U6 batching). Guard: `log_session_tool_stats()` dumps one
> `[TOOL_STATS]` line per registered tool (zero-call rows included) at
> game_loop exit; prefix registered. Measured: step1 schema 1603→1296 tok,
> step2 compact 1788→1495 tok, 26→21 registered tools. Suite: 620 passed
> (3 new tests; 2 updated for the retirements). Core zero-use tools
> (find_character, navigate_to, …) intentionally NOT pruned — scenario-limited
> evidence; re-check via [TOOL_STATS] after a scene-moving session.

Source data: the one real (non-mocked) instrumented run,
`/home/user/QubesIncoming/work/debug_20260703_201318.log` — 58 turns, 2026-07-03,
live LLM latencies from the TOOL_TIMING/[TIMING] instrumentation (branch
`rp_revise`). `git/catjot/debug.log` is almost entirely the mocked-LLM test era
(every [TIMING] value 0.0s) and was used only for schema/family weights and
all-time never-called checks.

## Turn shape (58 turns, 2525s wall, 43.5s/turn)

| Step | Share | Mean | LLM calls/turn | What it buys |
|---|---|---|---|---|
| step1 (reads + world-doc synthesis) | 36.6% | 15.9s | 1.66 | 305–525 tok world doc |
| step2 (canonical writes) | 37.0% | 16.1s | 2.95 | usually **one** `record_event` |
| step3 (prose) | 26.4% | 11.5s | 1 | 230–470 tok, the deliverable |

Per-call latency: step1 call1 (read requests) 6.0s mean, call2 (synthesis)
15.2s; step2 call1 10.3s, call2 3.9s, call3 2.0s.

## Dead costs — prune

### 1. yomi family — zero ROI, provably dead  ⚠ worst
`get_yomi` was the **single most-called tool in the run** (83 calls, 1.43/turn)
and returned `[no yomi recorded]` **83/83 times**. `save_yomi` has never been
called in this run **or anywhere in the 368k-line all-time debug.log** — the
write half is never used by the model, so the read can never become non-empty.
Cost: 156 tok in every step1 schema + 55 tok in every step2 compact schema +
~1.4 wasted request/response round-trips per turn feeding the 15.2s synthesis
call. Action: `RPJOT_DISABLE=yomi` (toggle already exists, registration-time
choke point). If yomi is ever wanted, the fix is write-side prompting in step2,
not the read.

### 2. conscience family — same pattern
`get_conscience` 63/63 empty (≈1.1/turn); `record_conscience` never called in
the real run. 151 tok step1 + 81 tok step2 per call. Action: add to
`RPJOT_DISABLE`.

### 3. interiority family — write-only tool that is never written
`record_interior` (the Inc-4 consolidation wrapper): **zero calls**; costs 157
tok in every step2 compact schema. No read half exists to justify it. Action:
add to `RPJOT_DISABLE`.

### 4. get_social_map — empty derived view
15/15 empty. It derives from relationship records that barely exist (4 writes
all run). It shares the `relationships` family with `get_relationship_arc`,
which **does** contribute (28/62 non-empty, improving as records accrue), so
this is a code-level deregistration, not a family toggle.

Dead schema weight if 1–3 land: step1 −307/1753 tok (−17.5%) on every step1
call; step2 compact −293/1788 tok (−16.4%) on every step2 call; plus ~2.5
empty read round-trips per turn removed from step1 call1 output and call2 input.

## Low-signal, keep for now (scenario-limited evidence)

Never called in the run but plausibly scene-shaped: `find_character`,
`search_world`, `navigate_to`, `place_object`, `record_knowledge`,
`save_object` (6 of 19 core tools). The run was a stationary two-NPC scene
(the stationary nudge fired all 58 turns), so movement/object tools never had
a trigger. Re-check after a run with scene changes before pruning core.

## Loop-shape improvements (the big latency wins)

### A. step2 call1 is 23% of the whole run and produces nothing
In **55/58 turns** step2's first call made zero canonical writes, drew the
zero-canonical nudge, and only then did call2 write. Call1 costs 10.3s mean —
595s total, ~576s of it waste. The nudge text evidently *is* the effective
prompt; the initial step2 instruction is not. Fix: fold the zero-canonical
nudge wording into the initial step2 prompt (and/or force tool use on call1).
46/58 turns end at `canonical=1` (a single `record_event`) — a single forced
write call would serve the common case. Projected: step2 16.1s → ~5–6s/turn.

### B. step2 call3 is pure termination overhead
2.0s × 55 turns = 112s (4.4%) just to observe "no more writes". If A lands
(writes on call1), the loop can end after the first zero-write round — one
call saved.

### C. step1 speculation WORKS — extend it, don't prune it
21 speculative seeds ready → 20 turns completed step1 with **0 tool rounds**
(one ~11–13s call instead of 6.0s + 15.2s). Near-perfect conversion. Only
21/58 turns had a seed ready, though (docs take 19–29s of idle LLM time to
build). Pruning the dead-family reads (§1–3) makes seed-building faster too —
more idle windows will finish in time.

### D. followup_instruction boilerplate
Static instruction text is re-sent inside read results (43× per run). Move it
into the step prompt once; return bare data.

## Projected effect
A + B + dead-family pruning: ~43.5s/turn → ~30–33s/turn (25–30%), with
step1/step2 schemas ~17% lighter and step1 synthesis input free of ~2.5
always-empty payloads per turn. No behavior loss: everything removed returned
sentinel-empty 100% of the time or was never invoked.

## Guards
- /timing already accumulates per-tool calls/errors/ms/result_toks — add a
  session-end log dump so dead-tool evidence lands in debug.log, not just the
  in-memory report (this audit had to reconstruct counts from [STEP1]/[TOOLS]
  lines).
- Lint idea, mirroring the WS audit: flag any registered read tool whose
  paired writer was never dispatched in the last N sessions ("read-only ghost
  family").
