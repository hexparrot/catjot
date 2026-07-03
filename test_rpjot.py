#!/usr/bin/env python3
"""test_rpjot_engine_live.py -- Live LLM regression suite for rpjot_engine.py.

All tests call the real LLM.

Run all:
    python3 -m pytest test_rpjot_engine_live.py -v

Run one class:
    python3 -m pytest test_rpjot_engine_live.py::TestExtractSceneContext -v

Stop on first failure:
    python3 -m pytest test_rpjot_engine_live.py -x -v
"""

import json
import os
import unittest

from catjot import Note, ContextBundle
from rpjot import (
    RPJotEngine,
    SessionState,
    CONTEXT_MAX_TOKS,
    CONTEXT_HARD_LIMIT_TOKS,
    MODEL_CONTEXT_LIMIT_TOKS,
    _RESPONSE_RESERVE_TOKS,
    _tok,
    _msg_toks,
)

TMP_CATNOTE = "tests/.catjot"
FIXED_CATNOTE = "tests/bellvue.jot"
Note.NOTEFILE = FIXED_CATNOTE


# ---------------------------------------------------------------------------
# Shared engine factory
# ---------------------------------------------------------------------------


def _make_engine(location="test-chamber", people=None):
    """Return a fresh RPJotEngine with tools registered."""
    engine = RPJotEngine(
        location=location,
        people_present=people if people is not None else {"player", "alice"},
    )
    engine.register_all_tools()
    return engine


def _base_messages(system_content=None):
    content = system_content or (
        "You are a game master. Use the provided tools when appropriate. "
        "Do not narrate; only call tools in response to the player."
    )
    return [{"role": "system", "content": content}]


# Token-count-aware text generators for threshold tests.
# Using realistic English so the pre-tokenizer produces ~12 tokens/chunk.
_CHUNK = "The manor's old stones whispered secrets to the night wind. "  # ~12 tok


def _text_over_soft(extra_toks: int = 200) -> str:
    """Return text with ≥ CONTEXT_MAX_TOKS + extra_toks tokens."""
    reps = max(1, (CONTEXT_MAX_TOKS + extra_toks + 11) // 12)
    return _CHUNK * reps


def _text_over_hard(extra_toks: int = 200) -> str:
    """Return text with ≥ CONTEXT_HARD_LIMIT_TOKS + extra_toks tokens."""
    reps = max(1, (CONTEXT_HARD_LIMIT_TOKS + extra_toks + 11) // 12)
    return _CHUNK * reps


def _over_limit_text() -> str:
    """Alias for _text_over_soft used by TestCondenseContext fallback tests."""
    return _text_over_soft()


# ---------------------------------------------------------------------------
# 1. SessionState
# ---------------------------------------------------------------------------


class TestSessionState(unittest.TestCase):
    """SessionState.header() must produce a well-formed string."""

    def test_header_contains_loc_tag(self):
        s = SessionState(location="ravenwood", people_present={"player"})
        self.assertIn("loc:ravenwood", s.header())

    def test_header_contains_present_names(self):
        s = SessionState(location="ravenwood", people_present={"alice", "bob"})
        h = s.header()
        self.assertIn("alice", h)
        self.assertIn("bob", h)

    def test_header_empty_people_shows_none(self):
        s = SessionState(location="ravenwood", people_present=set())
        self.assertIn("none", s.header())

    def test_header_people_are_sorted(self):
        s = SessionState(location="ravenwood", people_present={"zelda", "alice", "bob"})
        h = s.header()
        self.assertLess(h.index("alice"), h.index("bob"))
        self.assertLess(h.index("bob"), h.index("zelda"))

    def test_header_starts_with_bracket(self):
        s = SessionState(location="cellar", people_present={"player"})
        self.assertTrue(s.header().startswith("[CURRENT STATE"))

    def test_header_ends_with_bracket(self):
        s = SessionState(location="cellar", people_present={"player"})
        self.assertTrue(s.header().endswith("]"))


# ---------------------------------------------------------------------------
# 2. Static utilities (no LLM)
# ---------------------------------------------------------------------------


class TestStaticUtilities(unittest.TestCase):
    """strip_think_tags, extract_json_from_response, strip_followup_instruction."""

    # --- strip_think_tags ---

    def test_strip_removes_think_block(self):
        _, clean = RPJotEngine.strip_think_tags("<think>internal</think>Narrative.")
        self.assertEqual(clean, "Narrative.")

    def test_strip_returns_think_content(self):
        think, _ = RPJotEngine.strip_think_tags("<think>reasoning here</think>Text.")
        self.assertEqual(think, "reasoning here")

    def test_strip_no_think_block(self):
        think, clean = RPJotEngine.strip_think_tags("Plain text.")
        self.assertEqual(think, "")
        self.assertEqual(clean, "Plain text.")

    def test_strip_multiline_think_block(self):
        raw = "<think>\nline one\nline two\n</think>After."
        think, clean = RPJotEngine.strip_think_tags(raw)
        self.assertIn("line one", think)
        self.assertEqual(clean, "After.")

    def test_strip_preserves_content_after_think(self):
        _, clean = RPJotEngine.strip_think_tags("<think>x</think>Keep this.")
        self.assertEqual(clean, "Keep this.")

    # --- extract_json_from_response ---

    def test_extract_clean_json(self):
        parsed = RPJotEngine.extract_json_from_response('{"key": "value"}')
        self.assertEqual(parsed["key"], "value")

    def test_extract_json_embedded_in_prose(self):
        raw = 'Sure! Here you go: {"items": ["a", "b"]} Hope that helps.'
        parsed = RPJotEngine.extract_json_from_response(raw)
        self.assertIn("items", parsed)

    def test_extract_raises_on_no_json(self):
        with self.assertRaises(ValueError):
            RPJotEngine.extract_json_from_response("No JSON here at all.")

    def test_extract_raises_on_malformed_json(self):
        with self.assertRaises(Exception):
            RPJotEngine.extract_json_from_response("{bad json:}")

    def test_extract_returns_dict(self):
        parsed = RPJotEngine.extract_json_from_response('{"a": 1}')
        self.assertIsInstance(parsed, dict)

    # --- strip_followup_instruction ---

    def test_strip_followup_removes_key(self):
        raw = json.dumps({"people": ["alice"], "followup_instruction": "do something"})
        instruction, cleaned = RPJotEngine.strip_followup_instruction(raw)
        self.assertEqual(instruction, "do something")
        self.assertNotIn("followup_instruction", json.loads(cleaned))

    def test_strip_followup_preserves_other_keys(self):
        raw = json.dumps({"people": ["alice"], "followup_instruction": "x"})
        _, cleaned = RPJotEngine.strip_followup_instruction(raw)
        self.assertIn("people", json.loads(cleaned))

    def test_strip_followup_no_key_returns_none(self):
        raw = json.dumps({"people": ["alice"]})
        instruction, cleaned = RPJotEngine.strip_followup_instruction(raw)
        self.assertIsNone(instruction)

    def test_strip_followup_plain_string_does_not_crash(self):
        """Tool handlers that return plain strings must not crash the loop."""
        instruction, cleaned = RPJotEngine.strip_followup_instruction(
            "Event recorded: something happened..."
        )
        self.assertIsNone(instruction)
        self.assertEqual(cleaned, "Event recorded: something happened...")


# ---------------------------------------------------------------------------
# 3. Message construction helpers (no LLM)
# ---------------------------------------------------------------------------


class TestMessageConstruction(unittest.TestCase):
    """build_user_message and build_tool_result_message."""

    def setUp(self):
        self.engine = RPJotEngine(location="ravenwood", people_present={"player"})

    def test_build_user_message_role(self):
        msg = self.engine.build_user_message("I look around.")
        self.assertEqual(msg["role"], "user")

    def test_build_user_message_contains_original_text(self):
        msg = self.engine.build_user_message("I pick up the lantern.")
        self.assertIn("I pick up the lantern.", msg["content"])

    def test_build_user_message_contains_state_header(self):
        msg = self.engine.build_user_message("Hello.")
        self.assertIn("[CURRENT STATE", msg["content"])

    def test_build_user_message_header_before_text(self):
        msg = self.engine.build_user_message("Hello.")
        header_idx = msg["content"].index("[CURRENT STATE")
        text_idx = msg["content"].index("Hello.")
        self.assertLess(header_idx, text_idx)

    def test_build_tool_result_message_role(self):
        raw = json.dumps({"people": ["alice"]})
        _, msg = self.engine.build_tool_result_message("call_001", raw)
        self.assertEqual(msg["role"], "tool")

    def test_build_tool_result_message_has_tool_call_id(self):
        raw = json.dumps({"people": ["alice"]})
        _, msg = self.engine.build_tool_result_message("call_001", raw)
        self.assertEqual(msg["tool_call_id"], "call_001")

    def test_build_tool_result_message_strips_followup(self):
        raw = json.dumps({"people": ["alice"], "followup_instruction": "do x"})
        instruction, msg = self.engine.build_tool_result_message("call_001", raw)
        self.assertEqual(instruction, "do x")
        self.assertNotIn("followup_instruction", msg["content"])

    def test_build_tool_result_message_content_is_valid_json(self):
        raw = json.dumps({"people": ["alice"], "followup_instruction": "do x"})
        _, msg = self.engine.build_tool_result_message("call_001", raw)
        parsed = json.loads(msg["content"])
        self.assertIsInstance(parsed, dict)


# ---------------------------------------------------------------------------
# 4. extract_scene_context -- live LLM
# ---------------------------------------------------------------------------


class TestExtractSceneContext(unittest.TestCase):
    """extract_scene_context must always return a parsable dict from a live LLM."""

    SPARSE = "A small stone room. A torch flickers on the wall."
    RICH = (
        "The great hall of Ravenwood Manor. A long oak table dominates the center. "
        "Candelabras line the walls. A cracked mirror hangs above the fireplace. "
        "A sealed letter rests on the mantelpiece."
    )
    EMPTY = ""

    def setUp(self):
        self.engine = _make_engine()

    def _run(self, context):
        return self.engine.extract_scene_context(context)

    def test_sparse_returns_dict(self):
        self.assertIsInstance(self._run(self.SPARSE), dict)

    def test_sparse_has_required_keys(self):
        result = self._run(self.SPARSE)
        self.assertIn("noteworthy_objects", result)
        self.assertIn("established_props", result)

    def test_sparse_values_are_lists(self):
        result = self._run(self.SPARSE)
        self.assertIsInstance(result["noteworthy_objects"], list)
        self.assertIsInstance(result["established_props"], list)

    def test_sparse_list_items_are_strings(self):
        result = self._run(self.SPARSE)
        for key in ("noteworthy_objects", "established_props"):
            for item in result[key]:
                with self.subTest(key=key, item=item):
                    self.assertIsInstance(item, str)

    def test_rich_yields_at_least_one_object(self):
        result = self._run(self.RICH)
        total = len(result["noteworthy_objects"]) + len(result["established_props"])
        self.assertGreater(total, 0, "Expected at least one object from a rich scene.")

    def test_rich_items_are_strings_not_dicts(self):
        result = self._run(self.RICH)
        for key in ("noteworthy_objects", "established_props"):
            for item in result[key]:
                with self.subTest(key=key, item=item):
                    self.assertIsInstance(item, str)

    def test_empty_context_does_not_crash(self):
        try:
            result = self._run(self.EMPTY)
            self.assertIsInstance(result.get("noteworthy_objects"), list)
            self.assertIsInstance(result.get("established_props"), list)
        except ValueError:
            self.fail("extract_scene_context raised ValueError on empty context.")

    def test_no_extra_keys_returned(self):
        result = self._run(self.SPARSE)
        allowed = {"noteworthy_objects", "established_props"}
        extra = set(result.keys()) - allowed
        self.assertEqual(extra, set(), f"Unexpected keys: {extra}")

    def test_repeated_calls_all_parsable(self):
        for i in range(3):
            with self.subTest(run=i):
                result = self._run(self.SPARSE)
                self.assertIsInstance(result, dict)
                self.assertIn("noteworthy_objects", result)


# ---------------------------------------------------------------------------
# 5. Tool handler output -- live handlers, real JSON
# ---------------------------------------------------------------------------


class TestToolHandlerOutput(unittest.TestCase):
    """Real tool handlers must return valid JSON with the correct shape."""

    def setUp(self):
        self.engine = _make_engine(
            location="test-chamber",
            people={"player", "alice"},
        )

    # --- _tool_get_people_present ---

    def test_get_people_present_is_valid_json(self):
        parsed = json.loads(self.engine._tool_get_people_present())
        self.assertIsInstance(parsed, dict)

    def test_get_people_present_has_people_key(self):
        parsed = json.loads(self.engine._tool_get_people_present())
        self.assertIn("people", parsed)

    def test_get_people_present_people_is_list(self):
        parsed = json.loads(self.engine._tool_get_people_present())
        self.assertIsInstance(parsed["people"], list)

    def test_get_people_present_people_are_strings(self):
        parsed = json.loads(self.engine._tool_get_people_present())
        for p in parsed["people"]:
            with self.subTest(person=p):
                self.assertIsInstance(p, str)

    def test_get_people_present_reflects_session(self):
        parsed = json.loads(self.engine._tool_get_people_present())
        self.assertEqual(
            set(parsed["people"]),
            self.engine.session.people_present,
        )

    def test_get_people_present_has_followup_instruction(self):
        parsed = json.loads(self.engine._tool_get_people_present())
        self.assertIn("followup_instruction", parsed)
        self.assertIsInstance(parsed["followup_instruction"], str)
        self.assertTrue(parsed["followup_instruction"].strip())

    # --- _tool_examine_location (calls LLM internally) ---

    def test_examine_location_is_valid_json(self):
        parsed = json.loads(self.engine._tool_examine_location())
        self.assertIsInstance(parsed, dict)

    def test_examine_location_required_keys(self):
        parsed = json.loads(self.engine._tool_examine_location())
        for key in ("people", "location", "noteworthy_objects", "established_props"):
            with self.subTest(key=key):
                self.assertIn(key, parsed)

    def test_examine_location_location_matches_session(self):
        parsed = json.loads(self.engine._tool_examine_location())
        self.assertEqual(parsed["location"], self.engine.session.location)

    def test_examine_location_people_reflects_session(self):
        parsed = json.loads(self.engine._tool_examine_location())
        self.assertEqual(set(parsed["people"]), self.engine.session.people_present)

    def test_examine_location_objects_are_lists(self):
        parsed = json.loads(self.engine._tool_examine_location())
        self.assertIsInstance(parsed["noteworthy_objects"], list)
        self.assertIsInstance(parsed["established_props"], list)

    def test_examine_location_object_items_are_strings(self):
        parsed = json.loads(self.engine._tool_examine_location())
        for key in ("noteworthy_objects", "established_props"):
            for item in parsed[key]:
                with self.subTest(key=key, item=item):
                    self.assertIsInstance(item, str)

    def test_examine_location_has_followup_instruction(self):
        parsed = json.loads(self.engine._tool_examine_location())
        self.assertIn("followup_instruction", parsed)

    # --- _tool_record_event ---

    def test_record_event_returns_string(self):
        result = self.engine._tool_record_event(
            description="Alice opened the cellar door.",
            tags="alice loc:cellar door",
        )
        self.assertIsInstance(result, str)

    def test_record_event_result_starts_with_prefix(self):
        result = self.engine._tool_record_event(
            description="Bob lit the torch.",
            tags="bob loc:test-chamber torch",
        )
        self.assertTrue(result.startswith("Event recorded:"))

    def test_record_event_with_location_kwarg(self):
        result = self.engine._tool_record_event(
            description="The door slammed shut.",
            tags="door loc:cellar",
            location="loc:cellar",
        )
        self.assertIsInstance(result, str)

    def test_record_event_long_description_is_truncated_in_result(self):
        long_desc = "A" * 200
        result = self.engine._tool_record_event(
            description=long_desc,
            tags="test",
        )
        # The result string itself must be shorter than the raw description
        self.assertLess(len(result), len(long_desc) + len("Event recorded: "))


# ---------------------------------------------------------------------------
# 6. Tool dispatch -- LLM selects the correct tool by name
# ---------------------------------------------------------------------------


class TestToolDispatch(unittest.TestCase):
    """The LLM must select the correct tool for canonical trigger phrases."""

    def setUp(self):
        self.engine = _make_engine(
            location="ravenwood-manor",
            people={"player", "alice"},
        )

    def _tool_names_for(self, user_text):
        messages = _base_messages()
        messages.append(self.engine.build_user_message(user_text))
        from catjot import call_llm

        response = call_llm(
            messages, tools=self.engine._tool_schemas, tool_choice="auto"
        )
        tool_calls = response.get("tool_calls") or []
        return [tc["function"]["name"] for tc in tool_calls]

    # --- get_people_present triggers ---

    def test_who_is_here_triggers_get_people_present(self):
        self.assertIn(
            "get_people_present", self._tool_names_for("Who is here with me?")
        )

    def test_do_i_see_anyone_triggers_get_people_present(self):
        self.assertIn(
            "get_people_present", self._tool_names_for("Do I see anyone nearby?")
        )

    def test_am_i_alone_triggers_get_people_present(self):
        self.assertIn(
            "get_people_present", self._tool_names_for("Am I alone in this room?")
        )

    def test_who_am_i_with_triggers_get_people_present(self):
        self.assertIn(
            "get_people_present", self._tool_names_for("Who am I with right now?")
        )

    # --- examine_location triggers ---

    def test_what_is_here_triggers_examine_location(self):
        self.assertIn(
            "examine_location",
            self._tool_names_for("What is here? What can I interact with?"),
        )

    def test_what_interests_me_triggers_examine_location(self):
        self.assertIn(
            "examine_location", self._tool_names_for("What interests me here?")
        )

    def test_what_can_i_do_triggers_examine_location(self):
        self.assertIn("examine_location", self._tool_names_for("What can I do here?"))

    # --- look around triggers either tool ---

    def test_look_around_triggers_a_perception_tool(self):
        names = self._tool_names_for("I look around the room.")
        acceptable = {"get_people_present", "examine_location"}
        self.assertTrue(
            acceptable & set(names),
            f"Expected one of {acceptable}, got {names}",
        )

    # --- record_event triggers ---

    def test_clear_action_triggers_record_event(self):
        self.assertIn(
            "record_event",
            self._tool_names_for(
                "I pick up the iron key from the table and pocket it."
            ),
        )

    def test_record_event_args_are_valid_json(self):
        from catjot import call_llm

        messages = _base_messages()
        messages.append(
            self.engine.build_user_message("I light the torch on the wall.")
        )
        response = call_llm(
            messages, tools=self.engine._tool_schemas, tool_choice="auto"
        )
        for tc in response.get("tool_calls") or []:
            if tc["function"]["name"] == "record_event":
                with self.subTest():
                    parsed = json.loads(tc["function"]["arguments"])
                    self.assertIn("description", parsed)
                    self.assertIn("tags", parsed)

    def test_record_event_description_is_nonempty_string(self):
        from catjot import call_llm

        messages = _base_messages()
        messages.append(self.engine.build_user_message("I close the heavy oak door."))
        response = call_llm(
            messages, tools=self.engine._tool_schemas, tool_choice="auto"
        )
        for tc in response.get("tool_calls") or []:
            if tc["function"]["name"] == "record_event":
                args = json.loads(tc["function"]["arguments"])
                self.assertIsInstance(args["description"], str)
                self.assertTrue(args["description"].strip())

    def test_record_event_tags_is_string(self):
        from catjot import call_llm

        messages = _base_messages()
        messages.append(self.engine.build_user_message("I sit down at the table."))
        response = call_llm(
            messages, tools=self.engine._tool_schemas, tool_choice="auto"
        )
        for tc in response.get("tool_calls") or []:
            if tc["function"]["name"] == "record_event":
                args = json.loads(tc["function"]["arguments"])
                self.assertIsInstance(args["tags"], str)


# ---------------------------------------------------------------------------
# 8. World-entity tool handlers (no LLM)
# ---------------------------------------------------------------------------


class TestWorldEntityTools(unittest.TestCase):
    """New world-entity and navigation tools must return correct strings."""

    def setUp(self):
        self.engine = _make_engine(
            location="test-chamber",
            people={"player", "alice"},
        )

    # --- navigate_to ---

    def test_navigate_to_updates_session_location(self):
        # A hierarchical destination (shared prefix) is resolved deterministically
        # regardless of known_roots — this asserts the core "navigate updates
        # session.location" contract without depending on the shared fixture's
        # mutable root set. The G4 bare-name-nesting branch is covered
        # deterministically by test_resolve_bare_name_from_root_unknown_nests_as_child.
        self.engine._tool_navigate_to("test-chamber/dungeon")
        self.assertEqual(self.engine.session.location, "test-chamber/dungeon")

    def test_navigate_to_strips_loc_prefix(self):
        self.engine._tool_navigate_to("loc:great-hall")
        self.assertEqual(self.engine.session.location, "great-hall")

    def test_navigate_to_returns_confirmation_string(self):
        result = self.engine._tool_navigate_to("cellar")
        self.assertIsInstance(result, str)
        self.assertIn("cellar", result)

    def test_navigate_to_refreshes_location_context(self):
        old_ctx = self.engine.session.location_context
        self.engine._tool_navigate_to("library")
        self.assertIsNot(self.engine.session.location_context, old_ctx)

    # --- set_people_present ---

    def test_set_people_replaces_existing_set(self):
        self.engine._tool_set_people_present(["bob", "carol"])
        self.assertEqual(self.engine.session.people_present, {"bob", "carol"})

    def test_set_people_empty_list_clears_scene(self):
        self.engine._tool_set_people_present([])
        self.assertEqual(self.engine.session.people_present, set())

    def test_set_people_returns_string(self):
        result = self.engine._tool_set_people_present(["player"])
        self.assertIsInstance(result, str)

    # --- save_character ---

    def test_save_character_returns_string(self):
        result = self.engine._tool_save_character(
            name="elara", description="A sharp-eyed elven ranger."
        )
        self.assertIsInstance(result, str)
        self.assertIn("elara", result)

    def test_save_character_with_extra_tags(self):
        result = self.engine._tool_save_character(
            name="theron",
            description="An old wizard.",
            tags="npc wizard",
        )
        self.assertIsInstance(result, str)

    # --- save_location ---

    def test_save_location_returns_string(self):
        result = self.engine._tool_save_location(
            name="crypt", description="A damp stone crypt lit by cold blue flames."
        )
        self.assertIsInstance(result, str)
        self.assertIn("crypt", result)

    # --- save_object ---

    def test_save_object_returns_string(self):
        result = self.engine._tool_save_object(
            name="iron-key",
            description="A heavy iron key with a raven's-head bow.",
            location="cellar",
        )
        self.assertIsInstance(result, str)
        self.assertIn("iron-key", result)

    def test_save_object_strips_loc_prefix_from_location(self):
        result = self.engine._tool_save_object(
            name="torch",
            description="A burning wall torch.",
            location="loc:great-hall",
        )
        self.assertIn("great-hall", result)

    # --- get_character ---

    def test_get_character_returns_valid_json(self):
        parsed = json.loads(self.engine._tool_get_character("alice"))
        self.assertIsInstance(parsed, dict)

    def test_get_character_has_character_key(self):
        parsed = json.loads(self.engine._tool_get_character("alice"))
        self.assertIn("character", parsed)

    def test_get_character_has_followup_instruction(self):
        parsed = json.loads(self.engine._tool_get_character("alice"))
        self.assertIn("followup_instruction", parsed)

    # --- search_world ---

    def test_search_world_returns_valid_json(self):
        parsed = json.loads(self.engine._tool_search_world("loc:dungeon"))
        self.assertIsInstance(parsed, dict)

    def test_search_world_has_world_context_key(self):
        parsed = json.loads(self.engine._tool_search_world("magic"))
        self.assertIn("world_context", parsed)

    def test_search_world_has_followup_instruction(self):
        parsed = json.loads(self.engine._tool_search_world("magic"))
        self.assertIn("followup_instruction", parsed)


# ---------------------------------------------------------------------------
# 9. Tool registry isolation and decorator discovery
# ---------------------------------------------------------------------------


class TestToolRegistry(unittest.TestCase):
    """Engine must discover @rp_tool methods and isolate them per instance."""

    def setUp(self):
        self.engine = _make_engine()

    def test_register_all_tools_populates_schemas(self):
        self.assertGreater(len(self.engine._tool_schemas), 0)

    def test_schemas_have_required_tool_names(self):
        names = {s["function"]["name"] for s in self.engine._tool_schemas}
        for expected in (
            "record_event",
            "get_people_present",
            "examine_location",
            "navigate_to",
            "set_people_present",
            "save_character",
            "save_location",
            "save_object",
            "get_character",
            "search_world",
            "prepare_context",
            "record_knowledge",
        ):
            with self.subTest(tool=expected):
                self.assertIn(expected, names)

    def test_two_engines_have_independent_registries(self):
        engine2 = RPJotEngine(location="other-place")
        engine2.register_all_tools()
        self.assertIsNot(self.engine._tool_schemas, engine2._tool_schemas)

    def test_dispatch_unknown_tool_returns_error_json(self):
        result = self.engine._dispatch("nonexistent_tool", "{}")
        parsed = json.loads(result)
        self.assertIn("error", parsed)

    def test_extract_json_from_response_handles_nested_json(self):
        text = 'Here: {"outer": {"inner": 1}} end.'
        parsed = RPJotEngine.extract_json_from_response(text)
        self.assertEqual(parsed["outer"]["inner"], 1)

    def test_extract_json_from_response_ignores_trailing_json(self):
        text = '{"first": 1} {"second": 2}'
        parsed = RPJotEngine.extract_json_from_response(text)
        self.assertEqual(parsed["first"], 1)


# ---------------------------------------------------------------------------
# 10. Location hierarchy — compute_traversal, resolve_destination, navigate_to
# ---------------------------------------------------------------------------


class TestLocationHierarchy(unittest.TestCase):
    """Traversal algorithm and hierarchical navigate_to behaviour."""

    # --- compute_traversal ---

    def test_sibling_rooms_pass_through_parent(self):
        t = RPJotEngine.compute_traversal(
            "manor/foyer/kitchen", "manor/foyer/drawing_room"
        )
        self.assertEqual(
            t, ["manor/foyer/kitchen", "manor/foyer", "manor/foyer/drawing_room"]
        )

    def test_ascending_to_ancestor(self):
        t = RPJotEngine.compute_traversal(
            "manor/foyer/staircase/elevator", "manor/foyer"
        )
        self.assertEqual(
            t,
            [
                "manor/foyer/staircase/elevator",
                "manor/foyer/staircase",
                "manor/foyer",
            ],
        )

    def test_descending_into_child(self):
        t = RPJotEngine.compute_traversal("manor/foyer", "manor/foyer/closet")
        self.assertEqual(t, ["manor/foyer", "manor/foyer/closet"])

    def test_cross_branch_traversal(self):
        t = RPJotEngine.compute_traversal(
            "manor/east-wing/bedroom", "manor/west-wing/study"
        )
        self.assertEqual(
            t,
            [
                "manor/east-wing/bedroom",
                "manor/east-wing",
                "manor",
                "manor/west-wing",
                "manor/west-wing/study",
            ],
        )

    def test_same_location_is_single_element(self):
        t = RPJotEngine.compute_traversal("manor/foyer", "manor/foyer")
        self.assertEqual(t, ["manor/foyer"])

    def test_traversal_includes_from_and_to(self):
        t = RPJotEngine.compute_traversal(
            "manor/foyer/kitchen", "manor/foyer/drawing_room"
        )
        self.assertEqual(t[0], "manor/foyer/kitchen")
        self.assertEqual(t[-1], "manor/foyer/drawing_room")

    def test_traversal_no_empty_strings(self):
        t = RPJotEngine.compute_traversal("manor/foyer", "manor/bedroom")
        for node in t:
            self.assertTrue(node, f"empty string found in traversal: {t}")

    # --- resolve_destination ---

    def test_resolve_hierarchical_shared_prefix(self):
        dest, nav_type = RPJotEngine.resolve_destination("manor/foyer", "manor/bedroom")
        self.assertEqual(dest, "manor/bedroom")
        self.assertEqual(nav_type, "hierarchical")

    def test_resolve_inferred_bare_name_in_deep_location(self):
        dest, nav_type = RPJotEngine.resolve_destination(
            "manor/foyer/corridor", "cellar"
        )
        self.assertEqual(dest, "manor/cellar")
        self.assertEqual(nav_type, "inferred")

    def test_resolve_direct_multi_segment_different_roots(self):
        _, nav_type = RPJotEngine.resolve_destination("manor/foyer", "dungeon/keep")
        self.assertEqual(nav_type, "direct")

    def test_resolve_bare_name_from_root_unknown_nests_as_child(self):
        # G4 fix (LM §3.3): from a single-component root, an unknown bare
        # destination nests as a child instead of becoming a detached top-level
        # location that breaks location_ancestors recall.
        dest, nav_type = RPJotEngine.resolve_destination("manor", "garden")
        self.assertEqual(dest, "manor/garden")
        self.assertEqual(nav_type, "inferred")

    def test_resolve_bare_name_from_root_known_is_direct(self):
        # A bare destination naming a KNOWN saved sibling root is a direct
        # top-level move.
        dest, nav_type = RPJotEngine.resolve_destination(
            "manor", "garden", known_roots={"garden"}
        )
        self.assertEqual(dest, "garden")
        self.assertEqual(nav_type, "direct")

    def test_resolve_same_root_is_hierarchical(self):
        dest, nav_type = RPJotEngine.resolve_destination(
            "manor/foyer", "manor/foyer/closet"
        )
        self.assertEqual(nav_type, "hierarchical")

    # --- navigate_to with hierarchical paths ---

    def test_navigate_to_hierarchical_updates_location(self):
        engine = _make_engine(location="manor/foyer")
        engine._tool_navigate_to("manor/foyer/closet")
        self.assertEqual(engine.session.location, "manor/foyer/closet")

    def test_navigate_to_returns_traversal_key(self):
        engine = _make_engine(location="manor/foyer/kitchen")
        result = json.loads(engine._tool_navigate_to("manor/foyer/drawing_room"))
        self.assertIn("traversal", result)
        self.assertIn("manor/foyer", result["traversal"])

    def test_navigate_to_has_nav_type(self):
        engine = _make_engine(location="manor/foyer/kitchen")
        result = json.loads(engine._tool_navigate_to("manor/foyer/drawing_room"))
        self.assertIn(result["nav_type"], ("hierarchical", "inferred", "direct"))

    def test_navigate_to_sibling_nav_type_is_hierarchical(self):
        engine = _make_engine(location="manor/foyer/kitchen")
        result = json.loads(engine._tool_navigate_to("manor/foyer/drawing_room"))
        self.assertEqual(result["nav_type"], "hierarchical")

    def test_navigate_to_has_from_and_to(self):
        engine = _make_engine(location="manor/foyer")
        result = json.loads(engine._tool_navigate_to("manor/bedroom"))
        self.assertEqual(result["from"], "manor/foyer")
        self.assertEqual(result["to"], "manor/bedroom")

    def test_navigate_to_infers_sibling_path(self):
        engine = _make_engine(location="manor/foyer/corridor")
        result = json.loads(engine._tool_navigate_to("cellar"))
        self.assertEqual(result["to"], "manor/cellar")
        self.assertEqual(result["nav_type"], "inferred")

    def test_navigate_to_direct_different_roots(self):
        engine = _make_engine(location="manor/foyer")
        result = json.loads(engine._tool_navigate_to("dungeon/keep"))
        self.assertEqual(result["nav_type"], "direct")
        self.assertEqual(len(result["traversal"]), 2)

    def test_navigate_to_refreshes_location_context(self):
        engine = _make_engine(location="manor/foyer")
        old_ctx = engine.session.location_context
        engine._tool_navigate_to("manor/foyer/closet")
        self.assertIsNot(engine.session.location_context, old_ctx)

    # --- location_ancestors property ---

    def test_location_ancestors_single_segment(self):
        s = SessionState(location="manor")
        self.assertEqual(s.location_ancestors, ["manor"])

    def test_location_ancestors_deep_path(self):
        s = SessionState(location="manor/foyer/closet")
        self.assertEqual(
            s.location_ancestors, ["manor", "manor/foyer", "manor/foyer/closet"]
        )

    def test_location_ancestors_two_levels(self):
        s = SessionState(location="manor/foyer")
        self.assertEqual(s.location_ancestors, ["manor", "manor/foyer"])


# ---------------------------------------------------------------------------
# 10b. Canonicalization substrate (OBJECT_PERMANENCE Phase 0) -- zero LLM
# ---------------------------------------------------------------------------


class TestCanonicalizationSubstrate(unittest.TestCase):
    """Phase-0 substrate: room/object canonicalization, child slugs, node genesis.

    Deterministic, zero LLM. Seeds a small location hierarchy plus two objects
    (a canonical-node object and a legacy sighting-only object) into a temp
    notefile, then exercises the read-only canonicalization helpers
    (LOCATION_MARKING §3.2-§3.5, OBJECT_TOOLING §3.2/§3.5).
    """

    def setUp(self):
        Note.NOTEFILE = TMP_CATNOTE
        open(TMP_CATNOTE, "w").close()

        def seed(pwd, tag, message, now=None):
            Note.append(
                TMP_CATNOTE,
                Note.jot(message=message, tag=tag, context="seed", pwd=pwd, now=now),
            )

        # Location hierarchy (garden / garden-east share a prefix on purpose).
        seed("/story/location/manor", "loc:manor", "The manor.")
        seed("/story/location/manor/foyer", "loc:manor/foyer", "The foyer.")
        seed(
            "/story/location/manor/foyer/library",
            "loc:manor/foyer/library",
            "The library.",
        )
        seed("/story/location/manor/garden", "loc:manor/garden", "The garden.")
        seed(
            "/story/location/manor/garden-east",
            "loc:manor/garden-east",
            "The east garden.",
        )
        seed("/story/location/dungeon", "loc:dungeon", "The dungeon.")

        # Objects: a canonical-node object (iron-key) and a legacy sighting-only
        # object (silver-mirror, no /story/object node).
        seed("/story/object/iron-key", "obj:iron-key", "A heavy iron key.")
        seed(
            "/story/location/dungeon",
            "obj:iron-key",
            "The iron key rests on the altar.",
        )
        seed(
            "/story/location/manor/foyer",
            "obj:silver-mirror",
            "A tall silver mirror.",
        )

        self.engine = _make_engine(location="manor/foyer")

    def tearDown(self):
        try:
            os.remove(TMP_CATNOTE)
        except FileNotFoundError:
            pass
        Note.NOTEFILE = FIXED_CATNOTE

    # --- _known_location_roots ---

    def test_known_roots_are_depth_one(self):
        roots = self.engine._known_location_roots()
        self.assertIn("manor", roots)
        self.assertIn("dungeon", roots)
        self.assertNotIn("foyer", roots)  # depth-2, never a root

    # --- _child_room_slugs + TREE boundary (§3.6) ---

    def test_child_room_slugs_lists_real_children(self):
        children = self.engine._child_room_slugs("manor")
        self.assertIn("foyer", children)
        self.assertIn("garden", children)
        self.assertIn("garden-east", children)  # a real depth-2 child of manor

    def test_child_room_slugs_boundary_excludes_prefix_sibling(self):
        # garden vs garden-east: TREE prefix-matches both. Querying garden's
        # children must NOT emit a garbage "east" slug from garden-east.
        self.assertEqual(self.engine._child_room_slugs("manor/garden"), [])

    # --- _canonicalize_room precedence (§3.2) ---

    def test_canon_room_exact_node(self):
        self.assertEqual(
            self.engine._canonicalize_room("manor/foyer/library", "manor"),
            "manor/foyer/library",
        )

    def test_canon_room_child_of_current(self):
        self.assertEqual(
            self.engine._canonicalize_room("library", "manor/foyer"),
            "manor/foyer/library",
        )

    def test_canon_room_known_root_multi_component(self):
        # dungeon is a known root; dungeon/oubliette node does not exist → the
        # root-anchored path is trusted (precedence #3).
        self.assertEqual(
            self.engine._canonicalize_room("dungeon/oubliette", "manor"),
            "dungeon/oubliette",
        )

    def test_canon_room_create_new_nests_under_current(self):
        self.assertEqual(
            self.engine._canonicalize_room("wine-cellar", "manor/foyer"),
            "manor/foyer/wine-cellar",
        )

    def test_canon_room_none_on_empty(self):
        self.assertIsNone(self.engine._canonicalize_room("", "manor"))

    def test_canon_room_slugifies_and_strips_prefix(self):
        self.assertEqual(
            self.engine._canonicalize_room("loc:Secret Garden", "manor"),
            "manor/secret-garden",
        )

    # --- _ensure_location_node idempotency (§3.5) ---

    def test_ensure_node_creates_then_idempotent(self):
        self.assertTrue(self.engine._ensure_location_node("manor/attic"))
        self.assertFalse(self.engine._ensure_location_node("manor/attic"))

    def test_ensure_node_existing_returns_false(self):
        self.assertFalse(self.engine._ensure_location_node("manor/foyer"))

    # --- _canonicalize_object precedence (§3.2) ---

    def test_canon_object_exact_node(self):
        self.assertEqual(self.engine._canonicalize_object("iron-key"), "iron-key")

    def test_canon_object_variants_one_slug(self):
        # I9 identity stability: spelling variants collapse to one flat slug.
        for variant in ("the iron key", "Iron Key", "iron-key", "IRON  KEY"):
            self.assertEqual(
                self.engine._canonicalize_object(variant), "iron-key", variant
            )

    def test_canon_object_registry_slug_legacy(self):
        # silver-mirror has only a room sighting (no canonical node); the
        # registry-slug precedence still resolves the variant.
        self.assertEqual(
            self.engine._canonicalize_object("silver mirror"), "silver-mirror"
        )

    def test_canon_object_create_new_succeeds(self):
        # Create-new is the NORMAL path (asymmetry with room canon): assert it
        # returns the fresh slug rather than refusing.
        self.assertEqual(
            self.engine._canonicalize_object("brass lantern"), "brass-lantern"
        )

    # --- _object_registry + _parse_residence ---

    def test_registry_parses_room_residence(self):
        reg = self.engine._object_registry()
        self.assertEqual(reg["iron-key"]["residence"], {"room": "dungeon"})
        self.assertTrue(reg["iron-key"]["canonical"])

    def test_registry_legacy_object_has_no_canonical(self):
        reg = self.engine._object_registry()
        self.assertEqual(reg["silver-mirror"]["residence"], {"room": "manor/foyer"})
        self.assertFalse(reg["silver-mirror"]["canonical"])

    def test_parse_residence_holder(self):
        self.assertEqual(
            self.engine._parse_residence("/story/character/evie/inventory"),
            {"held_by": "evie"},
        )

    def test_parse_residence_event_channel(self):
        self.assertEqual(
            self.engine._parse_residence("/story/events/manor/foyer"),
            {"room": "manor/foyer"},
        )

    def test_parse_residence_canonical_is_none(self):
        self.assertIsNone(self.engine._parse_residence("/story/object/iron-key"))


# ---------------------------------------------------------------------------
# 11. render_context -- unit tests, no LLM
# ---------------------------------------------------------------------------


class TestRenderContext(unittest.TestCase):
    """render_context: recency sorting, size tiers, plain-string passthrough."""

    def setUp(self):
        self.engine = RPJotEngine(location="test-loc", people_present={"player"})

    def _note(self, message, context="ctx", ts=None):
        n = Note.jot(message=message, context=context, tag="test", pwd="/test")
        if ts is not None:
            n.now = ts
        return n

    def _bundle(self, notes):
        b = ContextBundle([])
        b.notes = list(notes)
        # _visible_notes only iterates self.notes when tags/dirs/ts is non-empty;
        # add a sentinel tag so the iteration fires without disk access.
        b.tags = {"_test_sentinel"}
        return b

    def test_empty_bundle_returns_empty_string(self):
        self.assertEqual(self.engine.render_context(ContextBundle([])), "")

    def test_returns_string(self):
        b = self._bundle([self._note("hello")])
        self.assertIsInstance(self.engine.render_context(b), str)

    def test_note_context_and_message_both_appear(self):
        b = self._bundle([self._note("the message body", context="the context line")])
        result = self.engine.render_context(b)
        self.assertIn("the message body", result)
        self.assertIn("the context line", result)

    def test_multiple_notes_all_appear(self):
        b = self._bundle([self._note(f"note {i}") for i in range(3)])
        result = self.engine.render_context(b)
        for i in range(3):
            self.assertIn(f"note {i}", result)

    def test_recency_order_newest_first(self):
        old = self._note("old message", ts=1_000_000)
        new = self._note("new message", ts=2_000_000)
        # insert old note first (simulates file/insertion order)
        b = self._bundle([old, new])
        result = self.engine.render_context(b)
        self.assertLess(result.index("new message"), result.index("old message"))

    def test_under_soft_limit_no_condensation(self):
        b = self._bundle([self._note("small content")])
        called = []

        def fake_condense(text, focus_hint=""):
            called.append(True)
            return text

        self.engine._condense_context = fake_condense
        self.engine.render_context(b)
        self.assertEqual(
            called, [], "_condense_context must not be called under soft limit"
        )

    def test_over_soft_limit_triggers_condensation_when_headroom_low(self):
        """Condensation fires when over soft limit AND the window is nearly full."""
        b = self._bundle([self._note(_text_over_soft())])
        called = []

        def fake_condense(text, focus_hint=""):
            called.append(True)
            return "condensed"

        self.engine._condense_context = fake_condense
        # Simulate a nearly-full payload so the headroom passthrough does not apply.
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        self.engine._last_payload_toks = capacity - 100
        self.engine.render_context(b)
        self.assertTrue(
            called,
            "_condense_context must be called when over soft limit and low headroom",
        )

    def test_headroom_passthrough_skips_condensation(self):
        """Bundle over soft limit but with plenty of window headroom → no condensation."""
        b = self._bundle([self._note(_text_over_soft())])
        called = []

        def fake_condense(text, focus_hint=""):
            called.append(True)
            return "condensed"

        self.engine._condense_context = fake_condense
        self.engine._last_payload_toks = 0  # empty history → full headroom available
        result = self.engine.render_context(b)
        self.assertEqual(
            called,
            [],
            "_condense_context must NOT be called when context window has plenty of headroom",
        )
        self.assertGreater(len(result), 0)

    def test_over_hard_limit_truncates_without_condensation(self):
        b = self._bundle([self._note(_text_over_hard())])
        called = []

        def fake_condense(text, focus_hint=""):
            called.append(True)
            return "condensed"

        self.engine._condense_context = fake_condense
        result = self.engine.render_context(b)
        self.assertEqual(
            called, [], "_condense_context must NOT be called above hard limit"
        )
        self.assertLessEqual(_tok(result), CONTEXT_HARD_LIMIT_TOKS)

    def test_plain_string_accepted_without_crash(self):
        result = self.engine.render_context("plain string context")
        self.assertIsInstance(result, str)

    def test_focus_hint_forwarded_to_condense(self):
        b = self._bundle([self._note(_text_over_soft())])
        received_hint = []

        def fake_condense(text, focus_hint=""):
            received_hint.append(focus_hint)
            return "condensed"

        self.engine._condense_context = fake_condense
        # Simulate a nearly-full payload so the headroom passthrough does not apply.
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        self.engine._last_payload_toks = capacity - 100
        self.engine.render_context(b, focus_hint="the iron key")
        self.assertEqual(received_hint, ["the iron key"])


# ---------------------------------------------------------------------------
# 12. _guard_payload -- pre-call context window safety net
# ---------------------------------------------------------------------------


class TestGuardPayload(unittest.TestCase):
    """_guard_payload: payload budget enforcement before every call_llm."""

    def _engine(self):
        return RPJotEngine(location="test-hall", people_present={"player"})

    def _msg(self, role, content):
        return {"role": role, "content": content}

    def _make_big_tool_msg(self, toks):
        """Tool-result message whose content is approximately `toks` tokens."""
        reps = max(1, (toks + 11) // 12)
        return {"role": "tool", "tool_call_id": "x", "content": _CHUNK * reps}

    def test_under_threshold_returns_same_list(self):
        """Well under 85% → same list object returned, no mutation."""
        eng = self._engine()
        msgs = [self._msg("user", "hello")]
        result = eng._guard_payload(msgs)
        self.assertIs(result, msgs)

    def test_at_85pct_returns_same_list(self):
        """At the warning threshold, no mutation — just a log."""
        eng = self._engine()
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        target_toks = int(capacity * 0.87)
        reps = max(1, (target_toks + 11) // 12)
        msgs = [{"role": "user", "content": _CHUNK * reps}]
        result = eng._guard_payload(msgs)
        self.assertIs(result, msgs)

    def test_over_limit_returns_new_list(self):
        """Over capacity → returns a new list (copy), not the original."""
        eng = self._engine()
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        big = self._make_big_tool_msg(capacity + 500)
        msgs = [self._msg("user", "go"), big, self._msg("user", "next")]
        result = eng._guard_payload(msgs)
        self.assertIsNot(result, msgs)

    def test_over_limit_total_fits_in_capacity(self):
        """After reduction the total payload must fit within capacity."""
        eng = self._engine()
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        big = self._make_big_tool_msg(capacity + 500)
        msgs = [self._msg("user", "go"), big, self._msg("user", "next")]
        result = eng._guard_payload(msgs)
        total = sum(_tok(str(m.get("content") or "")) for m in result)
        self.assertLessEqual(total, capacity)

    def test_system_message_never_dropped(self):
        """role=system is always preserved, even when over limit."""
        eng = self._engine()
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        system_content = "You are the narrator."
        big = self._make_big_tool_msg(capacity + 1000)
        msgs = [
            self._msg("system", system_content),
            big,
            self._msg("user", "continue"),
        ]
        result = eng._guard_payload(msgs)
        roles = [m.get("role") for m in result]
        self.assertIn("system", roles)
        self.assertEqual(result[0]["content"], system_content)

    def test_last_message_never_dropped(self):
        """The final message (the active prompt) is always preserved."""
        eng = self._engine()
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        last_content = "what happens next?"
        big = self._make_big_tool_msg(capacity + 1000)
        msgs = [self._msg("user", "go"), big, self._msg("user", last_content)]
        result = eng._guard_payload(msgs)
        self.assertEqual(result[-1]["content"], last_content)

    def test_tool_results_trimmed_before_user_messages_dropped(self):
        """Tool results are reduced before user/assistant history is dropped."""
        eng = self._engine()
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        big_tool = self._make_big_tool_msg(capacity + 500)
        msgs = [
            self._msg("user", "first"),
            big_tool,
            self._msg("user", "continue"),
        ]
        result = eng._guard_payload(msgs)
        roles = [m.get("role") for m in result]
        # user messages should still be present (tool result absorbed the cut)
        self.assertEqual(roles.count("user"), 2)

    # --- W2: tool_calls arguments must be counted (R2) ---

    def _assistant_tool_call_msg(self, arg_toks):
        """Assistant message whose weight lives entirely in tool_calls arguments."""
        reps = max(1, (arg_toks + 11) // 12)
        big_args = json.dumps({"description": _CHUNK * reps, "tags": "exp:player"})
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "record_event", "arguments": big_args},
                }
            ],
        }

    def test_msg_toks_counts_tool_call_arguments(self):
        """_msg_toks includes tool_calls argument JSON, not just content."""
        msg = self._assistant_tool_call_msg(500)
        content_only = _tok(str(msg.get("content") or ""))
        self.assertLess(content_only, 20)  # content is empty
        self.assertGreater(_msg_toks(msg), 400)  # arguments dominate

    def test_guard_counts_tokens_hidden_in_tool_calls(self):
        """A list whose tokens live only in tool_calls crosses 85% and is measured."""
        eng = self._engine()
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        # Arguments sized to ~90% of capacity; content is empty throughout.
        big = self._assistant_tool_call_msg(int(capacity * 0.9))
        msgs = [self._msg("user", "go"), big, self._msg("user", "next")]
        eng._guard_payload(msgs, schema_overhead=0)
        # Old content-only accounting would have measured ~0; the guard must now
        # see the tool_calls payload and record it above the warning threshold.
        self.assertGreater(eng._last_payload_toks, capacity * 0.85)

    # --- W3: pass 2 drops tool-call units atomically (R3) ---

    def test_tool_unit_indices_from_assistant(self):
        """From an assistant with tool_calls, the unit includes all its tool replies."""
        msgs = [
            self._msg("system", "sys"),
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "a", "function": {"name": "f", "arguments": "{}"}},
                    {"id": "b", "function": {"name": "g", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "content": "ra"},
            {"role": "tool", "tool_call_id": "b", "content": "rb"},
            self._msg("user", "u"),
        ]
        self.assertEqual(RPJotEngine._tool_unit_indices(msgs, 1), {1, 2, 3})

    def test_tool_unit_indices_from_tool_finds_parent(self):
        """From a tool reply, the unit walks back to its parent assistant + siblings."""
        msgs = [
            self._msg("system", "sys"),
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "a", "function": {"name": "f", "arguments": "{}"}},
                    {"id": "b", "function": {"name": "g", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "content": "ra"},
            {"role": "tool", "tool_call_id": "b", "content": "rb"},
            self._msg("user", "u"),
        ]
        self.assertEqual(RPJotEngine._tool_unit_indices(msgs, 3), {1, 2, 3})

    def test_pass2_drops_tool_unit_without_orphans(self):
        """Forced over 100% with a tool unit → no orphaned tool/assistant survives."""
        eng = self._engine()
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        digest = {"role": "user", "content": "STORY SO FAR:\nonce upon a time"}
        # Assistant weight lives in tool_calls arguments — pass 1 cannot shed it,
        # forcing pass 2 to drop the whole unit atomically.
        big_asst = self._assistant_tool_call_msg(capacity + 2000)
        big_asst["tool_calls"].append(
            {"id": "call_2", "function": {"name": "record_event", "arguments": "{}"}}
        )
        big_asst["tool_calls"][0]["id"] = "call_1"
        msgs = [
            self._msg("system", "sys"),
            digest,
            big_asst,
            {"role": "tool", "tool_call_id": "call_1", "content": "result one"},
            {"role": "tool", "tool_call_id": "call_2", "content": "result two"},
            self._msg("user", "final prompt"),
        ]
        result = eng._guard_payload(msgs, schema_overhead=0)

        # No orphaned tool messages: each surviving tool has a surviving parent.
        surviving_ids = set()
        for m in result:
            for tc in m.get("tool_calls") or []:
                surviving_ids.add(tc.get("id"))
        for m in result:
            if m.get("role") == "tool":
                self.assertIn(
                    m.get("tool_call_id"),
                    surviving_ids,
                    "orphaned tool message survived without its parent assistant",
                )
        # No orphaned assistant: a surviving tool_calls assistant keeps all replies.
        answered = {m.get("tool_call_id") for m in result if m.get("role") == "tool"}
        for m in result:
            for tc in m.get("tool_calls") or []:
                self.assertIn(
                    tc.get("id"),
                    answered,
                    "assistant tool_calls survived without its tool replies",
                )

    def test_pass2_preserves_final_message_and_digest(self):
        """The final message and the STORY SO FAR digest are never dropped."""
        eng = self._engine()
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        digest = {"role": "user", "content": "STORY SO FAR:\nkey memory preserved"}
        big = self._make_big_tool_msg(capacity + 3000)
        msgs = [
            self._msg("system", "sys"),
            digest,
            big,
            self._msg("assistant", "old reply"),
            self._msg("user", "the active prompt"),
        ]
        result = eng._guard_payload(msgs, schema_overhead=0)
        self.assertEqual(result[-1]["content"], "the active prompt")
        contents = [str(m.get("content") or "") for m in result]
        self.assertTrue(
            any(c.startswith("STORY SO FAR:") for c in contents),
            "compaction digest was dropped by the guard",
        )


class TestSafeDispatch(unittest.TestCase):
    """_safe_dispatch: one bad tool call must never crash the session."""

    def setUp(self):
        self.engine = _make_engine(
            location="ravenwood-manor", people={"player", "alice"}
        )

    def _assert_error_json(self, result):
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIn("error", parsed)
        return parsed

    def test_unknown_tool_returns_error_json(self):
        result = self.engine._dispatch_step2("no_such_tool", "{}")
        self._assert_error_json(result)

    def test_missing_required_arg_returns_error_json(self):
        # record_event requires description + tags; only tags supplied.
        result = self.engine._dispatch_step2("record_event", '{"tags": "exp:player"}')
        parsed = self._assert_error_json(result)
        self.assertIn("description", parsed["error"])

    def test_typoed_kwarg_returns_error_json(self):
        # 'descripton' is a typo → description missing → error, no exception.
        result = self.engine._dispatch_step2(
            "record_event", '{"descripton": "x", "tags": "y"}'
        )
        self._assert_error_json(result)

    def test_unexpected_kwarg_returns_error_json(self):
        # Required present but an extra unknown kwarg → handler TypeError, caught.
        result = self.engine._dispatch_step2(
            "record_event",
            '{"description": "x", "tags": "exp:player", "bogus_extra": 1}',
        )
        self._assert_error_json(result)

    def test_non_json_arguments_returns_error_json(self):
        result = self.engine._dispatch_step2("record_event", "this is not json")
        self._assert_error_json(result)

    def test_handler_internal_exception_returns_error_json(self):
        # description as int → description[:80] raises TypeError inside the handler.
        result = self.engine._dispatch_step2(
            "record_event", '{"description": 123, "tags": "exp:player"}'
        )
        self._assert_error_json(result)

    def test_wrong_type_arg_does_not_raise(self):
        # witnesses as a bare string is a wrong type; dispatch must not raise
        # and must return a JSON string the model can consume.
        result = self.engine._dispatch_step2(
            "record_knowledge",
            '{"content": "a secret", "witnesses": "alice"}',
        )
        self.assertIsInstance(result, str)
        json.loads(result)  # valid JSON, no exception

    def test_step1_dispatch_survives_bad_args(self):
        result = self.engine._safe_dispatch(
            self.engine._step1_handlers, "get_character", "not json"
        )
        self._assert_error_json(result)

    def test_loop_continues_after_a_bad_tool_call(self):
        """A step-2 round that errors must not abort the turn (T4 acceptance)."""
        import rpjot as rpjot_module

        rounds = [
            # Round 1: malformed args → dispatch returns error JSON, loop continues.
            {
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "record_event",
                            "arguments": '{"oops": true}',
                        },
                    }
                ]
            },
            # Round 2: valid record_event.
            {
                "tool_calls": [
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {
                            "name": "record_event",
                            "arguments": json.dumps(
                                {
                                    "description": "the heavy door creaks open",
                                    "tags": "exp:player",
                                }
                            ),
                        },
                    }
                ]
            },
            # Round 3: plain text → exits the loop.
            {"content": "All recorded."},
        ]
        calls = {"i": 0}

        def fake_call_llm(messages, **kwargs):
            r = rounds[calls["i"]]
            calls["i"] += 1
            return r

        original = rpjot_module.call_llm
        rpjot_module.call_llm = fake_call_llm
        try:
            engine = _make_engine(location="ravenwood-manor", people={"player"})
            engine.init_pipeline()
            step2 = [{"role": "system", "content": "rules"}]
            canonical, think = engine._compliance_step.run(
                "[MC action]: I open the door", "WORLD STATE: a door", step2
            )
        finally:
            rpjot_module.call_llm = original

        # Turn completed across all three rounds without raising.
        self.assertEqual(calls["i"], 3)
        # The successful record_event result is present; the errored round is too,
        # but at least one canonical result is a non-error success string.
        joined = " ".join(res for _fn, res in canonical)
        self.assertIn("Event recorded", joined)


# ---------------------------------------------------------------------------
# 12b. History compaction — play.compact_history (W4 / R1, R4b)
# ---------------------------------------------------------------------------


class TestHistoryCompaction(unittest.TestCase):
    """compact_history folds old turns into a bounded STORY SO FAR digest."""

    def _fixed_digest(self, toks=1000):
        reps = max(1, toks // _tok(_CHUNK))
        return _CHUNK * reps

    def _engine_with_stub(self):
        engine = _make_engine(location="ravenwood-manor", people={"player"})
        self.condense_inputs = []

        def stub_condense(raw_text, focus_hint=""):
            self.condense_inputs.append(raw_text)
            return self._fixed_digest()

        engine._condense_context = stub_condense
        return engine

    def _append_turn(self, step2, step3, n):
        # Realistic sizes: classified ~ small, narrative ~ 500 tok.
        classified = f"[MC action]: turn {n} " + ("do a thing. " * 6)
        narrative = f"Narrative for turn {n}. " + (_CHUNK * 38)  # ~500 tok
        for lst in (step2, step3):
            lst.append({"role": "user", "content": classified})
            lst.append({"role": "assistant", "content": narrative})
        return classified, narrative

    def test_50_turn_soak_stays_bounded(self):
        import play

        engine = self._engine_with_stub()
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        step2 = [{"role": "system", "content": "gameplay rules " * 40}]
        step3 = [{"role": "system", "content": "prose craft " * 40}]

        one_turn = 0
        for n in range(50):
            _c, narrative = self._append_turn(step2, step3, n)
            one_turn = max(one_turn, _tok(narrative) + 200)
            play.compact_history(engine, step2, step3)

            # History stays sawtooth-bounded on both lists.
            for lst in (step2, step3):
                self.assertLessEqual(
                    play._history_toks(lst),
                    play.HISTORY_SOFT_TOKS + one_turn,
                    f"history exceeded soft limit + one turn at turn {n}",
                )
            # At most one digest, and if present it sits at index 1.
            for lst in (step2, step3):
                digests = [
                    i
                    for i, m in enumerate(lst)
                    if str(m.get("content", "")).startswith("STORY SO FAR:")
                ]
                self.assertLessEqual(len(digests), 1)
                if digests:
                    self.assertEqual(digests[0], 1)

        # Compaction actually triggered during the run.
        self.assertGreaterEqual(len(self.condense_inputs), 1)

        # Exactly one digest at index 1 by the end.
        for lst in (step2, step3):
            digests = [
                i
                for i, m in enumerate(lst)
                if str(m.get("content", "")).startswith("STORY SO FAR:")
            ]
            self.assertEqual(len(digests), 1)
            self.assertEqual(digests[0], 1)

        # Last KEEP_RECENT_PAIRS pairs survive verbatim.
        keep_msgs = 2 * play.KEEP_RECENT_PAIRS
        tail2 = step2[-keep_msgs:]
        for m in tail2:
            self.assertFalse(str(m.get("content", "")).startswith("STORY SO FAR:"))
        self.assertEqual(tail2[-1]["content"], step3[-1]["content"])

        # The final guard measurement stays comfortably in the <85% tier: the
        # guard returns the same list object unchanged (no trim/drop) and the
        # measured payload is below the warning threshold.
        passed = list(step2)
        eng_check = engine._guard_payload(
            passed, schema_overhead=engine._cached_compact_step2_schema_toks
        )
        self.assertIs(eng_check, passed)
        self.assertLess(engine._last_payload_toks, 0.85 * capacity)

    def test_second_trigger_folds_old_digest_no_stacking(self):
        import play

        engine = self._engine_with_stub()
        step2 = [{"role": "system", "content": "rules"}]
        step3 = [{"role": "system", "content": "prose"}]

        triggers = 0
        prev_inputs = 0
        for n in range(60):
            self._append_turn(step2, step3, n)
            play.compact_history(engine, step2, step3)
            if len(self.condense_inputs) > prev_inputs:
                triggers += 1
                prev_inputs = len(self.condense_inputs)
                # After the first trigger, later triggers must fold the prior
                # digest into their raw input — proving no separate digest stacks.
                if triggers >= 2:
                    self.assertTrue(
                        self.condense_inputs[-1].startswith("STORY SO FAR:"),
                        "second compaction did not fold the previous digest",
                    )

        self.assertGreaterEqual(triggers, 2, "expected ≥2 compactions over 60 turns")
        # Still exactly one digest — never stacked.
        digests = [
            m
            for m in step2
            if str(m.get("content", "")).startswith("STORY SO FAR:")
        ]
        self.assertEqual(len(digests), 1)

    def test_no_op_below_soft_limit(self):
        import play

        engine = self._engine_with_stub()
        step2 = [{"role": "system", "content": "rules"}]
        step3 = [{"role": "system", "content": "prose"}]
        self._append_turn(step2, step3, 0)
        before2 = list(step2)
        play.compact_history(engine, step2, step3)
        self.assertEqual(step2, before2)  # untouched
        self.assertEqual(len(self.condense_inputs), 0)  # no LLM call


# ---------------------------------------------------------------------------
# 12c. Cast-drift detection — RPJotEngine._scan_cast_drift (W5 / T1)
# ---------------------------------------------------------------------------


class TestCastDrift(unittest.TestCase):
    """Named-but-absent NPCs must be detected (never auto-added to the cast)."""

    def _engine(self, people):
        return _make_engine(location="ravenwood-manor", people=people)

    def test_mentioned_but_absent_npc_warns(self):
        eng = self._engine({"player"})
        eng.npc_tracker.register("evie", "Evie", location="ravenwood-manor")
        warnings = eng._scan_cast_drift(
            "[MC action]: I look around", "Evie beckons you closer from the doorway."
        )
        self.assertIn("evie", warnings)
        self.assertIn("evie", eng._cast_warning_line())

    def test_present_npc_no_warning(self):
        eng = self._engine({"player", "evie"})
        eng.npc_tracker.register("evie", "Evie", location="ravenwood-manor")
        warnings = eng._scan_cast_drift(
            "[MC speaks aloud]: hello", "Evie smiles warmly at you."
        )
        self.assertEqual(warnings, [])
        self.assertEqual(eng._cast_warning_line(), "")

    def test_known_absent_but_unmentioned_no_warning(self):
        eng = self._engine({"player"})
        eng.npc_tracker.register("evie", "Evie", location="ravenwood-manor")
        warnings = eng._scan_cast_drift(
            "[MC action]: I sit down", "The room is empty and still."
        )
        self.assertEqual(warnings, [])

    def test_main_character_never_warns(self):
        eng = self._engine({"player"})
        eng.npc_tracker.register(eng.main_character, eng.main_character)
        warnings = eng._scan_cast_drift(
            "[MC action]: I move", f"{eng.main_character} steps into the light."
        )
        self.assertNotIn(eng.main_character, warnings)

    def test_word_boundary_avoids_substring_false_positive(self):
        eng = self._engine({"player"})
        eng.npc_tracker.register("eve", "Eve", location="ravenwood-manor")
        # 'eventually' contains 'eve' but must not match on a word boundary.
        warnings = eng._scan_cast_drift(
            "[MC action]: I wait", "Eventually the clock chimes; nobody appears."
        )
        self.assertEqual(warnings, [])

    def test_warning_clears_when_cast_resolves(self):
        eng = self._engine({"player"})
        eng.npc_tracker.register("evie", "Evie", location="ravenwood-manor")
        eng._scan_cast_drift("x", "Evie appears in the hall.")
        self.assertTrue(eng._cast_warnings)
        eng.session.people_present.add("evie")
        eng._scan_cast_drift("x", "Evie is still here.")
        self.assertEqual(eng._cast_warnings, [])


# ---------------------------------------------------------------------------
# 12d. Zero-canonical nudge — ComplianceStep (W6 / T2)
# ---------------------------------------------------------------------------


class TestZeroCanonicalNudge(unittest.TestCase):
    """An empty-canonical action/dialogue turn gets exactly one corrective round."""

    def _run(self, rounds, classified):
        import rpjot as rpjot_module

        calls = {"i": 0, "msgs": []}

        def fake_call_llm(messages, **kwargs):
            calls["msgs"].append(
                [str(m.get("content") or "") for m in messages]
            )
            r = rounds[min(calls["i"], len(rounds) - 1)]
            calls["i"] += 1
            return r

        original = rpjot_module.call_llm
        rpjot_module.call_llm = fake_call_llm
        try:
            eng = _make_engine(location="ravenwood-manor", people={"player"})
            eng.init_pipeline()
            step2 = [{"role": "system", "content": "rules"}]
            canonical, think = eng._compliance_step.run(
                classified, "WORLD STATE: a room", step2
            )
        finally:
            rpjot_module.call_llm = original
        return canonical, calls

    def _record_event_round(self):
        return {
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "record_event",
                        "arguments": json.dumps(
                            {"description": "opened the door", "tags": "exp:player"}
                        ),
                    },
                }
            ]
        }

    def _injected_nudge(self, calls):
        joined = " ".join(c for msgs in calls["msgs"] for c in msgs if c)
        return "No canonical record was written" in joined

    def test_prose_only_then_record_event_after_nudge(self):
        rounds = [
            {"content": "The door is only a door."},  # prose, no canon
            self._record_event_round(),  # responds to the nudge
            {"content": "All set."},  # exits
        ]
        canonical, calls = self._run(rounds, "[MC action]: I open the door")
        self.assertTrue(canonical)
        self.assertIn("record_event", [fn for fn, _ in canonical])
        self.assertEqual(calls["i"], 3)
        self.assertTrue(self._injected_nudge(calls))

    def test_done_reply_completes_with_zero_canonical(self):
        rounds = [
            {"content": "nothing of note happens"},
            {"content": "DONE"},
        ]
        canonical, calls = self._run(rounds, "[MC speaks aloud]: anyone there?")
        self.assertEqual(canonical, [])
        self.assertEqual(calls["i"], 2)  # initial + single nudge round
        self.assertTrue(self._injected_nudge(calls))

    def test_no_nudge_for_inner_monologue(self):
        rounds = [{"content": "a quiet private thought"}]
        canonical, calls = self._run(
            rounds, "[MC inner monologue — private, unspoken]: I wonder if…"
        )
        self.assertEqual(canonical, [])
        self.assertEqual(calls["i"], 1)  # no nudge
        self.assertFalse(self._injected_nudge(calls))

    def test_no_nudge_for_attention_shift(self):
        rounds = [{"content": "the gaze settles"}]
        canonical, calls = self._run(
            rounds, '[MC attention → "window"]: Shift focus to the window.'
        )
        self.assertEqual(calls["i"], 1)
        self.assertFalse(self._injected_nudge(calls))

    def test_never_nudges_twice(self):
        rounds = [
            {"content": "nope"},
            {"content": "still nothing"},
            {"content": "and nothing again"},
        ]
        canonical, calls = self._run(rounds, "[MC action]: I keep waiting")
        self.assertEqual(canonical, [])
        self.assertEqual(calls["i"], 2)  # nudged once, then returned

    def test_no_nudge_when_canon_already_written(self):
        rounds = [
            self._record_event_round(),  # writes canon on round 1
            {"content": "narrated"},  # exits, canon present → no nudge
        ]
        canonical, calls = self._run(rounds, "[MC action]: I open the door")
        self.assertTrue(canonical)
        self.assertEqual(calls["i"], 2)
        self.assertFalse(self._injected_nudge(calls))


# ---------------------------------------------------------------------------
# 12e. Compact-schema keep-list — _compact_step2_schemas (W7 / T3)
# ---------------------------------------------------------------------------


class TestCompactSchemaKeepList(unittest.TestCase):
    """Critical argument contracts survive step-2 schema compaction."""

    def setUp(self):
        self.engine = _make_engine(location="ravenwood-manor", people={"player"})
        self.compact = {
            s["function"]["name"]: s for s in self.engine._compact_step2_schemas
        }

    def _props(self, tool):
        return self.compact[tool]["function"]["parameters"]["properties"]

    def test_record_event_tags_keeps_grammar(self):
        desc = self._props("record_event")["tags"].get("description", "")
        self.assertIn("exp:", desc)
        self.assertIn("know:", desc)

    def test_record_knowledge_keeps_witness_and_observable_contracts(self):
        props = self._props("record_knowledge")
        self.assertIn("description", props["witnesses"])
        self.assertIn("description", props["observable_act"])

    def test_navigate_and_save_location_keeps_path_grammar(self):
        self.assertIn("description", self._props("navigate_to")["location_name"])
        self.assertIn("description", self._props("save_location")["name"])

    def test_non_keeplist_tool_has_no_param_descriptions(self):
        for tool in ("record_bond", "record_mood", "save_object"):
            for k, pdef in self._props(tool).items():
                self.assertNotIn(
                    "description", pdef, f"{tool}.{k} kept a description"
                )

    def test_compact_budget_under_3000(self):
        self.assertLessEqual(self.engine._cached_compact_step2_schema_toks, 3000)

    def test_compact_smaller_than_full_schema(self):
        self.assertLess(
            self.engine._cached_compact_step2_schema_toks,
            self.engine._cached_schema_toks,
        )

    def test_function_descriptions_match_keep_list(self):
        # Compaction strips ALL function-level descriptions (§3.1) except tools
        # on _COMPACT_KEEP_FUNCTION_DESCRIPTIONS. Assert exactly those carry a
        # description and it matches the map — holds whether the keep-list is
        # empty (default) or populated (the nudge_pos_desc upgrade).
        keep = self.engine._COMPACT_KEEP_FUNCTION_DESCRIPTIONS
        described = {
            name: s["function"]["description"]
            for name, s in self.compact.items()
            if "description" in s["function"]
        }
        self.assertEqual(described, dict(keep))


# ---------------------------------------------------------------------------
# 12f. Sigil classification — play.classify_input (W8d / R4d)
# ---------------------------------------------------------------------------


class TestClassifyInput(unittest.TestCase):
    """The prompt-facing input contract: sigils → explicit directives."""

    def _c(self, raw):
        import play

        return play.classify_input(raw)

    def test_speech_sigil(self):
        out = self._c('"Hello there.')
        self.assertTrue(out.startswith("[MC speaks aloud]"))
        self.assertIn("Hello there.", out)

    def test_action_sigil(self):
        out = self._c("*opens the heavy door")
        self.assertTrue(out.startswith("[MC action]"))
        self.assertIn("opens the heavy door", out)

    def test_attention_sigil_with_tail(self):
        out = self._c("@evie you look nervous")
        self.assertIn("[MC attention", out)
        self.assertIn("evie", out)
        self.assertIn("you look nervous", out)

    def test_attention_sigil_without_tail(self):
        out = self._c("@window")
        self.assertIn("[MC attention", out)
        self.assertIn("window", out)

    def test_inner_monologue_sigil(self):
        out = self._c("^I feel uneasy about this place")
        self.assertIn("[MC inner monologue", out)
        self.assertIn("I feel uneasy about this place", out)
        # The monologue sigil nudges toward backstory-preserving tools.
        self.assertIn("record_conscience", out)

    def test_default_is_dialogue(self):
        out = self._c("hello everyone")
        self.assertIn("[MC — likely spoken aloud", out)
        self.assertIn("hello everyone", out)


# ---------------------------------------------------------------------------
# 12f-bis. Stationary-turn classifier — ComplianceStep._is_stationary_turn (§3.5)
# ---------------------------------------------------------------------------


class TestStationaryClassifier(unittest.TestCase):
    """The sigil→mobility gate that decides whether the stationary nudge injects.

    Pure unit (NO LLM): this gate must be correct without a server, since it
    runs on every step-2 turn in production. True => MC did not move themselves
    => nudge fires; False => baseline (no injection). Mirrors the swept
    bakeoff_navnudge.classify_heuristic, including the "I follow her" collision
    and the fail-open on unrecognized prefixes.
    """

    def _stat(self, classified_input):
        from rpjot import ComplianceStep

        return ComplianceStep._is_stationary_turn(classified_input)

    # --- always-stationary prefixes (spoke / thought / attention) ---
    def test_speech_is_stationary(self):
        self.assertTrue(self._stat('[MC speaks aloud]: "Lead on, then."'))

    def test_likely_spoken_is_stationary(self):
        self.assertTrue(
            self._stat(
                "[MC — likely spoken aloud, interpret as dialogue unless "
                "clearly an action]: lead on"
            )
        )

    def test_inner_monologue_is_stationary(self):
        self.assertTrue(
            self._stat(
                "[MC inner monologue — private, unspoken]: why would she?"
            )
        )

    def test_attention_is_stationary(self):
        self.assertTrue(self._stat('[MC attention → "window"]: look outside'))

    # --- [MC action] resolved by first-person movement verb ---
    def test_action_with_move_verb_is_mobile(self):
        self.assertFalse(
            self._stat(
                "[MC action]: I follow her down the corridor to the drawing room."
            )
        )

    def test_action_climb_is_mobile(self):
        self.assertFalse(self._stat("[MC action]: I climb the stairs to the attic."))

    def test_action_without_move_verb_is_stationary(self):
        self.assertTrue(
            self._stat(
                "[MC action]: I pick up the iron key from the table and pocket it."
            )
        )

    # --- the "I follow her" collision (naive 'starts with I' would misfire) ---
    def test_i_follow_collision_is_mobile(self):
        self.assertFalse(self._stat("[MC action]: I follow her."))

    def test_i_without_move_verb_is_stationary(self):
        self.assertTrue(self._stat("[MC action]: I wait by the door."))

    # --- quoted-dialogue guard: a quote inside an action is not travel ---
    def test_quoted_action_body_is_stationary(self):
        self.assertTrue(self._stat('[MC action]: "I will follow you anywhere."'))

    # --- forced movement: stationary label, must still be overridable ---
    def test_forced_drag_is_stationary(self):
        # No first-person move verb (the MC is dragged, not self-moving), so the
        # nudge FIRES; its escape-hatch wording lets the model navigate anyway.
        self.assertTrue(
            self._stat(
                "[MC action]: I dig in my heels but the guards seize my arms "
                "and drag me down to the cells."
            )
        )

    def test_forced_carriage_speech_is_stationary(self):
        self.assertTrue(
            self._stat(
                '[MC speaks aloud]: "Where are you taking me?" The carriage '
                "rolls on, carrying you through the city gates."
            )
        )

    # --- fail-open: unrecognized / missing prefix → NOT stationary (baseline) ---
    def test_unrecognized_prefix_fails_open(self):
        self.assertFalse(self._stat("[MC teleports]: zap to the tower"))

    def test_no_prefix_fails_open(self):
        self.assertFalse(self._stat("just some raw text with go and walk in it"))

    def test_empty_and_none_fail_open(self):
        self.assertFalse(self._stat(""))
        self.assertFalse(self._stat(None))


# ---------------------------------------------------------------------------
# 12g. Production-shape tool activation — LLM-gated (W9 / T6, D9)
# ---------------------------------------------------------------------------


class TestProductionActivation(unittest.TestCase):
    """Phrase → tool selection in the real step-partitioned, compact-schema shape.

    Unlike TestToolDispatch (all 42 flat schemas, generic GM prompt, one pass),
    these assert selection under the exact production menus and system prompts.
    LLM-gated (skipped without openai_api_url) and stochastic: each phrase runs
    N=3 with a ≥2/3 pass threshold (D9).
    """

    def setUp(self):
        self.engine = _make_engine(
            location="ravenwood-manor", people={"player", "evie"}
        )

    def _passes(self, fn, n=3, need=2):
        return sum(1 for _ in range(n) if fn()) >= need

    def _step1_tools_for(self, phrase):
        from rpjot import _STEP1_SYSTEM
        from catjot import call_llm

        messages = [
            {"role": "system", "content": _STEP1_SYSTEM},
            {"role": "user", "content": f"PLAYER INPUT:\n{phrase}"},
        ]
        resp = call_llm(
            messages, tools=self.engine._step1_schemas, tool_choice="auto"
        )
        return [tc["function"]["name"] for tc in resp.get("tool_calls") or []]

    def _step2_tools_for(self, phrase, world_doc=None):
        from catjot import ContextBundle, call_llm
        from rpjot import ComplianceStep

        if world_doc is None:
            world_doc = "WORLD STATE: you stand in the manor foyer with Evie."
        rules = str(ContextBundle("system_role")).strip() or (
            "You are the game master. Use tools to record canon."
        )
        # Route through the exact production composition — WORLD STATE → NARRATOR
        # RULE → input → conditional stationary nudge — so the paired tests below
        # exercise precisely what ComplianceStep.run sends the model.
        content = ComplianceStep(self.engine)._compose_step2_user_content(
            phrase, world_doc
        )
        messages = [
            {"role": "system", "content": rules},
            {"role": "user", "content": content},
        ]
        resp = call_llm(
            messages,
            tools=self.engine._compact_step2_schemas,
            tool_choice="auto",
        )
        return [tc["function"]["name"] for tc in resp.get("tool_calls") or []]

    # --- Step 1 perception ---

    def test_who_is_here_selects_get_people_present(self):
        self.assertTrue(
            self._passes(
                lambda: "get_people_present"
                in self._step1_tools_for("Who is here with me?")
            )
        )

    # --- Step 2 consent: negative + positive pair ---

    def test_npc_invitation_does_not_select_navigate_to(self):
        phrase = (
            '[MC speaks aloud]: "Lead on, then." '
            "Evie beckons you to follow her down the corridor."
        )
        self.assertTrue(
            self._passes(
                lambda: "navigate_to" not in self._step2_tools_for(phrase)
            )
        )

    def test_player_movement_selects_navigate_to(self):
        phrase = "[MC action]: I follow her down the corridor to the drawing room."
        self.assertTrue(
            self._passes(
                lambda: "navigate_to" in self._step2_tools_for(phrase)
            )
        )

    def test_forced_movement_still_selects_navigate_to(self):
        # Guards drag the MC: the turn classifies STATIONARY (the nudge fires,
        # via _step2_tools_for's production composition), yet the scene must
        # still move. This is the anti-hard-gate guarantee — a hard tool-gate
        # would make navigate_to impossible here; the soft, overridable nudge
        # lets the model navigate when events physically carry the MC.
        phrase = (
            "[MC action]: I dig in my heels but the guards seize my arms "
            "and drag me down to the cells."
        )
        from rpjot import ComplianceStep

        self.assertTrue(
            ComplianceStep._is_stationary_turn(phrase),
            "phrase must classify stationary for this to test the override",
        )
        self.assertTrue(
            self._passes(
                lambda: "navigate_to" in self._step2_tools_for(phrase)
            )
        )

    # --- Step 2 cast membership ---

    def test_arrival_selects_set_people_present(self):
        phrase = (
            "[MC action]: I open the front door; a butler steps inside and "
            "introduces himself, joining me in the foyer."
        )
        world_doc = (
            "WORLD STATE: you are alone in the foyer until the butler arrives."
        )
        self.assertTrue(
            self._passes(
                lambda: "set_people_present"
                in self._step2_tools_for(phrase, world_doc)
            )
        )

    # --- Step 2 new-NPC persistence ---

    def test_new_npc_selects_save_character(self):
        phrase = (
            '[MC speaks aloud]: "And who might you be?" '
            "The gardener introduces himself as Tomas."
        )
        world_doc = (
            "WORLD STATE: a new character, a grizzled gardener named Tomas, "
            "has just been introduced.\n[NO CHARACTER NOTES ON FILE FOR]: tomas"
        )
        self.assertTrue(
            self._passes(
                lambda: "save_character"
                in self._step2_tools_for(phrase, world_doc)
            )
        )

    # --- Step 2 event canon ---

    def test_clear_action_selects_record_event(self):
        phrase = "[MC action]: I pick up the iron key from the table and pocket it."
        self.assertTrue(
            self._passes(
                lambda: "record_event" in self._step2_tools_for(phrase)
            )
        )


# ---------------------------------------------------------------------------
# 12h. Prose/tool seams — synthesis + directive prefixes (W11 / T5)
# ---------------------------------------------------------------------------


class TestSynthesisSeams(unittest.TestCase):
    """Tool results must never leak raw dicts (braces) into the prose synthesis."""

    def setUp(self):
        self.engine = _make_engine(location="ravenwood-manor", people={"player"})

    def test_record_bond_summary_has_no_braces(self):
        result = json.dumps(
            {"char_a": "evie", "char_b": "player", "bond_type": "trust"}
        )
        summary = RPJotEngine._summarize_write_result("record_bond", result)
        self.assertNotIn("{", summary)
        self.assertNotIn("}", summary)

    def test_generic_rel_int_tool_summaries_have_no_braces(self):
        # Tools without a bespoke branch used to render str(dict) with braces.
        cases = {
            "record_secret": {"character": "evie", "status": "recorded"},
            "record_lie": {"liar": "evie", "target": "player", "status": "recorded"},
            "record_leverage": {
                "holder": "evie",
                "subject": "player",
                "status": "recorded",
            },
            "record_unspoken": {"character": "evie", "status": "recorded"},
        }
        for fn, payload in cases.items():
            with self.subTest(tool=fn):
                summary = RPJotEngine._summarize_write_result(
                    fn, json.dumps(payload)
                )
                self.assertNotIn("{", summary)
                self.assertNotIn("}", summary)
                self.assertTrue(summary.strip())

    def test_full_synthesis_contains_no_braces(self):
        canonical = [
            ("record_secret", json.dumps({"character": "evie", "status": "recorded"})),
            (
                "record_bond",
                json.dumps(
                    {"char_a": "evie", "char_b": "player", "bond_type": "wary"}
                ),
            ),
            (
                "record_jealousy",
                json.dumps({"character": "player", "status": "recorded"}),
            ),
        ]
        synthesis = self.engine._build_narrative_synthesis([], canonical)
        self.assertNotIn("{", synthesis)
        self.assertNotIn("}", synthesis)

    def test_followup_injection_is_directive_prefixed(self):
        import rpjot as rpjot_module

        rounds = [
            {
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "record_event", "arguments": "{}"},
                    }
                ]
            },
            {"content": "done"},
        ]
        calls = {"i": 0, "seen": []}

        def fake_call_llm(messages, **kwargs):
            calls["seen"].extend(str(m.get("content") or "") for m in messages)
            r = rounds[min(calls["i"], len(rounds) - 1)]
            calls["i"] += 1
            return r

        eng = _make_engine(location="ravenwood-manor", people={"player"})
        eng.init_pipeline()
        # Force the dispatched result to carry a followup instruction.
        eng._dispatch_step2 = lambda name, args: json.dumps(
            {"ok": True, "followup_instruction": "gather more before narrating"}
        )
        original = rpjot_module.call_llm
        rpjot_module.call_llm = fake_call_llm
        try:
            step2 = [{"role": "system", "content": "rules"}]
            eng._compliance_step.run("[MC action]: I act", "WORLD", step2)
        finally:
            rpjot_module.call_llm = original

        self.assertTrue(
            any(
                c.startswith("[DIRECTIVE] gather more before narrating")
                for c in calls["seen"]
            ),
            "followup instruction was not [DIRECTIVE]-prefixed",
        )

    def test_zero_canonical_nudge_is_directive_prefixed(self):
        from rpjot import ComplianceStep

        self.assertTrue(
            ComplianceStep._ZERO_CANONICAL_NUDGE.startswith("[DIRECTIVE] ")
        )


# ---------------------------------------------------------------------------
# 12i. Hygiene — cache cap, honest guard message (W12 / R6)
# ---------------------------------------------------------------------------


class TestHygiene(unittest.TestCase):
    """R6: bounded query cache and an honest 90-99% guard message."""

    def test_query_cache_fifo_eviction(self):
        from rpjot import _QUERY_CACHE_MAX

        eng = _make_engine(location="ravenwood-manor", people={"player"})
        for i in range(_QUERY_CACHE_MAX + 25):
            eng._cache_put(f"k{i}", "value")
        cache = eng.session._query_cache
        self.assertLessEqual(len(cache), _QUERY_CACHE_MAX)
        self.assertNotIn("k0", cache)  # oldest evicted
        self.assertIn(f"k{_QUERY_CACHE_MAX + 24}", cache)  # newest kept

    def test_query_cache_update_existing_does_not_grow(self):
        from rpjot import _QUERY_CACHE_MAX

        eng = _make_engine(location="ravenwood-manor", people={"player"})
        for i in range(_QUERY_CACHE_MAX):
            eng._cache_put(f"k{i}", "v")
        self.assertEqual(len(eng.session._query_cache), _QUERY_CACHE_MAX)
        eng._cache_put("k0", "updated")  # existing key
        self.assertEqual(len(eng.session._query_cache), _QUERY_CACHE_MAX)
        self.assertEqual(eng.session._query_cache["k0"], "updated")

    def _user_msg_at_pct(self, low_pct):
        cap = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        low = low_pct * cap
        reps = int(low / 12)
        while _msg_toks({"role": "user", "content": _CHUNK * reps}) < low:
            reps += 5
        return {"role": "user", "content": _CHUNK * reps}

    def test_guard_90pct_history_only_logs_no_trimmable(self):
        eng = _make_engine(location="ravenwood-manor", people={"player"})
        cap = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        big = self._user_msg_at_pct(0.92)
        msgs = [big, {"role": "user", "content": "the active prompt"}]
        with self.assertLogs("rpjot_engine", level="WARNING") as cm:
            eng._guard_payload(msgs, schema_overhead=0)
        joined = " ".join(cm.output)
        # Precondition: the payload landed in the 90-99% band.
        self.assertLess(eng._last_payload_toks, cap)
        self.assertGreaterEqual(eng._last_payload_toks, 0.90 * cap)
        # No tool messages exist to trim → honest message, not a false "trimming".
        self.assertIn("no trimmable tool results", joined)


# ---------------------------------------------------------------------------
# 12j. Token panel — engine.history_report (W10 / R5)
# ---------------------------------------------------------------------------


class TestHistoryReport(unittest.TestCase):
    """The REPL token panel exposes the number that actually grows: history."""

    def setUp(self):
        self.engine = _make_engine(location="ravenwood-manor", people={"player"})

    def test_report_contains_expected_fields(self):
        step2 = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "STORY SO FAR:\nearlier events"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "there"},
        ]
        step3 = [{"role": "system", "content": "prose"}]
        report = self.engine.history_report(step2, step3, avg_pair_toks=640)
        self.assertIn("TOKEN BUDGET", report)
        self.assertIn("step2 history", report)
        self.assertIn("step3 history", report)
        self.assertIn("schema ovh", report)
        self.assertIn("last payload", report)
        self.assertIn("turns until 85%", report)

    def test_digest_presence_reported_per_history(self):
        step2 = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "STORY SO FAR:\nx"},
        ]
        step3 = [{"role": "system", "content": "prose"}]
        report = self.engine.history_report(step2, step3)
        # step2 has a digest, step3 does not.
        self.assertIn("digest present: yes", report)
        self.assertIn("digest present: no", report)

    def test_no_avg_gives_placeholder_estimate(self):
        step2 = [{"role": "system", "content": "rules"}]
        step3 = [{"role": "system", "content": "prose"}]
        report = self.engine.history_report(step2, step3, avg_pair_toks=None)
        self.assertIn("need ≥1 completed turn", report)


# ---------------------------------------------------------------------------
# 12k. Resume-time digest seeding — play.seed_digest_from_summaries (S1 / D10)
# ---------------------------------------------------------------------------


class TestResumeDigestSeeding(unittest.TestCase):
    """On resume, a STORY SO FAR digest is seeded from /summaries notes."""

    def setUp(self):
        import tempfile
        from rpjot import PWD_SUMMARIES

        self._saved_notefile = Note.NOTEFILE
        fd, self._path = tempfile.mkstemp(suffix=".jot")
        os.close(fd)
        Note.NOTEFILE = self._path
        for i in range(20):
            note = Note.jot(
                message=f"Turn {i}: something happens in the manor.",
                tag="summary",
                context=f"turn {i}",
                pwd=PWD_SUMMARIES,
            )
            Note.append(Note.NOTEFILE, note)

    def tearDown(self):
        Note.NOTEFILE = self._saved_notefile
        try:
            os.remove(self._path)
        except OSError:
            pass

    def _engine(self):
        engine = _make_engine(location="ravenwood-manor", people={"player"})
        engine._condense_context = lambda raw, focus_hint="": "distilled recap here"
        return engine

    def test_seed_installs_digest_at_index_1(self):
        import play

        engine = self._engine()
        step2 = [{"role": "system", "content": "rules"}]
        step3 = [{"role": "system", "content": "prose"}]
        play.seed_digest_from_summaries(engine, step2, step3)
        self.assertTrue(step2[1]["content"].startswith("STORY SO FAR:"))
        self.assertIn("distilled recap here", step2[1]["content"])
        self.assertTrue(step3[1]["content"].startswith("STORY SO FAR:"))
        # System message stays at index 0.
        self.assertEqual(step2[0]["role"], "system")

    def test_seeded_digest_is_recognized_by_compaction(self):
        import play

        engine = self._engine()
        step2 = [{"role": "system", "content": "rules"}]
        step3 = [{"role": "system", "content": "prose"}]
        play.seed_digest_from_summaries(engine, step2, step3)
        # _split_history must fold, not stack, the seeded digest.
        _head, prefix, _body = play._split_history(step2)
        self.assertTrue(prefix.startswith("STORY SO FAR:"))

    def test_no_summaries_is_noop(self):
        import play
        from rpjot import PWD_SUMMARIES

        # Point at an empty notefile with no summaries.
        import tempfile

        saved = Note.NOTEFILE
        fd, empty = tempfile.mkstemp(suffix=".jot")
        os.close(fd)
        Note.NOTEFILE = empty
        try:
            engine = self._engine()
            step2 = [{"role": "system", "content": "rules"}]
            step3 = [{"role": "system", "content": "prose"}]
            play.seed_digest_from_summaries(engine, step2, step3)
            self.assertEqual(len(step2), 1)  # untouched
            self.assertEqual(len(step3), 1)
        finally:
            Note.NOTEFILE = saved
            os.remove(empty)


# ---------------------------------------------------------------------------
# 13. _condense_context -- live LLM + fallback behavior
# ---------------------------------------------------------------------------


class TestCondenseContext(unittest.TestCase):
    """_condense_context: live LLM distillation and exception fallback."""

    def setUp(self):
        self.engine = RPJotEngine(location="test-hall", people_present={"player"})

    def _over_limit_text(self, extra_toks=200):
        return _text_over_soft(extra_toks)

    def test_returns_nonempty_string(self):
        result = self.engine._condense_context(self._over_limit_text())
        self.assertTrue(result.strip())

    def test_returns_shorter_than_input(self):
        raw = self._over_limit_text()
        result = self.engine._condense_context(raw)
        self.assertLess(_tok(result), _tok(raw))

    def test_fallback_on_llm_failure(self):
        import rpjot as rpjot_module

        original = rpjot_module.call_llm

        def boom(msgs, **kwargs):
            raise RuntimeError("simulated LLM failure")

        rpjot_module.call_llm = boom
        try:
            result = self.engine._condense_context(_over_limit_text())
            self.assertIsInstance(result, str)
            self.assertLessEqual(_tok(result), CONTEXT_MAX_TOKS)
        finally:
            rpjot_module.call_llm = original

    def test_focus_hint_appears_in_prompt(self):
        import rpjot as rpjot_module

        captured = []
        original = rpjot_module.call_llm

        def capture(msgs, **kwargs):
            captured.extend(msgs)
            return {"content": "condensed result"}

        rpjot_module.call_llm = capture
        try:
            self.engine._condense_context("some context text", focus_hint="alice")
        finally:
            rpjot_module.call_llm = original

        all_content = " ".join(m.get("content", "") for m in captured)
        self.assertIn("alice", all_content)

    def test_empty_llm_response_falls_back_to_truncation(self):
        import rpjot as rpjot_module

        original = rpjot_module.call_llm

        def empty_response(msgs, **kwargs):
            return {"content": ""}

        rpjot_module.call_llm = empty_response
        try:
            raw = _text_over_soft()
            result = self.engine._condense_context(raw)
            self.assertLessEqual(_tok(result), CONTEXT_MAX_TOKS)
        finally:
            rpjot_module.call_llm = original


# ---------------------------------------------------------------------------
# 13. gather_pov_context -- unit, per-character knowledge gaps
# ---------------------------------------------------------------------------


class TestGatherPovContext(unittest.TestCase):
    """gather_pov_context respects know: and exp: tags for knowledge gaps."""

    def setUp(self):
        Note.NOTEFILE = TMP_CATNOTE
        open(
            TMP_CATNOTE, "w"
        ).close()  # ensure file exists before ContextBundle reads it
        self.engine = RPJotEngine(location="manor", people_present={"alice", "mc"})

        Note.append(
            TMP_CATNOTE,
            Note.jot(
                message="Alice knows the location of the secret door.",
                tag="know:alice",
                context="alice private knowledge",
                pwd="/story/character/alice",
            ),
        )
        Note.append(
            TMP_CATNOTE,
            Note.jot(
                message="Alice and MC both witnessed the ghost in the hall.",
                tag="exp:alice exp:mc",
                context="shared experience",
                pwd="/story/events/manor",
            ),
        )

    def tearDown(self):
        import os

        try:
            os.remove(TMP_CATNOTE)
        except FileNotFoundError:
            pass
        Note.NOTEFILE = FIXED_CATNOTE

    def test_returns_context_bundle(self):
        self.assertIsInstance(self.engine.gather_pov_context("alice"), ContextBundle)

    def test_alice_pov_includes_private_knowledge(self):
        bundle = self.engine.gather_pov_context("alice")
        messages = [n.message for n in bundle]
        self.assertTrue(
            any("secret door" in m for m in messages),
            "Alice's POV must include her know:alice note",
        )

    def test_mc_pov_excludes_alice_private_knowledge(self):
        bundle = self.engine.gather_pov_context("mc")
        messages = [n.message for n in bundle]
        self.assertFalse(
            any("secret door" in m for m in messages),
            "MC's POV must NOT contain Alice's know:alice note",
        )

    def test_alice_pov_includes_shared_experience(self):
        bundle = self.engine.gather_pov_context("alice")
        messages = [n.message for n in bundle]
        self.assertTrue(
            any("ghost" in m for m in messages),
            "Alice's POV must include the shared exp: note",
        )

    def test_mc_pov_includes_shared_experience(self):
        bundle = self.engine.gather_pov_context("mc")
        messages = [n.message for n in bundle]
        self.assertTrue(
            any("ghost" in m for m in messages),
            "MC's POV must include the shared exp: note",
        )


# ---------------------------------------------------------------------------
# 14. _tool_prepare_context -- unit tests
# ---------------------------------------------------------------------------


class TestPrepareContextTool(unittest.TestCase):
    """_tool_prepare_context: JSON shape, session alignment, tool registration."""

    def setUp(self):
        self.engine = _make_engine(
            location="test-hall",
            people={"alice", "mc"},
        )

    def _parsed(self, focus_hint=""):
        return json.loads(self.engine._tool_prepare_context(focus_hint=focus_hint))

    def test_registered_as_tool(self):
        names = {s["function"]["name"] for s in self.engine._tool_schemas}
        self.assertIn("prepare_context", names)

    def test_returns_valid_json(self):
        result = self.engine._tool_prepare_context()
        self.assertIsInstance(json.loads(result), dict)

    def test_has_required_keys(self):
        parsed = self._parsed()
        for key in (
            "location",
            "people_present",
            "shared_context",
            "character_contexts",
            "followup_instruction",
        ):
            with self.subTest(key=key):
                self.assertIn(key, parsed)

    def test_location_matches_session(self):
        self.assertEqual(self._parsed()["location"], self.engine.session.location)

    def test_people_present_matches_session(self):
        self.assertEqual(
            set(self._parsed()["people_present"]),
            self.engine.session.people_present,
        )

    def test_character_contexts_keys_match_people_present(self):
        self.assertEqual(
            set(self._parsed()["character_contexts"].keys()),
            self.engine.session.people_present,
        )

    def test_shared_context_is_string(self):
        self.assertIsInstance(self._parsed()["shared_context"], str)

    def test_character_context_values_are_strings(self):
        for name, ctx in self._parsed()["character_contexts"].items():
            with self.subTest(character=name):
                self.assertIsInstance(ctx, str)

    def test_followup_instruction_is_nonempty_string(self):
        instruction = self._parsed()["followup_instruction"]
        self.assertIsInstance(instruction, str)
        self.assertTrue(instruction.strip())

    def test_no_focus_hint_does_not_crash(self):
        self.assertIsInstance(self._parsed(), dict)

    def test_with_focus_hint_does_not_crash(self):
        self.assertIsInstance(self._parsed(focus_hint="alice"), dict)

    def test_dispatches_correctly_by_name(self):
        result = self.engine._dispatch("prepare_context", "{}")
        self.assertIsInstance(json.loads(result), dict)


# ---------------------------------------------------------------------------
# 15. Knowledge-gap scenario: three actors, playing cards, bluffs
# ---------------------------------------------------------------------------


class TestKnowledgeGapScenario(unittest.TestCase):
    """Three actors (left, mid, right) stand in a line, each holding one card.

    Phase 1 — Private  : each actor holds their card face-down (know: tag only).
    Phase 2 — Adjacency: each actor tilts their card toward their neighbor so
                         only adjacent actors can see it (exp:X exp:Y pair tag).
                         left ↔ mid ↔ right   (left and right cannot see each other)
    Phase 3 — Bluff    : each actor calls out a false card aloud; all three hear
                         every statement (exp:left exp:mid exp:right on all bluffs).

    Expected knowledge after all three phases
    ─────────────────────────────────────────
    left : own real card ✓ | mid real card ✓ | right real card ✗
           hears all three bluffs ✓
    mid  : all three real cards ✓ | all three bluffs ✓  (can identify every lie)
    right: own real card ✓ | mid real card ✓ | left real card ✗
           hears all three bluffs ✓
    """

    CARDS = {
        "left": "Ace of Spades",
        "mid": "King of Hearts",
        "right": "Queen of Diamonds",
    }
    BLUFFS = {
        "left": "Two of Clubs",
        "mid": "Five of Hearts",
        "right": "Seven of Spades",
    }

    def setUp(self):
        Note.NOTEFILE = TMP_CATNOTE
        open(
            TMP_CATNOTE, "w"
        ).close()  # ensure file exists before ContextBundle reads it
        self.engine = RPJotEngine(
            location="card-table",
            people_present={"left", "mid", "right"},
        )

        # Phase 1: private card knowledge — each actor knows only their own card
        for actor, card in self.CARDS.items():
            Note.append(
                TMP_CATNOTE,
                Note.jot(
                    message=f"{actor} is holding the {card}.",
                    tag=f"know:{actor}",
                    context=f"{actor} private card knowledge",
                    pwd=f"/story/character/{actor}",
                ),
            )

        # Phase 2: left and mid tilt toward each other — both see each other's card
        Note.append(
            TMP_CATNOTE,
            Note.jot(
                message=(
                    f"left and mid hold their cards out toward each other. "
                    f"left's card is the {self.CARDS['left']}; "
                    f"mid's card is the {self.CARDS['mid']}."
                ),
                tag="exp:left exp:mid",
                context="adjacent card reveal: left and mid",
                pwd="/story/events/card-table",
            ),
        )

        # Phase 2: mid and right tilt toward each other — both see each other's card
        Note.append(
            TMP_CATNOTE,
            Note.jot(
                message=(
                    f"mid and right hold their cards out toward each other. "
                    f"mid's card is the {self.CARDS['mid']}; "
                    f"right's card is the {self.CARDS['right']}."
                ),
                tag="exp:mid exp:right",
                context="adjacent card reveal: mid and right",
                pwd="/story/events/card-table",
            ),
        )

        # Phase 3: public bluff statements — all three actors hear every claim
        for actor, bluff in self.BLUFFS.items():
            Note.append(
                TMP_CATNOTE,
                Note.jot(
                    message=f"{actor} announces to the group: 'My card is the {bluff}.'",
                    tag="exp:left exp:mid exp:right",
                    context=f"public bluff by {actor}",
                    pwd="/story/events/card-table",
                ),
            )

    def tearDown(self):
        import os

        try:
            os.remove(TMP_CATNOTE)
        except FileNotFoundError:
            pass
        Note.NOTEFILE = FIXED_CATNOTE

    def _msgs(self, actor):
        """All message strings visible from actor's point of view."""
        return [n.message for n in self.engine.gather_pov_context(actor)]

    # ── Phase 1: each actor knows their own real card ─────────────────────────

    def test_left_knows_own_real_card(self):
        self.assertTrue(any(self.CARDS["left"] in m for m in self._msgs("left")))

    def test_mid_knows_own_real_card(self):
        self.assertTrue(any(self.CARDS["mid"] in m for m in self._msgs("mid")))

    def test_right_knows_own_real_card(self):
        self.assertTrue(any(self.CARDS["right"] in m for m in self._msgs("right")))

    # ── Phase 2: adjacent actors see each other's cards ───────────────────────

    def test_left_sees_mid_real_card(self):
        self.assertTrue(any(self.CARDS["mid"] in m for m in self._msgs("left")))

    def test_mid_sees_left_real_card(self):
        self.assertTrue(any(self.CARDS["left"] in m for m in self._msgs("mid")))

    def test_mid_sees_right_real_card(self):
        self.assertTrue(any(self.CARDS["right"] in m for m in self._msgs("mid")))

    def test_right_sees_mid_real_card(self):
        self.assertTrue(any(self.CARDS["mid"] in m for m in self._msgs("right")))

    # ── Knowledge gap: ends cannot see across to the far actor ────────────────

    def test_left_cannot_see_right_real_card(self):
        # "announces" lines carry only the bluff card, never right's real card
        direct = [
            m
            for m in self._msgs("left")
            if self.CARDS["right"] in m and "announces" not in m
        ]
        self.assertEqual(
            direct,
            [],
            f"Left must not have direct knowledge of right's real card; found: {direct}",
        )

    def test_right_cannot_see_left_real_card(self):
        direct = [
            m
            for m in self._msgs("right")
            if self.CARDS["left"] in m and "announces" not in m
        ]
        self.assertEqual(
            direct,
            [],
            f"Right must not have direct knowledge of left's real card; found: {direct}",
        )

    # ── Phase 3: all bluff statements are audible to every actor ──────────────

    def test_left_hears_own_bluff(self):
        self.assertTrue(any(self.BLUFFS["left"] in m for m in self._msgs("left")))

    def test_left_hears_mid_bluff(self):
        self.assertTrue(any(self.BLUFFS["mid"] in m for m in self._msgs("left")))

    def test_left_hears_right_bluff(self):
        self.assertTrue(any(self.BLUFFS["right"] in m for m in self._msgs("left")))

    def test_mid_hears_left_bluff(self):
        self.assertTrue(any(self.BLUFFS["left"] in m for m in self._msgs("mid")))

    def test_mid_hears_own_bluff(self):
        self.assertTrue(any(self.BLUFFS["mid"] in m for m in self._msgs("mid")))

    def test_mid_hears_right_bluff(self):
        self.assertTrue(any(self.BLUFFS["right"] in m for m in self._msgs("mid")))

    def test_right_hears_left_bluff(self):
        self.assertTrue(any(self.BLUFFS["left"] in m for m in self._msgs("right")))

    def test_right_hears_mid_bluff(self):
        self.assertTrue(any(self.BLUFFS["mid"] in m for m in self._msgs("right")))

    def test_right_hears_own_bluff(self):
        self.assertTrue(any(self.BLUFFS["right"] in m for m in self._msgs("right")))

    # ── Mid omniscience: knows all real cards AND all bluffs ──────────────────

    def test_mid_knows_all_real_cards(self):
        msgs = self._msgs("mid")
        for actor, card in self.CARDS.items():
            with self.subTest(actor=actor):
                self.assertTrue(
                    any(card in m for m in msgs),
                    f"Mid should know {actor}'s real card ({card})",
                )

    def test_mid_knows_all_bluffs(self):
        msgs = self._msgs("mid")
        for actor, bluff in self.BLUFFS.items():
            with self.subTest(actor=actor):
                self.assertTrue(
                    any(bluff in m for m in msgs),
                    f"Mid should hear {actor}'s stated bluff ({bluff})",
                )

    def test_mid_can_detect_left_lie(self):
        msgs = self._msgs("mid")
        self.assertTrue(
            any(self.CARDS["left"] in m for m in msgs),
            "Mid must know left's real card to identify the lie",
        )
        self.assertTrue(
            any(self.BLUFFS["left"] in m for m in msgs),
            "Mid must also know left's stated bluff",
        )

    def test_mid_can_detect_right_lie(self):
        msgs = self._msgs("mid")
        self.assertTrue(
            any(self.CARDS["right"] in m for m in msgs),
            "Mid must know right's real card to identify the lie",
        )
        self.assertTrue(
            any(self.BLUFFS["right"] in m for m in msgs),
            "Mid must also know right's stated bluff",
        )

    # ── End actors: correct about mid's real card, wrong about far actor ───────

    def test_left_knows_mid_real_card_not_mid_bluff_card(self):
        msgs = self._msgs("left")
        self.assertTrue(
            any(self.CARDS["mid"] in m for m in msgs),
            "Left should know mid's real card via adjacency",
        )
        # The bluff card is a different value — confirm they differ so the test is meaningful
        self.assertNotEqual(self.CARDS["mid"], self.BLUFFS["mid"])

    def test_right_knows_mid_real_card_not_mid_bluff_card(self):
        msgs = self._msgs("right")
        self.assertTrue(
            any(self.CARDS["mid"] in m for m in msgs),
            "Right should know mid's real card via adjacency",
        )
        self.assertNotEqual(self.CARDS["mid"], self.BLUFFS["mid"])

    def test_left_is_wrong_about_right_knows_only_bluff(self):
        msgs = self._msgs("left")
        # Left hears the bluff
        self.assertTrue(
            any(self.BLUFFS["right"] in m for m in msgs),
            f"Left should hear right's bluff ({self.BLUFFS['right']})",
        )
        # Left has no note giving right's real card
        direct_real = [
            m for m in msgs if self.CARDS["right"] in m and "announces" not in m
        ]
        self.assertEqual(
            direct_real,
            [],
            f"Left should not know right's real card ({self.CARDS['right']}), only the bluff",
        )

    def test_right_is_wrong_about_left_knows_only_bluff(self):
        msgs = self._msgs("right")
        self.assertTrue(
            any(self.BLUFFS["left"] in m for m in msgs),
            f"Right should hear left's bluff ({self.BLUFFS['left']})",
        )
        direct_real = [
            m for m in msgs if self.CARDS["left"] in m and "announces" not in m
        ]
        self.assertEqual(
            direct_real,
            [],
            f"Right should not know left's real card ({self.CARDS['left']}), only the bluff",
        )

    # ── build_scene_context_map reflects the same isolations ──────────────────

    def test_scene_map_has_all_three_actors(self):
        ctx_map = self.engine.build_scene_context_map()
        for actor in ("left", "mid", "right"):
            with self.subTest(actor=actor):
                self.assertIn(actor, ctx_map)

    def test_scene_map_mid_contains_all_real_cards(self):
        mid_ctx = self.engine.build_scene_context_map()["mid"]
        for actor, card in self.CARDS.items():
            with self.subTest(actor=actor):
                self.assertIn(
                    card,
                    mid_ctx,
                    f"Mid's context map entry should contain {actor}'s real card ({card})",
                )

    def test_scene_map_left_lacks_right_real_card(self):
        left_ctx = self.engine.build_scene_context_map()["left"]
        non_bluff = "\n".join(
            line for line in left_ctx.splitlines() if "announces" not in line
        )
        self.assertNotIn(
            self.CARDS["right"],
            non_bluff,
            f"Left's context map entry must not contain right's real card ({self.CARDS['right']})",
        )

    def test_scene_map_right_lacks_left_real_card(self):
        right_ctx = self.engine.build_scene_context_map()["right"]
        non_bluff = "\n".join(
            line for line in right_ctx.splitlines() if "announces" not in line
        )
        self.assertNotIn(
            self.CARDS["left"],
            non_bluff,
            f"Right's context map entry must not contain left's real card ({self.CARDS['left']})",
        )


# ---------------------------------------------------------------------------
# 16. Asynchronous private-conversation scenario
# ---------------------------------------------------------------------------


class TestPrivateConversationKnowledge(unittest.TestCase):
    """A, B, and C are in the same room.  A whispers a secret to B.

    Social structure
    ────────────────
    All three actors observe each other's presence (shared exp note).
    C watches A lean toward B and whisper — C knows a private exchange
    occurred (shared exp note about the observable act).
    Only A and B know the content of the whisper (private exp:a exp:b note).

    Expected knowledge
    ──────────────────
    actor_a : present with b+c ✓ | secret content ✓ | c saw the exchange ✓
    actor_b : present with a+c ✓ | secret content ✓ | c saw the exchange ✓
    actor_c : present with a+b ✓ | secret content ✗ | c saw the exchange ✓
    """

    SECRET = "The vault combination is four, seven, three."
    OBSERVABLE = "actor_a leaned close to actor_b and whispered privately"

    def setUp(self):
        Note.NOTEFILE = TMP_CATNOTE
        open(TMP_CATNOTE, "w").close()
        self.engine = RPJotEngine(
            location="sitting-room",
            people_present={"actor_a", "actor_b", "actor_c"},
        )

        # All three know each other is in the room
        Note.append(
            TMP_CATNOTE,
            Note.jot(
                message="actor_a, actor_b, and actor_c are all present in the sitting room.",
                tag="exp:actor_a exp:actor_b exp:actor_c",
                context="shared presence",
                pwd="/story/events/sitting-room",
            ),
        )

        # A tells B the secret — only they share this note
        Note.append(
            TMP_CATNOTE,
            Note.jot(
                message=f"actor_a whispered to actor_b: '{self.SECRET}'",
                tag="exp:actor_a exp:actor_b",
                context="private whisper from actor_a to actor_b",
                pwd="/story/events/sitting-room",
            ),
        )

        # C observes the social act but cannot hear the words
        Note.append(
            TMP_CATNOTE,
            Note.jot(
                message=(
                    f"{self.OBSERVABLE} while actor_c watched. "
                    "actor_c could see that a private exchange occurred "
                    "but could not hear what was said."
                ),
                tag="exp:actor_a exp:actor_b exp:actor_c",
                context="observable private exchange",
                pwd="/story/events/sitting-room",
            ),
        )

    def tearDown(self):
        import os

        try:
            os.remove(TMP_CATNOTE)
        except FileNotFoundError:
            pass
        Note.NOTEFILE = FIXED_CATNOTE

    def _msgs(self, actor):
        return [n.message for n in self.engine.gather_pov_context(actor)]

    # ── All three know who is in the room ─────────────────────────────────────

    def test_a_knows_b_is_present(self):
        self.assertTrue(any("actor_b" in m for m in self._msgs("actor_a")))

    def test_a_knows_c_is_present(self):
        self.assertTrue(any("actor_c" in m for m in self._msgs("actor_a")))

    def test_b_knows_a_is_present(self):
        self.assertTrue(any("actor_a" in m for m in self._msgs("actor_b")))

    def test_b_knows_c_is_present(self):
        self.assertTrue(any("actor_c" in m for m in self._msgs("actor_b")))

    def test_c_knows_a_is_present(self):
        self.assertTrue(any("actor_a" in m for m in self._msgs("actor_c")))

    def test_c_knows_b_is_present(self):
        self.assertTrue(any("actor_b" in m for m in self._msgs("actor_c")))

    # ── A and B know the secret content ───────────────────────────────────────

    def test_a_knows_secret_content(self):
        self.assertTrue(
            any(self.SECRET in m for m in self._msgs("actor_a")),
            "actor_a must know the whispered secret",
        )

    def test_b_knows_secret_content(self):
        self.assertTrue(
            any(self.SECRET in m for m in self._msgs("actor_b")),
            "actor_b must know the whispered secret",
        )

    # ── C does NOT know the secret content ────────────────────────────────────

    def test_c_does_not_know_secret_content(self):
        self.assertFalse(
            any(self.SECRET in m for m in self._msgs("actor_c")),
            "actor_c must not know the whispered secret",
        )

    # ── C knows a private exchange occurred ───────────────────────────────────

    def test_c_knows_exchange_occurred(self):
        self.assertTrue(
            any(self.OBSERVABLE in m for m in self._msgs("actor_c")),
            "actor_c must know that a private exchange occurred",
        )

    def test_c_knows_exchange_was_between_a_and_b(self):
        observable_msgs = [m for m in self._msgs("actor_c") if self.OBSERVABLE in m]
        self.assertTrue(observable_msgs, "actor_c should have the observable act note")
        combined = " ".join(observable_msgs)
        self.assertIn("actor_a", combined)
        self.assertIn("actor_b", combined)

    # ── A and B also know C witnessed the exchange ────────────────────────────

    def test_a_knows_c_witnessed_exchange(self):
        self.assertTrue(
            any(self.OBSERVABLE in m for m in self._msgs("actor_a")),
            "actor_a must know actor_c observed the private exchange occurring",
        )

    def test_b_knows_c_witnessed_exchange(self):
        self.assertTrue(
            any(self.OBSERVABLE in m for m in self._msgs("actor_b")),
            "actor_b must know actor_c observed the private exchange occurring",
        )

    # ── Asymmetry: C knows THAT but not WHAT ──────────────────────────────────

    def test_c_knows_that_but_not_what(self):
        msgs = self._msgs("actor_c")
        knows_exchange_happened = any(self.OBSERVABLE in m for m in msgs)
        knows_secret_content = any(self.SECRET in m for m in msgs)
        self.assertTrue(
            knows_exchange_happened, "C must know a private exchange happened"
        )
        self.assertFalse(
            knows_secret_content, "C must not know the content of the exchange"
        )

    # ── build_scene_context_map enforces same isolation ───────────────────────

    def test_scene_map_c_lacks_secret(self):
        c_ctx = self.engine.build_scene_context_map()["actor_c"]
        self.assertNotIn(
            self.SECRET,
            c_ctx,
            "actor_c's context map entry must not contain the whispered secret",
        )

    def test_scene_map_a_has_secret(self):
        a_ctx = self.engine.build_scene_context_map()["actor_a"]
        self.assertIn(
            self.SECRET,
            a_ctx,
            "actor_a's context map entry must contain the whispered secret",
        )

    def test_scene_map_b_has_secret(self):
        b_ctx = self.engine.build_scene_context_map()["actor_b"]
        self.assertIn(
            self.SECRET,
            b_ctx,
            "actor_b's context map entry must contain the whispered secret",
        )

    def test_scene_map_c_has_observable_act(self):
        c_ctx = self.engine.build_scene_context_map()["actor_c"]
        self.assertIn(
            self.OBSERVABLE,
            c_ctx,
            "actor_c's context map entry must contain the observable act",
        )


# ---------------------------------------------------------------------------
# Location precision — _remark_location + gather_location_events (LM §4), zero LLM
# ---------------------------------------------------------------------------


class TestLocationPrecision(unittest.TestCase):
    """Notes file under the correct, precise room at write time (LOCATION_MARKING).

    Seeds a ravenwood-manor hierarchy plus a couple of sub-room events, then
    exercises the early location re-mark (both the step-1 CURRENT ROOM path and
    the gated lexical led-move fallback) and the down-walk event recall. world_doc
    is stubbed so no live LLM is needed.
    """

    ROOT = "ravenwood-manor"
    FOYER = "ravenwood-manor/foyer"

    def setUp(self):
        Note.NOTEFILE = TMP_CATNOTE
        open(TMP_CATNOTE, "w").close()

        def seed(pwd, tag, message, now=None):
            Note.append(
                TMP_CATNOTE,
                Note.jot(message=message, tag=tag, context="seed", pwd=pwd, now=now),
            )

        for room in (
            "ravenwood-manor",
            "ravenwood-manor/foyer",
            "ravenwood-manor/cottage",
            "ravenwood-manor/garage",
            "ravenwood-manor/secret-garden",
            "ravenwood-manor/garden",
            "ravenwood-manor/garden-east",
        ):
            seed(f"/story/location/{room}", f"loc:{room}", room.split("/")[-1])

        # Events for the down-walk / TREE-boundary tests.
        seed(
            "/story/events/ravenwood-manor/garden",
            "exp:evie",
            "Gardeners prune the roses.",
        )
        seed(
            "/story/events/ravenwood-manor/garden/shed",
            "exp:evie",
            "A rake leans in the garden shed.",
        )
        seed(
            "/story/events/ravenwood-manor/garden-east",
            "exp:evie",
            "The east beds are freshly turned.",
        )

    def tearDown(self):
        try:
            os.remove(TMP_CATNOTE)
        except FileNotFoundError:
            pass
        Note.NOTEFILE = FIXED_CATNOTE

    def _engine(self, location):
        return _make_engine(location=location, people={"player", "evie"})

    def _events_at(self, room):
        with __import__("catjot").NoteContext(
            TMP_CATNOTE,
            (__import__("catjot").SearchType.DIRECTORY, f"/story/events/{room}"),
        ) as nc:
            return list(nc)

    # --- KEY acceptance: led move names the room in the step-1 line ---

    def test_led_move_named_remarks_and_files_precisely(self):
        eng = self._engine(self.FOYER)
        wd = eng._remark_location(
            "[MC action]: Evie leads me into the cottage",
            "CURRENT ROOM: ravenwood-manor/cottage\nWORLD STATE: ...",
        )
        self.assertEqual(eng.session.location, "ravenwood-manor/cottage")
        self.assertNotIn("CURRENT ROOM:", wd)  # stripped before step 2/3
        # a subsequent record_event with no location stamps the precise room
        eng._tool_record_event("They sit by the fire.", "exp:evie")
        self.assertEqual(len(self._events_at("ravenwood-manor/cottage")), 1)

    # --- KEY acceptance: self-move defers to navigate_to (no double move) ---

    def test_self_move_defers_then_navigate_moves(self):
        eng = self._engine(self.FOYER)
        eng._remark_location(
            "[MC action]: I walk into the cottage", "CURRENT ROOM: UNCHANGED"
        )
        self.assertEqual(eng.session.location, self.FOYER)  # deferred to navigate_to
        eng._tool_navigate_to("cottage")
        self.assertEqual(eng.session.location, "ravenwood-manor/cottage")

    # --- fail-safe: nothing confident → location untouched ---

    def test_failsafe_unchanged_line_no_room(self):
        eng = self._engine(self.ROOT)
        eng._remark_location(
            "[MC speaks aloud]: 'Lovely weather today.'", "CURRENT ROOM: UNCHANGED"
        )
        self.assertEqual(eng.session.location, self.ROOT)

    # --- mention-without-movement (guards §3.1 gating) ---

    def test_mention_without_movement_does_not_remark(self):
        eng = self._engine(self.ROOT)
        # names a known child room (garage) but with no _LED_VERBS cue and an
        # UNCHANGED step-1 line — the lexical path must NOT fire on a mention.
        eng._remark_location(
            "[MC speaks aloud]: 'Meet me in the garage at dusk.'",
            "CURRENT ROOM: UNCHANGED",
        )
        self.assertEqual(eng.session.location, self.ROOT)

    # --- gated lexical fallback: led move names a known room, step-1 omits it ---

    def test_lexical_led_fallback_remarks_known_room(self):
        eng = self._engine(self.ROOT)
        eng._remark_location(
            "[MC action]: the car pulls into the garage", "CURRENT ROOM: UNCHANGED"
        )
        self.assertEqual(eng.session.location, "ravenwood-manor/garage")

    def test_lexical_fallback_ignores_unknown_room(self):
        eng = self._engine(self.ROOT)
        # led cue present, but "helipad" is not a known room → never create-new
        # from a lexical guess; fail-safe keeps the current location.
        eng._remark_location(
            "[MC action]: the pilot brings us down into the helipad",
            "CURRENT ROOM: UNCHANGED",
        )
        self.assertEqual(eng.session.location, self.ROOT)

    # --- deferred / self-heal: unnamed led move, then next-turn re-mark lands ---

    def test_deferred_unnamed_led_move_self_heals_next_turn(self):
        eng = self._engine(self.FOYER)
        # turn 1: unnamed led move (no directional prep) → fail-safe, unchanged
        eng._remark_location(
            "[MC action]: she takes my hand and we go somewhere",
            "CURRENT ROOM: UNCHANGED",
        )
        self.assertEqual(eng.session.location, self.FOYER)
        # turn 2: step-1 now sees the move and names the room → re-mark lands
        eng._remark_location(
            "[MC action]: I look around the new place",
            "CURRENT ROOM: ravenwood-manor/secret-garden",
        )
        self.assertEqual(eng.session.location, "ravenwood-manor/secret-garden")

    # --- gather_location_events: down-walk + TREE boundary (§3.6) ---

    def test_location_events_includes_subrooms(self):
        eng = self._engine(self.ROOT)
        events = eng.gather_location_events("ravenwood-manor/garden")
        self.assertIn("prune the roses", events)  # this room
        self.assertIn("rake leans", events)  # sub-room garden/shed

    def test_location_events_tree_boundary_excludes_prefix_sibling(self):
        eng = self._engine(self.ROOT)
        events = eng.gather_location_events("ravenwood-manor/garden")
        self.assertNotIn("east beds", events)  # garden-east must not leak in

    def test_location_events_empty_room_is_blank(self):
        eng = self._engine(self.ROOT)
        self.assertEqual(eng.gather_location_events("ravenwood-manor/cottage"), "")


# ---------------------------------------------------------------------------
# Tier 1 — the permanence contract (OBJECT_PERMANENCE §4.1), zero LLM
# ---------------------------------------------------------------------------


class TestObjectPermanence(unittest.TestCase):
    """Deterministic recall of whatever was written; stale-never-wrong-place.

    A tarnished locket travels ravenwood-manor: it is introduced in the foyer,
    restated in place, moved to the secret garden, pocketed by Evie, cracks in
    her keeping, has its canonical description re-saved, and is finally merely
    *talked about* back in the foyer. Each phase is seeded with an exact Note.now
    so the timeline is deterministic; each test seeds only the history it asserts
    against (_seed_through) and builds a fresh engine over that seeded file so the
    object registry is never stale. Invariant IDs from §2 are named per assertion.
    """

    SLUG = "tarnished-locket"
    ROOM_A = "ravenwood-manor/foyer"
    ROOM_B = "ravenwood-manor/secret-garden"
    HOLDER = "evie"
    T = 1_750_000_000  # base epoch; phases step by 100

    # PHASES[i] = list of Note.jot kwargs seeded for phase i.
    PHASES = [
        # P0 — genesis: canonical node + genesis sighting (both at T+0)
        [
            dict(
                now=T + 0,
                message="A tarnished silver locket, clasp worn, holding a faded portrait.",
                tag="obj:tarnished-locket",
                context="object canon: tarnished-locket",
                pwd="/story/object/tarnished-locket",
            ),
            dict(
                now=T + 0,
                message="A tarnished silver locket, clasp worn, holding a faded portrait.",
                tag="obj:tarnished-locket",
                context="object sighting: tarnished-locket at ravenwood-manor/foyer",
                pwd="/story/location/ravenwood-manor/foyer",
            ),
        ],
        # P1 — restate in place (place_object, no destination)
        [
            dict(
                now=T + 100,
                message="tarnished-locket is here.",
                tag="obj:tarnished-locket",
                pwd="/story/location/ravenwood-manor/foyer",
            )
        ],
        # P2 — moved to room B
        [
            dict(
                now=T + 200,
                message="The locket lies on the garden bench.",
                tag="obj:tarnished-locket",
                pwd="/story/location/ravenwood-manor/secret-garden",
            )
        ],
        # P3 — picked up by NPC (possession is a residence)
        [
            dict(
                now=T + 300,
                message="Evie pockets the locket.",
                tag="obj:tarnished-locket",
                pwd="/story/character/evie/inventory",
            )
        ],
        # P4 — state change files AT the current residence
        [
            dict(
                now=T + 400,
                message="The locket is now cracked across the portrait glass.",
                tag="obj:tarnished-locket",
                pwd="/story/character/evie/inventory",
            )
        ],
        # P5 — canonical re-save: the GLOBALLY NEWEST note
        [
            dict(
                now=T + 500,
                message="A tarnished silver locket, now cracked across the portrait glass.",
                tag="obj:tarnished-locket",
                context="object canon: tarnished-locket",
                pwd="/story/object/tarnished-locket",
            )
        ],
        # P6 — mention-pollution tripwire (event that only DISCUSSES it)
        [
            dict(
                now=T + 600,
                message="Evie talks about the locket she lost years ago.",
                tag="exp:evie obj:tarnished-locket",
                pwd="/story/events/ravenwood-manor/foyer",
            )
        ],
    ]

    def setUp(self):
        Note.NOTEFILE = TMP_CATNOTE
        open(TMP_CATNOTE, "w").close()

    def tearDown(self):
        try:
            os.remove(TMP_CATNOTE)
        except FileNotFoundError:
            pass
        Note.NOTEFILE = FIXED_CATNOTE

    # --- helpers ---

    def _seed(self, **kw):
        Note.append(TMP_CATNOTE, Note.jot(**kw))

    def _seed_through(self, k):
        """Append phases 0..k inclusive."""
        for phase in self.PHASES[: k + 1]:
            for kw in phase:
                self._seed(**kw)

    def _engine(self, location=None, people=None):
        """Fresh engine over the currently-seeded file (cold registry cache)."""
        return _make_engine(
            location=location or self.ROOM_A,
            people=people if people is not None else {"player", self.HOLDER},
        )

    def _residence(self, engine, slug=None):
        return engine._object_registry()[slug or self.SLUG]["residence"]

    def _get_object(self, engine, name=None):
        return json.loads(engine._tool_get_object(name or self.SLUG))

    # --- P0: genesis in the foyer (I2) ---

    def test_p0_residence_is_foyer(self):
        self._seed_through(0)
        eng = self._engine()
        self.assertEqual(self._residence(eng), {"room": self.ROOM_A})

    def test_p0_canonical_description_has_faded_portrait(self):
        self._seed_through(0)
        eng = self._engine()
        self.assertIn("faded portrait", self._get_object(eng)["canonical_description"])

    def test_p0_objects_here_lists_locket_in_foyer(self):
        self._seed_through(0)
        eng = self._engine()
        lines = eng._objects_here_lines(self.ROOM_A, {"player", self.HOLDER})
        self.assertTrue(any(self.SLUG in ln for ln in lines), lines)

    # --- P1: restate in place — history, not corruption (I1) ---

    def test_p1_residence_unchanged(self):
        self._seed_through(1)
        eng = self._engine()
        self.assertEqual(self._residence(eng), {"room": self.ROOM_A})

    def test_p1_two_sightings_newest_first(self):
        self._seed_through(1)
        eng = self._engine()
        sightings = eng._object_sightings(self.SLUG)
        self.assertEqual(len(sightings), 2)  # both foyer notes; canonical excluded
        self.assertEqual(eng._newest_by_now(sightings).now, self.T + 100)

    # --- P2: moved to room B; [OBJECTS HERE] is authoritative (I2, I7) ---

    def test_p2_residence_is_secret_garden(self):
        self._seed_through(2)
        eng = self._engine()
        self.assertEqual(self._residence(eng), {"room": self.ROOM_B})

    def test_p2_objects_here_foyer_excludes_but_blob_still_has_stale_notes(self):
        self._seed_through(2)
        eng = self._engine()
        # authoritative correction layer excludes the moved object from the foyer
        foyer_lines = eng._objects_here_lines(self.ROOM_A, {"player", self.HOLDER})
        self.assertFalse(any(self.SLUG in ln for ln in foyer_lines), foyer_lines)
        # ...while the stale foyer sighting notes still sit in the room blob
        blob = [n.message for n in ContextBundle(f"/story/location/{self.ROOM_A}")]
        self.assertTrue(any("tarnished-locket is here." in m for m in blob), blob)
        # ...and the secret-garden block lists it
        garden_lines = eng._objects_here_lines(self.ROOM_B, {"player", self.HOLDER})
        self.assertTrue(any(self.SLUG in ln for ln in garden_lines), garden_lines)

    # --- P3: possession is a residence; profile stays clean (I2, OT §3.1) ---

    def test_p3_residence_is_held_by_evie(self):
        self._seed_through(3)
        eng = self._engine()
        self.assertEqual(self._residence(eng), {"held_by": self.HOLDER})

    def test_p3_objects_here_shows_held_by_evie(self):
        self._seed_through(3)
        eng = self._engine()
        lines = eng._objects_here_lines(self.ROOM_B, {"player", self.HOLDER})
        self.assertIn(f"held — {self.HOLDER}: {self.SLUG}", lines)

    def test_p3_inventory_note_absent_from_pov_context(self):
        self._seed_through(3)
        eng = self._engine()
        pov = [n.message for n in eng.gather_pov_context(self.HOLDER)]
        self.assertFalse(any("Evie pockets the locket." in m for m in pov), pov)

    # --- P4: state change at residence; graceful without canon update (I1, I3) ---

    def test_p4_timeline_newest_is_cracked_while_canon_still_worn(self):
        self._seed_through(4)
        eng = self._engine()
        self.assertIn(
            "cracked", eng._newest_by_now(eng._object_sightings(self.SLUG)).message
        )
        self.assertIn("clasp worn", self._get_object(eng)["canonical_description"])
        self.assertEqual(self._residence(eng), {"held_by": self.HOLDER})

    # --- P5: FLAGSHIP — canonical re-save does not move residence (I3) ---

    def test_p5_canonical_resave_does_not_move_residence(self):
        self._seed_through(5)
        eng = self._engine()
        # canonical note is now the GLOBALLY newest, yet residence stays with Evie
        self.assertEqual(self._residence(eng), {"held_by": self.HOLDER})

    def test_p5_canonical_description_is_newest_canon_text(self):
        self._seed_through(5)
        eng = self._engine()
        self.assertIn("now cracked", self._get_object(eng)["canonical_description"])

    # --- P6: mention-pollution tripwire (documented limitation, §2 caveat) ---

    def test_p6_mention_pollution_tripwire_moves_residence(self):
        # An obj:-tagged event that merely DISCUSSES the locket moves its parsed
        # residence to the foyer — the mention≠presence twin of neg_navto. This
        # asserts the CURRENT behavior so any future change is deliberate (§2).
        self._seed_through(6)
        eng = self._engine()
        self.assertEqual(self._residence(eng), {"room": self.ROOM_A})

    # --- Side fixtures (separate slugs) ---

    def test_equal_now_later_in_file_wins(self):
        # I6 tie-break: two sightings, both now=T+50, foyer then garden.
        self._seed(
            now=self.T + 50,
            message="iron key on the sill.",
            tag="obj:iron-key",
            pwd="/story/location/ravenwood-manor/foyer",
        )
        self._seed(
            now=self.T + 50,
            message="iron key on the bench.",
            tag="obj:iron-key",
            pwd="/story/location/ravenwood-manor/secret-garden",
        )
        eng = self._engine()
        self.assertEqual(self._residence(eng, "iron-key"), {"room": self.ROOM_B})

    def test_event_tag_is_room_sighting(self):
        # I8 fallback channel: an obj:-tagged record_event note is a room sighting.
        self._seed(
            now=self.T + 10,
            message="A wax-sealed letter changes hands by the hearth.",
            tag="exp:evie obj:sealed-letter",
            pwd="/story/events/ravenwood-manor/foyer",
        )
        eng = self._engine()
        self.assertEqual(self._residence(eng, "sealed-letter"), {"room": self.ROOM_A})

    def test_legacy_note_is_genesis_sighting(self):
        # I10 migration: an old-style write-only save_object note (description at a
        # room pwd, no canonical node) is a valid genesis sighting.
        self._seed(
            now=self.T + 20,
            message="A tall silver mirror in a tarnished frame.",
            tag="obj:silver-mirror",
            context="object: silver-mirror at ravenwood-manor/foyer",
            pwd="/story/location/ravenwood-manor/foyer",
        )
        eng = self._engine()
        data = self._get_object(eng, "silver-mirror")
        self.assertEqual(data["residence"], {"room": self.ROOM_A})
        self.assertIn("silver mirror", data["canonical_description"].lower())

    def test_unknown_object_no_invented_residence(self):
        # I5 stale-never-wrong-place: zero notes → roster miss, no residence.
        eng = self._engine()
        data = self._get_object(eng, "phantom dagger")
        self.assertIn("known_objects", data)
        self.assertNotIn("residence", data)

    def test_residence_static_without_new_notes(self):
        # I5: residence changes only when a sighting is written — two reads over an
        # unchanged file agree.
        self._seed_through(3)
        eng = self._engine()
        first = self._residence(eng)
        # a fresh engine over the same unchanged file yields the same residence
        eng2 = self._engine()
        self.assertEqual(first, self._residence(eng2))


# ---------------------------------------------------------------------------
# Object write side — save_object / place_object (OBJECT_PERMANENCE Phase 3)
# ---------------------------------------------------------------------------


class TestObjectWriteSide(unittest.TestCase):
    """save_object dual-write, place_object residence changes, and I4 stamping."""

    def setUp(self):
        Note.NOTEFILE = TMP_CATNOTE
        open(TMP_CATNOTE, "w").close()
        for room in ("manor", "manor/foyer", "manor/garden"):
            Note.append(
                TMP_CATNOTE,
                Note.jot(
                    message=room.split("/")[-1],
                    tag=f"loc:{room}",
                    context="seed",
                    pwd=f"/story/location/{room}",
                ),
            )
        self.engine = _make_engine(location="manor/foyer", people={"player", "evie"})

    def tearDown(self):
        try:
            os.remove(TMP_CATNOTE)
        except FileNotFoundError:
            pass
        Note.NOTEFILE = FIXED_CATNOTE

    def _at(self, pwd):
        from catjot import NoteContext, SearchType

        with NoteContext(TMP_CATNOTE, (SearchType.DIRECTORY, pwd)) as nc:
            return list(nc)

    # --- save_object dual write + I4 default stamping ---

    def test_save_object_dual_write(self):
        self.engine._tool_save_object(name="iron-key", description="A heavy iron key.")
        self.assertEqual(len(self._at("/story/object/iron-key")), 1)  # canonical
        self.assertEqual(len(self._at("/story/location/manor/foyer")), 2)  # +sighting

    def test_save_object_defaults_to_session_location(self):
        # I4 true-room stamping: no explicit location → session.location.
        self.engine.session.location = "manor/garden"
        self.engine._tool_save_object(name="locket", description="A silver locket.")
        sighting = [
            n
            for n in self._at("/story/location/manor/garden")
            if "obj:locket" in n.tag.split()
        ]
        self.assertEqual(len(sighting), 1)

    def test_save_object_return_string_unchanged_shape(self):
        # Existing 649-666 tests rely on the slug appearing in the return string.
        result = self.engine._tool_save_object(
            name="iron-key", description="A key.", location="cellar"
        )
        self.assertIn("iron-key", result)
        self.assertIn("cellar", result)

    # --- place_object residence changes ---

    def test_place_object_holder_files_under_inventory(self):
        self.engine._tool_save_object(name="iron-key", description="A key.")
        self.engine._tool_place_object(name="iron-key", holder="evie")
        self.assertEqual(len(self._at("/story/character/evie/inventory")), 1)
        self.assertEqual(
            self.engine._object_registry()["iron-key"]["residence"],
            {"held_by": "evie"},
        )

    def test_place_object_room_canonicalizes(self):
        self.engine._tool_save_object(name="iron-key", description="A key.")
        self.engine._tool_place_object(name="iron-key", room="garden")
        self.assertEqual(
            self.engine._object_registry()["iron-key"]["residence"],
            {"room": "manor/garden"},
        )

    def test_place_object_neither_restates_current_residence(self):
        self.engine._tool_save_object(name="iron-key", description="A key.")
        self.engine._tool_place_object(name="iron-key", holder="evie")
        # neither holder nor room → restate where it is (still Evie)
        self.engine._tool_place_object(name="iron-key", state="glinting")
        self.assertEqual(
            self.engine._object_registry()["iron-key"]["residence"],
            {"held_by": "evie"},
        )

    def test_place_object_both_given_errors_writes_no_note(self):
        self.engine._tool_save_object(name="iron-key", description="A key.")
        before = len(list(Note.iterate(TMP_CATNOTE)))
        result = json.loads(
            self.engine._tool_place_object(name="iron-key", holder="evie", room="manor")
        )
        after = len(list(Note.iterate(TMP_CATNOTE)))
        self.assertIn("error", result)
        self.assertEqual(before, after)

    def test_place_object_auto_creates_canonical_node(self):
        # A never-saved object gains its canonical node on first place.
        self.engine._tool_place_object(name="brass-lantern", room="garden")
        self.assertEqual(len(self._at("/story/object/brass-lantern")), 1)

    def test_legacy_migration_creates_canonical_node(self):
        # I10 migration: a legacy sighting-only object gains a canonical node on
        # the first place_object touch.
        Note.append(
            TMP_CATNOTE,
            Note.jot(
                message="A tall silver mirror.",
                tag="obj:silver-mirror",
                pwd="/story/location/manor/foyer",
            ),
        )
        self.assertEqual(len(self._at("/story/object/silver-mirror")), 0)
        self.engine._tool_place_object(name="silver-mirror", holder="evie")
        self.assertEqual(len(self._at("/story/object/silver-mirror")), 1)

    def test_record_event_obj_tag_moves_residence(self):
        # I8 fallback channel through the real record_event handler + cache-drop.
        self.engine._tool_save_object(name="iron-key", description="A key.")
        self.engine._tool_record_event(
            "The key is pressed into Evie's palm.", "exp:evie obj:iron-key"
        )
        # the event is filed at /story/events/{session.location} → a room sighting
        self.assertEqual(
            self.engine._object_registry()["iron-key"]["residence"],
            {"room": "manor/foyer"},
        )

    def test_inventory_note_does_not_mint_phantom_character(self):
        # place under /story/character/evie/inventory must not register "inventory".
        self.engine._tool_save_object(name="iron-key", description="A key.")
        self.engine._tool_place_object(name="iron-key", holder="evie")
        fresh = _make_engine(location="manor/foyer", people={"player"})
        roster = json.loads(fresh._tool_find_character("nobody"))["roster"]
        self.assertNotIn("inventory", roster)


# ---------------------------------------------------------------------------
# Tier 2 — endpoint-gated acceptance sampling (OBJECT_PERMANENCE §4.2)
# ---------------------------------------------------------------------------


class TestObjectActivationLive(unittest.TestCase):
    """place_object / save_object selection in the production compact-schema shape.

    LLM-gated (registered in conftest._LLM_CLASSES so it fails hard when the
    endpoint is absent instead of silently passing) and stochastic: each phrase
    runs N=3 with a >=2/3 threshold. Reuses the TestProductionActivation shape;
    the helper additionally returns tool-call ARGUMENTS so the fallback assertion
    can inspect record_event.tags for an obj: word.

    The object minimal pair (the neg_navto diagnostic applied to
    mention!=presence): twin phrases share the object noun with opposite ground
    truth.
    """

    def setUp(self):
        self.engine = _make_engine(
            location="ravenwood-manor", people={"player", "evie"}
        )

    def _step2_calls_for(self, phrase, world_doc=None):
        from catjot import ContextBundle, call_llm
        from rpjot import ComplianceStep

        if world_doc is None:
            world_doc = "WORLD STATE: you stand in the manor foyer with Evie."
        rules = str(ContextBundle("system_role")).strip() or (
            "You are the game master. Use tools to record canon."
        )
        content = ComplianceStep(self.engine)._compose_step2_user_content(
            phrase, world_doc
        )
        messages = [
            {"role": "system", "content": rules},
            {"role": "user", "content": content},
        ]
        resp = call_llm(
            messages,
            tools=self.engine._compact_step2_schemas,
            tool_choice="auto",
        )
        calls = []
        for tc in resp.get("tool_calls") or []:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            calls.append((name, args))
        return calls

    def _passes(self, predicate, n=3, need=2):
        return sum(1 for _ in range(n) if predicate()) >= need

    def _fires(self, phrase, tool):
        return self._passes(
            lambda: any(nm == tool for nm, _ in self._step2_calls_for(phrase))
        )

    def _does_not_fire(self, phrase, tool):
        return self._passes(
            lambda: not any(nm == tool for nm, _ in self._step2_calls_for(phrase))
        )

    def test_pickup_selects_place_object(self):
        self.assertTrue(self._fires("Evie pockets the locket.", "place_object"))

    def test_mention_negative_twin_does_not_select_place_object(self):
        self.assertTrue(
            self._does_not_fire(
                "Evie talks about the locket she lost.", "place_object"
            )
        )

    def test_handover_selects_place_object(self):
        self.assertTrue(
            self._fires(
                "[MC action]: I press the iron key into Evie's palm.", "place_object"
            )
        )

    def test_genesis_selects_save_object(self):
        self.assertTrue(
            self._fires(
                "[MC action]: I examine the strange amulet and describe its "
                "markings and material in detail.",
                "save_object",
            )
        )

    def test_fallback_place_or_event_with_obj_tag(self):
        phrase = "The guards confiscate my satchel and toss it into the cell corner."

        def captured():
            calls = self._step2_calls_for(phrase)
            place = any(nm == "place_object" for nm, _ in calls)
            event_obj = any(
                nm == "record_event"
                and any(
                    t.startswith("obj:") for t in str(a.get("tags", "")).split()
                )
                for nm, a in calls
            )
            return place or event_obj

        self.assertTrue(self._passes(captured))


if __name__ == "__main__":
    unittest.main(verbosity=2)
