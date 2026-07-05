# PROMPT_MOVE — closing the navigate_to over-fire with a classification-gated nudge

Sprint handoff document. Everything needed to pick this up cold: the problem, the
measured data, the prompt-construction assessment, and the two-phase plan
(bakeoff refinement → production wiring). Status at time of writing: **bakeoff
validated, nothing wired into production yet** — production still ships the
inert caveat.

---

## 1. The problem

In step 2 of the RP engine (`ComplianceStep`, rpjot.py), models over-fire the
`navigate_to` tool on turns where the MC only *spoke* or *thought* — acting on
an NPC's invitation ("Evie beckons you to follow her down the corridor")
instead of the MC's own movement. The denoise sweep (`bakeoff_denoise.py`) put
this at **neg_navto 0/18 across all models**. The prose caveat that was supposed
to prevent it — the navigate_to tool description (rpjot.py `_tool_navigate_to`,
~2102) — is inert (see §3.1 for the structural reason why).

Fix direction (decided): **soft, classification-gated prompt injection**, not a
hard tool gate. Definitively classify each turn `stationary` (MC did not
physically move) vs `mobile`; on stationary turns only, inject a positive
DIRECTOR NOTE into the step-2 user message. `navigate_to` stays in the menu.

Why not the hard gate, even though it scored 100%: the user requires
**non-consensual movement to keep working** — overwhelmed by guards, tied up
and carried, unconscious, asleep in a moving vehicle. Those turns classify
stationary (the MC didn't move *themselves*) but the scene must still move.
With the tool removed from the menu, that is *impossible*; with a nudge, the
model can override when the story dictates. The current corpus does not yet
contain such a phrase — adding it is Phase A item 1, and it is the row where
hard_gate provably scores 0%.

---

## 2. Measured data (2026-07-03 sweep, `bakeoff_navnudge.py`, oracle classifier, ROUNDS=3)

Harness: 4 arms × 7 phrases × 3 rounds per model, production-shape step-2 call
(`_compact_step2_schemas`, system_role rules, WORLD STATE BRIEFING → NARRATOR
RULE → phrase [→ note]). Scoring: stationary correct = navigate_to did NOT
fire; mobile correct = it DID.

### Per-model (stat n/15, mob n/6)

| model                         | baseline | hard_gate | nudge_positive | nudge_negative |
|-------------------------------|----------|-----------|----------------|----------------|
| qwen3-30b-a3b-instruct-2507   | 12/15    | 15/15     | 15/15          | 15/15          |
| gemma4-31b-it                 | 12/15    | 15/15     | 15/15          | 15/15          |
| qwen3-vl-32b-instruct         | 12/15    | 15/15     | **12/15**      | **12/15**      |
| qwen3-235b-a22b-instruct-2507 |  9/15    | 15/15     | 15/15          | 15/15          |
| devstral2-123b                |  9/15    | 15/15     | 15/15          | 15/15          |
| granite41-30b                 | 12/15    | 15/15     | 15/15          | 15/15          |

(mobile was 6/6 for every model × arm — no regression anywhere)

### Aggregate scorecard

| strategy       | stationary | mobile | overall |
|----------------|-----------:|-------:|--------:|
| baseline       |        73% |   100% |     81% |
| hard_gate      |       100% |   100% |    100% |
| nudge_positive |        97% |   100% |     98% |
| nudge_negative |        97% |   100% |     98% |

### Per-phrase navigate_to firing rate (want stationary 0%, mobile 100%)

| phrase        | kind       | baseline | hard_gate | nudge_pos | nudge_neg | want |
|---------------|------------|---------:|----------:|----------:|----------:|-----:|
| neg_invite    | stationary |     100% |        0% |   **17%** |   **17%** |   0% |
| thought       | stationary |       0% |        0% |        0% |        0% |   0% |
| npc_moves     | stationary |      33% |        0% |        0% |        0% |   0% |
| dialogue_wish | stationary |       0% |        0% |        0% |        0% |   0% |
| action_nomove | stationary |       0% |        0% |        0% |        0% |   0% |
| pos_follow    | mobile     |     100% |      100% |      100% |      100% | 100% |
| pos_climb     | mobile     |     100% |      100% |      100% |      100% | 100% |

**Read:** the miss is entirely `neg_invite` (the beckon phrase — the original
0/18 shape). Both nudges close everything except a 17% residual on it, which is
qwen3-vl-32b (the only model at 12/15 under the nudges). Positive and negative
framing tied — framing mattered less than placement + conditionality (§3).
The nudge classifier defaults to an oracle (each phrase carries its true kind)
to isolate injection efficacy; `classify_heuristic()` reproduces the oracle on
this corpus (CLASSIFIER=heuristic mode).

---

## 3. Prompt-construction assessment (why the caveat failed, why the nudge works)

### 3.1 The caveat is structurally absent, not attentionally weak

`_compact_step2_schemas` (rpjot.py:1017) strips **all function-level
descriptions** from the 31 step-2 tools — it rebuilds each schema with only
`name` + `parameters`. The carefully-worded navigate_to caveat
(rpjot.py:2102–2113) **never reaches the model in step 2**. Only the five
param descriptions in `_COMPACT_KEEP_PARAM_DESCRIPTIONS` (rpjot.py:1008)
survive; `("navigate_to", "location_name")` is on that list, so the param
description (rpjot.py:2119–2127, which repeats the rule) does go out — but
buried inside schema JSON among 31 tools, the weakest attention position
available. Same fate for `begin_scene`'s sibling "opens on the MC's own
[MC action]" rule (~rpjot.py:2700). **The model has been selecting among 31
step-2 tools essentially by name.** This fully explains "prose caveat inert."

### 3.2 The composition order is already near-optimal; the winning lever is the last slot

Step-2 user message (`ComplianceStep.run`, rpjot.py:720–729):
`WORLD STATE BRIEFING → NARRATOR RULE → classified_input`. Player input sits
near the end (recency-favored) — correct. The bakeoff appends the DIRECTOR
NOTE **after** the input, i.e. the final block of the message, and that
placement is a large part of why a short note beats a schema-buried rule.
Preserve it when wiring: the note goes last, after `classified_input`.

### 3.3 Salience through scarcity — keep the nudge conditional

The `_NARRATOR_RULE` (rpjot.py:4258) appears identically in *every* turn's
user message → habituation; it becomes wallpaper. (The system-message entropy
refresh, `SYSTEM_REFRESH_INTERVAL`/`refresh_system_message` in play.py, exists
for exactly this reason.) A note that appears **only on stationary turns** is
salient precisely because it is rare and turn-specific. Do not make it
always-on — it would decay the same way the narrator rule did.

### 3.4 System-role hygiene is correct as-is — don't move the nudge into system

system = persistent gameplay rules (`system_role` notes, built in
play.py `build_step2_initial_messages` ~:280, paraphrase-refreshed every 8
turns). A per-turn fact ("nothing moved the MC this turn") in the system slot
would go stale next turn and poison the running `step2_messages` history. The
correct pattern already exists in this codebase: the D4 zero-canonical nudge
(`_ZERO_CANONICAL_NUDGE`, rpjot.py:683) — **role=user, turn-scoped, prefixed
so it can't be mistaken for player input**. Mirror it.

### 3.5 Classification is definitively derivable in production

`classify_input` (play.py:78) already sigil-stamps every input:

| sigil    | prefix produced                            | mobility          |
|----------|--------------------------------------------|-------------------|
| `"`      | `[MC speaks aloud]`                        | stationary always |
| (none)   | `[MC — likely spoken aloud…]`              | stationary always |
| `^`      | `[MC inner monologue — private, unspoken]` | stationary always |
| `@`      | `[MC attention → …]`                       | stationary always |
| `*`      | `[MC action]`                              | needs a check     |

Only `[MC action]` is ambiguous; `classify_heuristic`
(bakeoff_navnudge.py:105) resolves it: mobile requires a first-person
movement verb (handles the "I follow her down the corridor" collision), quoted
lines and everything else fall through to stationary. Unrecognized prefix →
treat as mobile (**fail open**: no injection = baseline behavior).

### 3.6 Note wording: fact-then-directive, positive framing, escape hatch

The tested POS_NOTE grounds the instruction in an asserted per-turn fact
("nothing has physically moved the MC this turn") before directing — models
comply better with a justified instruction than a bare prohibition. Keep that
shape. But the tested wording says "keep the scene where it is"
*unconditionally*, which fights the forced-movement requirement. Phase A tests
a v2 with an explicit escape hatch (§4.2).

---

## 4. Phase A — bakeoff extension (`bakeoff_navnudge.py`)

### 4.1 Forced-movement corpus rows

The case hard_gate provably cannot pass and the nudge must tolerate.
`kind="stationary"` (so the note/gate FIRES) with `expects_navigate=True`:

```python
dict(id="forced_drag", kind="stationary", expects_navigate=True,
     text="[MC action]: I dig in my heels but the guards seize my arms "
          "and drag me down to the cells."),
dict(id="forced_carriage", kind="stationary", expects_navigate=True,
     text='[MC speaks aloud]: "Where are you taking me?" '
          "The carriage rolls on, carrying you through the city gates."),
```

`classify_heuristic` already labels both stationary (no unquoted first-person
move verb), matching the oracle. **Scoring change required:** the per-kind
correctness split currently keys on `kind`; key the correct/incorrect buckets
on `expects_navigate` instead, and print these rows as their own `forced`
bucket in the scorecard so hard_gate's guaranteed 0% is visible in the output.

### 4.2 `nudge_positive_v2` arm — escape-hatch wording

> DIRECTOR NOTE (this turn): the MC has not moved themselves this turn. An
> NPC's invitation, or a place merely spoken or thought about, is not
> movement — the story continues at the current location. Only if events
> physically carry the MC elsewhere (dragged, carried, a moving vehicle) does
> the scene move.

### 4.3 `nudge_pos_desc` arm — v2 wording + schema micro-description

Targets the qwen3-vl 17% residual on `neg_invite`. Same injection as v2, plus
a one-line **function-level** description restored on navigate_to in the
compact schema (~30 tok — currently the model picks among 31 tools by name
alone, §3.1):

> Move the scene when the MC's own body travels (or is physically carried) to
> a new place; never for places merely mentioned, offered, or thought about.

In the harness, build the variant locally: copy `engine._compact_step2_schemas`
and set `t["function"]["description"]` on the navigate_to entry.

### 4.4 Arm roster

Keep `baseline` and `hard_gate` as controls; **drop `nudge_negative`** (tied
with positive at 97%; positive framing is preferred). Sweep = 5 arms:
`baseline, hard_gate, nudge_positive, nudge_positive_v2, nudge_pos_desc`.

---

## 5. Phase B — production wiring (`rpjot.py`)

> **STATUS (2026-07-03).** Items 1–2 shipped in `622752c` (`nudge_positive_v2`
> as `_STATIONARY_NUDGE`). The re-sweep then settled item 3: **`nudge_pos_desc`
> won** and its micro-description is now wired (see §4.3 result below). Items 4–5
> stand as written.
>
> **Re-sweep result** (6 models × ROUNDS=3, endpoint live). Aggregate vs the
> shipped `nudge_positive_v2`: stationary **97%→100%** (closes the qwen3-vl
> `neg_invite` residual, 17%→0%), forced **33%→58%**, mobile 100%→100%. Per
> model: 3 improve (qwen3-vl, qwen3-235b, devstral2), 3 tie, **0 regress** —
> strict Pareto win, so the keep-list entry is justified. Caveat: forced is *not*
> solved (58% ≪ 100%; qwen3-30b + granite41-30b stay 0/6 forced under any nudge,
> a pre-existing weak-model limit pos_desc neither causes nor fixes).

Run the Phase A sweep first; wire the winning arm. `nudge_pos_desc`
(`nudge_positive_v2` + the §4.3 micro-description) won:

1. **Classifier** — new `ComplianceStep._is_stationary_turn(classified_input)`
   classmethod beside `_should_nudge_zero_canonical` (rpjot.py:702),
   implementing the §3.5 table: speaks-aloud / likely-spoken / inner-monologue
   / attention prefixes → stationary; `[MC action]` → stationary unless a
   first-person movement verb is present (port `classify_heuristic`'s verb set
   + quoted-line guard); no recognized prefix → NOT stationary (fail open).
2. **Injection** — in `ComplianceStep.run` (rpjot.py:708): when stationary,
   append the winning note as the **last** element of `user_content_parts`
   (after `classified_input`). Module-level constant `_STATIONARY_NUDGE`
   beside `_ZERO_CANONICAL_NUDGE`. Log in D4 style on fire:
   `logger.info("[STEP2] stationary nudge → injected")`.
3. **Function-description keep-list** — ✅ DONE (re-sweep confirmed
   `nudge_pos_desc` > `nudge_positive_v2`). `_COMPACT_KEEP_FUNCTION_DESCRIPTIONS
   = {"navigate_to": "<one-liner §4.3>"}` populated beside
   `_COMPACT_KEEP_PARAM_DESCRIPTIONS`; `_compact_step2_schemas` already emits
   `description` for listed tools (consumer was pre-wired). Text kept identical
   to `bakeoff_navnudge.NAV_FUNCTION_DESC` so production == harness. Guards:
   `test_compact_budget_under_3000` (≤3000 tok with the +~30 tok) and
   `test_function_descriptions_match_keep_list` both pass.
4. **Source-of-truth prose** — leave the full navigate_to description
   (rpjot.py:2102) unless it contradicts the new wording; it still serves the
   non-compact `_step2_schemas` consumers and tests.
5. **Tests** — extend `TestProductionActivation` (test_rpjot.py:1834):
   - Route `_step2_tools_for` (test_rpjot.py:1864) through the same
     production composition (including the conditional nudge) so the paired
     tests — `test_npc_invitation_does_not_select_navigate_to` (:1900) and
     `test_player_movement_selects_navigate_to` (:1911) — exercise exactly
     what production sends.
   - New LLM-gated test: forced-movement phrase (guards drag) **with the
     nudge injected** still selects navigate_to — the anti-hard-gate
     guarantee.
   - New pure-unit tests (no LLM): `_is_stationary_turn` truth table over all
     sigil prefixes, the "I follow her" mobile collision, the quoted-dialogue
     guard, and the unrecognized-prefix fail-open.

---

## 6. Verification

1. `python3 bakeoff_navnudge.py` (6 default models, ROUNDS=3) after Phase A.
   Acceptance: stationary ≥97% aggregate, mobile 100%, forced bucket — nudge
   arms ≥ baseline while hard_gate shows 0% (record it; it is the
   justification for the soft approach). Pick the arm that lifts `neg_invite`
   highest without regressing forced/mobile.
2. **Endpoint preflight first** — a dead :5000 server silently x-fails live
   tests and fakes green. The harness refuses on a failed preflight; keep
   `FORCE` unset. (`catjot.call_llm` @ ~1362 reads
   `openai_api_model/url/key` from live env per call and sets **no request
   timeout** — the harness's `CALL_TIMEOUT` thread cap is the only cap.)
3. After Phase B: `pytest test_rpjot.py -k "ProductionActivation or stationary"`.
4. Re-run `bakeoff_denoise.py` and confirm `neg_navto` is closed from 0/18 in
   the production shape.
5. Update the `bakeoff-systematic-miss-hypothesis` memory: nudge wired,
   residual rate, whether the micro-description shipped.

---

## 7. Anchor table (line numbers as of branch `fixup-prompting-toolcycle`; grep the symbols if drifted)

| file                 | symbol / location                                        | role |
|----------------------|----------------------------------------------------------|------|
| bakeoff_navnudge.py  | whole file                                               | the A/B/C/D harness: CORPUS, POS_NOTE/NEG_NOTE, classify_heuristic, selected_tools, preflight, 60s cap |
| bakeoff_denoise.py   | whole file                                               | per-test sweep that surfaced neg_navto 0/18 |
| rpjot.py             | `ComplianceStep.run` @ 708; call site @ 743              | step-2 loop; injection point (user_content_parts @ 721–729) |
| rpjot.py             | `_ZERO_CANONICAL_NUDGE` @ 683; `_NUDGE_PREFIXES` @ 693; `_should_nudge_zero_canonical` @ 702 | the D4 role=user nudge precedent to mirror |
| rpjot.py             | `_compact_step2_schemas` @ 1017; `_COMPACT_KEEP_PARAM_DESCRIPTIONS` @ 1008 | strips ALL function descriptions (§3.1); keep-list to extend |
| rpjot.py             | `_tool_navigate_to` description @ 2102–2131              | the inert caveat (full-schema only) |
| rpjot.py             | `begin_scene` @ ~2700–2729                               | sibling tool under the same [MC action] rule |
| rpjot.py             | `_NARRATOR_RULE` @ 4258                                  | the always-on (habituated) rule |
| play.py              | `classify_input` @ 78                                    | sigil → `[MC ...]` prefix producer (§3.5) |
| play.py              | `build_step2_initial_messages` ~280; `refresh_system_message` @ 295 | step-2 system slot + entropy refresh |
| test_rpjot.py        | `TestProductionActivation` @ 1834; `_step2_tools_for` @ 1864; paired tests @ 1900 / 1911 | production-shape selection tests |
| catjot.py            | `call_llm` @ ~1362                                       | reads model/url/key from env per call; NO request timeout |
