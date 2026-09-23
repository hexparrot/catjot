#!/usr/bin/env python3
__author__ = "William Dizon"
__license__ = "MIT"
__version__ = "0.0.1"
__maintainer__ = "William Dizon"
__email__ = "wdchromium@gmail.com"
__status__ = "Development"

"""Tests for catjot_mcp — the MCP server over the catjot note store.

Most tests drive ``handle_message`` / the handlers directly against a temp
notefile; one exercises the real stdio loop through a subprocess so the
newline-delimited JSON-RPC framing is covered end to end.

The MCP note tools register into catjot's *global* tool registry, and the
server rebinds ``Note.NOTEFILE`` process-wide, so setUp/tearDown clear the
registry and restore the notefile to keep tests hermetic regardless of order.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import catjot
import catjot_mcp
from catjot import Note


def seed(path, notes):
    """Append (message, tag, context, pwd) tuples to a fresh notefile at *path*."""
    for message, tag, context, pwd in notes:
        Note.append(path, Note.jot(message, tag=tag, context=context, pwd=pwd))


class MCPTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_notefile = Note.NOTEFILE
        # hermetic registry: nothing from other test files leaks in
        catjot.TOOL_SCHEMAS.clear()
        catjot.TOOL_HANDLERS.clear()
        # ...and hermetic id state: _unique_now remembers what it handed out
        catjot_mcp._ISSUED_NOWS.clear()
        fd, self.notefile = tempfile.mkstemp(suffix=".jot")
        os.close(fd)

    def tearDown(self):
        Note.NOTEFILE = self._orig_notefile
        catjot.TOOL_SCHEMAS.clear()
        catjot.TOOL_HANDLERS.clear()
        for suffix in ("", ".new", ".old"):
            try:
                os.remove(self.notefile + suffix)
            except FileNotFoundError:
                pass

    def start(self, allow_writes=False):
        """Bind the temp notefile and register tools, as serve() would."""
        catjot_mcp.bind_notefile(self.notefile)
        catjot_mcp.register_note_tools(allow_writes=allow_writes)

    def call(self, name, arguments=None, msg_id=1):
        params = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params}
        return catjot_mcp.handle_message(msg)

    def tool_result(self, name, arguments=None):
        """Return the parsed JSON payload of a tools/call text result."""
        resp = self.call(name, arguments)
        return json.loads(resp["result"]["content"][0]["text"]), resp["result"]["isError"]


class TestSchemaAdapter(MCPTestBase):
    def test_openai_to_mcp_reshape(self):
        self.start()
        mcp_tools = [catjot_mcp._openai_to_mcp(s) for s in catjot.TOOL_SCHEMAS]
        self.assertTrue(mcp_tools)
        for t in mcp_tools:
            self.assertIn("name", t)
            self.assertIn("description", t)
            self.assertIn("inputSchema", t)
            self.assertNotIn("function", t)  # unwrapped
            self.assertNotIn("parameters", t)  # renamed to inputSchema
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_is_error_result(self):
        self.assertTrue(catjot_mcp._is_error_result('{"error": "boom"}'))
        self.assertFalse(catjot_mcp._is_error_result("[]"))  # hydrated lists
        self.assertFalse(catjot_mcp._is_error_result('[{"now": 1}]'))
        self.assertFalse(catjot_mcp._is_error_result("not json"))


class TestProtocol(MCPTestBase):
    def test_initialize(self):
        self.start()
        resp = catjot_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}}
        )
        r = resp["result"]
        self.assertEqual(r["protocolVersion"], catjot_mcp.PROTOCOL_VERSION)
        self.assertIn("tools", r["capabilities"])
        self.assertEqual(r["serverInfo"]["name"], "catjot")

    def test_initialize_unknown_version_falls_back(self):
        self.start()
        resp = catjot_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "1999-01-01"}}
        )
        self.assertEqual(resp["result"]["protocolVersion"], catjot_mcp.PROTOCOL_VERSION)

    def test_id_zero_gets_a_response(self):
        # id: 0 is a valid request id, not a notification — must not be dropped.
        self.start()
        resp = catjot_mcp.handle_message({"jsonrpc": "2.0", "id": 0, "method": "ping"})
        self.assertIsNotNone(resp)
        self.assertEqual(resp["id"], 0)
        self.assertEqual(resp["result"], {})

    def test_notification_gets_no_response(self):
        self.start()
        resp = catjot_mcp.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertIsNone(resp)

    def test_unknown_method(self):
        self.start()
        resp = catjot_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 5, "method": "bogus/method"}
        )
        self.assertEqual(resp["error"]["code"], catjot_mcp.METHOD_NOT_FOUND)


class TestToolListing(MCPTestBase):
    def test_readonly_hides_create_note(self):
        self.start(allow_writes=False)
        resp = catjot_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {"search_notes", "list_notes", "get_note"})
        self.assertNotIn("create_note", names)
        self.assertNotIn("note_claim", names)

    def test_allow_writes_exposes_create_note(self):
        self.start(allow_writes=True)
        resp = catjot_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertIn("create_note", names)
        self.assertIn("note_claim", names)


class TestReadTools(MCPTestBase):
    def setUp(self):
        super().setUp()
        seed(self.notefile, [
            ("buy tabby food", "shopping cats", "ls", "/home/user/proj"),
            ("fix the parser bug", "work", "pytest", "/home/user/proj/src"),
        ])
        self.start(allow_writes=False)

    def test_search_notes_returns_hydrated_notes(self):
        data, is_err = self.tool_result("search_notes", {"field": "tag", "query": "cats"})
        self.assertFalse(is_err)
        self.assertEqual(len(data), 1)
        note = data[0]
        # full note dict, not a bare id
        self.assertEqual(note["message"].strip(), "buy tabby food")
        self.assertEqual(note["directory"], "/home/user/proj")
        self.assertIn("now", note)

    def test_search_notes_batch_runs_every_query(self):
        """The batch form answers N independent queries in one call, each item
        tagged with the query that produced it."""
        data, is_err = self.tool_result(
            "search_notes", {"field": "tag", "queries": ["cats", "work"]}
        )
        self.assertFalse(is_err)
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["batched_by"], "query")
        by_q = {i["_batch_key"]: i["notes"] for i in data["batch"]}
        self.assertEqual(by_q["cats"][0]["message"].strip(), "buy tabby food")
        self.assertEqual(by_q["work"][0]["message"].strip(), "fix the parser bug")

    def test_search_notes_batch_dedups_queries(self):
        data, _ = self.tool_result(
            "search_notes", {"field": "tag", "queries": ["cats", "cats"]}
        )
        self.assertEqual(data["count"], 1)

    def test_search_notes_batch_matches_singular_results(self):
        """A batched query must return exactly what the singular call returns --
        the batch form is a round-trip optimization, never a semantic change."""
        single, _ = self.tool_result(
            "search_notes", {"field": "tag", "query": "cats"}
        )
        batched, _ = self.tool_result(
            "search_notes", {"field": "tag", "queries": ["cats"]}
        )
        self.assertEqual(batched["batch"][0]["notes"], single)

    def test_search_notes_requires_a_query(self):
        data, is_err = self.tool_result("search_notes", {"field": "tag"})
        self.assertTrue(is_err)
        self.assertIn("error", data)

    def test_search_notes_unknown_field(self):
        data, is_err = self.tool_result("search_notes", {"field": "bogus", "query": "x"})
        self.assertTrue(is_err)
        self.assertIn("error", data)

    def test_list_notes_exact_vs_tree(self):
        exact, _ = self.tool_result("list_notes", {"directory": "/home/user/proj"})
        self.assertEqual(len(exact), 1)
        tree, _ = self.tool_result(
            "list_notes", {"directory": "/home/user/proj", "tree": True}
        )
        self.assertEqual(len(tree), 2)

    def test_get_note_by_timestamp(self):
        listed, _ = self.tool_result("list_notes", {"directory": "/home/user/proj"})
        ts = listed[0]["now"]
        got, is_err = self.tool_result("get_note", {"timestamp": ts})
        self.assertFalse(is_err)
        self.assertEqual(got["now"], ts)

    def test_get_note_missing(self):
        got, is_err = self.tool_result("get_note", {"timestamp": 1})
        self.assertTrue(is_err)
        self.assertIn("error", got)

    def test_unknown_tool_is_error(self):
        _, is_err = self.tool_result("no_such_tool", {})
        self.assertTrue(is_err)

    def test_arguments_omitted_does_not_crash(self):
        # 'arguments' absent entirely — must surface a dispatch error, not KeyError.
        data, is_err = self.tool_result("list_notes", None)
        self.assertTrue(is_err)
        self.assertIn("error", data)


class TestCatjotFileBinding(MCPTestBase):
    def test_catjot_file_is_honored(self):
        # Seed OUR temp file, bind via env, confirm the server serves it (not ~/.catjot).
        seed(self.notefile, [("env note", "envtag", "", "/tmp/env")])
        os.environ["CATJOT_FILE"] = self.notefile
        try:
            catjot_mcp.bind_notefile(catjot_mcp.resolve_notefile())
            catjot_mcp.register_note_tools()
            self.assertEqual(Note.NOTEFILE, self.notefile)
            data, _ = self.tool_result("search_notes", {"field": "tag", "query": "envtag"})
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["message"].strip(), "env note")
        finally:
            os.environ.pop("CATJOT_FILE", None)


class TestMissingNotefileSurvival(MCPTestBase):
    def test_read_on_missing_file_does_not_exit_and_creates_it(self):
        # A read against a not-yet-existing file must not SystemExit (which would
        # sail past dispatch's `except Exception` and kill the server) or print a
        # cat to stdout. bind_notefile touch-creates the file to prevent it.
        missing = self.notefile + ".ghost"
        try:
            catjot_mcp.bind_notefile(missing)
            catjot_mcp.register_note_tools()
            self.assertTrue(os.path.exists(missing))  # touch-created
            data, is_err = self.tool_result("get_note", {"timestamp": 123})
            self.assertTrue(is_err)  # graceful error, not a crash
            self.assertIn("error", data)
            # search returns an empty list, not an exit
            empty, is_err2 = self.tool_result(
                "search_notes", {"field": "message", "query": "anything"}
            )
            self.assertEqual(empty, [])
            self.assertFalse(is_err2)
        finally:
            for suffix in ("", ".new", ".old"):
                try:
                    os.remove(missing + suffix)
                except FileNotFoundError:
                    pass


class TestWriteTool(MCPTestBase):
    def test_create_note_persists(self):
        self.start(allow_writes=True)
        data, is_err = self.tool_result(
            "create_note",
            {"message": "note via MCP", "tag": "mcp", "directory": "/tmp/x"},
        )
        self.assertFalse(is_err)
        self.assertEqual(data["message"].strip(), "note via MCP")
        # actually on disk
        on_disk = list(Note.iterate(self.notefile))
        self.assertEqual(len(on_disk), 1)
        self.assertEqual(on_disk[0].message.strip(), "note via MCP")

    def test_create_note_same_second_ids_stay_unique(self):
        # Regression: note identity is the one-second `now` stamp, and both
        # search_notes (folds via seen.setdefault(note.now, ...)) and get_note
        # (returns matches[0]) key off it.  A burst of creates inside one
        # second used to hand every note the same id, so all but the first
        # became invisible -- on disk, but unreachable by id and dropped from
        # search.  An agent checkpointing a fan-out hits this every time.
        self.start(allow_writes=True)
        n = 12
        ids = []
        for i in range(n):
            data, is_err = self.tool_result(
                "create_note",
                {"message": f"slice-{i}", "tag": "burst", "context": f"run:x/{i}"},
            )
            self.assertFalse(is_err)
            ids.append(data["now"])

        self.assertEqual(len(set(ids)), n, "ids collided within one second")
        # every note is on disk...
        self.assertEqual(len(list(Note.iterate(self.notefile))), n)
        # ...individually addressable...
        for i, ts in enumerate(ids):
            got, is_err = self.tool_result("get_note", {"timestamp": ts})
            self.assertFalse(is_err)
            self.assertEqual(got["message"].strip(), f"slice-{i}")
        # ...and none dropped by search's fold-by-id
        found, is_err = self.tool_result(
            "search_notes", {"field": "tag", "query": "burst"}
        )
        self.assertFalse(is_err)
        self.assertEqual(len(found), n)

    def test_create_note_id_skips_ids_already_on_disk(self):
        # Cross-process guard: separate stdio server spawns share one store, so
        # _unique_now consults the file, not just its own issued set.
        self.start(allow_writes=True)
        taken = int(time.time())
        Note.append(
            self.notefile,
            Note.jot("pre-existing", tag="burst", context="c", pwd="/tmp", now=taken),
        )
        catjot_mcp._ISSUED_NOWS.clear()  # as if a fresh process
        data, is_err = self.tool_result("create_note", {"message": "new", "tag": "burst"})
        self.assertFalse(is_err)
        self.assertNotEqual(data["now"], taken)

    def test_create_note_whitespace_message_is_error(self):
        # Regression: a whitespace-only body used to slip past the non-empty
        # guard and land an empty note on disk.  It must now surface as an
        # error and write nothing.
        self.start(allow_writes=True)
        data, is_err = self.tool_result("create_note", {"message": "   "})
        self.assertTrue(is_err)
        self.assertIn("error", data)
        self.assertEqual(list(Note.iterate(self.notefile)), [])

    def test_create_note_newline_in_tag_context_does_not_corrupt(self):
        # Regression: a newline in the LLM-supplied tag/context used to inject
        # extra lines and desync the parser, mangling the note and its pwd.
        self.start(allow_writes=True)
        data, is_err = self.tool_result(
            "create_note",
            {
                "message": "real body",
                "tag": "foo\nbar",
                "context": "ctx\nDate:9999",
                "directory": "/tmp/stamped",
            },
        )
        self.assertFalse(is_err)
        # returned payload is consistent with what lands on disk
        self.assertEqual(data["tag"], "foo bar")
        self.assertEqual(data["context"], "ctx Date:9999")

        on_disk = list(Note.iterate(self.notefile))
        self.assertEqual(len(on_disk), 1)
        note = on_disk[0]
        self.assertEqual(note.pwd, "/tmp/stamped")
        self.assertEqual(note.tag, "foo bar")
        self.assertEqual(note.context, "ctx Date:9999")
        self.assertEqual(note.message.strip(), "real body")

    def test_create_note_absent_when_readonly(self):
        self.start(allow_writes=False)
        _, is_err = self.tool_result("create_note", {"message": "x"})
        self.assertTrue(is_err)  # unknown tool


class TestNoteClaim(MCPTestBase):
    """note_claim builds the life-notebook structure the skill specifies.

    The point of this tool is that a small local model supplies only plain
    fields, so every test here asserts something the *caller* did not spell:
    the directory, the tag string, or the body template.
    """

    def claim(self, **kwargs):
        return self.tool_result("note_claim", kwargs)

    def life(self, *parts):
        """The expected life/ path for this test's temp store."""
        return os.path.join(
            os.path.dirname(os.path.abspath(self.notefile)), "life", *parts
        )

    def test_minimum_jot_builds_directory_tag_and_body(self):
        self.start(allow_writes=True)
        data, is_err = self.claim(
            verdict="Ada moved to Reno in 2019.",
            subject_type="person",
            subject="ada-lovelace",
        )
        self.assertFalse(is_err)
        self.assertEqual(data["directory"], self.life("people", "ada-lovelace"))
        self.assertEqual(data["tag"], "claim person:ada-lovelace")
        self.assertEqual(data["message"].strip(), "VERDICT: Ada moved to Reno in 2019.")
        self.assertEqual(len(list(Note.iterate(self.notefile))), 1)

    def test_life_root_follows_the_bound_store(self):
        # Each workspace's store owns its own life/ subtree, so the root is
        # derived from the notefile rather than hard-coded to workspace-recall.
        self.start(allow_writes=True)
        data, _ = self.claim(
            verdict="x", subject_type="topic", subject="woodworking"
        )
        self.assertTrue(data["directory"].startswith(os.path.dirname(self.notefile)))
        self.assertEqual(data["directory"], self.life("topics", "woodworking"))

    def test_event_files_under_date_prefixed_slug(self):
        self.start(allow_writes=True)
        data, is_err = self.claim(
            verdict="3-night coast trip with Ada.",
            subject_type="event",
            subject="coast-trip",
            date="2019-06-14",
            confidence="strong - geo anchor + photos + purchases",
            tools=["did_i_go"],
            entities=["person:ada-lovelace", "place:cedar-harbor"],
            evidence=["a1b2c3d4 geo_locations 2019-06-15 overnight anchor"],
            replay='did_i_go(place="Cedar Harbor")',
        )
        self.assertFalse(is_err)
        self.assertEqual(data["directory"], self.life("events", "2019-06-14-coast-trip"))
        # kind, confidence word, cross-cutting entities, then producing tools
        self.assertEqual(
            data["tag"],
            "claim strong person:ada-lovelace place:cedar-harbor tool:did_i_go",
        )
        self.assertEqual(
            data["message"].strip().splitlines(),
            [
                "VERDICT: 3-night coast trip with Ada.",
                "CONFIDENCE: strong - geo anchor + photos + purchases",
                "EVIDENCE:",
                "  a1b2c3d4 geo_locations 2019-06-15 overnight anchor",
                'REPLAY: did_i_go(place="Cedar Harbor")',
            ],
        )

    def test_event_without_date_is_rejected_with_the_fix(self):
        # The date is structural (it is half the directory name), so this is
        # one of the few things the server cannot supply for the caller.
        self.start(allow_writes=True)
        data, is_err = self.claim(
            verdict="x", subject_type="event", subject="coast-trip"
        )
        self.assertTrue(is_err)
        self.assertIn("YYYY-MM-DD", data["error"])
        self.assertEqual(list(Note.iterate(self.notefile)), [])

    def test_era_takes_the_year_from_subject_or_date(self):
        self.start(allow_writes=True)
        by_subject, _ = self.claim(
            verdict="2019 was the travel year.", subject_type="era", subject="2019"
        )
        self.assertEqual(by_subject["directory"], self.life("eras", "2019"))
        by_date, _ = self.claim(
            verdict="2020 was the quiet year.",
            subject_type="era",
            subject="2020",
            date="2020",
        )
        self.assertEqual(by_date["directory"], self.life("eras", "2020"))
        bad, is_err = self.claim(
            verdict="x", subject_type="era", subject="the-nineties"
        )
        self.assertTrue(is_err)
        self.assertIn("4-digit year", bad["error"])

    def test_replay_becomes_the_context_dedup_key(self):
        # context is what the consult-first protocol searches before re-running
        # an expensive chain, so it has to be the literal call, not a summary.
        self.start(allow_writes=True)
        self.claim(
            verdict="Ada was in Reno.",
            subject_type="person",
            subject="ada-lovelace",
            replay='did_i_go(place="Reno")',
        )
        found, is_err = self.tool_result(
            "search_notes", {"field": "context", "query": "did_i_go"}
        )
        self.assertFalse(is_err)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["context"], 'did_i_go(place="Reno")')

    def test_lead_drops_confidence_but_keeps_next_step(self):
        # A one-signal finding IS a lead and carries no confidence tag; the
        # rubric word is silently dropped rather than costing the jot.
        self.start(allow_writes=True)
        data, is_err = self.claim(
            verdict="Possible 2021 Reno visit.",
            subject_type="person",
            subject="ada-lovelace",
            kind="lead",
            confidence="strong",
            next_step='structured_search(query="booking")',
        )
        self.assertFalse(is_err)
        self.assertEqual(data["tag"], "lead person:ada-lovelace")
        self.assertNotIn("CONFIDENCE", data["message"])
        self.assertIn('NEXT: structured_search(query="booking")', data["message"])

    def test_absence_is_a_claim_polarity(self):
        self.start(allow_writes=True)
        data, _ = self.claim(
            verdict="No Japan trip in 2020.",
            subject_type="place",
            subject="japan",
            confidence="plausible",
            absence=True,
        )
        self.assertEqual(data["tag"], "claim plausible absence place:japan")
        # ...and only for claims: a lead cannot be an absence verdict
        lead, _ = self.claim(
            verdict="Maybe no Japan trip.",
            subject_type="place",
            subject="japan",
            kind="lead",
            absence=True,
        )
        self.assertEqual(lead["tag"], "lead place:japan")

    def test_repeatable_fields_accept_a_bare_string(self):
        # Small models routinely send a string where the schema says array; a
        # silently-dropped tool tag is worse than accommodating that.
        self.start(allow_writes=True)
        data, is_err = self.claim(
            verdict="x",
            subject_type="person",
            subject="ada-lovelace",
            tools="sweep",
            evidence="a1b2c3d4 chat_messages 2019-01-02 first contact",
        )
        self.assertFalse(is_err)
        self.assertIn("tool:sweep", data["tag"])
        self.assertIn("  a1b2c3d4 chat_messages", data["message"])

    def test_duplicate_tags_collapse(self):
        self.start(allow_writes=True)
        data, _ = self.claim(
            verdict="x",
            subject_type="person",
            subject="ada-lovelace",
            entities=["person:ada-lovelace"],
            tools=["sweep", "sweep"],
        )
        self.assertEqual(data["tag"], "claim person:ada-lovelace tool:sweep")

    def test_malformed_inputs_are_named_errors_not_notes(self):
        self.start(allow_writes=True)
        for kwargs, expected in [
            ({"verdict": "  ", "subject_type": "person", "subject": "ada"}, "verdict"),
            ({"verdict": "x", "subject_type": "person", "subject": "Ada Lovelace"}, "slug"),
            ({"verdict": "x", "subject_type": "pet", "subject": "rex"}, "subject_type"),
            ({"verdict": "x", "subject_type": "person", "subject": "ada", "kind": "hunch"}, "kind"),
            ({"verdict": "x", "subject_type": "person", "subject": "ada", "confidence": "certain"}, "confidence"),
            ({"verdict": "x", "subject_type": "person", "subject": "ada", "entities": ["dog:rex"]}, "entity"),
        ]:
            with self.subTest(expected=expected):
                data, is_err = self.claim(**kwargs)
                self.assertTrue(is_err)
                self.assertIn(expected, data["error"])
        self.assertEqual(list(Note.iterate(self.notefile)), [])

    def test_missing_required_field_is_caught_before_the_handler(self):
        self.start(allow_writes=True)
        data, is_err = self.claim(verdict="x", subject_type="person")
        self.assertTrue(is_err)
        self.assertIn("subject", data["error"])

    def test_note_claim_is_write_gated(self):
        self.start(allow_writes=False)
        data, is_err = self.claim(
            verdict="x", subject_type="person", subject="ada-lovelace"
        )
        self.assertTrue(is_err)
        self.assertIn("unknown tool", data["error"])

    def test_back_to_back_jots_stay_addressable(self):
        # Same guarantee create_note gained: a session checkpointing several
        # findings in one second must not make all but the first invisible.
        self.start(allow_writes=True)
        ids = []
        for i in range(6):
            data, is_err = self.claim(
                verdict=f"finding {i}", subject_type="person", subject=f"person-{i}"
            )
            self.assertFalse(is_err)
            ids.append(data["now"])
        self.assertEqual(len(set(ids)), 6)


class TestStdioSubprocess(MCPTestBase):
    def test_end_to_end_over_stdio(self):
        seed(self.notefile, [("subprocess note", "e2e", "", "/tmp/e2e")])
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_notes",
                        "arguments": {"field": "tag", "query": "e2e"}}},
        ]
        stdin = "".join(json.dumps(m) + "\n" for m in messages)
        env = dict(os.environ, CATJOT_FILE=self.notefile)
        proc = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__) or ".", "catjot_mcp.py")],
            input=stdin, capture_output=True, text=True, env=env, timeout=30,
        )
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        # exactly three responses (init, tools/list, tools/call) — the
        # notification produced none.
        self.assertEqual(len(lines), 3)
        responses = [json.loads(l) for l in lines]
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "catjot")
        names = {t["name"] for t in responses[1]["result"]["tools"]}
        self.assertEqual(names, {"search_notes", "list_notes", "get_note"})
        payload = json.loads(responses[2]["result"]["content"][0]["text"])
        self.assertEqual(payload[0]["message"].strip(), "subprocess note")


if __name__ == "__main__":
    unittest.main()
