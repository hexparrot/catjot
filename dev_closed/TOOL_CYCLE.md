# TOOL_CYCLE.md — Tool-Activation Dependability & Prose/Tool Separation Review

**Scope:** How dependably does the LLM activate each tool when it should — in particular
the person/presence tools when characters are addressed or perceived — and is the
three-step cycle a streamlined process that takes from each tool exactly what it needs
while keeping prose and tool output properly separated?

**Review date:** 2026-07-02, at commit `0c506e8`. Line numbers refer to that tree;
function/class names are the stable anchors. Companion document:
`dev_open/PROMPTING.md` (prompt-size robustness, history growth — items referenced
there as R1–R6 are not repeated here).

**Method note:** Every load-bearing claim below was verified directly against source or
debug.log. Two widely-assumed beliefs turned out FALSE and are corrected in §2.2 and
§5.1 — read those even if you skim the rest.

---

## 1. Executive verdict

| Question | Answer |
|---|---|
| Is any tool call *guaranteed* to fire? | **No.** All step-1/step-2 calls use `tool_choice="auto"`; activation is 100% instruction-driven. There is no required-tool set, no post-hoc validation, no retry-on-miss. (Contrast: catjot.py's own `run_tool_loop` **enforces** four required tools — the pattern exists in this repo but rpjot doesn't use it. §4.3) |
| Is person/presence information assured despite that? | **Yes, by construction — not by tool call.** `_build_baseline_context` deterministically injects every present character's full profile into step 1 whether or not any tool fires, and `_fallback_doc` guarantees a world doc even on total LLM failure (§4.1). The *assured* path for persons is the baseline, and the tools are enrichment. |
| Where does person tracking actually break? | `set_people_present` (cast changes) is voluntary and unverified. If the LLM narrates an arrival/departure without calling it, `session.people_present` drifts from the story, and every downstream deterministic guarantee (baseline profiles, POV contexts, `exp:` witness tagging, observable-act non-witness lists) silently keys off the **wrong cast**. This is the highest-stakes activation dependency in the system (§5.2). |
| Is the cycle robust against bad tool calls? | **No — one hallucinated argument crashes the session.** `_dispatch_step2`/`_dispatch`/step-1 inline dispatch invoke handlers with `(**args)` and no try/except; a `TypeError` (unexpected/missing kwarg) or `json.JSONDecodeError` propagates uncaught past `run_turn` (play.py catches only `LLMError`) and kills `play.py` with a traceback (§6.1). This is the single worst defect found in this review. |
| Is prose properly separated from tool output? | **Yes — this is the strongest part of the design.** Tool results are summarized (never verbatim) into a synthesis, step 3 runs `tool_choice="none"` with bare stubs, and `<think>`/`<tool_call>` markup is stripped at every boundary. Minor residual leaks noted in §7.3. |
| Do the live tests prove production activation? | **Only weakly.** `TestToolDispatch` tests a single-pass call with the **combined** flat schema set and a generic GM system prompt — a shape no production code path uses. Green tests do not certify that the same phrase activates the same tool inside the real step-partitioned, compact-schema, real-system-prompt pipeline (§8). |

---

## 2. Anatomy of the tool cycle

### 2.1 Per-turn flow

```
player input
  → classify_input (play.py:74-111): sigil → explicit directive
      "→[MC speaks aloud]   *→[MC action]   @→[MC attention→target]
      ^→[MC inner monologue] + nudge to record_conscience/record_secret
  → run_turn (rpjot.py:988-1049)
      STEP 1  WorldStateStep (437-590)
        tools: _step1_schemas (11 read-only tools), tool_choice="auto",
               temp 0.1, ≤8 rounds, isolated messages
        deterministic floor: _build_baseline_context (470-511) injected in the
               initial message; _fallback_doc (574-589) on stall/failure
        output: WorldStateDoc (prose summary of facts, think-stripped)
      STEP 2  ComplianceStep (592-680)
        tools: _compact_step2_schemas (31 write tools, param descriptions
               stripped), tool_choice="auto", ≤10 rounds
        per tool call: result → canonical_results[(fn,result)] (670);
               followup_instruction split out and re-injected as a user msg
               (672-677); <think> harvested into accumulated_think (643-658)
        output: (canonical_results, accumulated_think) — step 2's final
               non-tool TEXT is DISCARDED except its <think> content
      STEP 3  ProseStep (683-744)
        tools: _bare_tool_schemas (name-only stubs), tool_choice="none",
               temp 1.2
        input: world_doc + attention + mood + _build_narrative_synthesis
               (4113-4150: framed summaries of think + canonical results)
        output: narrative (think/tool_call-stripped) → player
  → play.py journals narrative to /summaries note (446-452), appends
    classified+narrative to both histories (455-458)
```

### 2.2 Tool census — corrected

**All 42 `@rp_tool` methods are registered and live.** There is **no disable list**:
`register_all_tools` (rpjot.py:951-987) registers every decorated method
unconditionally; no `_DISABLED_TOOLS` or equivalent exists anywhere in the tree.
(Commit `efc3b30` "disabled tools entry" is unrelated — it added the empty-narrative
guard and `<tool_call>`-block stripping in `strip_think_tags`.) Confirmed by debug.log:

```
registered step1=11 step2=31 tools | schema overhead: step1=1626 tok,
step2_compact=2310 tok (full=8399), bare=901 tok
```

**Step 1 (11 read-only):** `get_people_present` (1750), `examine_location` (1779),
`get_character` (2131), `find_character` (2175), `search_world` (2261), `get_scene`
(2476), `get_conscience` (2814), `prepare_context` (2865), `get_yomi` (2767),
`get_relationship_arc` (3850), `get_social_map` (3895).

**Step 2 (31 write):** core 12 — `record_event` (1702), `navigate_to` (1833),
`set_people_present` (1932), `save_character` (1983), `save_location` (2041),
`save_object` (2091), `record_knowledge` (2328), `begin_scene` (2432),
`record_conscience` (2568), `update_attn` (2653), `save_yomi` (2717), `record_mood`
(3809) — plus the 10-tool relationship suite (`record_bond/history/dynamic/
power_dynamic/wound/promise/debt/lie/leverage/impression`, 2937-3407, `rel:` tags,
`PWD_REL`) and the 9-tool interior suite (`record_secret/desire/longing/jealousy/mask/
subtext/reputation/trigger/unspoken`, 3407-3809, `int:` tags, `PWD_INTERIOR`).

Implication of the correction: the 31-tool step-2 surface is what the compact-schema
work (§5.1) is compensating for. 19 of the 31 are fine-grained rel/int taxonomy tools
whose selection burden falls entirely on the model.

---

## 3. The instruction stack that drives activation

Activation confidence rests on four layers of text, all advisory:

1. **System prompts.** `_STEP1_SYSTEM` (206-215): "Your only job is to retrieve facts…
   call lookup tools to gather everything relevant: character profiles, relationships,
   location details, story context, yomi, and conscience constraints." Step-2 system =
   seed gameplay rules (create_canonical_seed.py, loaded via `ContextBundle
   ("system_role")` in play.py `build_step2_initial_messages`) — narration guidelines,
   player-agency rules; notably it does **not** enumerate tool obligations.
   `_STEP3_SYSTEM` (218-226): "Do not call tools. Do not plan. Only narrate."
2. **Per-tool trigger language** in descriptions. Strongest examples:
   - `get_people_present`: enumerates 7 canonical player phrasings ("who is here?",
     "look around", "do I see anyone?", …).
   - `find_character`: "Call this BEFORE naming any new NPC… If a match is found, use
     that character instead of inventing a new name."
   - `navigate_to`: consent-guarded — "ONLY call this when the player explicitly
     chooses to move… NPC invitation, NPC escort arrival, or NPC suggestion is NOT
     player movement." (commit `0c3a29e` "better narrative consent")
   - `begin_scene`: "Call at most ONCE per player turn. Do not call it a second time
     within the same tool-call sequence." (instruction-only rate limit)
   - `update_attn`: "Call this as the FIRST tool when processing any player action that
     involves social interaction…" (instruction-only ordering — nothing enforces call
     order)
3. **Followup directives** (rpjot.py:128-161), split out of tool-result JSON by
   `strip_followup_instruction` (1183-1196) and re-injected as user messages
   (ComplianceStep 672-677). They chain tools: FOLLOWUP_QUERY → "…end additional
   knowledge-gathering then record_event"; FOLLOWUP_USE_CHARACTER → "If no notes exist,
   invent consistent details and save them with save_character";
   FOLLOWUP_SCENE_CONTINUITY → "…you may summarize it and call begin_scene";
   FOLLOWUP_CONSCIENCE_ACTIVE → reshape-not-refuse rule. This is the system's main
   mechanism for making one tool's output *pull* the next tool — clever, but each link
   is still voluntary.
4. **Sigil directives** (play.py:74-111). `^` explicitly nudges tools: "use
   record_conscience or record_secret to preserve it as MC backstory." `@` instructs
   discovery-or-graceful-absence. `_NARRATOR_RULE` (3961-3969) is injected into every
   step-2 user message and back-stops the navigate_to/begin_scene consent rules from
   the narrative side.

---

## 4. What is guaranteed vs. what is hoped

### 4.1 Deterministic floor (activation-independent — the real guarantees)

- **Baseline context** (`_build_baseline_context`, 470-511): docstring states the
  contract — "Guarantees that the LLM always sees the present characters' full
  backstories and the shared location snapshot exactly once, **even if every tool call
  is skipped**." Location-ancestor lore + per-present-character profile bundles,
  rendered through the token-tiered `render_context`. Characters with no notes are
  explicitly listed (`[NO CHARACTER NOTES ON FILE FOR]: …`, 509-510) — a good nudge for
  step 2 to `save_character`.
- **Fallback world doc** (`_fallback_doc`, 574-589): on LLM stall (8 rounds exhausted)
  or network failure, a deterministic WORLD STATE (location + scene + baseline +
  any collected tool results) still feeds steps 2–3. Step 1 can fail *entirely* and the
  turn still runs on notes-derived truth.
- **Canonical-results capture** (670): every step-2 tool that *does* fire is captured
  and force-fed to prose as "CANONICAL FACTS ESTABLISHED THIS TURN — these just
  happened and **must be present in the narrative**" (4136-4143). Tool→prose delivery
  is dependable; it's tool *activation* that isn't.
- **Turn journal**: play.py:446-452 writes every narrative to a `/summaries` note
  regardless of tool activity — a canon-of-last-resort when `record_event` is missed,
  though untagged (no `exp:`/`scene:`), so it doesn't feed POV contexts.

### 4.2 Voluntary layer (everything else)

`tool_choice="auto"` at step 1 (532) and step 2 (634). The exit condition for both tool
loops is simply "the model produced text instead of tool calls" (541, 642). Nothing
checks *which* tools ran. NPCTracker records `not-yet-saved`/`unnamed` flags
(roster_summary, 348-371) — **diagnostic display only** (`/prompt`, step-1 header);
no code path acts on them.

### 4.3 The enforcement pattern the repo already owns

catjot.py `run_tool_loop` (1677-1794) demonstrates hard enforcement: system prompt
says the model MUST call all four search tools; the loop checks
`REQUIRED_TOOLS.issubset(called)` (1729-1772) and only then permits the final answer.
rpjot adopted none of this. That's partly reasonable — most RP tools are conditionally
appropriate, not always-required — but a *conditional* variant is feasible and is the
core remediation (T2, §9).

---

## 5. Per-tool-family activation confidence

Confidence scale: **High** = deterministic or live-test-backed with strong trigger
language; **Medium** = strong language, no verification; **Low** = judgment call left
to the model, no language pinning it, no detection when missed.

| Family | Tools | Confidence | Basis & failure consequence |
|---|---|---|---|
| **Perception of persons** | `get_people_present` | **High** | 7 canonical trigger phrases in the description; 4 live tests assert activation (§8). Even on miss, baseline context already carries present-character profiles → consequence of a miss is mild (missing *disposition* flavor only). |
| **Cast membership** | `set_people_present` | **Low — highest-stakes gap** | No trigger-phrase language comparable to the perception tools; purely the model's judgment that "the cast changed." On miss: `people_present` drifts → baseline profiles for wrong cast, `update_attn`/`mood` resets don't fire, `record_knowledge` `observable_act` computes wrong non-witness lists, `prepare_context` builds wrong POV set. Drift is **persistent and invisible** — nothing compares narrative cast to session cast. See T1/T2. |
| **Person identity** | `get_character`, `find_character`, `save_character` | **Medium** | `find_character`'s "BEFORE naming any new NPC" is the strongest wording in the codebase, and FOLLOWUP_USE_CHARACTER chains lookup→save. NPCTracker `not-yet-saved` flag detects the miss but nothing consumes it. On miss: duplicate/contradictory NPCs, or characters that never enter canon (future `get_character` returns nothing → re-invention). |
| **Events → canon** | `record_event`, `record_knowledge` | **Medium** | `record_event` has a live test for a clear action phrase; FOLLOWUP_QUERY pushes toward it. The event/knowledge *split* (public vs. selective) is judgment-based; wrong pick corrupts knowledge asymmetry (private info tagged public or vice versa). On full miss: turn exists only in prose + untagged `/summaries` journal — invisible to step 1 lookups and POV contexts. Observed in logs: `[TURN] step2 done: canonical=0 think=1` turns do occur. |
| **World entities** | `save_location`, `save_object` | **Medium** | Clear do/don't language ("Do NOT use this for player interactions… use record_event"). Miss consequence moderate: scenery re-invented later; `examine_location` returns thin results. |
| **Movement/scene** | `navigate_to`, `begin_scene` | **Medium-High for not-firing-wrongly; Medium for firing** | The consent language + `_NARRATOR_RULE` work *against* false positives (the historically observed failure — commit `0c3a29e`). Nothing verifies the true-positive side (player says "I follow her" but model narrates without navigating → location state lags the story; same silent-drift class as cast membership). `begin_scene`'s once-per-turn cap is instruction-only. |
| **Transient state** | `update_attn`, `record_mood` | **Low, low stakes** | "Call FIRST" ordering unenforced; session-only, auto-reset on nav/cast change; `/attn` and `/mood` REPL commands even print "[no attention state set this turn]" — the codebase itself expects misses. Miss degrades prose nuance only. |
| **Interiority** | `save_yomi`, `get_yomi`, `record_conscience`, `get_conscience` | **Medium** | Rich "call after significant interaction" guidance; `^` sigil explicitly names `record_conscience`. Redundancy helps reads: conscience and yomi are ALSO auto-injected via `gather_pov_context`/`_gather_yomi` — so read-tool misses are cushioned; write-tool misses just slow arc development. |
| **Relationship suite (10) / interior suite (9)** | `record_bond`…`record_impression`, `record_secret`…`record_unspoken` | **Low** | 19 fine-grained taxonomy tools competing for selection with parameter guidance stripped by compaction (§5.1). No followups chain into them; no tests cover them; their reads (`get_relationship_arc`, `get_social_map`) are step-1-only. Expect chronic under-use and mis-binning (e.g. `record_wound` vs `record_history`). Consider whether 19 distinct tools is the right interface at all (T6). |

### 5.1 The compaction trade-off — corrected understanding

`_compact_step2_schemas` (860-888) strips **parameter** descriptions only; top-level
trigger language survives. But the parameter descriptions are where the *argument
contracts* live, and step 2 — the only step that writes canon — is exactly the step
that runs compacted (8,399 → 2,310 tok). Verified examples of guidance the production
step-2 model never sees:

- `record_event.tags`: "Use exp:name for each person present (e.g., exp:evie
  exp:bartholomew). Use know:name for private knowledge. **Avoid loc: and char:
  prefixes**…" (1685-1693) — the entire tag grammar that powers POV retrieval.
- `record_knowledge.witnesses`: "Names of every actor who learns this information.
  **Only these actors will be able to recall it later**" and the whole
  `observable_act` public/private contract (2290-2326).

So the knowledge-asymmetry engine's data quality depends on argument conventions the
model must guess. Malformed tags don't error — they silently produce notes that POV
queries can't find. Remediation T3.

### 5.2 The person-tracking chain, end to end (the user's core question)

"Is the persons tool assuredly activated when characters are called?" — decomposed:

1. Player *perceives* people → `get_people_present` — **near-assured in practice**
   (trigger phrases + live tests), and redundant with the deterministic baseline.
2. Player *addresses/mentions* a character not present → step-1 initial message
   instructs "look up any character mentioned in the player input but not yet present"
   (462-467) → `get_character`/`find_character` — advisory; miss is cushioned because
   step 2/3 still see the baseline and can improvise, but improvisation is exactly what
   `find_character` exists to prevent.
3. Narrative *changes the cast* → `set_people_present` — **weakest link, no cushion**
   (§5 table). Every deterministic guarantee downstream keys off this set.
4. Character becomes *established* → `save_character` — advisory; detectable
   (NPCTracker `not-yet-saved`) but undetected-by-code.

Answer: perception is dependable; **cast maintenance and persistence are hope-based**,
and their failures are silent and compounding. T1/T2 close this.

---

## 6. Failure paths when the model calls tools *wrongly*

### 6.1 CRITICAL — unguarded handler dispatch crashes the session

Verified: none of the three dispatch sites wraps handler invocation:

- `_dispatch` (932-938): `return handlers[name](**args)`
- `_dispatch_step2` (940-945): `return self._step2_handlers[name](**args)`
- Step-1 inline dispatch (558-561): `engine._step1_handlers[fn_name](**json.loads(...))`

Unknown tool *names* are handled (JSON error string returned to the model — good), but:
- hallucinated/missing/extra **argument names** → `TypeError` from `(**args)`;
- non-JSON `arguments` → `json.JSONDecodeError` from `json.loads`;
- handler-internal exceptions (e.g. bad `witnesses` type iterated in
  `record_knowledge`) → propagate raw.

`ComplianceStep.run` has no try around `_dispatch_step2` (667); `run_turn` doesn't
catch; play.py catches **only `LLMError`** (431-433). Result: one malformed tool call
from the model = full traceback, session over, in-memory state (attention, mood,
NPC tracker, histories) lost. Historical note: commit `f1b1f9d` "fixed tooling failure
from relationship tools" did **not** add arg validation — it only added
`tool_choice="none"`+schemas to a legacy narrative call so the model would stop
emitting tool calls there. Argument-level robustness has never existed. Remediation T4
(this is the sprint's P0 alongside PROMPTING.md R1).

### 6.2 Handled paths (adequate)

- Unknown tool name → `{"error": "step2 unknown tool: …"}` back to the model, loop
  continues; model may retry or move on. No retry cap per se beyond loop iterations.
- LLM network failure mid-loop → warn + break; step 1 falls back to `_fallback_doc`,
  step 2 returns whatever canonical results accumulated, step 3 raises `LLMError`
  (738-739) which play.py catches. Graceful degradation order is correct: the
  prose step — the only step whose failure the player must see — is the only one that
  throws.
- Non-JSON tool result in `strip_followup_instruction` → passed through untouched
  (1191-1196). Fine.
- Guard-trim interaction: within-loop `_guard_payload` can truncate old tool-result
  messages mid-conversation (see PROMPTING.md §4.1/R2/R3 for the tool_calls
  undercount and orphaning risks — both directly affect this loop).

---

## 7. Prose/tool separation audit

### 7.1 Tool → prose direction (strong)

Raw tool output never reaches the narrator. The chain is:
`canonical_results` → `_summarize_write_result` (4067-4111: per-tool one-liners, e.g.
`record_knowledge` → "Private knowledge (alice, bob): <preview>") →
`_build_narrative_synthesis` (4113-4150) which **frames** them: think blocks as
"NARRATOR PLANNING NOTES — … Weave them into the prose naturally; do not list or
announce them separately", results as "CANONICAL FACTS ESTABLISHED THIS TURN — … must
be present in the narrative". Framing-as-directive (not as data) is the right
technique for keeping JSON artifacts out of prose voice.

### 7.2 Prose → tool direction (strong)

Step 3 sends `tool_choice="none"` **plus** `_bare_tool_schemas` (833-844) — name-only,
empty-parameter stubs. Rationale: some OpenAI-compatible backends misbehave when a
conversation references tools that aren't declared (this is exactly what `f1b1f9d`
fixed); stubs keep the declaration without offering a callable surface, at 901 tok.
Defense-in-depth for models that emit pseudo-XML anyway: `strip_think_tags`
(1069-1100) removes closed *and unclosed* `<think>` and `<tool_call>` blocks from
every step's output; play.py's empty-after-strip guard (435-442) handles the
"response was *only* tool markup" case.

### 7.3 Residual leaks (minor, fix cheap)

- `_summarize_write_result` fallback: unrecognized tools render as
  `f"{fn_name}: {str(parsed)[:150]}"` — raw dict text, function name included, handed
  to the narrator. All 19 rel/int tools plus any future tool without an explicit
  summarizer branch take this path. (T5)
- Step 2's final non-tool text is discarded except `<think>` content (643-654). By
  design step 2's "voice" never leaks to the player — good — but on models that don't
  emit think tags, **all** step-2 reasoning is lost to prose (`canonical=0 think=1`
  and `think=0` turns visible in logs). The synthesis then contains only tool
  summaries. Not a leak but the inverse: a separation so strict it can starve step 3.
  Worth a deliberate decision (T6 note).
- Followup directives are injected as `role:"user"` messages inside step 2 (677).
  They're indistinguishable from player input to the model. Prefix them (e.g.
  `[SYSTEM DIRECTIVE]`) or use the tool message itself; cheap hygiene. (T5)

---

## 8. Test validity — what green actually proves

`TestToolDispatch` (test_rpjot.py:450-571) are **live-LLM** tests, skipped entirely
unless `openai_api_url` is set (conftest.py:24-61 — no mock fallback; LLM tests also
serialized with a 0.5s cooldown, conftest.py:74-82). Assertions are binary
`assertIn(tool, called_names)` for canonical phrases: 4 phrasings →
`get_people_present`, 3 → `examine_location`, "look around" → either, 1 clear action →
`record_event`, plus arg-shape checks on `record_event`.

**Shape mismatch with production** (verified at test_rpjot.py:459-466): the tests call
`call_llm(messages, tools=self.engine._tool_schemas, tool_choice="auto")` — i.e.
**all 42 schemas combined, full (uncompacted) descriptions, one pass, and a generic
"You are a game master" system prompt** (`_base_messages`, :50-55). Production never
runs this shape: step 1 offers 11 schemas under `_STEP1_SYSTEM`; step 2 offers 31
**compacted** schemas under the seed gameplay-rules system prompt with a world-doc
briefing prepended. Consequences:

- A pass proves the model *can* map phrase→tool given ideal schemas; it does not prove
  step 2 selects `record_event` when its parameter guidance is stripped and 30 rivals
  are present, nor that step 1's smaller menu changes selection.
- There is no test for: `set_people_present` on cast change, `save_character` on new
  NPC, `navigate_to` consent (either direction), `begin_scene` once-per-turn,
  `record_knowledge` vs `record_event` selection, any rel/int tool, or any
  *negative* case ("model must NOT call navigate_to when an NPC invites").
- Nothing measures *rates*. Binary single-shot assertions on a stochastic system will
  flake before they inform; there's no N-trial pass-threshold harness.

---

## 9. Remediation backlog (sprint-ready)

Ordered by severity. T4 and T1/T2 are the substance; the rest are hardening.

### T4 (P0) — Harden tool dispatch against bad arguments
**Where:** `_dispatch` (rpjot.py:932-938), `_dispatch_step2` (940-945), step-1 inline
dispatch (558-561). Route step 1 through `_dispatch`-style handling or extract a shared
`_safe_dispatch(handlers, name, args_json)`.
**Change:** wrap parse+invoke; on `json.JSONDecodeError`, `TypeError`, or any handler
exception, log at WARNING and return
`json.dumps({"error": f"tool {name} failed: {exc}", "hint": "check argument names/types against the schema"})`
so the model can self-correct within the loop instead of killing the process. Optionally
validate `args` keys against the schema's `properties`/`required` before invoking
(schemas are already in `_step2_schemas` — cheap lookup).
**Acceptance:** unit tests calling `_dispatch_step2("record_event", '{"descripton": "x"}')`
(typo), `'not json'`, and `record_knowledge` with `witnesses: "alice"` (wrong type) all
return error JSON, no exception; a fake 3-round step-2 loop where round 1 errors still
completes the turn.

### T1 (P1) — Detect cast drift (make person-tracking failures visible)
**Where:** end of `run_turn` (rpjot.py:1035-1049) or a post-step-2 hook.
**Change:** after step 2, scan the turn's narrative + classified input for NPC display
names/slugs known to `NPCTracker` (`_records` already holds both) that are absent from
`session.people_present`, and for present members never mentioned in N recent turns.
Emit `logger.warning("[CAST] mentioned-but-absent: %s", …)` and surface the same in
`/stats` and the step-1 SCENE STATE header ("CAST WARNING: narrative mentions X who is
not in people_present"). Detection only — no auto-mutation of the cast (wrong guesses
would corrupt canon the same way misses do).
**Acceptance:** scripted turn where the mock narrative introduces "Evie" without
`set_people_present` → warning logged and visible in `/stats`; no warning when the
tool was called.

### T2 (P1) — Conditional required-tools check in ComplianceStep
**Where:** `ComplianceStep.run` exit path (rpjot.py:641-654).
**Change:** adapt catjot's REQUIRED_TOOLS pattern (catjot.py:1729-1772) to a
*conditional* form: when the model exits the tool loop with `canonical_results` empty
**and** the classified input is a `*` action or `"` dialogue (not a pure `^`
monologue or OOC query), inject one corrective user message — "No canonical record was
written this turn. If anything happened (action, dialogue, discovery), call
record_event or record_knowledge now; otherwise reply DONE." — and allow one extra
round. One retry only; never loop.
**Acceptance:** mock LLM that returns prose-only on round 1 and `record_event` on the
nudge → canonical_results non-empty; mock that replies DONE → turn completes with
`canonical=0` and a log line `[STEP2] zero-canonical turn acknowledged`.

### T3 (P1) — Restore argument contracts lost to compaction
**Where:** `_compact_step2_schemas` (860-888) + tool descriptions.
**Change:** either (a) move the load-bearing grammar into top-level descriptions,
which survive compaction — `record_event`: "tags MUST be space-separated exp:<name>
per witness (know:<name> for private); never loc:/char:"; `record_knowledge`: witness
semantics + observable_act contract; or (b) add a keep-list so compaction preserves
`description` for the ~6 critical params (`record_event.tags`,
`record_knowledge.witnesses/observable_act`, `navigate_to.location_name`,
`save_location.name`). Measure: `_cached_compact_step2_schema_toks` stays ≤ ~3,000.
**Acceptance:** compact schema JSON contains the exp:/know: grammar; token log line
confirms budget; live smoke shows correctly-tagged event notes.

### T5 (P2) — Close residual prose/tool seams
- Add `_summarize_write_result` branches for all rel/int tools (or a generic
  `"{fn_name}: {human_label}"` map) so the `str(parsed)[:150]` raw fallback never
  feeds the narrator (rpjot.py:4067-4111).
- Prefix followup injections (ComplianceStep:677) with `[DIRECTIVE]` so they're not
  confusable with player input.
**Acceptance:** grep test — synthesis output for a turn using `record_bond` contains no
`{`/`}` characters; followup messages carry the prefix.

### T6 (P2) — Test harness fidelity + selection telemetry
- Port `TestToolDispatch` to production shape: phrase → `WorldStateStep.run` asserting
  step-1 tool selection; phrase+world-doc → `ComplianceStep.run` with
  `_compact_step2_schemas` asserting step-2 selection. Add negative consent tests
  ("Evie beckons you to follow" must NOT trigger `navigate_to`; player "I follow her"
  MUST). Run each N=3 with ≥2/3 pass threshold to absorb stochasticity (keep the
  conftest gating/serialization).
- Add per-turn selection telemetry: log `[TOOLS] turn=N step2 called=[…]` (data
  already in `canonical_results`) so under-used tools (the 19 rel/int taxonomy tools)
  become measurable before deciding whether to consolidate them into fewer
  parameterized tools (e.g. one `record_relationship(kind=…)`), which would also cut
  the 2,310-tok compact overhead.
**Acceptance:** new tests green against the live endpoint at the thresholds; debug.log
shows the per-turn tool census.

---

## 10. Verification procedure (post-sprint)

1. `python -m pytest test_rpjot.py -k "Dispatch or Guard"` with the endpoint
   configured — old + new activation tests green at thresholds; T4 unit tests green
   without any endpoint.
2. Fault-injection smoke: monkeypatch `call_llm` to return one malformed
   `tool_calls` entry (typo'd arg) mid-session — session survives, model receives the
   error JSON, turn completes (T4).
3. Live smoke: 10-turn `play.py` session — `/stats` shows no CAST warnings during
   normal play; deliberately roleplay an NPC arrival and verify either
   `set_people_present` fires or the T1 warning appears; check debug.log `[TOOLS]`
   census lines and `[STEP2] zero-canonical` handling on a pure-inquiry turn.
4. Note hygiene: after the session, `jot` search events by `exp:` tags and confirm
   witness tagging matches who was actually present in the transcript (T3 quality).
