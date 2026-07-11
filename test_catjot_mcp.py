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

    def test_allow_writes_exposes_create_note(self):
        self.start(allow_writes=True)
        resp = catjot_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertIn("create_note", names)


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
