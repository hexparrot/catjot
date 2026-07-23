# CATJOT_NOTEFILE — add `-f/--notefile PATH`, a CLI flag that forces the jotfile

## Context

Pointing catjot at a specific jotfile currently requires the `CATJOT_FILE` environment variable,
which has proven unreliable in practice — especially for MCP hosts and other wrappers, where the
variable must survive every layer between the user and the process. Worse, the env override only
half-works even when it does arrive: `main()` resolves it into a *local* `NOTEFILE`
(`catjot.py:2272-2280`) that the command branches use, but the LLM/tool layer and `ContextBundle`
read the class attribute `Note.NOTEFILE` (frozen at import time to `~/.catjot`, `catjot.py:282`)
directly — `ContextBundle.refresh` (`catjot.py:1049`), `run_tool_loop` searches (`catjot.py:1678`),
`_all_tags` (`catjot.py:1798`). So `jot llm` searches the default file even when `CATJOT_FILE`
points elsewhere.

`catjot_mcp.py` hit this exact problem and solved it with `bind_notefile()`, which rebinds
`Note.NOTEFILE` globally (`catjot_mcp.py:78-116`; its comment documents that the env override
"lives only as a local in catjot.main() and is never exported").

**Goal:** a `-f/--notefile PATH` flag that supersedes `CATJOT_FILE` and reliably reaches *every*
code path, by adopting the MCP wrapper's rebinding approach.

## Hard invariants

- **rpjot / library compatibility untouched.** rpjot imports only `Note`, `NoteContext`,
  `SearchType`, `ContextBundle`, `call_llm` and reads `catjot.LAST_USAGE`; it never calls `main()`.
  The flag lives entirely inside `main()`, so library use is structurally unaffected.
- **No behavior change without the flag.** The `CATJOT_FILE` env path keeps today's local-only
  semantics byte-for-byte. (Making env also rebind `Note.NOTEFILE` would silently change
  `jot llm` behavior for current env users — explicitly out of scope.)
- **All 129 existing tests stay green, unmodified.** No existing test drives `main()`, so none can
  collide with the flag.

## Design

### 1. New argparse flag (`catjot.py:2246-2268`, with the existing flags)

```python
parser.add_argument(
    "-f", "--notefile", type=str, default=None,
    help="use this jotfile for all reads/writes (supersedes CATJOT_FILE)",
)
```

- `-f` is free (existing short flags: `-a -t -p -m -w -c -d`). No long options exist yet;
  `--notefile` matches the flag `catjot_mcp.py` already accepts (`catjot_mcp.py:494-498`), so both
  entry points share one vocabulary. The parsed attribute is `args.notefile`.
- No path expansion (the shell handles `~`) — identical treatment to the env var value today.

### 2. Resolution block (`catjot.py:2272-2280`) gains the flag at top precedence

```python
NOTEFILE = Note.NOTEFILE
import sys

if args.notefile:
    # explicit flag supersedes CATJOT_FILE and reaches every code path:
    # rebind the class attribute (as catjot_mcp.bind_notefile does) so
    # ContextBundle / run_tool_loop / _all_tags honour it too
    Note.NOTEFILE = args.notefile
    NOTEFILE = args.notefile
    with open(NOTEFILE, "a") as file:
        pass
elif "CATJOT_FILE" in environ:
    # ...existing env block, unchanged...
```

- **Precedence: flag > `CATJOT_FILE` > `~/.catjot` default.**
- The flag path rebinds `Note.NOTEFILE` — this is the reliability fix that makes the flag govern
  the LLM/tool-layer and `ContextBundle` paths the env var never reached.
- Touch-create mirrors the env path so a fresh file works for both reads and writes.
- `jot -f path mcp` needs no extra work: the mcp branch already forwards the resolved local
  (`catjot_mcp.serve(notefile=NOTEFILE)`, `catjot.py:3043-3046`), and `serve → bind_notefile`
  rebinds identically.

### 3. Documentation

- `main()` docstring: extend the "Environment variable override" paragraph (`catjot.py:2186-2188`)
  with the flag, its precedence, and the note that the flag (unlike the env var) also governs the
  LLM/tool-layer paths.
- `README.md:224-228` (relocate-your-notefile section): show `jot -f /path/to/file.jot ...` /
  `jot --notefile ...`, state precedence over `CATJOT_FILE`, recommend the flag for MCP/wrapper
  scenarios.
- argparse epilog: no change needed (flag help suffices).

## Files touched

- **`catjot.py`** — argparse flag, resolution block, docstring paragraph.
- **`README.md`** — notefile relocation section.
- **`test_catjot.py`** — new `TestNotefileFlag` class (below).

## Tests — `TestNotefileFlag` (subprocess-driven)

Drive the CLI via `subprocess` (`sys.executable catjot.py`, controlled `cwd`/`env`, temp jotfiles).
Subprocess isolation matters: the flag rebinds the `Note.NOTEFILE` class attribute, and per-process
runs keep that mutation from leaking across tests.

1. `echo hi | jot -f X` writes to X; X is created; the default/env file is untouched.
2. `jot -f X h` reads back from X (stdout contains the note).
3. Precedence: with `CATJOT_FILE=Y` set *and* `-f X`, the note lands in X and Y stays empty.
4. Env-only behavior unchanged: `CATJOT_FILE=Y`, no flag → note lands in Y.
5. `--notefile=X` long form behaves identically to `-f X`.

## Verification ("largely does not impact other use")

1. **Full suite green:** `python -m pytest test_catjot.py test_context.py test_catjot_mcp.py` —
   129 existing tests unmodified and passing is the no-impact evidence for the API surface, plus
   the new flag tests.
2. **Leak check:** end-to-end smoke against a scratch jotfile — piped write, `h`, `l`, `m`, `pop`,
   and amend (`jot -f X -at tag`) all against `-f X`; byte-compare `~/.catjot` and a decoy
   `CATJOT_FILE` target before/after to prove zero writes escaped X.
3. **MCP handshake:** `jot -f X mcp` — start, confirm the stderr "serving" line names X, kill.
4. **Import surface:** `python -c "import catjot; import catjot_mcp"` still clean.
