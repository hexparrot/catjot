# PROMPTING.md — LLM Prompt Architecture Review & Remediation Spec

**Scope:** catjot/rpjot LLM functionality — the accumulating-tool problem (#16), the
three-step pipeline, dynamic context, structural tags, token efficiency of iterative RP,
and robustness/debuggability against runaway prompt sizes.

**Review date:** 2026-07-02, at commit `0c506e8` (merge of `c0c1846`, "catjot iteration
fix,cleanup (#17)"). All line numbers below refer to that tree. If line numbers have
drifted, the function/class names are the stable anchors.

**Intended use:** This is a sprint-ready spec. Section 1–6 is the verified accounting of
how the system works today and where it breaks. Section 7 is the remediation backlog —
each item has the problem, exact code location, a concrete proposed change, and
acceptance criteria. A future session should be able to implement directly from Section 7
without re-deriving the analysis.

---

## 1. Executive verdict

| Question asked | Answer |
|---|---|
| Is the #16 accumulating-tool fix complete? | **Yes** — `register_tool` dedupes by name (catjot.py:1444-1448); no other accumulation path found in catjot.py or rpjot.py. **But zero regression-test coverage** (item R4). |
| Are per-call prompts bounded? | **Yes** — `_guard_payload` (rpjot.py:4378-4492) runs before every LLM call. A runaway API payload is structurally impossible. |
| Does iterative RP stay token-efficient? | **Per-turn: yes** (heavy injections are per-call only). **Per-session: no** — the persistent histories in play.py grow ~400–750 tok/turn with no compaction, degrading long sessions from ~turn 25–35 onward (item R1, the core fix). |
| Is the note hierarchy used well? | **Yes** — location-ancestor traversal, per-character POV views with knowledge asymmetry, tiered condensation. Well-tested. |
| Is a runaway prompt debuggable? | **On disk: yes** (debug.log guard lines with token counts). **At the REPL: no** — `/prompt` and `/stats` show no history-size numbers (item R5). |

---

## 2. System map

### 2.1 Files

| File | Lines | Role |
|---|---|---|
| `catjot.py` | 3344 | Note store + CLI; generic LLM tool registry (`TOOL_SCHEMAS`/`TOOL_HANDLERS`), `call_llm`, `run_tool_loop`, `jot chat/convo/llm` |
| `rpjot.py` | 4492 | RP engine: 3-step pipeline, SessionState, tag namespace, context gathering/condensation, `_guard_payload`, NPC tracker, debug report |
| `play.py` | 531 | Interactive REPL: sigil classification, `game_loop`, session file management, slash commands, **owner of the persistent message histories** |
| `create_canonical_seed.py` | 427 | Builds the seed `.jot` (system roles, premise, characters, locations) |
| `sessions/` | — | Timestamped `.jot` session files (copy of seed + accumulated notes) |
| `debug.log` | ~117k lines | `rpjot_engine` logger output (DEBUG level, also on stderr) |

### 2.2 The three-step pipeline (rpjot.py)

Orchestrated by `RPJotEngine.run_turn(classified_input, step2_messages, step3_messages)`
(rpjot.py:988-1049). Logs per step: `[TURN] step1 done: world_doc=X tok`,
`[TURN] step2 done: canonical=N think=M`, `[TURN] step3 done: narrative=X tok`.

```
run_turn(classified_input, step2_messages, step3_messages)
├─ WorldStateStep.run(classified_input)                       (rpjot.py:437-590)
│  │  ISOLATED message list — never touches persistent history.
│  ├─ system: _STEP1_SYSTEM (206-215, "scene intelligence system")
│  ├─ user:   _build_initial_message (447-468): SCENE STATE header,
│  │          NPC roster, _build_baseline_context (470-511: location
│  │          ancestors + per-character knowledge), player input
│  ├─ tool loop ≤8 iters, read-only step=1 tools
│  │          (_step1_schemas/_step1_handlers, 769-771; dispatch 932-938)
│  └─ returns WorldStateDoc, think-stripped (544). Observed 279–1830 tok.
│
├─ ComplianceStep.run(classified_input, world_doc, step2_messages)   (592-680)
│  │  Works on a COPY: messages = list(step2_messages) + [user msg]  (622-624)
│  ├─ user msg = "WORLD STATE BRIEFING:\n{world_doc}"
│  │           + "NARRATOR RULE: ..." (_NARRATOR_RULE, 3961-3969)
│  │           + classified_input                                   (615-624)
│  ├─ tool loop ≤10 iters; _guard_payload before EVERY call (627-629)
│  │          with schema_overhead=_cached_compact_step2_schema_toks
│  ├─ tools: _compact_step2_schemas (860-888) — param descriptions
│  │          stripped, ~7,700 → ~2,000 tok
│  ├─ per tool call: canonical_results.append((fn_name, result)) (670);
│  │          tool msg via build_tool_result_message (672-674);
│  │          followup_instruction appended as separate user msg (676-677)
│  ├─ <think> blocks harvested into accumulated_think (643-658)
│  └─ returns (canonical_results, accumulated_think)
│
└─ ProseStep.run(classified, world_doc, canonical_results,
                 accumulated_think, step3_messages)                  (683-744)
   │  Works on a COPY: prose_messages = list(step3_messages) + [inj] (722-724)
   ├─ injection: "PROSE PHASE ..." + WORLD STATE + _gather_attn_for_scene
   │          (1368-1387) + _gather_mood_for_scene (1389-1405)
   │          + _build_narrative_synthesis (4113-4150) + input      (706-720)
   ├─ _guard_payload with schema_overhead=_cached_bare_schema_toks  (726-728)
   ├─ call_llm: tools=_bare_tool_schemas (name-only stubs, 833-844),
   │          tool_choice="none", temp=STEP3_TEMPERATURE=1.2,
   │          max_tokens=MAX_TOKENS_STEP3=3072                      (731-737)
   └─ returns think-stripped narrative (742). Observed 294–601 tok.
```

### 2.3 The caller loop (play.py `game_loop`, 323-462)

```python
step2_messages = build_step2_initial_messages()   # [system: gameplay rules]
step3_messages = build_step3_initial_messages()   # [system: prose craft]
while True:
    classified = classify_input(user_input)       # sigils → directives (74-111)
    narrative = engine.run_turn(classified, step2_messages, step3_messages)
    Note.jot(message=narrative, tag="summary", context=user_input,
             pwd=PWD_SUMMARIES); Note.append(...)  # 446-452: journal every turn
    step2_messages += [user: classified, assistant: narrative]   # 455-456
    step3_messages += [user: classified, assistant: narrative]   # 457-458
    if engine._system_refresh_pending:            # every 8 turns (rpjot.py:203)
        refresh_system_message(engine, step2_messages)  # REPLACES system msg
```

Key facts:
- **Only `classified + narrative` persists.** World docs, synthesis, tool traffic,
  followups — all live only in the per-call copies inside the steps. This is the
  pipeline's principal token-efficiency property. ✅
- **Nothing ever removes messages from these lists.** ❌ (see §4.2, fix R1)
- Session resume (play.py:469-527, `create_session`/`set_session_file`) reloads the
  `.jot` note file only; message lists always start fresh from the seed system messages.
  So resume is token-lean, and continuity comes exclusively from notes.
- LLM failures are graceful: `LLMError` caught (431-433) and empty narratives append
  `"(no response)"` placeholders (435-442) — small, bounded.

### 2.4 Token budget model (rpjot.py)

| Constant | Value | Line | Meaning |
|---|---|---|---|
| `MODEL_CONTEXT_LIMIT_TOKS` | 30,000 | 179 | model window (reduced from 62k+ by commit `ab5356a`; old debug.log lines show `cap=62000`) |
| `_RESPONSE_RESERVE_TOKS` | 2,000 | 180 | reserved for the reply → **effective cap = 28,000** |
| `CONTEXT_MAX_TOKS` | 2,000 | 167 | soft limit per rendered context bundle |
| `CONTEXT_HARD_LIMIT_TOKS` | 8,000 | 170 | hard ceiling per bundle (truncate, no LLM) |
| `MAX_TOKENS_STEP1/2/3` | 4096/4096/3072 | 193-229 | per-step output budgets |
| `MAX_TOKENS_CONDENSE` | 2,048 | ~193 | distillation output budget |
| `SYSTEM_REFRESH_INTERVAL` | 8 | 203 | turns between system-msg paraphrase |

Tokenizer: `_tok()` (rpjot.py:42-88) — cl100k_base pre-tokenizer regex when the `regex`
package is available, else `len(text)//4`. `_TOK_SOURCE` logs which is active.
`self._last_payload_toks` (773, set at 4399) caches the last guard measurement and feeds
adaptive headroom in `render_context` (1483).

Observed steady-state per-call payloads (debug.log, young session, cap=28,000):
step1 ≈ 2.9–3.9k (10–14%), step2 ≈ 10.3–11k (37–39%), step3 ≈ 8.3–9.1k (30–32%).
The delta between step2 and step1 payloads is dominated by the persistent history plus
the world-doc injection.

---

## 3. Finding: accumulating tool problem (#16) — FIXED, UNTESTED

**History:** `register_tool` (catjot.py:1427-1448) unconditionally did
`TOOL_SCHEMAS.append(...)`. `register_search_tools()` (catjot.py:1576-1618) is called on
**every** `run_tool_loop` invocation (catjot.py:1704), so each `jot llm` run within a
process appended 4 duplicate schemas — prompts grew linearly with invocations.

**Fix (commit `ae38475`):** catjot.py:1444-1448 scans `TOOL_SCHEMAS` for an existing
entry with the same `function.name` and replaces it in place; append only if new.

**Verified complete:**
- `TOOL_HANDLERS` (catjot.py:1421) is a dict keyed by name — overwrite-safe by
  construction (assignment at 1435).
- The search-tool factory closures (`make_*_search_handler`, catjot.py:1475-1559)
  capture no mutable state; re-registration is idempotent.
- rpjot.py never had the problem class: `@rp_tool`-decorated methods (decorator
  237-251) are discovered once at engine init by `register_all_tools()` (951-987) into
  step-partitioned lists (`_step1_schemas`/`_step1_handlers`,
  `_step2_schemas`/`_step2_handlers`), and schema token costs are computed once and
  cached (`_cached_schema_toks`, `_cached_bare_schema_toks`,
  `_cached_step1_schema_toks`, `_cached_compact_step2_schema_toks`).

**Residual gap:** no test anywhere exercises repeated registration. See **R4a**.

---

## 4. Finding: prompt-growth robustness

### 4.1 `_guard_payload` — what it guarantees (rpjot.py:4378-4492)

Called before **every** LLM call in all three steps (lines 627, 726, and inside the
step-1 loop at ~525) with the appropriate `schema_overhead`. Tiers against
`capacity = 28,000`:

| Payload % of cap | Action |
|---|---|
| < 85% | `logger.debug("[CTX] guard: N tok (msg=X schema=Y, Z% of cap)")`, return unchanged |
| 85–89% | `logger.warning("APPROACHING LIMIT")`, return unchanged |
| 90–99% | warning + **pass 1**: truncate oldest `role=="tool"` message contents (4443-4464), appending `"[payload guard: truncated]"` |
| ≥ 100% | pass 1 + **pass 2**: null-out oldest non-system messages (4466-4483), target ≈88% of cap |

Invariants: never touches `role=="system"`, never touches the final message; when
mutating, works on a **shallow copy** (`[dict(m) for m in messages]`, 4439) — the
caller's list is never modified.

This guarantees no single API call can exceed the window. That guarantee is real and
tested (`TestGuardPayload`, test_rpjot.py:1051-1141).

### 4.2 The unbounded-growth problem — moved up one level (CORE ISSUE)

Because the guard trims a *copy* at *send time*, and play.py never prunes the persistent
lists, a long RP session has this trajectory (numbers from debug.log observations:
classified input ≈ 50–150 tok, narrative ≈ 300–600 tok → **~400–750 tok/turn** added to
*each* history; step-2 non-history overhead ≈ 8–10k tok):

1. **Turns 1–~25:** payloads climb from ~37% toward 85%. Silent, healthy.
2. **~Turn 25–35:** step 2 crosses 85% → every call logs APPROACHING LIMIT.
3. **90–99% band:** pass 1 looks for `role=="tool"` messages to trim — **but the
   persistent history contains only user/assistant pairs** (play.py appends only those),
   and within-turn tool messages are young/small. Pass 1 sheds ≈ nothing; the guard
   *warns that it is trimming while shedding no tokens*. Payload keeps growing.
4. **≥ 100%:** pass 2 silently drops the oldest story turns — **recomputed from scratch
   on every call** (the persistent list still holds everything), so every subsequent
   call pays: full-history `_tok()` scan, warning spam (210 warning-tier lines already
   in debug.log), and a re-derived drop set. Narrative continuity now depends on which
   prefix survived *this* call's trim.

Net effect: the system never crashes, but "constant back and forth during RP" degrades
permanently after roughly 30–40 turns — silently, with the only signal buried in
debug.log. **This is the single highest-priority remediation (R1).**

Mitigating assets that already exist (reuse, don't rebuild):
- Every turn's narrative is journaled to `/summaries` notes
  (play.py:446-452, `PWD_SUMMARIES` = rpjot.py:107).
- `_condense_context()` (rpjot.py:1533-1599) is a working LLM distiller with
  hard-truncate fallback and reduction-percentage logging.
- The scene system has an end-scene summarize affordance (prompt text at rpjot.py:145,
  2457).
- `refresh_system_message` (play.py:291, driven by `_system_refresh_pending`,
  rpjot.py:778/1042-1047) already demonstrates the "replace, don't append" pattern.

### 4.3 Bug A — guard undercounts `tool_calls` payloads

rpjot.py:4397: `msg_toks = sum(_tok(str(m.get("content") or "")) for m in messages)`.
Assistant messages appended during the step-2 loop (`messages.append(response_msg)`,
line 660) carry their function-call arguments in `m["tool_calls"]` with `content` often
empty/None. Those argument JSONs (freeform strings — e.g. `record_event`,
`record_knowledge` payloads — can be hundreds of tokens each, ×10 iterations) are
**invisible to the guard**, so the real payload exceeds the measured one exactly in the
step most likely to be near the cap. Fix: **R2**.

### 4.4 Bug B — pass 2 can orphan tool messages

Pass 2 (rpjot.py:4467-4483) drops messages individually, oldest-first, skipping only
`system`. Within-turn step-2 message lists interleave
`assistant(tool_calls=[id…]) → tool(tool_call_id=…) → user(followup)`. Dropping the
assistant while keeping its `tool` reply (or vice versa) yields a sequence that violates
the OpenAI chat format; several OpenAI-compatible backends (vLLM, llama.cpp server,
OpenAI proper) hard-reject it → the guard's rescue attempt itself causes an `LLMError`.
Fix: **R3**.

### 4.5 Minor per-session growth (RAM only, low risk — note, don't necessarily fix)

- `SessionState._query_cache` (rpjot.py:389-391; `_cache_put/_cache_get/_cache_drop`
  1135-1149): grows with distinct query keys; invalidated on writes
  (e.g. `_cache_drop("char:alice")` in `save_character`) but never globally evicted.
- `NPCTracker._records` (rpjot.py:279-372, preloaded by
  `_preload_npc_tracker_from_notes`, 801-823): monotonic per session; also inflates the
  roster block injected into step-1's initial message as the cast grows.
- `canonical_results`/`accumulated_think` are per-turn locals — bounded by iteration
  caps, no cross-turn leak. ✅

---

## 5. Finding: dynamic context, structural tags, hierarchy — all sound

### 5.1 Dynamic context / boilerplate
- `build_user_message(user_input, dynamic_context="")` (rpjot.py:3971-3989) prepends
  `"SCENE CONTEXT:\n…"` **only to the outgoing message** — never persisted. ✅
- Dynamic boilerplate / entropy refresh: every `SYSTEM_REFRESH_INTERVAL=8` turns (or on
  `begin_scene`, rpjot.py:2438) `_system_refresh_pending` is set; play.py:460-461 calls
  `refresh_system_message` which **replaces** `step2_messages[0]` — no growth. ✅
- play.py `build_dynamic_context` extracts *domain* tags (filtering the structural
  prefixes, play.py:159-201) to pull related lore per turn — again per-call only. ✅

### 5.2 Structural tags & sigils
- Tag namespace (rpjot.py:94-114): `loc:` `char:` `exp:` `know:` `scene:` `cons:`
  `yomi:` `rel:` `int:` — used as retrieval keys, cleanly separated from domain tags.
- Input sigils (play.py:61-111, `classify_input`): `"`=speech, `*`=action,
  `@`=attention, `^`=inner monologue → translated to explicit directives before the LLM
  sees them.
- Output hygiene: `strip_think_tags` (rpjot.py:1069-1100) removes `<think>`
  (incl. unclosed/truncated) and `<tool_call>` blocks from **every** step's output
  before anything is stored or re-injected; `strip_followup_instruction`
  (1183-1196) removes the `followup_instruction` key from tool-result JSON before it
  enters message history (the instruction rides as a separate, per-call user msg).
  Empty-after-strip narratives handled (commit `efc3b30`; play.py:435-442). ✅
  **No tag garbage compounds across turns.**

### 5.3 Hierarchy usage
- Notes are flat records (catjot.py:177-405: pwd/now/tag/context/message); hierarchy is
  **directory-prefix based**: `SearchType.TREE` (catjot.py:145-156) matches
  `inst.pwd.startswith(...)` in `Note.match` (685-790, TREE at 732-733).
- `location_ancestors` (rpjot.py:399-405) expands `"manor/foyer/closet"` →
  `["manor", "manor/foyer", "manor/foyer/closet"]`; baseline context includes notes at
  every level.
- `gather_pov_context(char)` (rpjot.py:1300-1326) assembles a character's knowledge
  view: ancestor-location notes + own profile + `cons:` constraints + private
  `know:{char}` + shared `exp:{char}` + interior `int:` + active-scene notes — and
  **excludes other characters' private knowledge**. Knowledge asymmetry is the
  best-tested behavior in the repo (`TestGatherPovContext` test_rpjot.py:1223-1295,
  `TestKnowledgeGapScenario` 1378-1703, `TestPrivateConversationKnowledge` 1705-1902).
- `render_context` (rpjot.py:1449-1531) bounds every bundle: headroom passthrough
  (<50% of `capacity - _last_payload_toks`) → soft passthrough (≤2k) → LLM condense to
  ~1k via `_condense_context` → hard truncate above 8k. Newest-first sort (1465), focus
  hints for biased condensation. Tested (`TestRenderContext` 912-1043,
  `TestCondenseContext` 1148-1216). ✅

**Conclusion:** the hierarchical/tag design is doing its job; token pressure does not
come from note context (it's tiered and capped) — it comes from conversation history
(§4.2).

---

## 6. Finding: debug facilities inventory

| Facility | Where | Shows | Gap |
|---|---|---|---|
| `rpjot_engine` logger | rpjot.py:77-88 → stderr + `debug.log`, DEBUG level | every guard measurement (`[CTX] guard: N tok (msg=X schema=Y, Z% of cap=…)`), step completions with tok counts, condensation ratios (`CONDENSE 4136 tok → target ~1000`), hard truncations, trims/drops | not surfaced in the REPL; log is huge (~117k lines) with no per-session segmentation |
| `/stats` → `scene_debug_report()` | rpjot.py:4160-4372 | per-entity **note-context** token budgets vs cap, NPC tracker state | does **not** include step2/step3 history size — the number that actually grows |
| `/prompt [text]` | play.py:365-385 | step-2 system (first 800 chars), step-3 system (full), NPC roster, classified-input preview | **zero token numbers**; doesn't show history length, schema overhead, or `_last_payload_toks` |
| `_last_payload_toks` | rpjot.py:773/4399 | last measured payload | internal only |
| `[TURN]` info logs | run_turn | world_doc/narrative tok per step | log-only |

**Bottom line:** you can reconstruct a runaway from debug.log after the fact, but a
player at the REPL has no live indicator that turn 30 is about to start dropping story
history. Fix: **R5**.

Test-coverage gaps (verified by sweep of test_rpjot.py/test_catjot.py/test_context.py):
- No test for #16 re-registration idempotency.
- No multi-turn test measuring history growth (`TestGuardPayload` covers single
  oversized payloads only).
- No test that pass 2 preserves tool_calls/tool pairing.
- No test that `tool_calls` tokens are counted.
- No sigil-classification unit tests (`classify_input`) — implicit coverage only.
- `scene_debug_report` untested.

---

## 7. Remediation backlog (sprint-ready)

Priority order. R1 is the core fix; R2/R3 are correctness bugs in the safety net;
R4 locks everything in; R5 is observability.

### R1 — Persistent history compaction (play.py + one helper in rpjot.py)

**Problem (§4.2):** `step2_messages`/`step3_messages` grow forever; guard re-trims a
copy every call once >85%, silently dropping story context, recomputed per call.

**Change:** compaction after append, in `game_loop` (immediately after play.py:455-458):

```python
HISTORY_SOFT_TOKS = int(0.5 * (MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS))  # 14k
KEEP_RECENT_PAIRS = 8   # last 8 user/assistant pairs stay verbatim

def compact_history(engine, messages, keep_pairs=KEEP_RECENT_PAIRS):
    """Fold oldest turns into one 'STORY SO FAR' message. Mutates in place."""
    hist_toks = sum(_tok(str(m.get("content") or "")) for m in messages)
    if hist_toks <= HISTORY_SOFT_TOKS:
        return
    # messages[0] is system; an existing STORY SO FAR digest, if present, is messages[1]
    head, body = messages[:1], messages[1:]
    digest_prefix = ""
    if body and body[0]["role"] == "user" and body[0]["content"].startswith("STORY SO FAR:"):
        digest_prefix = body.pop(0)["content"]
    cut = max(0, len(body) - 2 * keep_pairs)
    old, recent = body[:cut], body[cut:]
    if not old:
        return
    raw = digest_prefix + "\n\n" + "\n\n".join(
        f"[{m['role']}] {m['content']}" for m in old
    )
    digest = engine._condense_context(raw, focus_hint=None)   # rpjot.py:1533-1599,
    #   already has hard-truncate fallback on LLM failure — reuse, don't reimplement
    messages[:] = head + [{"role": "user",
                           "content": f"STORY SO FAR:\n{digest}"}] + recent
    logger.info("[HIST] compacted %d msgs → digest (%d tok history now)", len(old),
                sum(_tok(str(m.get('content') or '')) for m in messages))
```

Call for both lists each turn. Design notes:
- Trigger at 50% of cap so compaction happens *well before* the guard's 85% tier; the
  guard remains the backstop, not the mechanism.
- The digest replaces prior digests (idempotent single "STORY SO FAR" slot right after
  system) — history token count becomes sawtooth-bounded, roughly
  `digest(~1k) + 8 pairs(~5k) + system`, permanently.
- `_condense_context` signature/behavior: distills toward `CONTEXT_MAX_TOKS/2 ≈ 1000`
  tok, logs reduction %, hard-truncates on LLM failure — exactly the needed semantics.
- Alternative source: rebuild the digest from `/summaries` notes
  (`ContextBundle(PWD_SUMMARIES)`) instead of the message list; equivalent content
  (play.py journals every narrative there). Message-list folding is preferred because it
  preserves the user side of exchanges; keep the notes path in mind for session-resume
  continuity (a resumed session could seed `STORY SO FAR` from `/summaries`).
- Compaction must never touch `messages[-1]` mid-turn — running it after the
  post-turn appends (455-458) guarantees that.

**Acceptance:** simulated 60-turn session (mock LLM returning ~500-tok narratives) keeps
`_tok(history)` ≤ `HISTORY_SOFT_TOKS + one turn` at all times and the guard never logs
above the <85% tier; exactly one `STORY SO FAR` message exists; last `KEEP_RECENT_PAIRS`
exchanges survive verbatim.

### R2 — Count `tool_calls` in `_guard_payload` (rpjot.py:4397)

**Problem (§4.3):** guard measures `content` only; step-2 assistant messages carry
uncounted `tool_calls` argument JSON.

**Change:**

```python
def _msg_toks(m):
    t = _tok(str(m.get("content") or ""))
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function", {})
        t += _tok(fn.get("name", "")) + _tok(str(fn.get("arguments", "")))
    return t
msg_toks = sum(_msg_toks(m) for m in messages)
```

Use `_msg_toks` also at the recount (rpjot.py:4485) and in pass-2's per-message `ctoks`
(4474) so drop accounting matches. **Acceptance:** unit test — a message list whose
tokens live entirely in `tool_calls` arguments crosses the 85% threshold and triggers
the warning tier.

### R3 — Drop tool-call units atomically in pass 2 (rpjot.py:4466-4483)

**Problem (§4.4):** dropping an `assistant(tool_calls)` without its `tool` replies (or a
`tool` without its parent) creates an API-invalid sequence.

**Change:** when pass 2 selects message `i` for dropping:
- if `messages[i]` has `tool_calls`: also null every subsequent `role=="tool"` whose
  `tool_call_id` is in that call's ids;
- if `messages[i]["role"]=="tool"`: instead locate and drop its parent assistant plus
  all sibling `tool` messages as one unit;
- decrement `excess` by the unit's total (use `_msg_toks` from R2).

**Acceptance:** unit test — build `[system, filler…, assistant(tool_calls=[a,b]),
tool(a), tool(b), user, assistant, user]` forced over 100%; after guarding, assert no
`tool` message survives without its parent and vice versa; final message untouched.

### R4 — Regression tests

- **R4a (#16):** in test_catjot.py — call `register_tool("t", d1, p, h)` five times;
  assert `len([s for s in TOOL_SCHEMAS if s["function"]["name"]=="t"]) == 1`; register
  again with a new description and assert the schema was updated in place, and
  `TOOL_HANDLERS["t"]` is the latest handler. Also: call `register_search_tools()`
  three times, assert exactly 4 search-tool schemas total. (Reset the module globals in
  setup — they're process-level.)
- **R4b (multi-turn growth):** in test_rpjot.py — drive 50 iterations of the play.py
  append pattern + `compact_history` with stubbed `_condense_context` (returns fixed
  1k-tok string); assert bounded history per R1 acceptance. This is the test that would
  have caught the current degradation and will catch any future "forgot to compact"
  regression.
- **R4c (guard):** the R2 and R3 acceptance tests above, added to `TestGuardPayload`.
- **R4d (optional):** `classify_input` sigil unit tests (`"`,`*`,`@`,`^`, plain text) —
  cheap insurance for the prompt-facing input contract.

### R5 — REPL observability for prompt sizes

**Problem (§6):** the growing number (history) is invisible at the REPL; `/prompt` shows
no quantities.

**Change:** add a token panel to `/prompt` (play.py:365-385) — and optionally a
`/tokens` alias — printing:

```
[TOKEN BUDGET]                       cap = 28000 (MODEL 30000 − RESERVE 2000)
step2 history : 9,412 tok (34%)  [n=41 msgs, digest present: yes]
step3 history : 9,020 tok (32%)
schema ovh    : step1 1,626 · step2(compact) 2,310 · step3(bare) 901
last payload  : 10,275 tok (36.7%)          (engine._last_payload_toks)
est. headroom : ~19 turns until 85% at ~640 tok/turn (trailing-5 avg)
```

All inputs already exist: `_tok`, `_cached_*_schema_toks`, `_last_payload_toks`;
per-turn growth = trailing average of appended pair sizes (track a small deque in
`game_loop`). Optionally append the same block to `scene_debug_report()` (rpjot.py:4160)
so `/stats` shows conversation + note budgets side by side. **Acceptance:** manual —
`/prompt` after a few turns shows non-zero history tok and a sane turns-until-85%
estimate.

### R6 (low priority) — hygiene
- Cap `SessionState._query_cache` (simple max-entries eviction, rpjot.py:1135-1149).
- Segment debug.log per session (timestamped filename beside the session `.jot`), so a
  runaway investigation doesn't grep a 117k-line shared file.
- Guard log message in the 90–99% band says "trimming tool results" even when nothing
  sheds (§4.2 step 3) — after R1 this band should be rare, but make the message reflect
  actual tokens shed.

---

## 8. Verification procedure (post-sprint)

1. `python -m pytest test_catjot.py test_rpjot.py test_context.py` — existing suites
   stay green (LLM calls are mocked per conftest.py patterns); new R4 tests pass.
2. Synthetic soak: R4b at 200 turns — history token curve is sawtooth-bounded; zero
   guard lines at ≥85%.
3. Live smoke: `python play.py` against the configured endpoint
   (`openai_api_url`/`openai_api_key`/`openai_api_model`, catjot.py:45-89), play
   10–15 turns; confirm in debug.log that all `[CTX] guard:` lines stay in the DEBUG
   tier, `[HIST] compacted` appears once history crosses the soft limit, and `/prompt`
   shows the token panel; confirm narrative still references early-session events after
   a compaction (digest quality check).
