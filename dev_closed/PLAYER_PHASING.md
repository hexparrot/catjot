# PLAYER_PHASING

Formalizes **where** each unit of per-turn work runs relative to the player:
while the player is *reading* the last narrative, or *after* the player submits
the next input. The separation already exists in the code (the idle-window
precompute path, `RPJOT_BG_SEED` / `RPJOT_BG_REFRESH`) but was never named,
mapped, or measured. This document supplies the vocabulary, the switching
seam, the "this-turn-vs-next-turn" trade-off, and the instrumentation needed to
tune the balance.

**Status:** design only. No code changes accompany this document. §4's readout
and §2's registry are specifications for a later implementation pass. The one
knob this doc introduces, `EXPECTED_READ_S`, is a *human* tuning target, not an
auto-governor — see §4.

**Composes with:** the seed machinery (`speculate_step1` / `_consume_seed`),
`LOCATION_MARKING.md` (why location marking is pinned to the active phase), and
`movement_tree.md`'s future Cartographer (a plausible next reading-window
tenant).

---

## §1 — The three phases

A turn is not one span of time; it is three, and only one of them is felt as
latency. Name them against the current code seams:

### RW — Reading Window
Narrative displayed (`play.py:1000`) → the player submits real input
(`play.py:964`). This is wall-clock the player spends *reading and thinking*,
not waiting. Work placed here runs **off the main thread** in the daemon idle
worker (`start_idle_work` → `_idle_worker`, `play.py:480-524`).

Work in RW is **speculative**: it executes against turn N's post-state *before*
turn N+1's input exists. Two consequences that gate everything in §2–§3:
- it must be **input-independent** (there is no input yet to depend on);
- its result must be **revalidated on consume**, because state may have moved by
  the time it is used.

Current RW tenants: `speculate_step1` (the step-1 seed) and
`refresh_system_message` (entropy paraphrase).

### PS — Active Post-Submission
`input()` returns → narrative displayed. **This is the felt-latency critical
path** — what the player experiences as "the game thinking." It is the `run_turn`
pipeline (`rpjot.py:2110-2203`):

    seed consume → step 1 → _remark_location → step 2 → _reconcile_loc_hint → step 3

Every second here is a second the player waits at a blank prompt. The entire
point of phasing is to move as much off PS as safely possible.

### PD — Post-Display housekeeping
Narrative displayed → RW worker launched (`play.py:1000-1028`): summary jot,
history append, `compact_history`, and the sync refresh fallback. PD is *not* on
the felt-latency path — the player is already reading — **but it runs on the main
thread at the front of the reading window**, so it delays when the RW worker can
even start. PD time is therefore budget stolen from RW before RW does any work.

### The boundary artifact: `wait_s`
RW work that does **not** finish before the player submits is paid at the **top
of PS**, at the join (`play.py:947-953`), recorded as `wait_s`. So the real felt
latency of a turn is:

    felt = wait_s + step1 + step2 + step3

`wait_s` is the penalty for over-filling the reading window. Driving it toward
zero — while keeping RW as full as possible — is the balance this doc exists to
strike.

### One turn, on a timeline

```
  ── turn N ───────────────────────────────────────────────────────────────►

  [ display N ]                                                    player
       │  PD (main thread: jot, compact) ── launch RW worker           │
       │                                        │                      │
       │        RW worker runs  ░░░░░░░░░░░░     │  (player reading)    │
       │        seed + entropy   spec_s/refresh_s│                      │
       ▼                                         ▼                      ▼
   display done ──────────── read_s ──────────────────────────► player submits
                          (the free budget)                            │
                                                                       ▼
                                                          join ── wait_s ──┐
                                                                           ▼
                                     PS: seed→s1→remark→s2→reconcile→s3  (FELT)
                                                                           │
                                                                           ▼
                                                                    [ display N+1 ]

  bg = spec_s + refresh_s   fits under read_s ⇒ wait_s = 0 ⇒ bg is FREE
  bg > read_s               ⇒ overflow billed as wait_s at the join
```

---

## §2 — The switching abstraction (target seam)

Today, phase placement is **hardcoded**: `_idle_worker` names its two RW calls
directly, and `run_turn` runs its PS units as an inline sequence. Moving a unit
between phases means editing both call sites and reasoning about correctness from
scratch. This section specifies a registry that makes the placement a **data**
decision — one field to flip — so the trade-off in §3 can be exercised freely.

### The work-unit registry

A table of the engine's per-turn work units, each carrying:

| field | meaning |
|---|---|
| `name` | unit id (`seed_step1`, `entropy_refresh`, `remark_location`, …) |
| `phase` | `RW` \| `PS` \| `PD` — **the one field you flip to move it** |
| `input_dependent` | if true, can **never** be RW (no input exists yet) — the eligibility gate |
| `lag` | prose-lag turns introduced by RW placement (see §3) |
| `revalidate` | how a RW result is checked stale on consume — e.g. seed's `_seed_state_snapshot` + delta |
| `cost_s` | typical wall-clock, informed by §4 measurement |

Two dispatch sites read the table instead of naming units directly:
- `_idle_worker` (`play.py:480`) runs every `phase == RW` unit whose
  `input_dependent` is false;
- `run_turn` (`rpjot.py:2110`) runs every `phase == PS` unit at its pipeline slot.

Flipping a unit's `phase` from `PS` to `RW` moves the work; the dispatch sites
need no change. The gate `input_dependent` mechanically forbids the incoherent
moves (you cannot speculate step 2 — it needs the player's input).

This registry is the **target seam**, described here with its exact call sites
named. It is *not* built in this pass.

### Current census — what is even movable

| unit | code | phase today | movable to RW? |
|---|---|---|---|
| `seed_step1` | `speculate_step1` `rpjot.py:2230` | **RW** | already there |
| `entropy_refresh` | `refresh_system_message` `play.py:391` | **RW** | already there |
| seed consume | `_consume_seed` `rpjot.py:2292` | PS | no — consumes RW output at turn start |
| step 1 | `WorldStateStep.run` `rpjot.py:844` | PS | *speculatively*, yes — that **is** `seed_step1` |
| `remark_location` | `_remark_location` `rpjot.py:1979` | PS | **no** — needs real input + step-1 doc (see `LOCATION_MARKING.md`) |
| step 2 | `ComplianceStep.run` `rpjot.py:1161` | PS | **no** — `input_dependent`, mutates state |
| `reconcile_loc_hint` | `_reconcile_loc_hint` `rpjot.py:2071` | PS | **no** — depends on step-2 results |
| step 3 | `ProseStep.run` `rpjot.py:1376` | PS | **no** — `input_dependent`, the prose itself |
| cast-drift scan | `_scan_cast_drift` `rpjot.py:2207` | PS | detection only; could move to PD |
| `compact_history` | `play.py:563` | PD | already off the felt path |
| summary jot | `Note.jot` `play.py:1002` | PD | already off the felt path |

The lesson at a glance: the felt path (step 2, step 3, remark, reconcile) is
almost entirely **input-dependent** and therefore pinned to PS. The one large
input-independent cost — step-1 world-state assembly — is *already* speculated as
the seed. Further RW gains come from **new** input-independent work (e.g. a
Cartographer prebuild), not from relocating existing PS units.

---

## §3 — "This turn vs. next turn": the lag / cost map

Every placement decision trades two axes. Spell both out so the choice is
mechanical:

| placement | applies | prose lag | latency cost |
|---|---|---|---|
| **PS** | **this turn** | **zero** | **full** — on the felt critical path |
| **RW** | **next turn** (consumed at start of N+1) | **0, unless state changed and revalidation fails → 1** | **hidden** if it fits the reading budget; overflow billed as `wait_s` |

### Why RW lag is usually zero
A speculative RW result is computed against turn N's post-state. When the player
submits turn N+1, the seed is revalidated: `_consume_seed` checks
`_seed_state_snapshot` (location, cast, scene — `rpjot.py:2222-2228`) and runs a
cheap delta. If the world did not meaningfully move, the seed is adopted (`seed=hit`
/ `hit-unchanged` in `[TIMING]`) and effective lag is **zero**. If it did move
and revalidation cannot reconcile it, the seed is dropped (`seed=miss`) and the
work is **rebuilt on PS** — no stale prose reaches the player, but that turn pays
the full step-1 cost on the felt path. The one-turn "lag" is thus not stale
output; it is the *risk of a miss* that pushes cost back onto PS.

### The invariant
**Moving work to RW trades guaranteed latency for conditional staleness.** A unit
is a good RW candidate only when all three hold:
1. `input_dependent == false` — it can be computed before the input exists;
2. it has a **cheap revalidation** — a miss must be detectable and correctable
   without leaking stale prose;
3. its `cost_s` **fits the reading budget** — otherwise the "hidden" cost surfaces
   as `wait_s`.

### Worked examples (current units)
- **`seed_step1` — excellent candidate.** Input-independent (runs on
  `_SPECULATIVE_INPUT`), snapshot+delta revalidation, large cost moved off PS.
  The paradigm case.
- **`entropy_refresh` — excellent candidate.** Purely cosmetic (paraphrases the
  system message); **no lag at all** because there is no correctness to stale —
  worst case is a slightly older phrasing. Pure win whenever it fits the budget.
- **`remark_location` — not movable.** Needs the real input *and* the step-1 doc
  to pick the CURRENT ROOM; both are unavailable during RW. Pinned to PS between
  step 1 and step 2 by design (`LOCATION_MARKING.md` §3.1).

---

## §4 — Instrumentation: the balance readout

To strike the balance you must see all three quantities at once. Two of the three
are already logged; the linchpin — the reading window itself — is not.

### The three quantities and their sources

| # | quantity | symbol | status | source |
|---|---|---|---|---|
| 1 | background work cost | `bg = spec_s + refresh_s` | **measured** | `_idle_worker` `play.py:492-498` |
| 2 | time until player submits | `read_s` | **NOT measured** | new — see below |
| 3 | active post-submission | `active = step1+step2+step3` (+`wait_s`) | **measured** | `run_turn` t0..t3 `rpjot.py:2111-2203` |

`read_s` (#2) is the **free budget** every RW unit must fit inside, and it is the
one number the engine never records. Today `input("> ")` (`play.py:837`) has no
clock around it.

### Measuring `read_s`
Capture a timestamp at end of narrative display (`play.py:1000`) and at
real-input acceptance (`play.py:964`):

    read_s = t_submit − t_display

**Meta-command nuance:** `/stats`, `/prompt`, `/people`, … `continue` without
submitting a real turn. The timer must therefore span display → *next real*
submission, so time spent inspecting counts (correctly) as part of reading, not
reset by each meta detour.

### One economical extra: `pd_s`
`pd_s` = display → RW-worker-launch (`play.py:1000-1028`): the PD housekeeping
that runs on the main thread and eats the *front* of the reading window before
`bg` can even start. Cheap to capture, and it completes the "where did the time
go" picture — without it, `bg` looks like it has the whole `read_s` to work in
when it actually has `read_s − pd_s`.

### The balance line (spec — not emitted this pass)

```
[PHASING] turn=N read=9.2s | pd=0.3s bg=6.1s(spec5.4 refresh0.7) wait=0.0s
          | active=4.3s(s1 1.2 s2 2.1 s3 1.0) | budget=8.0s headroom=+1.9s hidden=100%
```

- `budget` = `EXPECTED_READ_S`, the CONST (see below) — the tunable knob.
- `headroom = budget − pd − bg` — **positive** ⇒ room to move more work into RW;
  **negative** ⇒ back off, `wait_s` will appear.
- `hidden = 1 − wait/bg` — fraction of background work fully absorbed by the
  reading window (100% = every background second was free).

### `EXPECTED_READ_S`: a constant, calibrated from real play
Reading duration is modeled as **a single fixed constant**, not a tracked
distribution. Output length varies turn to turn, but it is fine to assume
roughly-equal reading time per turn — the max/min are unimportant. The constant
lives beside the other engine constants (`rpjot.py:355-363`) and is the *budget*
against which `headroom` is computed.

Real measured `read_s` exists for exactly one purpose: **to calibrate that
constant economically**. The loop (measure-first — the human is the governor,
there is no auto-skip):

1. Play normally; watch the logged `read_s` values.
2. Pick a comfortable representative `EXPECTED_READ_S` — one number reflecting how
   long *you* actually take, from real evidence.
3. Move work into RW (flip a unit's `phase` per §2) only while `headroom` stays
   positive across turns.
4. If `wait_s` starts appearing / `hidden` drops below 100%, you have overfilled
   the window — back a unit out.

The constant is the decision; the measurement is the evidence. Nothing here
auto-governs — it hands you a clean readout so you can tune the `RPJOT_BG_*`
flags and (future) `phase` fields by hand.

---

## §5 — Scope & non-goals

- **Doc-only this pass.** The registry (§2) and the `[PHASING]` line (§4) are
  designs with named seams, to be built in a follow-up. No code changes ship with
  this document.
- **Measure first, gate later.** No automatic skip/defer of background work. The
  engine logs the balance; the human tunes.
- **Single reader.** Tuned to one player's own reading pace via one constant.
  Multi-reader / adaptive-per-user modeling is explicitly out of scope.
- **Acceptance target for the follow-up:** a later `read_s` / `[PHASING]`
  implementation is validated under `TestTimingTelemetry` (`test_rpjot.py:2117+`),
  extending the existing `[TIMING]` coverage.
