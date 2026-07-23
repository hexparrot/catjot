# CJ_REDUCE — decompose `main()` into a command-handler registry

> **Status: implemented** on branch `cj_reduce` (decomposition in commit
> "Decompose main() into module-level cmd_* handlers", approved behavior fixes in
> "Error with exit 2 on bad CLI input"). Line references below describe the
> pre-refactor file.

## Context

`catjot.py` is a 3,547-line single-file app that is also a public library — `catjot_mcp.py`,
`rpjot.py`, the bake-off scripts, and all 129 tests do `from catjot import ...`. The single-file
form is a deliberate install convenience (`cp catjot.py → ~/.local/bin/jot`), so it must stay one
file, and the exported symbols (`Note`, `ContextBundle`, `NoteContext`, `SearchType`, `call_llm`,
`register_tool`, `dispatch_tool_call`, `TOOL_SCHEMAS`, `is_binary_string`, `NOTEFILE`, …) must stay
importable and behave identically.

The single worst readability/reusability problem is **`main()` — one ~1,395-line function**
(`catjot.py:2152-3545`). It bundles argparse setup, a hand-rolled `SHORTCUTS` alias table defined as
a *local* variable, three helper closures, and ~30 command bodies inlined into a deeply-nested
`if / elif` ladder (chat, convo, spaced-repetition scheduling, GraphQL, MCP launch, side-by-side
transcription, bulk-edit-in-`$EDITOR`, etc.). Because every command body lives inside `main()`'s
closure, none of it is importable or unit-testable, and reading any one command means scrolling past
all the others.

**Goal:** break `main()` into named, module-level command handlers dispatched through a registry
(`{action → handler}`):

```
main() 1,395 lines  ->  COMMANDS = {'head': cmd_head, 'last': cmd_last, ...}
                        each cmd_* is module-level + unit-testable
```

**This is a behavior-preserving refactor plus a small, enumerated set of approved fixes.**
Non-goals (explicitly out of scope this round): splitting the file, touching
`Note`/`ContextBundle`/the LLM subsystem internals, or changing CLI behavior beyond the items in
the "Approved behavior fixes" section. All other quirks are **preserved, not corrected** (see
checklist). rpjot compatibility was audited: rpjot imports only `Note`, `NoteContext`,
`SearchType`, `ContextBundle`, `call_llm` and reads `catjot.LAST_USAGE` — it never invokes the CLI,
so nothing in `main()`'s dispatch is protected by rpjot.

## Hard invariants

- **One file.** No new modules. Everything stays in `catjot.py`.
- **Stable public API.** No existing top-level symbol is renamed or removed. New `cmd_*` functions
  and the `COMMANDS` registry are *additive* exports.
- **Byte-identical CLI behavior** except for the enumerated "Approved behavior fixes", verified by
  the golden-diff harness.
- **All tests stay green** (`test_catjot.py`, `test_catjot_mcp.py`, `test_rpjot.py` — 129 as of the
  notefile-flag commit).

## Design

### 1. Hoist the three helper closures to module scope
`flatten` (`:2284`), `flatten_pipe` (`:2287`), and `printout` (`:2290`) are defined inside `main()`.
Move them to module-level functions. `printout` currently defaults `time_only=args.d` via closure;
change its signature to `printout(note_obj, message_only=False, time_only=False)` and have callers
pass `time_only=ctx.args.d`. They are pure/near-pure and become reusable by handlers and tests.

### 2. Promote `SHORTCUTS` to module scope; keep the `CATGPT_ROLE` env read lazy
`SHORTCUTS` (`:2380-2413`) is currently a *local* dict inside the final `else` branch, so
`catjot_mcp.py` and tests can't reference the canonical alias table. Move it to module level,
keeping its `# USER-EDITABLE AREA` banner comment so power users still know where to remap words.
Precompute an inverse map once:

```python
_ALIAS_TO_ACTION = {alias: action for action, aliases in SHORTCUTS.items() for alias in aliases}
def _canonical(verb):            # "h" -> "MOST_RECENTLY_WRITTEN_ALLTIME", unknown -> None
    return _ALIAS_TO_ACTION.get(verb)
```

**Do NOT move the `getenv("openai_api_sysrole", ...)` call to module scope** (an earlier draft
proposed this — the proposal itself was a footgun). A module-level read freezes the value at import
time, an observable change for library consumers and tests; today it is re-read on every `main()`
invocation. Instead: put a `CATGPT_ROLE_DEFAULT` string constant inside the module-level
`USER-EDITABLE AREA` (preserving the customization contract) and call
`getenv("openai_api_sysrole", CATGPT_ROLE_DEFAULT)` inside `cmd_chat` at run time. The separate
read with a `""` default at `:1862` in `run_tool_loop` is a different subsystem — leave it alone.

### 3. A small `Ctx` bundle for uniform handler signatures
Handlers need shared state currently held in `main()`'s locals: `args`, the resolved `notefile`,
and a fresh `params` dict seeded from the `-c/-t/-p` flags. Give every handler the uniform signature
`cmd_x(ctx)` so they can live in one registry. `Ctx` is a tiny class (or `SimpleNamespace`) holding:

```python
class Ctx:
    def __init__(self, args, notefile):
        self.args = args
        self.notefile = notefile
    def params(self):            # rebuilds the flag-seeded dict (was main():2300-2306)
        p = {}
        if self.args.c: p["context"] = flatten(self.args.additional_args)
        if self.args.t: p["tag"] = self.args.t
        if self.args.p: p["pwd"] = self.args.p
        return p
```

Handlers that mutate params (chat/convo/scoop add `tag`/`pwd`/`now`/`context`) call `ctx.params()`
to get their own fresh dict — identical to today, where `params` is a single mutable local.

### 4. Extract each command body into a module-level `cmd_*`
Mechanical move: each branch body becomes a function taking `ctx`, with its inline logic verbatim
(including inline imports, exit codes, and `return`/`exit()` calls — a `return` inside a handler ends
`main()` just as it does today; `exit()`/`sys.exit()` still terminate the process). Inventory:

| Handler            | Aliases (SHORTCUTS key)                    | Source lines | Arity guard |
|--------------------|--------------------------------------------|--------------|-------------|
| `cmd_amend_flags`  | `-a` + one of `-c/-t/-p`                   | 2307-2330    | flag path   |
| `cmd_flag_context` | `-c`                                       | 2331-2343    | flag path   |
| `cmd_flag_tag`     | `-t` (guarded, see approved fixes)         | 2344-2364    | flag path   |
| `cmd_flag_pwd`     | `-p`                                       | 2365-2378    | flag path   |
| `cmd_chat`         | CHAT (`chat/catgpt/c`)                     | 2427-2588    | any arity   |
| `cmd_convo`        | CONVO (`convo/continue/cat/sum/...`)       | 2589-2925    | any arity   |
| `cmd_default`      | (no verb)                                  | 2927-2951    | n == 0      |
| `cmd_last`         | MOST_RECENTLY_WRITTEN_HERE (`last/l`)      | 2928-2936; 3517-3545 | n == 1 or 2 |
| `cmd_head`         | MOST_RECENTLY_WRITTEN_ALLTIME (`head/h`)   | 2937-2946; 3490-3516 | n == 1 or 2 |
| `cmd_pop`          | DELETE_MOST_RECENT_PWD (`pop/p`)           | 2947-2957    | n == 1      |
| `cmd_home`         | HOMENOTES (`home`)                         | 2958-2976    | n == 1      |
| `cmd_dump`         | SHOW_ALL (`dump/d`)                        | 2977-2985    | n == 1      |
| `cmd_payload`      | MESSAGE_ONLY (`payload/pl`)                | 2986-2995; 3359-3383 | n == 1 or 2 |
| `cmd_zzz`          | SLEEPING_CAT (`zzz`)                       | 2996-3026    | n == 1      |
| `cmd_mcp`          | START_MCP_SERVER (`mcp`)                   | 3027-3046    | n == 1      |
| `cmd_scoop`        | BULK_MANAGE_NOTES (`scoop`)                | 3047-3113    | n == 1      |
| `cmd_stray`        | NOTES_REFERENCING_ABSENT_DIRS (`stray`)    | 3114-3127    | n == 1      |
| `cmd_graphql`      | GRAPHQL (`ql`)                            | 3128-3161    | n == 1      |
| `cmd_newsr`        | CREATE_SPACED_REPETITION (`newsr`)         | 3162-3169    | n == 1      |
| `cmd_sr`           | ITERATE_SPACED_REPETITIONS (`sr`)          | 3170-3277    | n == 1      |
| `cmd_llm`          | LLM (`llm`)                                | 3278-3300    | n == 1      |
| `cmd_match`        | MATCH_NOTE_NAIVE (`match/m`)               | 3306-3315    | n == 2      |
| `cmd_search`       | MATCH_NOTE_NAIVE_I (`search/s/mi`)         | 3316-3325    | n == 2      |
| `cmd_ts`           | MATCH_TIMESTAMP (`ts`)                     | 3326-3335    | n == 2      |
| `cmd_remove`       | REMOVE_BY_TIMESTAMP (`remove/r`)           | 3336-3348    | n == 2      |
| `cmd_show_tag`     | SHOW_TAG (`tag/t`)                         | 3349-3358    | n == 2      |
| `cmd_sbs`          | SIDE_BY_SIDE (`sbs/rewrite`)               | 3384-3489    | n == 2      |

`cmd_head` / `cmd_last` / `cmd_payload` today have **separate n==1 and n==2 code paths** (single
note vs N-notes / timestamp lookup). Fold each pair into one handler that branches on
`len(ctx.args.additional_args)` internally — this collapses the current duplication while producing
identical output.

### 5. Replace the ladder with a slim resolver + registry
Build the registry once at module level:

```python
COMMANDS = {
    "MOST_RECENTLY_WRITTEN_ALLTIME": cmd_head,
    "MOST_RECENTLY_WRITTEN_HERE":    cmd_last,
    "MATCH_NOTE_NAIVE":              cmd_match,
    ...                              # one entry per SHORTCUTS key above
}
```

`main()` shrinks to argparse setup + `CATJOT_FILE` resolution + this resolver, preserving the exact
current precedence:

```python
ctx = Ctx(args, notefile)
if args.a and (args.c or args.t or args.p):
    cmd_amend_flags(ctx)
elif args.c:
    cmd_flag_context(ctx)
elif args.t and not set(args.additional_args) & _TAG_FLAG_SKIP:   # derived set, see fixes
    cmd_flag_tag(ctx)
elif args.p:
    cmd_flag_pwd(ctx)
else:
    verb = args.additional_args[0] if args.additional_args else None
    if verb in SHORTCUTS["CHAT"]:
        cmd_chat(ctx)
    elif verb in SHORTCUTS["CONVO"]:
        cmd_convo(ctx)
    elif not args.additional_args:
        cmd_default(ctx)
    else:
        handler = COMMANDS.get(_canonical(verb))
        if handler:
            handler(ctx)          # handler errors (stderr + exit 2) at unsupported arity
        else:
            print(f"jot: unknown command '{verb}'", file=sys.stderr)
            sys.exit(2)
```

Each `cmd_*` keeps the arity *branching* from its original branch (e.g. `cmd_match` handles only
`len(additional_args) == 2`), but per the approved fixes below, an unsupported arity prints a
one-line usage hint to stderr and exits 2 instead of silently doing nothing. The `len==0` default
paths (pwd listing, piped note write) are untouched.

## Approved behavior fixes (user-approved: bad input errors with exit 2)

These are the only deliberate CLI behavior changes in this round; everything else stays
byte-identical. rpjot cannot observe any of them (see Context).

1. **`_TAG_FLAG_SKIP` is derived, not hardcoded.** The current 7-item literal (`:2344-2346`) has
   drifted from the alias table: it has `chat` but not `catgpt`/`c`, `scoop` but not `cherry-pick`,
   and none of `cat`/`catenate`/`talk`. So `jot -t x chat` starts a chat but `jot -t x c` does a
   tag search. Define
   `_TAG_FLAG_SKIP = set(SHORTCUTS["CONVO"]) | set(SHORTCUTS["CHAT"]) | set(SHORTCUTS["BULK_MANAGE_NOTES"])`
   — the in-code comments (`:2347-2352`) show this was always the intent. Swallowing *other* verbs
   (`jot -t x pop` → tag search) is the documented `-t` mode semantics and stays.
2. **Unknown verb / unsupported arity → stderr + `sys.exit(2)`.** Today `jot frobnicate` and
   `jot m multi word term` silently do nothing, and `echo data | jot typo` silently discards the
   pipe (verified: no fall-through to a note write exists — the only stdin-write path is the
   no-verb `len==0` branch).
3. **Guard the three unguarded `int()` conversions like `remove` already does.** `jot pl abc`
   (`:3363`), `jot ts abc` (`:3328`), and `jot sbs x abc` (`:3413`) crash with raw `ValueError`
   tracebacks; `remove` catches it and exits 2 (`:3343`). Mirror that: catch `ValueError`, short
   stderr message, `sys.exit(2)`. Preserve `pl <ts>`'s falsy-timestamp fallback to pwd behavior
   (`:3368`) — only non-numeric input changes.

## Files touched
- **`catjot.py`** — the only production file changed. `main()` is gutted to the resolver; ~26 new
  `cmd_*` module-level functions; `flatten`/`flatten_pipe`/`printout`/`SHORTCUTS`/`CATGPT_ROLE`/
  `Ctx`/`COMMANDS` promoted to module scope.
- **(optional) `test_catjot.py`** — add a `TestCommandDispatch` class asserting `_canonical()`
  resolves each alias and that `COMMANDS` covers every non-chat/convo `SHORTCUTS` key. Low cost,
  locks in the new seam. No changes to existing tests are required or expected.

## Behavior-preservation checklist (replicate these verbatim)
- **Strict arity equality.** The ladder fires only at `len == 0/1/2` (`== 2`, not `>= 2`). Keep the
  `== 2` branching; do not broaden it. (What changes, per the approved fixes, is only what happens
  on the *failing* side: stderr + exit 2 instead of silence.)
- **Exit codes on existing error paths:** amend `exit(0)` → `sys.exit(0)`; pop `sys.exit(1/2)`;
  remove `exit(2)` → `sys.exit(2)`; sbs `exit(2/3)` → `sys.exit(2/3)`; chat binary/oversize
  `sys.exit(1)`. Same numbers everywhere; see cleanup item on `exit()` unification below.
- **`return` inside chat/convo/llm/sr loops** — becomes `return` from the handler, which ends `main()`
  identically. Verify no handler accidentally swallows a `return` meant to end the program.
- **`pl <ts>` falsy-timestamp fallback** (`:3368`) — the only place `pl <ts>` degrades to pwd
  behavior; keep it when folding the two `pl` arity paths into one handler.
- **`USER-EDITABLE AREA` banner** — keep it around the module-level `SHORTCUTS`/`CATGPT_ROLE_DEFAULT`
  so the customization contract is preserved.
- **Inline imports** (`from datetime import ...`, `import os`, `deque`, etc.) — move them with their
  bodies rather than hoisting, to avoid import-order/timing surprises. **One exception (convo):**
  `:2599` binds `from time import time` (used as `int(time())` at `:2601`) and `:2867` later rebinds
  `import time` (used as `time.sleep()` at `:2880`) *in the same scope* — it only works by execution
  order. Normalize `cmd_convo` to a single `import time` + `time.time()`/`time.sleep()`; copying the
  shadowing verbatim would plant a landmine.

## Mandatory cleanups while extracting (zero behavior change)
- **Unify `exit()` → `sys.exit()`.** amend/convo/remove/sbs use the builtin `exit()` (site.Quitter —
  `NameError` under `python -S`); pop/chat use `sys.exit()`. Same `SystemExit`, same codes. Unit
  tests calling `cmd_*` directly must wrap in `pytest.raises(SystemExit)`.
- **Delete the dead chat-`home` block** (`:2517-2518` — an earlier draft cited `:2549`, which is
  wrong). The chat branch only fires when `additional_args[0] in ["chat","catgpt","c"]`, so the
  `additional_args[0] in ["home"]` test is unreachable. Delete rather than copy.
- **Drop the redundant `from os import getenv`** (`:2416`) — already imported at module level (`:42`).
- **`ctx.params()` one-call rule.** `params` is a single mutable dict today; convo mutates it before
  its while-loop and reads it inside (`:2668,2782,2841`), as do chat (`:2513`) and scoop
  (`:3109-3111`). `ctx.params()` returns a *fresh* dict per call, so every handler binds
  `params = ctx.params()` exactly once at the top and mutates that local.

## Verification
1. **Golden-diff harness** (scratch script, `CATJOT_FILE` pointed at a copy of `tests/example.jot`
   under `local/scratch/`): run a command matrix and diff stdout+stderr+exit-code+notefile-bytes
   before vs. after the refactor. Matrix (non-interactive, no-network subset):
   `""` (show pwd), `h`, `h 3`, `h ~2`, `l`, `l 2`, `l ~2`, `d`/`dump`, `home`, `pl`, `pl <ts>`,
   `m <term>`, `s <TERM>`, `ts <ts>`, `t <tag>`, `r <ts>`, `stray`, `newsr` (piped), plus piped
   writes (`echo hi | jot`, `echo hi | jot -c foo`, `echo hi | jot -t bar`, `echo hi | jot home`),
   and amend paths (`jot -at newtag`, `echo x | jot -ac`). Expected result: empty diff on every case
   **except the approved-fix carve-outs**, which must show exactly their new outcome (one-line
   stderr diagnostic + exit 2) and nothing else:
   `jot frobnicate`, `echo hi | jot frobnicate`, `jot m multi word term`, `jot pop extra`,
   `jot pl abc`, `jot ts abc`. Skip-set carve-outs (`jot -t x c` → chat, `echo hi | jot -t x cat`
   → convo) route to interactive/network commands, so verify their *routing* manually rather than
   diffing output. Interactive/network commands (`scoop`, `sbs`, `zzz`, `chat`, `convo`, `llm`,
   `mcp`) are excluded from the automated diff; smoke-check `sbs`/`zzz` manually and confirm
   `jot mcp` still imports and serves.
2. **Full suite:** `python -m pytest test_catjot.py test_catjot_mcp.py test_rpjot.py` — all green
   (129 as of the notefile-flag commit, plus new dispatch/behavior tests).
3. **Import surface:** `python -c "import catjot; catjot.main"` and
   `python -c "from catjot import Note, SearchType, register_tool, dispatch_tool_call, TOOL_SCHEMAS"`
   plus `python -c "import catjot_mcp"` — confirm the MCP wrapper and the new `SHORTCUTS`/`COMMANDS`
   exports coexist without breaking existing imports.
