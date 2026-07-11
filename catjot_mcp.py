#!/usr/bin/env python3
__author__ = "William Dizon"
__license__ = "MIT"
__version__ = "0.0.1"
__maintainer__ = "William Dizon"
__email__ = "wdchromium@gmail.com"
__status__ = "Development"

"""
catjot_mcp — a Model Context Protocol server over the catjot note store
=======================================================================

catjot itself is an LLM *host*: it drives an OpenAI-compatible endpoint and
hands *it* tools so a model can rummage through your notes (`jot llm`).  This
module flips the direction.  It exposes catjot's directory-aware notes as an
MCP *server*, so any MCP host — Claude Code, Claude Desktop, an IDE extension —
can search, list, read, and (opt-in) create notes as first-class tools.

Why it is thin
──────────────
catjot already ships both halves of an MCP tool server without calling them
that: a tool registry in OpenAI function-call format (``Note``-searching tools
registered via ``catjot.register_tool`` into ``catjot.TOOL_SCHEMAS``) and a
defensive dispatcher (``catjot.dispatch_tool_call``) that converts every
failure mode into an error string instead of raising.  MCP's ``tools/list`` and
``tools/call`` map onto these almost 1:1.  All this file adds is:

  * a 5-line schema reshape (OpenAI ``{function:{...}}`` -> MCP ``inputSchema``),
  * note-oriented tool handlers that return *hydrated* notes, not bare IDs,
  * a newline-delimited JSON-RPC 2.0 loop over stdin/stdout.

Transport is pure stdlib (no ``mcp`` SDK, no new dependency) — matching the
project's zero-dependency, stdlib+requests ethos.  Note that ``import catjot``
transitively pulls in ``requests`` (catjot needs it for the LLM endpoint) even
though this server never touches the network.

Run it
──────
    jot mcp                        # via the CLI shim, or:
    python catjot_mcp.py           # read-only: search / list / get
    python catjot_mcp.py --allow-writes   # also expose create_note

The note file honoured is ``$CATJOT_FILE`` (falling back to ``~/.catjot``), or
``--notefile PATH``.  Diagnostics go to stderr; stdout carries only protocol.

Register with a host (Claude Code):
    claude mcp add catjot -- python /path/to/catjot_mcp.py
"""

import os
import sys
import json

import catjot
from catjot import Note, SearchType, register_tool, dispatch_tool_call, TOOL_SCHEMAS

# MCP protocol version we implement.  We echo the client's requested version
# only when it matches; otherwise we answer with this one (see _handle_initialize).
PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC 2.0 error codes we emit at the transport layer.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def log(*parts):
    """Emit a diagnostic line to stderr.

    stdout is reserved exclusively for JSON-RPC frames, so every human-facing
    message this server produces must go here.
    """
    print("[catjot-mcp]", *parts, file=sys.stderr, flush=True)


# ── notefile binding ──────────────────────────────────────────────────────────
#
# Note.NOTEFILE is fixed at import time to "$HOME/.catjot" (catjot.py:282).  The
# CATJOT_FILE override lives only as a *local* in catjot.main() and is never
# written back to the class attribute — and every helper we reuse
# (make_field_search_handler, fetch_notes_by_ids) reads the class attribute.  So
# an imported server would silently serve ~/.catjot regardless of CATJOT_FILE.
# We bind it explicitly here, mirroring what the CLI does for itself.


def resolve_notefile(explicit=None):
    """Return the note-file path this server should serve.

    Precedence: an explicit ``--notefile`` argument, then ``$CATJOT_FILE`` (when
    set and non-empty), then catjot's built-in ``~/.catjot`` default.
    """
    if explicit:
        return explicit
    env = os.environ.get("CATJOT_FILE")
    if env:  # set-but-empty is treated as "unset", matching catjot.main()
        return env
    return Note.NOTEFILE


def bind_notefile(path):
    """Point catjot at *path* and guarantee the file exists.

    Two hazards this closes (both verified in catjot.py):

    * Every reused handler reads ``Note.NOTEFILE``; without this assignment the
      server serves the wrong file.
    * ``NoteContext.__enter__`` prints an ASCII cat to *stdout* and calls
      ``sys.exit(1)`` when the file is missing (catjot.py:1155-1162).  That
      ``SystemExit`` is *not* caught by ``dispatch_tool_call``'s ``except
      Exception`` (catjot.py:1630), so a single read on a missing file would
      corrupt the protocol stream and kill the server.  Touch-creating the file
      here means the first-run branch never triggers.
    """
    Note.NOTEFILE = path
    open(path, "a").close()


# ── note tools (registered into catjot's shared registry) ─────────────────────
#
# These return hydrated note dicts, unlike the internal search_notes tool used
# by `jot llm`, which returns only a list of Note.now IDs.  We register under
# distinct names so that even if catjot.register_search_tools() were ever run in
# this process (it is not — the server never calls run_tool_loop) it could not
# clobber these by name (register_tool replaces by name, catjot.py:1566).

_FIELD_SEARCH_TYPES = {
    "tag": SearchType.TAG,
    "context": SearchType.CONTEXT_I,
    "message": SearchType.MESSAGE_I,
    "directory": SearchType.DIRECTORY,
}


def _hydrate(note):
    """Project a Note into the flat dict shape MCP callers consume."""
    return {
        "now": note.now,
        "tag": note.tag,
        "context": note.context,
        "directory": note.pwd,
        "message": note.message,
    }


def _read_notes(criteria, logic="and"):
    """Return hydrated notes matching *criteria*, tolerating a missing file.

    Reads via ``Note.match`` directly (not ``NoteContext``) so a
    ``FileNotFoundError`` surfaces as an ordinary exception the caller can turn
    into an error string, rather than ``NoteContext``'s stdout-printing
    ``sys.exit``.  ``bind_notefile`` already touch-creates the file, so this is
    belt-and-suspenders.
    """
    return [_hydrate(n) for n in Note.match(Note.NOTEFILE, criteria, logic=logic)]


def _handle_mcp_search_notes(field, query):
    """Search one note field and return the full matching notes as JSON.

    OR-combines whitespace-split terms within the field, de-duplicating by
    timestamp while preserving on-disk order.
    """
    st = _FIELD_SEARCH_TYPES.get(field)
    if st is None:
        return json.dumps(
            {
                "error": f"unknown field: {field}",
                "hint": "field must be one of: " + ", ".join(_FIELD_SEARCH_TYPES),
            }
        )
    seen = {}
    for word in query.split():
        for note in Note.match(Note.NOTEFILE, [(st, word)], logic="or"):
            seen.setdefault(note.now, note)
    return json.dumps([_hydrate(n) for n in seen.values()])


def _handle_mcp_list_notes(directory, tree=False):
    """Return every note written from *directory* (or its subtree when tree)."""
    st = SearchType.TREE if tree else SearchType.DIRECTORY
    return json.dumps(_read_notes([(st, directory)]))


def _handle_mcp_get_note(timestamp):
    """Return the single note whose ``now`` timestamp equals *timestamp*."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return json.dumps(
            {"error": f"timestamp must be an integer, got: {timestamp!r}"}
        )
    matches = _read_notes([(SearchType.TIMESTAMP, ts)])
    if not matches:
        return json.dumps({"error": f"no note with timestamp {ts}"})
    return json.dumps(matches[0])


def _handle_mcp_create_note(message, tag="", context="", directory=None):
    """Create a note and append it to the store; return the created note.

    Best-effort concurrency: ``Note.append`` is append-safe against other
    appenders, but a create racing a CLI ``pop``/``scoop`` (which rewrite the
    file) can be lost, and note identity is one-second-granular so two creates
    in the same second share a timestamp.  Acceptable under the read-only
    default; documented so callers aren't surprised.
    """
    pwd = directory or os.getcwd()
    note = Note.jot(message, tag=tag, context=context, pwd=pwd)
    Note.append(Note.NOTEFILE, note)
    return json.dumps(_hydrate(note))


def register_note_tools(allow_writes=False):
    """Register the MCP note tools into catjot's shared registry.

    Read tools are always registered; ``create_note`` only when *allow_writes*
    is set — a read-only default is the safe posture for a surface an external
    model drives.
    """
    register_tool(
        name="search_notes",
        description=(
            "Search catjot notes by one field and return the full matching "
            "notes. A note has four searchable fields — 'tag' (space-separated "
            "labels), 'context' (the command or summary that produced the "
            "note), 'message' (the free-form body), and 'directory' (the path "
            "it was written from). Whitespace-separated terms are OR-combined."
        ),
        parameters={
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "enum": list(_FIELD_SEARCH_TYPES),
                    "description": "Which note field to search.",
                },
                "query": {
                    "type": "string",
                    "description": "Space-separated search terms.",
                },
            },
            "required": ["field", "query"],
        },
        handler=_handle_mcp_search_notes,
    )
    register_tool(
        name="list_notes",
        description=(
            "List catjot notes written from a directory. Set tree=true to "
            "include notes from every subdirectory beneath it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Absolute path the notes were written from.",
                },
                "tree": {
                    "type": "boolean",
                    "description": "Include the whole subtree, not just this exact directory.",
                },
            },
            "required": ["directory"],
        },
        handler=_handle_mcp_list_notes,
    )
    register_tool(
        name="get_note",
        description="Fetch a single catjot note by its integer timestamp (the note's 'now' id).",
        parameters={
            "type": "object",
            "properties": {
                "timestamp": {
                    "type": "integer",
                    "description": "The note's Unix-epoch 'now' id.",
                },
            },
            "required": ["timestamp"],
        },
        handler=_handle_mcp_get_note,
    )
    if allow_writes:
        register_tool(
            name="create_note",
            description=(
                "Create a new catjot note and append it to the store. "
                "Best-effort under concurrency: a create racing a CLI pop/scoop "
                "may be lost, and two creates in the same second share a "
                "timestamp id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The note body (required, non-empty).",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Optional space-separated labels.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional context annotation.",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Absolute path to stamp the note with; defaults to the server's cwd.",
                    },
                },
                "required": ["message"],
            },
            handler=_handle_mcp_create_note,
        )


# ── MCP <-> catjot schema adapter ─────────────────────────────────────────────


def _openai_to_mcp(schema):
    """Reshape one OpenAI function schema into an MCP tool descriptor.

    catjot's registry stores ``{"type":"function","function":{name,description,
    parameters}}`` (catjot.py:1558); MCP wants ``{name,description,inputSchema}``.
    """
    fn = schema["function"]
    return {
        "name": fn["name"],
        "description": fn["description"],
        "inputSchema": fn["parameters"],
    }


def _is_error_result(text):
    """True when a tool result is a top-level JSON object carrying an 'error' key.

    catjot's error strings (dispatch_tool_call, the field-search handlers) are
    such objects; hydrated results are JSON *lists*, so this never false-positives.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, dict) and "error" in parsed


# ── JSON-RPC 2.0 framing ──────────────────────────────────────────────────────


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _write(obj):
    """Serialise one JSON-RPC frame to stdout as a single newline-terminated line.

    MCP stdio framing forbids embedded newlines, so we use compact separators.
    """
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


# ── method handlers ───────────────────────────────────────────────────────────


def _handle_initialize(msg_id, params):
    requested = (params or {}).get("protocolVersion")
    version = requested if requested == PROTOCOL_VERSION else PROTOCOL_VERSION
    return _result(
        msg_id,
        {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "catjot", "version": __version__},
        },
    )


def _handle_tools_list(msg_id, params):
    return _result(msg_id, {"tools": [_openai_to_mcp(s) for s in TOOL_SCHEMAS]})


def _handle_tools_call(msg_id, params):
    params = params or {}
    name = params.get("name")
    if not name:
        return _error(msg_id, INVALID_PARAMS, "tools/call requires a 'name'")
    # 'arguments' is optional per spec; dispatch_tool_call tolerates {} but a
    # bare params["arguments"] would KeyError *outside* its defenses.
    arguments = params.get("arguments") or {}
    # dispatch_tool_call's `except Exception` cannot catch SystemExit; our
    # read helpers avoid NoteContext, but guard here as a final backstop so a
    # rogue exit can never take the server down mid-loop.
    try:
        text = dispatch_tool_call(name, arguments)
    except SystemExit as exc:
        text = json.dumps({"error": f"tool {name} attempted to exit: {exc}"})
    return _result(
        msg_id,
        {
            "content": [{"type": "text", "text": text}],
            "isError": _is_error_result(text),
        },
    )


_METHODS = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
    "ping": lambda msg_id, params: _result(msg_id, {}),
}

# Notifications carry no id and expect no reply; we simply absorb the ones we
# know about (and ignore any other notification, per JSON-RPC).
_NOTIFICATIONS = {"notifications/initialized", "notifications/cancelled"}


def handle_message(msg):
    """Route one parsed JSON-RPC message; return a response frame or None.

    A message is a *notification* iff it has no ``id`` key — tested by presence,
    not truthiness, because ``id: 0`` is a valid request id.  Notifications
    never get a reply.
    """
    is_request = "id" in msg
    msg_id = msg.get("id")
    method = msg.get("method")

    if not is_request:
        if method not in _NOTIFICATIONS:
            log("ignoring unknown notification:", method)
        return None

    handler = _METHODS.get(method)
    if handler is None:
        return _error(msg_id, METHOD_NOT_FOUND, f"unknown method: {method}")
    try:
        return handler(msg_id, msg.get("params"))
    except Exception as exc:  # never let a handler bug break the loop
        log("handler error:", type(exc).__name__, exc)
        return _error(msg_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")


def serve(notefile=None, allow_writes=False, stdin=None):
    """Run the stdio JSON-RPC loop until stdin closes.

    *stdin* defaults to the process stream but is injectable for tests; output
    goes to ``sys.stdout`` (tests capture it with ``contextlib.redirect_stdout``
    or exercise ``handle_message`` directly).  Loops one line-delimited message
    at a time.
    """
    infile = stdin or sys.stdin
    bind_notefile(resolve_notefile(notefile))
    register_note_tools(allow_writes=allow_writes)
    log(
        "serving", Note.NOTEFILE,
        "(writes enabled)" if allow_writes else "(read-only)",
    )

    for line in infile:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _write(_error(None, PARSE_ERROR, "invalid JSON"))
            continue
        if not isinstance(msg, dict):
            _write(_error(None, INVALID_REQUEST, "message must be a JSON object"))
            continue
        response = handle_message(msg)
        if response is not None:
            _write(response)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    allow_writes = os.environ.get("CATJOT_MCP_WRITES") == "1"
    notefile = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--allow-writes":
            allow_writes = True
        elif arg == "--notefile":
            i += 1
            notefile = argv[i] if i < len(argv) else None
        elif arg.startswith("--notefile="):
            notefile = arg.split("=", 1)[1]
        else:
            log("ignoring unknown argument:", arg)
        i += 1
    serve(notefile=notefile, allow_writes=allow_writes)


if __name__ == "__main__":
    main()
