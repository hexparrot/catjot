# FIXUP.md — Unified Execution Plan for PROMPTING.md (R1–R6) and TOOL_CYCLE.md (T1–T6)

**Purpose:** One autonomous, decision-complete execution plan that fixes every issue
identified in `dev_open/PROMPTING.md` and `dev_open/TOOL_CYCLE.md`. All open questions
from those documents are resolved here (§2 "Decisions locked") — an executing session
should not need to ask anything. Read those two documents first for the *why*; this
document is the *what, in what order, and how verified*.

**Baseline:** commit `0c506e8`. Line numbers are from that tree; if drifted, anchor on
the named functions. Work happens in `rpjot.py`, `play.py`, `test_rpjot.py`,
`test_catjot.py`. Nothing else changes.

---

## 1. Scope contract

**In scope (12 work items, 6 phases):**

| Item | Source | One-liner |
|---|---|---|
| W1 | T4 (P0) | Harden tool dispatch — bad args must not crash the session |
| W2 | R2 | Guard counts `tool_calls` tokens |
| W3 | R3 | Guard drops tool-call units atomically |
| W4 | R1 (P0) | Rolling history compaction ("STORY SO FAR" digest) |
| W5 | T1 | Cast-drift detection (warn, never auto-fix) |
| W6 | T2 | Conditional zero-canonical nudge in ComplianceStep |
| W7 | T3 | Restore critical argument contracts lost to schema compaction |
| W8 | R4 | Regression tests: #16, multi-turn growth, guard behavior |
| W9 | T6 | Production-shape live tests + per-turn tool telemetry |
| W10 | R5 | REPL token panel (`/prompt`) |
| W11 | T5 | Prose/tool seam fixes (summarizer fallback, directive prefix) |
| W12 | R6 | Hygiene: query-cache cap, per-session debug log, honest guard message |

**Explicitly OUT of scope (do not do, even if tempting):**
- No consolidation of the 19 relationship/interior tools (T6 telemetry first; a later
  sprint decides with data).
- No change to the note storage format, tag grammar, `Note`/`ContextBundle` APIs, or
  the 3-step architecture itself.
- No enabling/disabling of tools (all 42 stay registered — TOOL_CYCLE §2.2).
- No auto-mutation of `session.people_present` from narrative analysis (detection only
  — TOOL_CYCLE T1 rationale).
- No changes to catjot.py's `jot chat`/`jot convo`/`run_tool_loop` beyond the test in
  W8a (their issues are documented but not part of the RP loop).

---

## 2. Decisions locked (resolves every open question from both docs)

- **D1 — Compaction design (R1):** lives in play.py as `compact_history(engine,
  messages)`; threshold `HISTORY_SOFT_TOKS = 14_000` (50% of the 28k effective cap);
  `KEEP_RECENT_PAIRS = 8`; digest occupies exactly one `{"role":"user"}` slot at index
  1 (right after system), content prefixed `STORY SO FAR:\n`. Digest is built via the
  existing `engine._condense_context()` (rpjot.py:1533-1599) — do not write a new
  distiller. Since step2 and step3 histories hold identical pairs (play.py:455-458),
  **compute the digest once per trigger and install it into both lists**; measure each
  list independently but they will trip together.
- **D2 — Guard protects the digest:** `_guard_payload` pass 2 must treat a message
  whose content starts with `"STORY SO FAR:"` like a system message (skip; drop only if
  literally nothing else remains). Without this, the guard's oldest-first drop would
  delete the compacted memory first — the opposite of intent.
- **D3 — Compaction-loss fix (T3) uses the keep-list mechanism (option b), not
  description rewrites:** `_compact_step2_schemas` gains
  `_COMPACT_KEEP_PARAM_DESCRIPTIONS = {("record_event","tags"),
  ("record_knowledge","witnesses"), ("record_knowledge","observable_act"),
  ("navigate_to","location_name"), ("save_location","name")}`. Budget: the
  `registered …` log line must show `step2_compact ≤ 3,000` tok; if over, trim the
  keep-list from the bottom of the list above, never the first three entries.
- **D4 — Zero-canonical nudge (T2) exact behavior:** trigger only when
  `canonical_results` is empty AND the classified input starts with `[MC action]` or
  `[MC speaks aloud]` or `[MC — likely spoken aloud` (skip `[MC inner monologue` and
  `[MC attention`). Inject one user message:
  `"[DIRECTIVE] No canonical record was written this turn. If anything happened — an action, dialogue, or discovery — call record_event or record_knowledge now. If truly nothing canonical occurred, reply exactly: DONE."`
  Allow exactly one extra LLM round (still inside `max_iterations` headroom — raise the
  loop bound by 1 for this round only, or run the extra call after the loop). Never
  nudge twice. Log `[STEP2] zero-canonical nudge → {outcome}`.
- **D5 — Dispatch hardening (T4) via one shared helper:** add
  `RPJotEngine._safe_dispatch(self, handlers: dict, name: str, args_json) -> str`;
  `_dispatch`, `_dispatch_step2`, and WorldStateStep's inline dispatch
  (rpjot.py:558-561) all route through it. It catches `json.JSONDecodeError`,
  `TypeError`, and `Exception` from the handler, logs at WARNING with traceback, and
  returns `json.dumps({"error": f"tool {name} failed: {type(exc).__name__}: {exc}",
  "hint": "check argument names and types against the tool schema"})`. Add pre-invoke
  key validation against the schema's `required` list (schemas are reachable via
  `self._step1_schemas`/`self._step2_schemas`); missing keys short-circuit to the same
  error JSON without invoking.
- **D6 — Shared token accounting:** add module-level `def _msg_toks(m: dict) -> int` in
  rpjot.py (content + tool_calls name/arguments). `_guard_payload` (W2), the recount at
  4485, pass-2 per-message costs (4474), `compact_history` (W4), and the `/prompt`
  panel (W10) all use it. play.py imports it from rpjot.
- **D7 — Telemetry (T6) is log-only:** one INFO line per turn from `run_turn`:
  `[TOOLS] turn=%d step2=%s` with the list of fn names from `canonical_results`
  (empty list logs too). No persistence, no UI beyond debug.log.
- **D8 — Hygiene specifics (R6):** `_query_cache` capped at 256 entries, FIFO eviction
  in `_cache_put` (rpjot.py:1135-1149); debug log file becomes
  `debug_{session_stamp}.log` where play.py passes the session timestamp into a new
  `rpjot.configure_logging(stamp)` (falls back to `debug.log` when never called, so
  tests/imports keep working); guard's 90-99% message reports actual tokens shed and
  says "no trimmable tool results" when pass 1 sheds 0.
- **D9 — Live-test policy (W9):** keep conftest gating (skip without `openai_api_url`)
  and the serializer fixture. New activation tests run each phrase **N=3 and require
  ≥2 passes** (helper `_passes(fn, n=3, need=2)` inside test_rpjot.py). Production-shape
  means: step-1 selection asserted via `call_llm(messages, tools=engine._step1_schemas,
  ...)` under `_STEP1_SYSTEM`; step-2 selection via `engine._compact_step2_schemas`
  under a representative gameplay-rules system message with a world-doc briefing
  prepended (mirror ComplianceStep 615-624; don't call the full pipeline — assert
  selection, not narration).
- **D10 — Session-resume digest seeding is a stretch goal (S1), not required:** if all
  phases complete and verify, seed `STORY SO FAR` on resume from the newest ~15
  `/summaries` notes via `ContextBundle(PWD_SUMMARIES)` + `_condense_context`. Skip
  without guilt if anything else is unfinished.
- **D11 — Commit strategy:** one commit per phase (6 commits), message format
  `fixup phase N: <items>` on a branch `fixup-prompting-toolcycle` off `main`. Run the
  Phase-0 test baseline before the first commit and the full verification (§5) before
  the last. Do not push or open a PR unless the user asks.

---

## 3. Phase plan (dependency-ordered)

### Phase 0 — Baseline (no code changes)
1. `python -m pytest test_catjot.py test_rpjot.py test_context.py -q` — record which
   tests pass/skip. LLM-gated classes skip cleanly without `openai_api_url`; that is
   expected and fine. Any pre-existing failure gets noted in the phase-0 commit message
   of Phase 1 (do not fix unrelated failures; do not proceed if the non-LLM suite is
   red for reasons the plan touches).
2. `grep -c "OVER LIMIT" debug.log` and note the last `registered step1=… step2=…`
   line — these are the before-numbers for §5.

### Phase 1 — Stop the bleeding (W1, W2, W3) — all rpjot.py
Independent of each other; do together, they share tests.

- **W1 (T4):** implement `_safe_dispatch` per D5; rewire `_dispatch` (932-938),
  `_dispatch_step2` (940-945), and step-1 inline dispatch (558-561). Preserve the
  existing unknown-tool JSON error behavior (it must remain the first check).
- **W2 (R2):** add `_msg_toks` per D6; replace the content-only sums in
  `_guard_payload` at 4397 and 4485, and the pass-2 `ctoks` at 4474.
- **W3 (R3):** pass 2 drops tool-call units atomically: when the victim has
  `tool_calls`, also null every subsequent `role=="tool"` whose `tool_call_id` is in
  its ids; when the victim is a `role=="tool"`, locate its parent assistant and drop
  parent + all sibling tools together. Also apply D2 (digest protection) here — one
  skip-condition alongside the existing system-role skip.

**Phase tests (no LLM needed), add to `TestGuardPayload` and a new
`TestSafeDispatch`:**
- typo'd kwarg, non-JSON args, wrong-type arg (`witnesses: "alice"`) → error JSON, no
  exception; loop-continuation test (fake 3-round step-2 where round 1 errors).
- tokens-in-tool_calls-only list crosses 85% (W2).
- forced ≥100% list with `[…, assistant(tool_calls=[a,b]), tool(a), tool(b), …]` →
  no orphans either direction; final message untouched; digest message survives (D2).

### Phase 2 — History compaction (W4) — play.py + one guard touch
Depends on `_msg_toks` (Phase 1).

- Implement `compact_history` per D1 (the reference implementation in PROMPTING.md R1
  is close to final — adjust for D1's compute-once/install-twice and `_msg_toks`).
  Call it for both lists right after the post-turn appends (play.py:455-458) and after
  the empty-narrative appends (438-441).
- Ensure ordering with the system-refresh hook (play.py:460-461): compaction runs
  FIRST, then `refresh_system_message` — refresh replaces index 0 and must not clobber
  the digest at index 1 (verify `refresh_system_message` only touches index 0; if it
  rebuilds the list, fix it to preserve the digest slot).

**Phase tests (`TestHistoryCompaction`, mock `_condense_context` to a fixed 1k-tok
string):** 50 simulated turns of the append pattern → history `_msg_toks` sum stays ≤
`HISTORY_SOFT_TOKS` + one turn; exactly one `STORY SO FAR` message, at index 1; last
`KEEP_RECENT_PAIRS` pairs verbatim; a second trigger folds the old digest into the new
one (no digest stacking); guard never leaves the <85% tier across the run (this is
R4b, satisfied here).

### Phase 3 — Activation dependability (W5, W6, W7) — rpjot.py

- **W5 (T1):** post-step-2 cast-drift scan in `run_turn` per TOOL_CYCLE T1: compare
  NPCTracker known names/slugs mentioned in `classified_input + narrative` against
  `session.people_present`; WARNING log `[CAST] mentioned-but-absent: …`; surface the
  same string in `scene_debug_report()` and in WorldStateStep's SCENE STATE header
  next turn (store on `self._cast_warnings`, cleared when the discrepancy resolves).
  Word-boundary matching on display_name and slug, case-insensitive; skip the main
  character.
- **W6 (T2):** zero-canonical nudge per D4, implemented at the ComplianceStep exit
  path (641-654).
- **W7 (T3):** keep-list in `_compact_step2_schemas` per D3; assert the token budget
  in the register_all_tools log line.

**Phase tests:** W5 — scripted narrative mentioning an unregistered-in-cast NPC →
warning present; registered-and-present NPC → none (no LLM: call the scan helper
directly). W6 — mock LLM: prose-only round then `record_event` → non-empty canonical;
"DONE" reply → clean `canonical=0` completion, single nudge only. W7 — compact schema
JSON for `record_event` contains the `exp:`/`know:` grammar; `record_bond` (not in
keep-list) has no param descriptions; cached tok value ≤ 3,000.

### Phase 4 — Test-harness completion (W8, W9)

- **W8a (#16):** in test_catjot.py — 5× `register_tool` same name → one schema; changed
  description updates in place; `TOOL_HANDLERS` holds the latest handler; 3×
  `register_search_tools()` → exactly 4 search schemas. Snapshot/restore the module
  globals in setUp/tearDown.
- **W8b:** multi-turn growth test — already delivered as Phase 2's
  `TestHistoryCompaction`; confirm it covers the R4b assertions, add any missing.
- **W8c:** guard tests — delivered in Phase 1; audit against PROMPTING R4c list.
- **W8d (cheap):** `classify_input` unit tests for all four sigils + default.
- **W9 (T6):** production-shape live tests per D9, including the negative consent pair
  ("Evie beckons you to follow" must NOT select `navigate_to`; "I follow her" must),
  `set_people_present` on an arrival phrase, and `save_character` after a
  new-NPC-introduction briefing. Add the `[TOOLS]` telemetry line in `run_turn` per D7.

### Phase 5 — Observability & seams (W10, W11, W12)

- **W10 (R5):** `/prompt` token panel per PROMPTING R5 mock-up: per-history `_msg_toks`
  totals and %, message counts, digest present y/n, the three cached schema overheads,
  `engine._last_payload_toks`, and turns-until-85% from a trailing-5-turn deque of pair
  sizes maintained in `game_loop`. Append the same block to `scene_debug_report()`
  (pass the histories in, or expose a `history_report(step2_messages, step3_messages)`
  helper on the engine — helper preferred, play.py stays thin).
- **W11 (T5):** summarizer branches (or a name→label map) for all rel/int tools so the
  `str(parsed)[:150]` fallback never renders a dict into the synthesis; prefix followup
  injections (ComplianceStep:677) and the W6 nudge with `[DIRECTIVE] `.
- **W12 (R6):** per D8 — cache cap, per-session debug log via
  `configure_logging(stamp)` called from play.py `set_session_file`, honest 90-99%
  guard message.

**Phase tests:** synthesis output for a fake `record_bond` result contains no braces;
`_cache_put` past 256 evicts oldest; guard 90-99% path with zero trimmable results
logs the "no trimmable tool results" variant. Panel is manual-verified in §5.

### Phase 6 — Stretch (S1, optional per D10)
Resume-time digest seeding from `/summaries`. Only if Phases 1–5 are done and §5 is
green. Test: create a session file with 20 summary notes, resume, assert index-1
digest exists and step-1 briefing reflects it.

---

## 4. Cross-cutting interactions to keep in mind while editing

- W4's digest + W3's unit-dropping both modify `_guard_payload`'s pass-2 skip logic —
  implement W3 with the D2 skip in the same edit; W4 then needs no guard changes.
- W6 adds up to 2 messages + 1 LLM round inside step 2 — the guard call at 627 already
  covers it; do not add a separate guard call.
- W2 changes measured totals, which will make the guard fire *earlier* than before
  (correctly). `TestGuardPayload`'s existing threshold fixtures may need their filler
  sizes retuned — adjust the fixtures, not the thresholds.
- W12's per-session log file must not break the module-import-time logger used by
  tests: `configure_logging` re-points the FileHandler; default behavior unchanged.
- W5's SCENE STATE warning adds tokens to step-1's initial message — trivial (<50 tok),
  no budget action needed.

---

## 5. Final verification (run before the last commit)

1. **Unit/deterministic:** `python -m pytest test_catjot.py test_rpjot.py
   test_context.py -q` — all non-LLM tests green, including the ~10 new classes/tests
   from Phases 1–5. Compare against the Phase-0 baseline: no previously-passing test
   now fails.
2. **Live (only if `openai_api_url` is configured):** same command with env set —
   D9-thresholded activation tests green; if the endpoint is unavailable, record that
   W9's live portion is unverified in the final commit message rather than skipping
   silently.
3. **Fault injection:** monkeypatch script (scratch, not committed) that makes the
   step-2 LLM return a typo'd-arg tool call mid-session → session survives, error JSON
   visible in debug log, turn completes (W1 end-to-end).
4. **Soak:** extend `TestHistoryCompaction` locally to 200 turns → sawtooth-bounded
   history, zero guard lines ≥85%.
5. **Live smoke (manual, if endpoint available):** `python play.py`, ~10 turns:
   `/prompt` shows the token panel with sane numbers; debug_{stamp}.log exists and
   shows `[TOOLS]` census lines, all `[CTX] guard:` at DEBUG tier, `[HIST] compacted`
   after crossing 14k (drive it faster by temporarily setting HISTORY_SOFT_TOKS=2000 —
   revert before commit); roleplay an NPC arrival → either `set_people_present` fires
   (see `[TOOLS]`) or `[CAST]` warning appears in `/stats`; a pure-inquiry turn logs
   the zero-canonical DONE path; narrative still references early events after a
   compaction (digest quality spot-check).
6. **Note hygiene:** after the smoke session, verify event notes carry `exp:` tags
   matching who was present (W7 quality).

**Definition of done:** all 12 work items merged on `fixup-prompting-toolcycle` in 6
phase commits; §5 items 1, 3, 4 green unconditionally; 2 and 5 green or explicitly
recorded as environment-blocked; every acceptance criterion in PROMPTING R1–R6 and
TOOL_CYCLE T1–T6 either satisfied or covered by a line in this plan's decisions (D1–D11).
