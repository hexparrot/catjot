# EXEC_DEBUG — `/debug` player-concern capture with mechanism-activity forensics

Design note for a `/debug <description>` command that lets the player pin a felt-but-
unnamed misbehavior to the evidence that explains it, *at the moment they notice it*,
and preserve that evidence past the log's lifetime. The engine already emits ~20
structured telemetry prefixes to a per-session `debug_{stamp}.log`
(`configure_logging`, rpjot.py:111) and retains rich per-turn state; what's missing is
a way to bind a concern to that evidence and keep it for later study.

## 1. Problem

The player experiences failures they can describe but not diagnose:

- **"why don't you remember X?"** — a canon detail vanished from a reply.
- **"why did the location change?"** — the scene teleported, or refused to move.
- **crossed wires** — an essential detail attributed to the wrong character.
- **tense-as-speech** — a typed action narrated as a spoken line.
- **sudden prose rewriting** — voice/style shifts with no in-story cause.

The evidence exists but is **scattered and perishable**:

- Telemetry is spread across ~20 prefixes — `[CTX]`, `[COMMIT-LOC]`, `[REMARK]`,
  `[LOCDRIFT]`, `[CAST]`, `[ENTROPY]`, `[STEP2]`, `[TOOLS]`, `[SEED]`, `[DISPATCH]` … —
  interleaved across every turn in `debug_{stamp}.log`.
- Per-turn state that names the fault directly (`_cast_warnings`, `_loc_warnings`,
  `_turn_stationary`, `_seed_status`, rpjot.py:1447-1472) lives only in memory and is
  reset each turn.
- The log **rotates per session** (`configure_logging` re-points `_h_file`,
  rpjot.py:118-128) and is overwritten/lost before it can be studied.
- Nothing lets the player say "*this reply was wrong*" at the instant it happens, when
  the relevant state is still live.

**Goal.** A non-LLM command that snapshots the concern + the live state + a parsed
digest of recent mechanism activity into a **self-contained** report, and appends a
machine-readable row to an index so the reports can be **aggregated and mined** to find
which mechanisms correlate with which complaints.

**Out of scope:** fixing any mechanism (this is diagnostics only); LLM analysis of the
concern (every step is deterministic); changing what the engine logs.

## 2. Decisions

- **Self-contained reports.** Parse the log *now* and embed a recent-activity digest +
  a full state snapshot into the `.md`, so it survives log rotation/deletion. Also emit
  ready-to-run grep commands for deeper dives.
- **~3–5 turn scan window.** A cause often fires a turn or two before the symptom
  surfaces (a condense last turn → a rewrite this turn), so scan the last ~5 turn
  blocks, not just the current one.
- **Append-only JSONL index.** Each `/debug` appends one structured row to
  `debug/index.jsonl`; markdown stays the human view. This is the aggregation surface
  for later pattern-mining.
- **Keyword-routed categories, never exclusive.** Route the description to a category by
  keyword to highlight the most relevant readout, but always render the full readout so
  nothing is hidden; unmatched → `uncategorized` (show all).
- **Log path from the handler, not reconstruction.** Resolve the active log via
  `_h_file.baseFilename` — the handler is the source of truth across session rotation.

## 3. Design

All engine code in `rpjot.py`; dispatch + file IO in `play.py`.

### 3.1 `RpjotEngine.build_debug_report(concern, step2_messages, step3_messages)`
Returns `(report_text: str, index_row: dict)`. Non-LLM, deterministic (same contract
family as `scene_debug_report`, rpjot.py:6546). Steps 3.2–3.6.

### 3.2 State snapshot
From attributes already retained after `run_turn`: `_turn_count`,
`session.location` / `people_present` / `current_scene`, `_seed_status`,
`_turn_stationary`, `_cast_warnings`, `_loc_warnings`, `_last_payload_toks`,
`npc_tracker.all()` (per-NPC `location_last_seen` / `turn_last_active`). `has_digest`
is computed from the messages by scanning for a user message beginning `"STORY SO
FAR:"` (the `_has_digest` idiom, play.py).

### 3.3 Log-path resolution
`_h_file.baseFilename` on the `rpjot_engine` logger (rpjot.py:97/122), falling back to
`debug.log`. Never reconstruct from the session stamp — rotation makes the handler
authoritative.

### 3.4 `_scan_recent_activity(log_path, turns_back=4)` → recent mechanism activity
Read the file; split into per-turn blocks on `[TURN k] run_turn: START` markers
(rpjot.py:2058); keep the last `turns_back + 1` blocks; within them collect lines whose
prefix is in the interesting set — `[CTX]` (condense/hard-truncate), `[COMMIT-LOC]`,
`[REMARK]`, `[LOCDRIFT]`, `[CAST]`, `[ENTROPY]`, `[STEP2] tool`, `[TOOLS]`,
`[DISPATCH]`, `[SEED]`. Block-delimiting (not per-line `turn=` matching) is deliberate:
most lines carry no turn number, so block boundaries are the only reliable turn key.
`condensed_this_turn` is derived from whether the current block contains a
`[CTX] _condense_context` line.

### 3.5 Category → signal taxonomy
Lowercased substring match on the concern; the matched category's readout is rendered
first, the rest below.

| Category | Trigger keywords | Evidence (log prefixes + engine state) |
|---|---|---|
| **memory** | remember, forgot, recall, lost, missing | `[CTX] _condense_context` / `HARD-TRUNCATE` (a note dropped to fit budget), `has_digest` ("STORY SO FAR:" compaction), `[HIST] compacted`, `_turn_refs` (was the subject cited?). If a subject noun parses out, present-vs-fed note count — the `/construct` census from [[slash-improvement-doc]] is the natural enrichment. |
| **location** | location, room, move, where, teleport, went | `[COMMIT-LOC] …(source=)`, `[REMARK] action=…`, `[LOCDRIFT]`, `_turn_stationary`, `_loc_warnings`. |
| **misattribution** | wrong person, who, attributed, mixed up, confused, said that | `[CAST] mentioned-but-absent`, `_cast_warnings`, `[STEP2] tool …` (which record tool + witnesses), `[DISPATCH]`, npc_tracker `location_last_seen` vs `session.location`, event `exp:` tags vs `people_present`. |
| **classification** | tense, spoke, spoken, said, action, meant to | `classify_input` result in `step2_messages[-2]` (esp. the no-sigil default `[MC — likely spoken aloud…]`, play.py:138), `_turn_stationary` verdict, `[STEP2] stationary nudge → injected`. |
| **prose/rewrite** | rewrote, rewrite, changed style, suddenly different | context-shift correlation: `[ENTROPY] refresh`, `[CTX] _condense_context`, `_seed_status`, `has_digest`. |

### 3.6 Report schema (stable, for aggregation)
YAML frontmatter + fixed sections so a future aggregator scans every report uniformly:

```
---
session: session_20260705_141230.jot
turn: 42
timestamp: 2026-07-05 14:31:07
concern: "why did the location change?"
category: location
location: manor/library
people: [evie, bartholomew]
flags:
  seed_status: hit-unchanged
  turn_stationary: false
  has_digest: true
  condensed_this_turn: false
  cast_warnings: []
  loc_warnings: ["filed manor != session manor/library"]
log_file: debug_20260705_141230.log
---

## Player concern
<verbatim>

## Auto-category: location  (matched keyword: "location")

## State snapshot (turn 42)
location / people / scene / seed_status / stationary / last_payload_toks /
has_digest / npc_tracker last-seen per NPC

## Recent mechanism activity (turns 38–42, parsed from log)
- turn 41  [CTX] _condense_context: input=6120 tok → target ~1000
- turn 42  [COMMIT-LOC] session.location → manor/library (source=record_event)
- turn 42  [REMARK] proposed='library' canonical=manor/library action=committed

## Failure-mode readout (category: location)
<evidence lines + engine flags for the matched category>

## Log links
Path: ./debug_20260705_141230.log
  grep -nE '\[(COMMIT-LOC|REMARK|LOCDRIFT)\]' debug_20260705_141230.log
  grep -n 'turn=42' debug_20260705_141230.log

## Suggested next checks
<category-specific hints, e.g. compare _turn_stationary vs [COMMIT-LOC] source>
```

### 3.7 Dispatch + file writing (`play.py`)
- Add `"/debug"` to `_SLASH_PREFIX` (takes a description arg like `/yomi`/`/objects`).
- Handler (mirrors `/stats`, which already holds `step2_messages`/`step3_messages`):
  `os.makedirs("debug", exist_ok=True)`; `engine.build_debug_report(desc, …)`; write
  the `.md`; append the JSONL row (`json.dumps(row) + "\n"`, mode `"a"`); print the path.
  Bare `/debug` → usage line.
- Filename `debug/{stamp}_turn{N}_{slug}.md`; slug via the existing `[a-z0-9-]` idiom.
- List `/debug <description>` in `_HELP_TEXT` (play.py:90-98).
- `debug/` is a new output dir; gitignore it.

## 4. Invariants

- **V1 — non-LLM.** No section calls the model; the report is reproducible from state +
  log in the same conditions.
- **V2 — self-contained.** The recent-activity digest and state snapshot are embedded,
  not linked-only; deleting the log afterward does not blank the report.
- **V3 — read-only over the game.** `build_debug_report` writes no notes and mutates no
  session state; it only reads engine attributes and the log file. The only writes are
  the report `.md` and the appended index row.
- **V4 — append-only index.** `debug/index.jsonl` is only ever appended; each row is
  valid standalone JSON with a distinct `report_file`.
- **V5 — routing never hides.** A matched category highlights but never suppresses the
  other readouts; `uncategorized` shows all.
- **V6 — authoritative log path.** Resolved from the live handler, correct across
  per-session rotation.

## 5. Verification

1. **Offline build (no :5000).** Seed a session + a hand-written `debug_TEST.log` with a
   few `[TURN k]` blocks of known prefixes; assert category routing, that the digest
   contains exactly the `[COMMIT-LOC]`/`[REMARK]` lines from the last ~5 blocks, and
   that the snapshot matches engine attributes.
2. **Self-containment.** Delete the log after building; the report still shows the digest.
3. **Index aggregation.** Two calls → two valid rows in `debug/index.jsonl` with distinct
   `report_file`; a one-liner `json.loads` over every line parses clean.
4. **Category routing.** "remember the locket" → memory; "attributed to the wrong
   person" → misattribution; a vague concern → uncategorized with all readouts.
5. **Dispatch/help.** Bare `/debug` prints usage; unknown-command guard unaffected;
   `/debug …` never reaches the LLM.
6. **Regression.** Full suite green; focused tests for `_scan_recent_activity`
   block-splitting and keyword routing.

## 6. Status

Designed 2026-07-05 (doc only, in `dev_open/`). Not yet implemented. Reuses the existing
logging handler (`_h_file`, rpjot.py:97/122), `scene_debug_report`/`history_report`, and
the retained per-turn warning/verdict attributes. The `memory` category is the natural
consumer of the `/construct` census in [[slash-improvement-doc]] (SLASH_IMPROVEMENT);
graduates to `dev_closed/` once its machinery lands.
