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
import re
import sys
import json
import time

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


def _search_one(st, query, term_cache):
    """Notes matching one query string, OR-combining its whitespace-split terms.

    ``term_cache`` memoizes term -> matched notes for the life of a single call,
    so a batch whose queries share terms (subjects usually do — "alice" recurs
    across a roster sweep) reads the notefile once per DISTINCT term rather than
    once per query.
    """
    seen = {}
    for word in query.split():
        if word not in term_cache:
            term_cache[word] = list(Note.match(Note.NOTEFILE, [(st, word)], logic="or"))
        for note in term_cache[word]:
            seen.setdefault(note.now, note)
    return [_hydrate(n) for n in seen.values()]


def _handle_mcp_search_notes(field, query=None, queries=None):
    """Search one note field and return the full matching notes as JSON.

    OR-combines whitespace-split terms within the field, de-duplicating by
    timestamp while preserving on-disk order.

    ``queries`` is the batch form: several independent searches of the SAME
    field in one call, answered as ``{"batch": [...]}`` with each item tagged
    ``_batch_key``. The consult-first protocol checks the notebook per subject
    before an investigation, which is inherently N-wide; without this it costs N
    round-trips and N notefile reads.
    """
    st = _FIELD_SEARCH_TYPES.get(field)
    if st is None:
        return json.dumps(
            {
                "error": f"unknown field: {field}",
                "hint": "field must be one of: " + ", ".join(_FIELD_SEARCH_TYPES),
            }
        )
    term_cache = {}
    if queries is not None:
        if isinstance(queries, str):
            queries = [queries]
        wanted, seen_q = [], set()
        for q in queries:
            if q is None or q in seen_q:
                continue
            seen_q.add(q)
            wanted.append(q)
        return json.dumps(
            {
                "batch": [
                    {"_batch_key": q, "notes": _search_one(st, q, term_cache)}
                    for q in wanted
                ],
                "count": len(wanted),
                "batched_by": "query",
            }
        )
    if query is None:
        return json.dumps({"error": "pass either 'query' or 'queries'"})
    return json.dumps(_search_one(st, query, term_cache))


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


# Note ids this process has handed out.  Checked alongside the on-disk ids so
# a burst of creates inside one second cannot reuse an id even before the
# earlier notes are flushed and re-read.
_ISSUED_NOWS = set()


def _unique_now():
    """Return a note id no other note is using -- on disk or issued by us.

    Note identity is the one-second ``now`` stamp, and ``search_notes`` folds
    results by it (``seen.setdefault(note.now, ...)``), so two notes sharing an
    id means one of them is silently invisible to every reader: it is on disk,
    ``get_note`` returns only the first, and search drops the rest.  A burst of
    creates inside one second -- an agent checkpointing a fan-out, several
    sub-agents reducing into one store -- hits this every time.

    So we bump forward until the id is free rather than trusting the clock.
    Ordering and second-level granularity carry no meaning here (notes are read
    back by tag/context, and on-disk order is preserved independently), so
    drifting a few seconds into the future costs nothing and buys uniqueness.

    On-disk ids are re-read per call so *separate* server processes writing the
    same store -- the per-session stdio spawns -- also see each other's notes.
    A genuine simultaneous write from two processes can still pick the same
    slot (there is no lock); the window is tiny and the cost is the pre-existing
    behavior, not a regression.
    """
    try:
        taken = {n["now"] for n in _read_notes([(SearchType.ALL, "")])}
    except FileNotFoundError:
        # First write to a store nobody has created yet: Note.append will make
        # the file.  The old handler never read it, so tolerate this rather
        # than turning a working create into an error.
        taken = set()
    taken |= _ISSUED_NOWS
    now = int(time.time())
    while now in taken:
        now += 1
    _ISSUED_NOWS.add(now)
    return now


def _handle_mcp_create_note(message, tag="", context="", directory=None):
    """Create a note and append it to the store; return the created note.

    The note's id comes from :func:`_unique_now`, not the bare clock, so rapid
    creates stay individually addressable (see that function for why).

    Best-effort concurrency: ``Note.append`` is append-safe against other
    appenders (verified: 16 concurrent processes x 200KB bodies, no
    interleaving), but a create racing a CLI ``pop``/``scoop`` (which rewrite
    the file) can still be lost.  Documented so callers aren't surprised.
    """
    pwd = directory or os.getcwd()
    note = Note.jot(message, tag=tag, context=context, pwd=pwd, now=_unique_now())
    Note.append(Note.NOTEFILE, note)
    return json.dumps(_hydrate(note))


# ── note_claim: the life-notebook jot, built server-side ──────────────────────
#
# The low-level create_note asks the caller for a directory, a tag string and a
# fully escaped multi-line body.  Small local models emit that unreliably (a
# 30B will answer "done" without ever having emitted the call), so the whole
# structure the `life-notebook` skill specifies -- directory precedence, tag
# grammar, VERDICT/CONFIDENCE/EVIDENCE/REPLAY body -- is built here in Python
# from a handful of plain fields instead.  The skill's contract is the spec:
# the minimum jot is verdict + subject_type + subject.

# subject_type -> the subdirectory under <store-dir>/life/ that owns it.
_CLAIM_SUBJECT_DIRS = {
    "person": "people",
    "place": "places",
    "event": "events",
    "era": "eras",
    "topic": "topics",
}

# subject_type -> the colon-namespaced tag slot for it, when one exists.  Only
# person/place/topic have an entity namespace; event and era are located by
# their directory, and any cross-cutting facet rides in `entities`.
_CLAIM_ENTITY_TAGS = {"person": "person", "place": "place", "topic": "topic"}

_CLAIM_KINDS = ("claim", "lead", "story")
_CLAIM_CONFIDENCES = ("strong", "plausible")

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_RE = re.compile(r"^\d{4}$")


def _claim_error(msg):
    """Shape one note_claim rejection like every other tool error here."""
    return json.dumps({"error": f"note_claim: {msg}"})


def _as_list(value):
    """Accept a list, a lone string, or nothing for a repeatable field.

    Small models routinely send a bare string where the schema says array; a
    silently-dropped tool tag is worse than accommodating that here.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value if str(v).strip()]


def _life_root():
    """Return the ``life/`` tree for the store this server is bound to.

    Derived from the bound notefile rather than hard-coded, so each workspace's
    store maps to its own subtree (workspace-recall/.catjot ->
    workspace-recall/life/) and a test store lands in its own tmpdir.
    """
    return os.path.join(os.path.dirname(os.path.abspath(Note.NOTEFILE)), "life")


def _claim_directory(subject_type, subject, date):
    """Build the virtual pwd for a finding, or raise ValueError with the fix.

    One note per finding, filed under its PRIMARY facet -- so the directory is
    a pure function of (subject_type, subject, date) and the caller never hand-
    spells a path that a typo could hide from list_notes.
    """
    if subject_type not in _CLAIM_SUBJECT_DIRS:
        raise ValueError(
            f"subject_type must be one of {'|'.join(_CLAIM_SUBJECT_DIRS)}, got {subject_type!r}"
        )
    root = os.path.join(_life_root(), _CLAIM_SUBJECT_DIRS[subject_type])

    if subject_type == "era":
        # An era is the year itself; accept it from either field.
        year = subject if _YEAR_RE.match(subject) else (date or "")
        if not _YEAR_RE.match(year):
            raise ValueError("an era note needs a 4-digit year as 'subject' (or as 'date')")
        return os.path.join(root, year)

    if not _SLUG_RE.match(subject):
        raise ValueError(
            f"subject {subject!r} is not a slug: lowercase ascii and hyphens only, no '/' or '#'"
        )

    if subject_type == "event":
        # events/<yyyy-mm-dd>-<slug>, the first day of the event window.
        if not _DATE_RE.match(date or ""):
            raise ValueError("an event note needs 'date' as YYYY-MM-DD (the first day of the window)")
        return os.path.join(root, f"{date}-{subject}")

    return os.path.join(root, subject)


def _claim_tags(kind, subject_type, subject, confidence, absence, tools, entities):
    """Build the tag string: kind, then confidence/polarity, entities, tools.

    Confidence and the `absence` polarity are claim-only per the skill's rubric
    (a one-signal finding is a lead and carries no confidence).  They are
    dropped rather than rejected on a lead/story: the note is still correct
    without them, and a hard error here would cost a jot.
    """
    tags = [kind]
    if kind == "claim":
        if confidence:
            tags.append(confidence.split()[0])
        if absence:
            tags.append("absence")

    slot = _CLAIM_ENTITY_TAGS.get(subject_type)
    if slot:
        tags.append(f"{slot}:{subject}")
    for entity in entities:
        if entity not in tags:
            tags.append(entity)
    for tool in tools:
        tag = f"tool:{tool}"
        if tag not in tags:
            tags.append(tag)
    return " ".join(tags)


def _claim_body(verdict, confidence, kind, evidence, replay, next_step):
    """Render the life-notebook body template for one finding."""
    lines = [f"VERDICT: {verdict.strip()}"]
    if confidence and kind == "claim":
        lines.append(f"CONFIDENCE: {confidence.strip()}")
    if evidence:
        lines.append("EVIDENCE:")
        lines.extend(f"  {line.strip()}" for line in evidence)
    if replay:
        lines.append(f"REPLAY: {replay.strip()}")
    if next_step:
        lines.append(f"NEXT: {next_step.strip()}")
    return "\n".join(lines)


def _handle_mcp_note_claim(
    verdict,
    subject_type,
    subject,
    kind="claim",
    confidence=None,
    date=None,
    tools=None,
    entities=None,
    evidence=None,
    replay=None,
    next_step=None,
    absence=False,
):
    """Jot one life-notebook finding from plain fields; return the built note.

    Everything structural is derived here, so the caller supplies only what it
    actually knows.  Rejections are error strings (the module convention) and
    name the fix, since the model sees them inside its own tool loop.
    """
    if not str(verdict).strip():
        return _claim_error("'verdict' must be a non-empty one-sentence finding")

    kind = (kind or "claim").strip().lower()
    if kind not in _CLAIM_KINDS:
        return _claim_error(f"kind must be one of {'|'.join(_CLAIM_KINDS)}, got {kind!r}")

    if confidence:
        head = str(confidence).strip().split()[0].lower()
        if head not in _CLAIM_CONFIDENCES:
            return _claim_error(
                f"confidence must start with {'|'.join(_CLAIM_CONFIDENCES)}, got {confidence!r}"
            )
        confidence = f"{head}{str(confidence).strip()[len(head):]}"

    entities = _as_list(entities)
    for entity in entities:
        slot, _, value = entity.partition(":")
        if slot not in _CLAIM_ENTITY_TAGS or not _SLUG_RE.match(value):
            return _claim_error(
                f"entity {entity!r} must be person:<key>, place:<slug> or topic:<slug>"
            )

    subject_type = str(subject_type).strip().lower()
    subject = str(subject).strip()
    try:
        directory = _claim_directory(subject_type, subject, (date or "").strip())
    except ValueError as exc:
        return _claim_error(str(exc))

    tag = _claim_tags(
        kind, subject_type, subject, confidence, absence, _as_list(tools), entities
    )
    message = _claim_body(
        verdict, confidence, kind, _as_list(evidence), replay, next_step
    )
    note = Note.jot(
        message,
        tag=tag,
        context=(replay or "").strip(),
        pwd=directory,
        now=_unique_now(),
    )
    Note.append(Note.NOTEFILE, note)
    return json.dumps(_hydrate(note))


def register_note_tools(allow_writes=False):
    """Register the MCP note tools into catjot's shared registry.

    Read tools are always registered; the write pair — ``create_note`` and the
    high-level ``note_claim`` — only when *allow_writes* is set: a read-only
    default is the safe posture for a surface an external model drives.
    """
    register_tool(
        name="search_notes",
        description=(
            "Search catjot notes by one field and return the full matching "
            "notes. A note has four searchable fields — 'tag' (space-separated "
            "labels), 'context' (the command or summary that produced the "
            "note), 'message' (the free-form body), and 'directory' (the path "
            "it was written from). Whitespace-separated terms are OR-combined. "
            "BATCH: pass 'queries' (a list) instead of 'query' to run several "
            "independent searches of the same field in ONE call — use this when "
            "consulting the notebook about several subjects before an "
            "investigation. Results come back as batch[], each tagged "
            "_batch_key with its query."
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
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Batch form: several independent queries, each a "
                        "space-separated term set. Use instead of 'query'."
                    ),
                },
            },
            "required": ["field"],
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
        register_tool(
            name="note_claim",
            description=(
                "Jot ONE life-notebook finding (a claim, lead, or story) into "
                "catjot from plain fields. PREFER THIS over create_note for any "
                "finding: the server builds the directory, the tag string, and "
                "the VERDICT/CONFIDENCE/EVIDENCE/REPLAY body for you, so you "
                "never hand-spell a path or an escaped multi-line message. The "
                "minimum jot is verdict + subject_type + subject; add the rest "
                "only when you actually have it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "verdict": {
                        "type": "string",
                        "description": "The finding in one sentence (required).",
                    },
                    "subject_type": {
                        "type": "string",
                        "enum": list(_CLAIM_SUBJECT_DIRS),
                        "description": (
                            "The finding's PRIMARY facet, which decides where it "
                            "is filed. Precedence when several fit: event > "
                            "person > place > era > topic."
                        ),
                    },
                    "subject": {
                        "type": "string",
                        "description": (
                            "The subject's people.yaml key or lowercase-hyphen "
                            "slug; for subject_type='era', the 4-digit year."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": list(_CLAIM_KINDS),
                        "description": (
                            "claim = a corroborated verdict; lead = one unproven "
                            "thread with a NEXT step; story = the one synthesis "
                            "note for an event. Defaults to claim."
                        ),
                    },
                    "confidence": {
                        "type": "string",
                        "description": (
                            "Claims only: 'strong' (3+ converging signals) or "
                            "'plausible' (2), optionally followed by ' - <which "
                            "signals carried it>'. A one-signal finding is a lead "
                            "and takes no confidence."
                        ),
                    },
                    "date": {
                        "type": "string",
                        "description": (
                            "YYYY-MM-DD, the first day of the window - REQUIRED "
                            "for subject_type='event'. A 4-digit year for an era."
                        ),
                    },
                    "tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "The detamonogatari tool names that produced the "
                            "finding, e.g. ['did_i_go']; tagged tool:<name>."
                        ),
                    },
                    "entities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Cross-cutting facets this note is ALSO about, "
                            "already namespaced: person:<key>, place:<slug>, "
                            "topic:<slug>. Tags are the cross-cutting axis - add "
                            "them here rather than duplicating the note."
                        ),
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "One line per exhibit: '<8hex uid> <index> "
                            "<yyyy-mm-dd> <one-phrase role>'."
                        ),
                    },
                    "replay": {
                        "type": "string",
                        "description": (
                            "The single cheapest call that re-verifies this, as a "
                            "pasteable literal, e.g. did_i_go(place=\"Reno\"). "
                            "Also the dedup key - search it before jotting."
                        ),
                    },
                    "next_step": {
                        "type": "string",
                        "description": "Leads only: the concrete call that would promote or refute it.",
                    },
                    "absence": {
                        "type": "boolean",
                        "description": "True for a corroborated did-NOT-happen verdict; adds the 'absence' tag.",
                    },
                },
                "required": ["verdict", "subject_type", "subject"],
            },
            handler=_handle_mcp_note_claim,
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
