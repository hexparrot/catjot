# SCENE_MOVER — symmetric agency + scene backstop

> **Status:** IMPLEMENTED on branch `rp_revise` (rpjot.py + play.py + test_rpjot.py). 535 deterministic tests pass. `bakeoff_agency.py` re-tuning is the remaining follow-up.
> **Reframe:** retire the PC-centric-movement-only stance in favor of **symmetric agency** — neither PC nor NPC privileged; the LLM decides who drives each beat from scene state, with NPC initiative **always available on any turn** (no sigil, no toggle, no separate pass). A deterministic scene auto-advance stays as a **safety-net backstop**.
>
> **Implementation note — one refinement from the plan:** the record_event MC-tag move gate was *not* changed to immediate-commit. Because `navigate_to` can now fire on any turn, immediate-commit would risk a double-move / traversal collapse. Instead both branches were unified to **always defer** to `_reconcile_loc_hint` (which commits iff `navigate_to` didn't fire) — decoupled from `_turn_stationary`, and safe on any turn.

## Context

The engine is currently built so **only the player moves the MC**: NPCs may invite/beckon/lead but cannot move the MC or resolve the MC's choices. On passive turns (`"wait"`, `"let her lead me"`) this produces dead, monotonous output — the same beat re-records every turn (a live 22-turn session stayed on `scene:opening`, ~217 near-duplicate notes) — because the guardrails forbid the NPC from ever carrying the story forward.

We are retiring that stance. **Goal:** agency is shared — on any turn the model may have an NPC act, lead, escort, or carry the MC, chosen from scene state (who's leading, mood, conscience/interior), with the player's input as one strong input rather than a movement monopoly. Passive input becomes the common case where an NPC naturally takes the wheel. Scenes then move primarily because NPCs move them; a deterministic staleness advance remains as a backstop.

Zero backward-compat concern. NPC motivations are already available in the normal flow: step-1's `build_scene_context_map`/`prepare_context` builds a per-character POV (conscience + interior) for every present NPC into the `world_doc`, so **no separate NPC context-gathering pass is needed**.

**Design decisions:** guardrails = **symmetric agency**; cede posture = **always available to the LLM** (any turn, no gating); scene advance = **keep deterministic backstop**.

**Explicitly not doing** (unnecessary under symmetric/always-on): a `~` cede sigil, `_is_cede_phrase`, a `CEDE_PREFIX`, a `_STATIONARY_PREFIXES` addition, a gated step-2.5 "NPC initiative" pass, an NPC-selection heuristic, an `RPJOT_NPC_INITIATIVE` toggle, or a separate `npc_synthesis` block. NPCs act inside the normal step-2/step-3 flow.

---

## Mechanism 1 — Symmetric agency (retire PC-centric enforcement)

Rewrite the directives that forbid NPC-led movement/initiative into **neutral, symmetric** language, and relax the two hard code gates that suppress non-player movement.

**Balance guard (symmetric ≠ NPC railroading):** NPCs may act ON/AROUND the MC and move them; the model must **not** invent the MC's *willed* decisions or dialogue the player didn't give.

### Soft guardrails — directive rewrites

| Constant / site | Current (PC-centric) | Change |
|---|---|---|
| **`_NARRATOR_RULE`** rpjot.py:7575, injected step-2 at 1479 | *"advance only as its direct consequence. Do not skip ahead or resolve an NPC's offer… narrate the offer and let it hang; the player answers on their own next turn."* | Replace with **`_AGENCY_RULE`**: *"Resolve the beat the scene calls for. Agency is shared — the MC and the NPCs are equally able to drive the moment. If an NPC leads, beckons, escorts, or carries the MC, the MC goes and the scene moves; you do not need the player's permission for an NPC to act. Honor a concrete MC action or line exactly; when the player's input is passive or cedes ('wait', 'let her lead'), let the present NPC(s) take initiative with a concrete new development. Do not invent the MC's deliberate choices or dialogue the player did not give — narrate what is done to or around the MC and their involuntary responses, leaving the MC's willed decisions to the player."* |
| **`_STATIONARY_NUDGE`** rpjot.py:1374, injected 1491-1492 | *"the MC has not moved themselves… an NPC's invitation… is not movement… Only if events physically carry the MC elsewhere does the scene move."* | **Retire the injection** (remove the conditional at 1491-1492). The whole "stationary suppression" concept is PC-centric; under symmetric agency it is counter-productive. Keep the string in-source only for the bakeoff's legacy control arm. |
| **navigate_to full desc** rpjot.py:4895-4917 | *"Call ONLY when the player's own [MC action] moves them… An NPC's invite/beckon/escort becomes navigation only when the player's next [MC action] takes it up."* | *"Call when the MC's location changes — by the MC's own action **or** because an NPC leads, escorts, or carries them, or an event moves them. Dialogue alone is not travel; a place merely mentioned or thought about is not travel until someone acts on it."* |
| **navigate_to compact desc** rpjot.py:2098-2103 | *"…never for places merely mentioned, offered, or thought about."* | *"Move the scene when the MC travels — by their own action, NPC escort, or forced movement. Not for places merely mentioned or thought about."* |
| **begin_scene desc** rpjot.py:5758-5759 | *"Like navigate_to, a scene opens on the player's own [MC action], not on an NPC's invitation or escort."* | *"A scene opens when the dramatic context shifts — by player action or NPC initiative alike (an escort or invitation that starts a new beat is a valid trigger)."* |

### Hard guardrails — gate relaxations

- **record_event MC-tag move gate** (rpjot.py:4772): drop the `and self._turn_stationary` condition so an MC-tagged event filed at a new location **commits the move regardless of stationary classification** (the NPC led the MC there). The no-MC-tag branch (4787-4790, off-screen event → session stays) is unchanged. The deferral/reconcile path (`_pending_loc_hint`/`_reconcile_loc_hint`) simplifies to: MC-tagged new location → commit.
- **step-1 auto-move early-return** (rpjot.py:2709-2714): `_remark_location` currently early-returns on `not _turn_stationary`. Relax so NPC-driven location changes proposed in context are not suppressed by the movement-verb classification. Keep computing `_turn_stationary` as an **informational signal** (still read by the dedup and the `/timing` self-checks at 8303-8476; update the 8338 cross-check note to reflect that it no longer gates movement).
- `_is_stationary_turn`/`_MOVE_VERBS`/`_MOVE_VERBS_3P` (1385-1463): retained for the informational `_turn_stationary` signal only; **no longer a movement gate**. No deletion needed.

> `navigate_to` and `begin_scene` are already in the step-2 schema every turn, so once the directives permit it, NPC-led moves/scene-opens require no new tool wiring.

---

## Mechanism 2 — Deterministic scene-advance backstop (`RPJOT_SCENE_MOVER`, default ON)

NPC-led movement is now the primary scene motion; this is the backstop for a genuinely stuck scene. **Turn-arm only** in v1 (drop the note-count arm — model-dependent and persists across resume; let the bakeoff justify re-adding).

**Constants** (near rpjot.py:665) + `import difflib`:
```
SCENE_STALE_TURNS         = 6    # elapsed turns → inject advance suggestion
SCENE_HARD_ADVANCE_STREAK = 3    # consecutive SHOWN-and-ignored suggestions → engine forces begin_scene
RECORD_EVENT_DEDUP_RATIO  = 0.90
```

**State** (`__init__` ~1899): `_scene_start_turn=0`, `_scene_stale_streak=0`, `_scene_advance_shown=False`, `scene_mover_enabled=True`.

**Single reset discipline** — reset `_scene_start_turn=_turn_count` and `_scene_stale_streak=0` in `_tool_begin_scene` (after `current_scene = name`). Covers model calls, bootstrap (play.py:1202/1271), hard-advance. **Add the same reset to `apply_resume_state`** — the 4th path that sets `current_scene` directly; otherwise resuming a scene with many notes is "stale" from turn 1.

**Methods:**
- `_should_suggest_scene_advance()` → `scene_mover_enabled` AND `current_scene` set AND `_turn_count - _scene_start_turn >= SCENE_STALE_TURNS`. (Flag checked here so `RPJOT_SCENE_MOVER=0` silences both the nudge and the hard advance.)
- `_auto_scene_slug()` → deterministic, no LLM: `slug = f"{last_loc_segment}-t{_turn_count+1}"`; **description from location + present cast** ("continuation at {loc} with {npcs}") since `get_scene`/resume surface it later; add a collision nonce (`_turn_count` resets per process → two resumes can otherwise mint the same slug).
- `_maybe_hard_advance_scene(canonical_results)`:
  - Any `begin_scene` already present **or any location change (navigate_to / committed move) this turn** → reset streak, return. A moving scene is not stalled (under symmetric agency the real signal is "did the scene move," not "who was allowed to move it").
  - Else if `not _scene_advance_shown` → do **not** increment (streak counts *shown-and-ignored* suggestions, not merely stale turns).
  - Else increment; at `>= SCENE_HARD_ADVANCE_STREAK` force-advance via `_dispatch_step2("begin_scene", {...})` (so it lands in `_turn_tool_events`/`/timing`) and append `("begin_scene", …)` to `canonical_results`.

**Soft-nudge injection** — in `_compose_step2_user_content`, after the `_scene_hint_pending` branch (1481-1490): `elif self.engine._should_suggest_scene_advance():` append a DIRECTOR NOTE ("this scene has run N turns without advancing; if the beat has settled, call begin_scene"). Keep the method side-effect-free; set the `_scene_advance_shown` one-shot in `run_turn` before compose (mirrors the predicate) so the streak reflects what was actually shown.

**run_turn wiring** (between `_reconcile_loc_hint` 2880 and `prose.run` 2883): `if self.scene_mover_enabled: self._maybe_hard_advance_scene(canonical_results)`.

---

## Supporting hardening — record_event dedup

In `_tool_record_event` (4746), before `Note.jot` (4798): scan recent same-`pwd`+same-`scene`, last ~8 notes; if `difflib.SequenceMatcher(None, new.lower(), old.lower()).ratio() >= RECORD_EVENT_DEDUP_RATIO` (0.90 — conservative, since active NPC-driven turns must keep fidelity), **skip the write** and return the **original** event text ("already canon: …; do not re-record") — never "merged (near-duplicate)" (renders as nonsense CANONICAL FACTS and invites paraphrase-retry). **Filter dedup-skips out of `_build_narrative_synthesis`.** Location-commit side effects (4763-4790, now relaxed) run before the write and stay (movement is real even if text dups). Ratio is a bakeoff tunable.

---

## Bakeoff re-tuning (behavior is being inverted)

`bakeoff_navnudge.py` was tuned to **prevent** `navigate_to` firing on NPC invitations (winning arm `nudge_positive_v2` = the `_STATIONARY_NUDGE`). That objective is now reversed. Add **`bakeoff_agency.py`** (clone the harness): flip the corpus so `neg_invite`/`npc_moves`/escort cases become **expected-navigate** (NPC-led mobile), keep `forced_drag`/`forced_carriage` as navigate, and score the new `_AGENCY_RULE` wording arms against the legacy PC-centric control arm on: (a) NPC-led moves correctly navigate, (b) the model does **not** author the MC's willed choices (agency-capture rate), (c) genuinely no-op turns still don't spuriously move. Keep the legacy strings in-source as the control arm.

---

## Critical files

- **rpjot.py** — constants ~665 (+`import difflib`); `_STATIONARY_NUDGE` retire injection 1491-1492; `_compose_step2_user_content` scene-advance `elif` ~1490; `_is_stationary_turn` family 1385-1463 (demote to informational); `_remark_location` gate 2709-2714; `_dispatch_step2` 2352; `run_turn` 2880-2889; navigate_to descs 2098-2103 & 4895-4917; `_tool_record_event` 4746 & MC-tag gate 4772; `_reconcile_loc_hint`/`_pending_loc_hint` 2768-2780; `_tool_begin_scene` 5788 & desc 5758-5759; `apply_resume_state` (reset); `_NARRATOR_RULE`→`_AGENCY_RULE` 7575; self-check note 8338. New methods: `_should_suggest_scene_advance`, `_auto_scene_slug`, `_maybe_hard_advance_scene`.
- **play.py** — toggle `RPJOT_SCENE_MOVER` ~69-90; flag wiring ~909. (No `classify_input` change — ceding is model-judged, not a sigil.)
- **bakeoff_agency.py** (new, from bakeoff_navnudge.py). **test_rpjot.py** — units + live regression.

## Verification

**Deterministic units:** `_should_suggest_scene_advance` (turn arm; flag-off silences); `_auto_scene_slug` (determinism, kebab, collision nonce); `_maybe_hard_advance_scene` (streak advances only on shown-and-ignored; resets on any begin_scene **or location change**; resume-with-notes doesn't insta-advance); dedup (near-identical skipped w/ original text returned & filtered from synthesis; distinct-but-similar ≈0.92 case written; dissimilar written); `_STATIONARY_NUDGE` no longer injected; record_event MC-tag new-location commits regardless of `_turn_stationary`.

**Live regression:** (1) scripted `"wait"×6` with an NPC present → an NPC takes initiative (a canonical write attributed to the NPC and/or an NPC-led `navigate_to`), scene advances by ~turn 6-9, opening-scene notes stop growing after dedup, and prose shows a **new development, not a re-narrated hanging offer**; (2) an NPC "leads the MC to the cellar" beat → `navigate_to`/location commits **without** a player `[MC action]`; (3) a concrete MC action still executes as authored, and NPC turns **do not** narrate the MC's willed decisions (agency-capture check).

**A/B:** `bakeoff_agency.py` — `_AGENCY_RULE` wording + dedup ratio + `SCENE_STALE_TURNS` sweeps vs. the legacy PC-centric control, scored on NPC-led-navigate success, agency-capture rate, false-move rate, turns-until-advance.

## Risks

- **NPC railroading / agency capture** — symmetric ≠ NPC-centric. Mitigated by the explicit "do not invent the MC's willed choices/dialogue" clause in `_AGENCY_RULE` and the bakeoff agency-capture metric. **Primary thing to watch.**
- **Over-eager movement** — relaxed gates + permissive navigate_to desc could move the scene on a mere mention. Mitigated by keeping "not for places merely mentioned/thought about until acted on" in the descs, and the false-move bakeoff metric.
- **Auto-advance mid-beat** — gated on a shown-and-ignored streak of 3 **and** no location movement in the window, plus the `RPJOT_SCENE_MOVER=0` kill switch.
- **`begin_scene` not LLM-free in effect** — it sets `_system_refresh_pending` (5794) → a paraphrase LLM call (sync in `game_loop` 1163-1164 when BG refresh off); every hard advance incurs it.
- **Dedup false-positive** — 0.90 over same loc+scene+8-note window, tunable; conservative because active NPC turns must keep fidelity.
- **`_turn_stationary` demotion fallout** — audit its readers (2712, 4772 relaxed; 8303-8476 diagnostics) so nothing still treats it as a hard movement gate.
