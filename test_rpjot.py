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
import logging
import os
import threading
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

    def test_header_contains_plain_location(self):
        s = SessionState(location="ravenwood", people_present={"player"})
        h = s.header()
        self.assertIn("location: ravenwood", h)
        self.assertNotIn("loc:", h)

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
            tags="alice door",
        )
        self.assertIsInstance(result, str)

    def test_record_event_result_starts_with_prefix(self):
        result = self.engine._tool_record_event(
            description="Bob lit the torch.",
            tags="bob torch",
        )
        self.assertTrue(result.startswith("Event recorded:"))

    def test_record_event_with_location_kwarg(self):
        result = self.engine._tool_record_event(
            description="The door slammed shut.",
            tags="door",
            location="cellar",
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

    def test_navigate_to_bare_destination(self):
        self.engine._tool_navigate_to("great-hall")
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

    def test_set_people_display_name_collapses_to_mc(self):
        # the live cast-wipe class: a display-name variant of the MC replaced
        # the whole cast with a bogus slug.
        self.engine.mc_aliases = frozenset({"mc", "bartholomew", "bart"})
        self.engine._tool_set_people_present(["Bartholomew Wentworth", "evie"])
        self.assertEqual(
            self.engine.session.people_present,
            {self.engine.main_character, "evie"},
        )

    def test_set_people_display_name_resolves_registered_slug(self):
        self.engine.npc_tracker.register("evie", "Madame Evie Bellvue")
        self.engine._tool_set_people_present(["Madame Evie Bellvue", "player"])
        self.assertEqual(self.engine.session.people_present, {"evie", "player"})

    def test_set_people_unknown_name_slugified(self):
        self.engine._tool_set_people_present(["Old Guard"])
        self.assertEqual(self.engine.session.people_present, {"old-guard"})

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

    def test_save_object_with_explicit_location(self):
        result = self.engine._tool_save_object(
            name="torch",
            description="A burning wall torch.",
            location="great-hall",
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
        parsed = json.loads(self.engine._tool_search_world("dungeon"))
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
        seed("/story/location/manor", "", "The manor.")
        seed("/story/location/manor/foyer", "", "The foyer.")
        seed(
            "/story/location/manor/foyer/library",
            "",
            "The library.",
        )
        seed("/story/location/manor/garden", "", "The garden.")
        seed(
            "/story/location/manor/garden-east",
            "",
            "The east garden.",
        )
        seed("/story/location/dungeon", "", "The dungeon.")

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

    # --- _sibling_room_slugs ---

    def test_sibling_room_slugs_lists_parents_other_children(self):
        siblings = self.engine._sibling_room_slugs("manor/foyer")
        self.assertIn("garden", siblings)
        self.assertIn("garden-east", siblings)
        self.assertNotIn("foyer", siblings)  # never lists itself

    def test_sibling_room_slugs_top_level_has_none(self):
        # a single-component room has no parent under PWD_WORLD; top-level
        # neighbors are _known_location_roots territory, not siblings.
        self.assertEqual(self.engine._sibling_room_slugs("manor"), [])

    def test_sibling_room_slugs_prefix_boundary(self):
        # garden's siblings come from manor's children; garden-east is a real
        # sibling (not a boundary artifact) and garden itself is excluded.
        siblings = self.engine._sibling_room_slugs("manor/garden")
        self.assertIn("foyer", siblings)
        self.assertIn("garden-east", siblings)
        self.assertNotIn("garden", siblings)

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

    def test_canon_room_sibling_of_current(self):
        # closet is a sibling of library under foyer. resolve_destination would
        # anchor a bare name at the ROOT ("manor/closet" — no node), so before
        # the sibling step this create-new'd "manor/foyer/library/closet".
        Note.append(
            TMP_CATNOTE,
            Note.jot(
                message="The closet.",
                tag="",
                context="seed",
                pwd="/story/location/manor/foyer/closet",
            ),
        )
        self.assertEqual(
            self.engine._canonicalize_room("closet", "manor/foyer/library"),
            "manor/foyer/closet",
        )

    def test_canon_room_child_beats_sibling(self):
        # "library" names both a child of foyer and (after this seed) a sibling
        # of foyer under manor — the child must win (precedence #2 over #2.5).
        Note.append(
            TMP_CATNOTE,
            Note.jot(
                message="The manor library.",
                tag="",
                context="seed",
                pwd="/story/location/manor/library",
            ),
        )
        self.assertEqual(
            self.engine._canonicalize_room("library", "manor/foyer"),
            "manor/foyer/library",
        )

    def test_canon_room_underscores_normalized(self):
        # the live-session fragmentation class: manor/garden_east and
        # manor/garden-east must be the same room.
        canon = self.engine._canonicalize_room("manor/garden_east", "manor")
        self.assertEqual(canon, "manor/garden-east")
        self.assertNotIn("_", canon)

    def test_canon_room_none_on_empty(self):
        self.assertIsNone(self.engine._canonicalize_room("", "manor"))

    def test_canon_room_slugifies(self):
        self.assertEqual(
            self.engine._canonicalize_room("Secret Garden", "manor"),
            "manor/secret-garden",
        )

    # --- _ensure_location_node idempotency (§3.5) ---

    def test_ensure_node_creates_then_idempotent(self):
        self.assertTrue(self.engine._ensure_location_node("manor/attic"))
        self.assertFalse(self.engine._ensure_location_node("manor/attic"))

    def test_ensure_node_writes_no_location_tag(self):
        from catjot import NoteContext, SearchType

        self.engine._ensure_location_node("manor/attic")
        with NoteContext(
            Note.NOTEFILE, (SearchType.DIRECTORY, "/story/location/manor/attic")
        ) as nc:
            notes = list(nc)
        self.assertTrue(notes)
        for note in notes:
            self.assertNotIn("loc:", note.tag)

    def test_save_location_writes_no_location_tag(self):
        from catjot import NoteContext, SearchType

        self.engine._tool_save_location(
            name="manor/solarium", description="A glass-roofed solarium."
        )
        with NoteContext(
            Note.NOTEFILE, (SearchType.DIRECTORY, "/story/location/manor/solarium")
        ) as nc:
            notes = list(nc)
        self.assertTrue(notes)
        for note in notes:
            self.assertNotIn("loc:", note.tag)

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

    def test_last_ctx_now_captures_rendered_note_timestamps(self):
        # Phase 0b (E1): every Note reaching the render boundary is recorded in
        # _last_ctx_now, the shared substrate for /construct census and /debug
        # memory forensics. Deterministic — no LLM (bundle is under soft limit).
        self.engine._last_ctx_now = set()
        b = self._bundle(
            [self._note("a", ts=111_111), self._note("b", ts=222_222)]
        )
        self.engine.render_context(b)
        self.assertEqual(self.engine._last_ctx_now, {111_111, 222_222})

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

    def test_scan_drift_cast_parity(self):
        # The unified _scan_drift("cast", ...) seam (Phase 0d) must produce the
        # exact same warnings as the underlying _scan_cast_drift.
        eng = self._engine({"player"})
        eng.npc_tracker.register("evie", "Evie", location="ravenwood-manor")
        warnings = eng._scan_drift(
            "cast", "[MC action]: I look", "Evie beckons from the doorway."
        )
        self.assertIn("evie", warnings)
        self.assertEqual(warnings, eng._cast_warnings)

    def test_scan_drift_unknown_kind_is_noop(self):
        # No room-drift scanner exists yet; an unknown kind is a harmless no-op
        # and must not touch _cast_warnings.
        eng = self._engine({"player"})
        eng.npc_tracker.register("evie", "Evie", location="ravenwood-manor")
        eng._scan_drift("cast", "x", "Evie appears.")
        before = list(eng._cast_warnings)
        self.assertEqual(eng._scan_drift("room", "x", "Evie appears."), [])
        self.assertEqual(eng._cast_warnings, before)


# ---------------------------------------------------------------------------
# 12c-tris. Telemetry-prefix registry — register_prefix / _PREFIX_REGISTRY (E2)
# ---------------------------------------------------------------------------


class TestPrefixRegistry(unittest.TestCase):
    """The telemetry-prefix registry (Phase 0c) is the source of truth for
    'what mechanisms announce'; later phases (EXEC_DEBUG) consume it."""

    def test_preregistered_prefixes_present(self):
        from rpjot import _PREFIX_REGISTRY

        for prefix, cat in (
            ("[COMMIT-LOC]", "location"),
            ("[REMARK]", "location"),
            ("[LOCDRIFT]", "location"),
            ("[CAST]", "misattribution"),
            ("[CTX]", "memory"),
            ("[ENTROPY]", "prose"),
            ("[SEED]", "memory"),
            ("[STEP2]", "tooling"),
            ("[TOOLS]", "tooling"),
            ("[DISPATCH]", "tooling"),
            ("[BUDGET]", "budget"),
        ):
            self.assertIn(prefix, _PREFIX_REGISTRY)
            entry = _PREFIX_REGISTRY[prefix]
            self.assertEqual(entry["category"], cat)
            self.assertTrue(entry["meaning"])

    def test_register_prefix_adds_and_overwrites(self):
        from rpjot import _PREFIX_REGISTRY, register_prefix

        key = "[__TEST_PREFIX__]"
        self.assertNotIn(key, _PREFIX_REGISTRY)
        try:
            register_prefix(key, "testcat", "a test meaning")
            self.assertEqual(
                _PREFIX_REGISTRY[key],
                {"category": "testcat", "meaning": "a test meaning"},
            )
            # Idempotent overwrite.
            register_prefix(key, "testcat2", "updated")
            self.assertEqual(_PREFIX_REGISTRY[key]["category"], "testcat2")
        finally:
            _PREFIX_REGISTRY.pop(key, None)


# ---------------------------------------------------------------------------
# 12c-bis. Location drift observability — LOCDRIFT / [REMARK] action log
# ---------------------------------------------------------------------------


class TestLocationDriftObservability(unittest.TestCase):
    """LOCDRIFT warnings surface in headers + /stats; [REMARK] always logs."""

    def _engine(self):
        eng = _make_engine(location="ravenwood-manor", people={"player"})
        eng.init_pipeline()
        return eng

    def test_no_warning_no_header_block(self):
        eng = self._engine()
        self.assertEqual(eng._loc_warning_line(), "")
        msg = eng._world_state_step._build_initial_message("[MC action]: I wait")
        self.assertNotIn("LOCATION WARNING", msg)

    def test_warning_surfaces_in_both_headers_and_stats(self):
        eng = self._engine()
        eng._loc_warn("event filed at manor/gallery but session is manor/foyer")
        initial = eng._world_state_step._build_initial_message("[MC action]: hm")
        seeded = eng._world_state_step._build_seeded_message(
            "[MC action]: hm", "WORLD STATE: ..."
        )
        report = eng.scene_debug_report()
        for text in (initial, seeded, report):
            self.assertIn("LOCATION WARNING", text)
            self.assertIn("manor/gallery", text)

    def test_loc_warn_logs_locdrift(self):
        eng = self._engine()
        with self.assertLogs("rpjot_engine", level="WARNING") as cm:
            eng._loc_warn("drift detail")
        self.assertTrue(any("[LOCDRIFT]" in line for line in cm.output))

    def test_remark_logs_action_no_line(self):
        eng = self._engine()
        with self.assertLogs("rpjot_engine", level="INFO") as cm:
            eng._remark_location("[MC speaks aloud]: 'hi'", "WORLD STATE: ...")
        self.assertTrue(any("action=no-line" in line for line in cm.output))

    def test_remark_logs_action_unchanged(self):
        eng = self._engine()
        with self.assertLogs("rpjot_engine", level="INFO") as cm:
            eng._remark_location(
                "[MC speaks aloud]: 'hi'", "CURRENT ROOM: UNCHANGED\nWORLD STATE:"
            )
        self.assertTrue(any("action=unchanged" in line for line in cm.output))

    def test_remark_logs_action_mobile_defer(self):
        eng = self._engine()
        with self.assertLogs("rpjot_engine", level="INFO") as cm:
            eng._remark_location(
                "[MC action]: I walk into the garden", "CURRENT ROOM: UNCHANGED"
            )
        self.assertTrue(any("action=mobile-defer" in line for line in cm.output))


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
# 12e-2. Per-turn timing telemetry — [TIMING] line + last_rounds (TOOL_UNIFY U0)
# ---------------------------------------------------------------------------


class TestTimingTelemetry(unittest.TestCase):
    """run_turn emits one parseable [TIMING] line; steps track LLM-call counts."""

    def _patched(self, rounds):
        """Context: swap call_llm for a scripted fake; returns (engine, calls)."""
        import rpjot as rpjot_module

        calls = {"i": 0}

        def fake_call_llm(messages, **kwargs):
            r = rounds[min(calls["i"], len(rounds) - 1)]
            calls["i"] += 1
            return r

        original = rpjot_module.call_llm
        rpjot_module.call_llm = fake_call_llm
        eng = _make_engine(location="ravenwood-manor", people={"player"})
        eng.init_pipeline()
        return rpjot_module, original, eng, calls

    def _record_event_round(self):
        return {
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "record_event",
                        "arguments": json.dumps(
                            {"description": "paced the room", "tags": "exp:player"}
                        ),
                    },
                }
            ]
        }

    def test_timing_line_emitted_per_turn(self):
        rounds = [
            {"content": "WORLD STATE — the room"},  # step 1 text → 1 call
            {"content": "nothing canonical"},  # step 2 text → 1 call
            {"content": "The room stays quiet."},  # step 3 prose
        ]
        mod, original, eng, _ = self._patched(rounds)
        try:
            with self.assertLogs("rpjot_engine", level="INFO") as cm:
                eng.run_turn(
                    "[MC attention] I glance around the room",
                    [{"role": "system", "content": "rules"}],
                    [{"role": "system", "content": "prose"}],
                )
        finally:
            mod.call_llm = original
        timing = [m for m in cm.output if "[TIMING]" in m]
        self.assertEqual(len(timing), 1)
        self.assertRegex(
            timing[0],
            r"\[TIMING\] turn=1 step1=\d+\.\ds/1it "
            r"step2=\d+\.\ds/1it step3=\d+\.\ds total=\d+\.\ds "
            r"seed=off bg=0\.0s wait=0\.0s",
        )

    def test_step2_last_rounds_counts_calls(self):
        rounds = [
            self._record_event_round(),  # tool round
            {"content": "done"},  # exit text
        ]
        mod, original, eng, _ = self._patched(rounds)
        try:
            eng._compliance_step.run(
                "[MC action]: I open the door",
                "WORLD STATE: a room",
                [{"role": "system", "content": "rules"}],
            )
        finally:
            mod.call_llm = original
        self.assertEqual(eng._compliance_step.last_rounds, 2)

    def test_step1_last_rounds_single_text_call(self):
        rounds = [{"content": "WORLD STATE — the room"}]
        mod, original, eng, _ = self._patched(rounds)
        try:
            eng._world_state_step.run("[MC attention] I look around")
        finally:
            mod.call_llm = original
        self.assertEqual(eng._world_state_step.last_rounds, 1)

    def test_step2_last_rounds_at_max_iterations(self):
        # The fake repeats its final round, so the loop runs to the bound.
        rounds = [self._record_event_round()]
        mod, original, eng, _ = self._patched(rounds)
        try:
            eng._compliance_step.run(
                "[MC action]: I open the door",
                "WORLD STATE: a room",
                [{"role": "system", "content": "rules"}],
                max_iterations=4,
            )
        finally:
            mod.call_llm = original
        self.assertEqual(eng._compliance_step.last_rounds, 4)


# ---------------------------------------------------------------------------
# 12e. Step-1 delta mode — WorldStateStep.run(seed_doc=...) (idle-window seed)
# ---------------------------------------------------------------------------


class TestStep1DeltaMode(unittest.TestCase):
    """Seeded step-1 runs a short delta; any failure falls back to full rebuild."""

    def _patched(self, rounds):
        """Swap call_llm for a scripted fake that records each payload."""
        import rpjot as rpjot_module

        calls = {"i": 0, "messages": []}

        def fake_call_llm(messages, **kwargs):
            calls["messages"].append([dict(m) for m in messages])
            r = rounds[min(calls["i"], len(rounds) - 1)]
            calls["i"] += 1
            if isinstance(r, Exception):
                raise r
            return r

        original = rpjot_module.call_llm
        rpjot_module.call_llm = fake_call_llm
        eng = _make_engine(location="ravenwood-manor", people={"player"})
        eng.init_pipeline()
        return rpjot_module, original, eng, calls

    def _tool_round(self):
        # Unknown tool name → _safe_dispatch error JSON; loop continues.
        # Side-effect-free way to burn iterations.
        return {
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "lookup_thing", "arguments": "{}"},
                }
            ]
        }

    def test_seeded_message_replaces_baseline(self):
        rounds = [{"content": "WORLD STATE — updated for input"}]
        mod, original, eng, calls = self._patched(rounds)
        try:
            doc = eng._world_state_step.run(
                "[MC action]: I wave", seed_doc="SEEDED DOC MARKER"
            )
        finally:
            mod.call_llm = original
        self.assertEqual(doc, "WORLD STATE — updated for input")
        self.assertTrue(eng._world_state_step.last_seed_used)
        self.assertTrue(eng._world_state_step.last_ok)
        self.assertEqual(eng._world_state_step.last_rounds, 1)
        user_msg = calls["messages"][0][1]["content"]
        self.assertIn("PRECOMPUTED WORLD STATE", user_msg)
        self.assertIn("SEEDED DOC MARKER", user_msg)
        self.assertNotIn("BASELINE CONTEXT", user_msg)

    def _rooms_fixture_engine(self):
        """Engine at manor/foyer with a child and a sibling room on file."""
        Note.NOTEFILE = TMP_CATNOTE
        open(TMP_CATNOTE, "w").close()
        for room in ("manor", "manor/foyer", "manor/foyer/closet", "manor/garage"):
            Note.append(
                TMP_CATNOTE,
                Note.jot(
                    message=room, tag="", context="seed",
                    pwd=f"/story/location/{room}",
                ),
            )
        eng = _make_engine(location="manor/foyer", people={"player"})
        eng.init_pipeline()
        self.addCleanup(self._restore_notefile)
        return eng

    @staticmethod
    def _restore_notefile():
        try:
            os.remove(TMP_CATNOTE)
        except FileNotFoundError:
            pass
        Note.NOTEFILE = FIXED_CATNOTE

    def test_seeded_message_carries_rooms_vocab_and_first_line_rule(self):
        # the delta model gets no CURRENT ROOM exemplar (the seed's line was
        # stripped at speculation time) — the vocabulary block and the explicit
        # first-line instruction are what make a 0-round delta emit one.
        eng = self._rooms_fixture_engine()
        msg = eng._world_state_step._build_seeded_message(
            "[MC action]: I wave", "SEED DOC"
        )
        self.assertIn("[ROOMS KNOWN HERE]", msg)
        self.assertIn("children: closet", msg)
        self.assertIn("siblings: garage", msg)
        self.assertIn("VERY FIRST line", msg)
        self.assertIn("CURRENT ROOM: UNCHANGED", msg)
        # the whole-doc UNCHANGED short-circuit clause must survive too
        self.assertIn("reply with exactly:\nUNCHANGED", msg)

    def test_baseline_rooms_vocab_lists_siblings(self):
        eng = self._rooms_fixture_engine()
        baseline = eng._world_state_step._build_baseline_context("[MC action]: hm")
        self.assertIn("[ROOMS KNOWN HERE]", baseline)
        self.assertIn("children: closet", baseline)
        self.assertIn("siblings: garage", baseline)

    def test_delta_exhaustion_falls_back_to_full(self):
        # 3 tool rounds exhaust the delta bound; the 4th call (full path)
        # returns text. last_rounds counts every real LLM call this turn.
        rounds = [
            self._tool_round(),
            self._tool_round(),
            self._tool_round(),
            {"content": "WORLD STATE — full rebuild"},
        ]
        mod, original, eng, calls = self._patched(rounds)
        try:
            with self.assertLogs("rpjot_engine", level="WARNING") as cm:
                doc = eng._world_state_step.run(
                    "[MC action]: I wave", seed_doc="SEEDED DOC"
                )
        finally:
            mod.call_llm = original
        self.assertEqual(doc, "WORLD STATE — full rebuild")
        self.assertFalse(eng._world_state_step.last_seed_used)
        self.assertEqual(eng._world_state_step.last_rounds, 4)  # 3 delta + 1 full
        self.assertTrue(any("[SEED] delta step-1 failed" in m for m in cm.output))
        # Full-path message carries the baseline, not the seed.
        full_user_msg = calls["messages"][3][1]["content"]
        self.assertIn("BASELINE CONTEXT", full_user_msg)
        self.assertNotIn("PRECOMPUTED WORLD STATE", full_user_msg)

    def test_delta_request_exception_falls_back(self):
        import requests as requests_module

        rounds = [
            requests_module.exceptions.RequestException("boom"),
            {"content": "WORLD STATE — full rebuild"},
        ]
        mod, original, eng, _ = self._patched(rounds)
        try:
            doc = eng._world_state_step.run(
                "[MC action]: I wave", seed_doc="SEEDED DOC"
            )
        finally:
            mod.call_llm = original
        self.assertEqual(doc, "WORLD STATE — full rebuild")
        self.assertFalse(eng._world_state_step.last_seed_used)
        self.assertEqual(eng._world_state_step.last_rounds, 1)  # 0 delta + 1 full

    def test_empty_delta_doc_falls_back(self):
        rounds = [{"content": ""}, {"content": "WORLD STATE — full rebuild"}]
        mod, original, eng, _ = self._patched(rounds)
        try:
            doc = eng._world_state_step.run(
                "[MC action]: I wave", seed_doc="SEEDED DOC"
            )
        finally:
            mod.call_llm = original
        self.assertEqual(doc, "WORLD STATE — full rebuild")
        self.assertFalse(eng._world_state_step.last_seed_used)
        self.assertEqual(eng._world_state_step.last_rounds, 2)  # 1 delta + 1 full

    def test_full_path_unchanged_without_seed(self):
        rounds = [{"content": "WORLD STATE — the room"}]
        mod, original, eng, calls = self._patched(rounds)
        try:
            doc = eng._world_state_step.run("[MC attention] I look around")
        finally:
            mod.call_llm = original
        self.assertEqual(doc, "WORLD STATE — the room")
        self.assertFalse(eng._world_state_step.last_seed_used)
        self.assertTrue(eng._world_state_step.last_ok)
        self.assertEqual(eng._world_state_step.last_rounds, 1)
        user_msg = calls["messages"][0][1]["content"]
        self.assertIn("BASELINE CONTEXT", user_msg)
        self.assertNotIn("PRECOMPUTED WORLD STATE", user_msg)

    # -- UNCHANGED short-circuit ------------------------------------------

    def test_unchanged_short_circuit_returns_seed_verbatim(self):
        rounds = [{"content": "UNCHANGED"}]
        mod, original, eng, calls = self._patched(rounds)
        try:
            with self.assertLogs("rpjot_engine", level="INFO") as cm:
                doc = eng._world_state_step.run(
                    '[MC speaks aloud]: "hello there"',
                    seed_doc="SEEDED DOC MARKER",
                )
        finally:
            mod.call_llm = original
        self.assertEqual(doc, "SEEDED DOC MARKER")
        self.assertTrue(eng._world_state_step.last_seed_used)
        self.assertTrue(eng._world_state_step.last_unchanged)
        self.assertEqual(eng._world_state_step.last_rounds, 1)
        self.assertTrue(
            any("UNCHANGED short-circuit" in m for m in cm.output)
        )
        # The sentinel is offered in the delta instructions.
        user_msg = calls["messages"][0][1]["content"]
        self.assertIn("UNCHANGED", user_msg)

    def test_unchanged_tolerates_current_room_line_and_punctuation(self):
        rounds = [{"content": "CURRENT ROOM: UNCHANGED\nUnchanged."}]
        mod, original, eng, _ = self._patched(rounds)
        try:
            doc = eng._world_state_step.run(
                "[MC attention] I study her face", seed_doc="SEEDED DOC MARKER"
            )
        finally:
            mod.call_llm = original
        self.assertEqual(doc, "SEEDED DOC MARKER")
        self.assertTrue(eng._world_state_step.last_seed_used)

    def test_unchanged_distrusted_on_mobile_turn(self):
        rounds = [
            {"content": "UNCHANGED"},  # delta claims nothing changed...
            {"content": "WORLD STATE — full rebuild"},  # ...but MC moved
        ]
        mod, original, eng, _ = self._patched(rounds)
        try:
            with self.assertLogs("rpjot_engine", level="WARNING") as cm:
                doc = eng._world_state_step.run(
                    "[MC action]: I walk into the gallery",
                    seed_doc="SEEDED DOC MARKER",
                )
        finally:
            mod.call_llm = original
        self.assertEqual(doc, "WORLD STATE — full rebuild")
        self.assertFalse(eng._world_state_step.last_seed_used)
        self.assertEqual(eng._world_state_step.last_rounds, 2)  # 1 delta + 1 full
        self.assertTrue(any("mobile turn distrusted" in m for m in cm.output))

    def test_prose_containing_unchanged_is_not_sentinel(self):
        rounds = [
            {"content": "WORLD STATE — the mood is unchanged since arrival."}
        ]
        mod, original, eng, _ = self._patched(rounds)
        try:
            doc = eng._world_state_step.run(
                '[MC speaks aloud]: "hello"', seed_doc="SEEDED DOC MARKER"
            )
        finally:
            mod.call_llm = original
        # A doc that merely mentions the word streams through as a normal delta.
        self.assertEqual(doc, "WORLD STATE — the mood is unchanged since arrival.")
        self.assertTrue(eng._world_state_step.last_seed_used)
        self.assertFalse(eng._world_state_step.last_unchanged)  # ordinary hit


# ---------------------------------------------------------------------------
# 12f. Speculative seed lifecycle — speculate_step1 / _consume_seed
# ---------------------------------------------------------------------------


class TestSpeculativeSeed(unittest.TestCase):
    """Idle-window speculation stores a validated, single-use seed."""

    def _patched(self, rounds):
        import rpjot as rpjot_module

        calls = {"i": 0}

        def fake_call_llm(messages, **kwargs):
            r = rounds[min(calls["i"], len(rounds) - 1)]
            calls["i"] += 1
            if isinstance(r, Exception):
                raise r
            return r

        original = rpjot_module.call_llm
        rpjot_module.call_llm = fake_call_llm
        eng = _make_engine(location="ravenwood-manor", people={"player"})
        eng.init_pipeline()
        eng.seed_enabled = True
        return rpjot_module, original, eng, calls

    def test_clean_speculation_stores_seed(self):
        rounds = [
            {"content": "CURRENT ROOM: UNCHANGED\nWORLD STATE — the gallery"}
        ]
        mod, original, eng, _ = self._patched(rounds)
        eng._turn_refs = ["turn-ref-marker"]
        try:
            eng.speculate_step1()
        finally:
            mod.call_llm = original
        self.assertIsNotNone(eng._seed)
        self.assertEqual(eng._seed["doc"], "WORLD STATE — the gallery")
        self.assertNotIn("CURRENT ROOM", eng._seed["doc"])
        self.assertEqual(eng._seed["turn"], eng._turn_count)
        self.assertEqual(eng._seed["state"], eng._seed_state_snapshot())
        # Citation isolation: the completed turn's refs are untouched; the
        # speculative run's refs travel with the seed.
        self.assertEqual(eng._turn_refs, ["turn-ref-marker"])
        self.assertEqual(eng._seed["refs"], [])

    def test_disabled_speculation_noops(self):
        rounds = [{"content": "WORLD STATE — the gallery"}]
        mod, original, eng, calls = self._patched(rounds)
        eng.seed_enabled = False
        try:
            eng.speculate_step1()
        finally:
            mod.call_llm = original
        self.assertIsNone(eng._seed)
        self.assertEqual(calls["i"], 0)

    def test_fallback_doc_discarded(self):
        import requests as requests_module

        # Every call raises → full path returns _fallback_doc, last_ok False.
        rounds = [requests_module.exceptions.RequestException("down")]
        mod, original, eng, _ = self._patched(rounds)
        try:
            eng.speculate_step1()
        finally:
            mod.call_llm = original
        self.assertIsNone(eng._seed)

    def test_unexpected_exception_swallowed(self):
        rounds = [ValueError("bad json from endpoint")]
        mod, original, eng, _ = self._patched(rounds)
        eng._turn_refs = ["turn-ref-marker"]
        try:
            with self.assertLogs("rpjot_engine", level="WARNING") as cm:
                eng.speculate_step1()  # must not raise
        finally:
            mod.call_llm = original
        self.assertIsNone(eng._seed)
        self.assertTrue(
            any("[SEED] speculative step-1 failed" in m for m in cm.output)
        )
        # finally-block restored the completed turn's refs.
        self.assertEqual(eng._turn_refs, ["turn-ref-marker"])

    # -- _consume_seed validation ------------------------------------------

    def _engine_with_seed(self):
        eng = _make_engine(location="ravenwood-manor", people={"player"})
        eng.init_pipeline()
        eng.seed_enabled = True
        eng._seed = {
            "doc": "WORLD STATE — seeded",
            "refs": ["r1"],
            "state": eng._seed_state_snapshot(),
            "turn": eng._turn_count,
            "rounds": 1,
            "elapsed": 0.5,
        }
        return eng

    def test_consume_hit_on_matching_state(self):
        eng = self._engine_with_seed()
        seed = eng._consume_seed()
        self.assertIsNotNone(seed)
        self.assertEqual(seed["doc"], "WORLD STATE — seeded")
        self.assertIsNone(eng._seed)  # single-use pop

    def test_consume_miss_on_location_change(self):
        eng = self._engine_with_seed()
        eng.session.location = "somewhere-else"
        self.assertIsNone(eng._consume_seed())
        self.assertEqual(eng._seed_status, "miss")

    def test_consume_miss_on_turn_advance(self):
        eng = self._engine_with_seed()
        eng._turn_count += 1
        self.assertIsNone(eng._consume_seed())
        self.assertEqual(eng._seed_status, "miss")

    def test_consume_single_use(self):
        eng = self._engine_with_seed()
        self.assertIsNotNone(eng._consume_seed())
        self.assertIsNone(eng._consume_seed())
        self.assertEqual(eng._seed_status, "miss")

    def test_consume_disabled_reports_off(self):
        eng = self._engine_with_seed()
        eng.seed_enabled = False
        self.assertIsNone(eng._consume_seed())
        self.assertEqual(eng._seed_status, "off")

    def test_consume_miss_after_commit_location(self):
        # the record_event auto-move path (LM §3.7) must invalidate a seed
        # speculated in the pre-move room — same guarantee as a direct change.
        eng = self._engine_with_seed()
        eng._commit_location("somewhere-else", source="record_event")
        self.assertIsNone(eng._consume_seed())
        self.assertEqual(eng._seed_status, "miss")


# ---------------------------------------------------------------------------
# 12g. Seed wiring in run_turn — hit/miss status, ref adoption, [TIMING] tail
# ---------------------------------------------------------------------------


class TestSeedRunTurn(unittest.TestCase):
    """run_turn consumes the seed, finalizes hit/miss, and reports timing."""

    def _patched(self, rounds):
        import rpjot as rpjot_module

        calls = {"i": 0, "messages": []}

        def fake_call_llm(messages, **kwargs):
            calls["messages"].append([dict(m) for m in messages])
            r = rounds[min(calls["i"], len(rounds) - 1)]
            calls["i"] += 1
            return r

        original = rpjot_module.call_llm
        rpjot_module.call_llm = fake_call_llm
        eng = _make_engine(location="ravenwood-manor", people={"player"})
        eng.init_pipeline()
        return rpjot_module, original, eng, calls

    def _plant_seed(self, eng, refs=None):
        eng.seed_enabled = True
        eng._seed = {
            "doc": "WORLD STATE — precomputed gallery",
            "refs": refs or [],
            "state": eng._seed_state_snapshot(),
            "turn": eng._turn_count,
            "rounds": 2,
            "elapsed": 6.0,
        }

    def _run(self, eng):
        return eng.run_turn(
            "[MC action]: I wave",
            [{"role": "system", "content": "rules"}],
            [{"role": "system", "content": "prose"}],
        )

    def test_seed_hit_flow(self):
        rounds = [
            {"content": "WORLD STATE — updated"},  # step 1 delta
            {"content": "nothing canonical"},  # step 2
            {"content": "The gallery hums."},  # step 3
        ]
        mod, original, eng, calls = self._patched(rounds)
        self._plant_seed(eng, refs=["1783000001"])
        try:
            with self.assertLogs("rpjot_engine", level="INFO") as cm:
                self._run(eng)
        finally:
            mod.call_llm = original
        timing = [m for m in cm.output if "[TIMING] turn=" in m]
        self.assertEqual(len(timing), 1)
        self.assertIn("seed=hit", timing[0])
        # Step-1 message was the seeded delta form.
        step1_user = calls["messages"][0][1]["content"]
        self.assertIn("PRECOMPUTED WORLD STATE", step1_user)
        self.assertIn("precomputed gallery", step1_user)
        # Speculative provenance adopted (capture order preserved).
        self.assertEqual(eng._turn_refs[0], "1783000001")

    def test_unchanged_short_circuit_reports_hit_unchanged(self):
        rounds = [
            {"content": "UNCHANGED"},  # step 1 delta short-circuits
            {"content": "nothing canonical"},  # step 2
            {"content": "The gallery hums."},  # step 3
        ]
        mod, original, eng, _ = self._patched(rounds)
        self._plant_seed(eng, refs=["1783000001"])
        try:
            with self.assertLogs("rpjot_engine", level="INFO") as cm:
                self._run(eng)  # "[MC action]: I wave" — stationary
        finally:
            mod.call_llm = original
        timing = [m for m in cm.output if "[TIMING] turn=" in m]
        self.assertIn("seed=hit-unchanged", timing[0])
        # Seed doc reused verbatim, provenance still adopted.
        self.assertEqual(eng._seed_status, "hit-unchanged")
        self.assertEqual(eng._turn_refs[0], "1783000001")

    def test_delta_failure_reports_miss_and_drops_seed_refs(self):
        rounds = [
            {"content": ""},  # delta clean-but-empty → full rebuild
            {"content": "WORLD STATE — full"},  # full path
            {"content": "nothing canonical"},  # step 2
            {"content": "Quiet."},  # step 3
        ]
        mod, original, eng, _ = self._patched(rounds)
        self._plant_seed(eng, refs=["1783000001"])
        try:
            with self.assertLogs("rpjot_engine", level="INFO") as cm:
                self._run(eng)
        finally:
            mod.call_llm = original
        timing = [m for m in cm.output if "[TIMING] turn=" in m]
        self.assertIn("seed=miss", timing[0])
        self.assertNotIn("1783000001", eng._turn_refs)

    def test_enabled_without_seed_reports_miss(self):
        rounds = [
            {"content": "WORLD STATE — full"},
            {"content": "nothing canonical"},
            {"content": "Quiet."},
        ]
        mod, original, eng, _ = self._patched(rounds)
        eng.seed_enabled = True  # no seed planted
        try:
            with self.assertLogs("rpjot_engine", level="INFO") as cm:
                self._run(eng)
        finally:
            mod.call_llm = original
        timing = [m for m in cm.output if "[TIMING] turn=" in m]
        self.assertIn("seed=miss", timing[0])

    def test_bg_stats_passthrough_and_cleared(self):
        rounds = [
            {"content": "WORLD STATE — full"},
            {"content": "nothing canonical"},
            {"content": "Quiet."},
        ]
        mod, original, eng, _ = self._patched(rounds)
        eng._bg_stats = {"spec_s": 1.5, "refresh_s": 0.5, "wait_s": 0.2}
        try:
            with self.assertLogs("rpjot_engine", level="INFO") as cm:
                self._run(eng)
        finally:
            mod.call_llm = original
        timing = [m for m in cm.output if "[TIMING] turn=" in m]
        self.assertIn("bg=2.0s", timing[0])
        self.assertIn("wait=0.2s", timing[0])
        self.assertIsNone(eng._bg_stats)


# ---------------------------------------------------------------------------
# 12h. Idle worker — play.py background refresh + speculation
# ---------------------------------------------------------------------------


class TestIdleWorker(unittest.TestCase):
    """_idle_worker/start_idle_work: flag gating, stats, crash safety."""

    def _flags(self, play, seed=False, refresh=False):
        """Set play._BG_SEED/_BG_REFRESH; return restorer for finally."""
        saved = (play._BG_SEED, play._BG_REFRESH)
        play._BG_SEED, play._BG_REFRESH = seed, refresh

        def restore():
            play._BG_SEED, play._BG_REFRESH = saved

        return restore

    def test_refresh_path(self):
        import play

        restore = self._flags(play, refresh=True)
        eng = _make_engine()
        eng._system_refresh_pending = True
        step2 = [{"role": "system", "content": "OLD RULES"}]
        original = play.call_llm
        play.call_llm = lambda *a, **k: {"content": "REFRESHED RULES"}
        try:
            play._idle_worker(eng, step2)
        finally:
            play.call_llm = original
            restore()
        self.assertEqual(step2[0]["content"], "REFRESHED RULES")
        self.assertFalse(eng._system_refresh_pending)
        self.assertIn("refresh_s", eng._bg_stats)

    def test_refresh_skipped_when_not_pending(self):
        import play

        restore = self._flags(play, refresh=True)
        eng = _make_engine()
        eng._system_refresh_pending = False
        step2 = [{"role": "system", "content": "OLD RULES"}]
        original = play.call_llm
        play.call_llm = lambda *a, **k: self.fail("call_llm must not run")
        try:
            play._idle_worker(eng, step2)
        finally:
            play.call_llm = original
            restore()
        self.assertEqual(step2[0]["content"], "OLD RULES")
        self.assertNotIn("refresh_s", eng._bg_stats)

    def test_seed_path(self):
        import play

        restore = self._flags(play, seed=True)
        eng = _make_engine()
        called = []
        eng.speculate_step1 = lambda: called.append(1)
        try:
            play._idle_worker(eng, [])
        finally:
            restore()
        self.assertEqual(called, [1])
        self.assertIn("spec_s", eng._bg_stats)

    def test_both_off_no_thread_no_work(self):
        import play

        restore = self._flags(play)  # both off
        eng = _make_engine()
        eng.speculate_step1 = lambda: self.fail("must not speculate")
        try:
            self.assertIsNone(play.start_idle_work(eng, []))
            play._idle_worker(eng, [])
        finally:
            restore()
        self.assertEqual(eng._bg_stats, {})

    def test_worker_never_raises(self):
        import play

        restore = self._flags(play, seed=True)
        eng = _make_engine()

        def boom():
            raise RuntimeError("endpoint exploded")

        eng.speculate_step1 = boom
        try:
            with self.assertLogs("play", level="WARNING") as cm:
                play._idle_worker(eng, [])  # must not raise
        finally:
            restore()
        self.assertTrue(any("[BG] idle worker failed" in m for m in cm.output))
        self.assertIsInstance(eng._bg_stats, dict)

    def test_thread_smoke(self):
        import play

        restore = self._flags(play, seed=True)
        eng = _make_engine()
        called = []
        eng.speculate_step1 = lambda: called.append(1)
        try:
            thread = play.start_idle_work(eng, [])
            self.assertIsNotNone(thread)
            self.assertTrue(thread.daemon)
            self.assertEqual(thread.name, play._BGConsoleGate.THREAD_NAME)
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            # Gate attached to the rpjot console handler(s), not the file one.
            for h in play._rpjot_console_handlers():
                self.assertIn(play._BG_GATE, h.filters)
        finally:
            for h in play._rpjot_console_handlers():
                h.removeFilter(play._BG_GATE)
            restore()
        self.assertEqual(called, [1])
        self.assertIn("spec_s", eng._bg_stats)


class TestBGConsoleGate(unittest.TestCase):
    """Idle-thread console logs are withheld at the prompt, replayed at join."""

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    def _gated_logger(self, name):
        import play

        gate = play._BGConsoleGate()
        capture = self._Capture()
        capture.addFilter(gate)
        test_logger = logging.getLogger(name)
        test_logger.setLevel(logging.DEBUG)
        test_logger.propagate = False
        test_logger.addHandler(capture)
        self.addCleanup(test_logger.removeHandler, capture)
        return gate, capture, test_logger

    def _log_from_idle_thread(self, fn):
        import play

        worker = threading.Thread(target=fn, name=play._BGConsoleGate.THREAD_NAME)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())

    def test_gate_buffers_idle_thread_and_passes_main(self):
        gate, capture, test_logger = self._gated_logger("test-bg-gate")
        self._log_from_idle_thread(lambda: test_logger.info("from idle thread"))
        self.assertEqual(capture.records, [])  # withheld from console
        self.assertEqual(len(gate.buffer), 1)  # ...but buffered

        test_logger.info("from main thread")
        self.assertEqual(len(capture.records), 1)  # main passes through
        self.assertEqual(len(gate.buffer), 1)  # untouched

    def test_gate_flush_replays_in_order_and_clears(self):
        gate, capture, test_logger = self._gated_logger("test-bg-gate-flush")

        def log_three():
            for i in range(3):
                test_logger.info("bg record %d", i)

        self._log_from_idle_thread(log_three)
        self.assertEqual(len(gate.buffer), 3)

        gate.flush_to([capture])  # main thread → passes the filter
        self.assertEqual(len(capture.records), 3)
        self.assertEqual(gate.buffer, [])
        self.assertEqual(capture.records[0].getMessage(), "bg record 0")
        self.assertEqual(capture.records[2].getMessage(), "bg record 2")

    def test_console_handler_helper_excludes_file_handler(self):
        import play

        for h in play._rpjot_console_handlers():
            self.assertNotIsInstance(h, logging.FileHandler)


# ---------------------------------------------------------------------------
# 12h². Prose streaming — call_llm SSE mode + _StreamThinkGate
# ---------------------------------------------------------------------------


class TestCallLLMStreaming(unittest.TestCase):
    """call_llm(on_token=...) parses SSE and returns the standard shape."""

    class _FakeResp:
        def __init__(self, lines):
            self._lines = lines

        def raise_for_status(self):
            pass

        def iter_lines(self):
            return iter(self._lines)

        def json(self):  # non-streaming path fallback
            return {"choices": [{"message": {"role": "assistant", "content": "x"}}]}

    def _sse(self, text):
        return (
            "data: "
            + json.dumps({"choices": [{"delta": {"content": text}}]})
        ).encode()

    def test_streaming_accumulates_and_calls_on_token(self):
        import catjot as catjot_module

        lines = [
            b"",  # keepalive
            self._sse("The gal"),
            self._sse("lery "),
            b"data: {malformed",  # skipped, stream survives
            self._sse("hums."),
            b"data: [DONE]",
            self._sse("after done — never seen"),
        ]
        captured = {}

        def fake_post(url, headers=None, json=None, stream=False):
            captured["payload"] = json
            captured["stream_kw"] = stream
            return self._FakeResp(lines)

        tokens = []
        original = catjot_module.requests.post
        catjot_module.requests.post = fake_post
        try:
            msg = catjot_module.call_llm(
                [{"role": "user", "content": "hi"}], on_token=tokens.append
            )
        finally:
            catjot_module.requests.post = original
        self.assertEqual(tokens, ["The gal", "lery ", "hums."])
        self.assertEqual(msg, {"role": "assistant", "content": "The gallery hums."})
        self.assertTrue(captured["payload"]["stream"])
        self.assertTrue(captured["stream_kw"])

    def test_non_streaming_payload_has_no_stream_key(self):
        import catjot as catjot_module

        captured = {}

        def fake_post(url, headers=None, json=None, stream=False):
            captured["payload"] = json
            return self._FakeResp([])

        original = catjot_module.requests.post
        catjot_module.requests.post = fake_post
        try:
            catjot_module.call_llm([{"role": "user", "content": "hi"}])
        finally:
            catjot_module.requests.post = original
        self.assertNotIn("stream", captured["payload"])


class TestStreamThinkGate(unittest.TestCase):
    """Live-display filter: think/tool_call blocks withheld, prose streams."""

    def _feed_all(self, gate, chunks):
        return "".join(gate.feed(c) for c in chunks)

    def test_plain_prose_passes_through(self):
        from rpjot import _StreamThinkGate

        gate = _StreamThinkGate()
        self.assertEqual(gate.feed("The gallery "), "The gallery ")
        self.assertEqual(gate.feed("hums."), "hums.")

    def test_think_prefix_swallowed(self):
        from rpjot import _StreamThinkGate

        gate = _StreamThinkGate()
        shown = self._feed_all(
            gate,
            ["<think>plan the", " scene</think>", "\n\nThe gallery hums."],
        )
        self.assertEqual(shown, "The gallery hums.")

    def test_tag_split_across_feeds(self):
        from rpjot import _StreamThinkGate

        gate = _StreamThinkGate()
        shown = self._feed_all(
            gate, ["<th", "ink>hidden</th", "ink>Prose", " continues"]
        )
        self.assertEqual(shown, "Prose continues")

    def test_unclosed_think_displays_nothing(self):
        from rpjot import _StreamThinkGate

        gate = _StreamThinkGate()
        shown = self._feed_all(gate, ["<think>reasoning that never", " closes"])
        self.assertEqual(shown, "")

    def test_tool_call_block_swallowed(self):
        from rpjot import _StreamThinkGate

        gate = _StreamThinkGate()
        shown = self._feed_all(
            gate,
            ["Before. <tool_call>", '{"name": "x"}', "</tool_call> After."],
        )
        self.assertEqual(shown, "Before.  After.")

    def test_matches_strip_think_tags_on_closed_cases(self):
        from rpjot import _StreamThinkGate, RPJotEngine

        text = "<think>inner plan</think>\n\nThe hall glows. <tool_call>{}</tool_call>Then quiet."
        # Feed in awkward 3-char chunks to stress tag reassembly.
        gate = _StreamThinkGate()
        chunks = [text[i : i + 3] for i in range(0, len(text), 3)]
        shown = self._feed_all(gate, chunks)
        _, clean = RPJotEngine.strip_think_tags(text)
        self.assertEqual(shown.strip(), clean.strip())

    def test_false_alarm_angle_bracket_flushes(self):
        from rpjot import _StreamThinkGate

        gate = _StreamThinkGate()
        # "<t" could open a tag; "he" disambiguates — text must not be lost.
        shown = self._feed_all(gate, ["He said <t", "hen> we left"])
        self.assertEqual(shown, "He said <then> we left")


class TestProseStreaming(unittest.TestCase):
    """ProseStep streams through the gate to prose_stream_cb when set."""

    _RAW = "<think>plan it</think>\n\nThe gallery hums softly."

    def _patched(self, honor_streaming=True):
        import rpjot as rpjot_module

        captured = {}

        def fake_call_llm(messages, **kwargs):
            captured["on_token"] = kwargs.get("on_token")
            on_token = kwargs.get("on_token")
            if honor_streaming and on_token is not None:
                # Feed awkward chunks to exercise the gate in situ.
                for i in range(0, len(self._RAW), 7):
                    on_token(self._RAW[i : i + 7])
                return {"role": "assistant", "content": self._RAW}
            return {"role": "assistant", "content": self._RAW}

        original = rpjot_module.call_llm
        rpjot_module.call_llm = fake_call_llm
        eng = _make_engine(location="ravenwood-manor", people={"player"})
        eng.init_pipeline()
        return rpjot_module, original, eng, captured

    def _run_step3(self, eng):
        return eng._prose_step.run(
            "[MC action]: I wave",
            "WORLD STATE: a room",
            [],
            [],
            [{"role": "system", "content": "prose"}],
        )

    def test_streams_clean_prose_and_sets_flag(self):
        mod, original, eng, captured = self._patched()
        chunks = []
        eng.prose_stream_cb = chunks.append
        try:
            narrative = self._run_step3(eng)
        finally:
            mod.call_llm = original
        self.assertIsNotNone(captured["on_token"])
        self.assertTrue(eng._prose_streamed)
        self.assertEqual("".join(chunks), "The gallery hums softly.")
        self.assertEqual(narrative, "The gallery hums softly.")  # canonical path

    def test_no_callback_means_no_streaming(self):
        mod, original, eng, captured = self._patched()
        try:
            narrative = self._run_step3(eng)
        finally:
            mod.call_llm = original
        self.assertIsNone(captured["on_token"])
        self.assertFalse(eng._prose_streamed)
        self.assertEqual(narrative, "The gallery hums softly.")

    def test_all_think_response_streams_nothing(self):
        import rpjot as rpjot_module

        raw = "<think>only reasoning, no prose"

        def fake_call_llm(messages, **kwargs):
            on_token = kwargs.get("on_token")
            if on_token is not None:
                for i in range(0, len(raw), 5):
                    on_token(raw[i : i + 5])
            return {"role": "assistant", "content": raw}

        original = rpjot_module.call_llm
        rpjot_module.call_llm = fake_call_llm
        eng = _make_engine(location="ravenwood-manor", people={"player"})
        eng.init_pipeline()
        chunks = []
        eng.prose_stream_cb = chunks.append
        try:
            narrative = self._run_step3(eng)
        finally:
            rpjot_module.call_llm = original
        self.assertEqual(chunks, [])
        self.assertFalse(eng._prose_streamed)  # play falls back to full print
        self.assertEqual(narrative, "")

    def test_stream_printer_borders_and_flag_gating(self):
        import io
        import play
        from contextlib import redirect_stdout

        eng = _make_engine()
        printer = play.make_stream_printer(eng)
        out = io.StringIO()
        with redirect_stdout(out):
            eng._prose_streamed = False
            printer("First ")  # border printed (flag still False here)
            eng._prose_streamed = True  # ProseStep flips it after first emit
            printer("chunk.")
        text = out.getvalue()
        self.assertEqual(text.count("=" * 60), 1)  # top border exactly once
        self.assertIn("First chunk.", text.replace("\n", ""))

    def test_stream_flag_off_leaves_cb_unset(self):
        import play

        self.assertFalse(play._STREAM)  # env not set in the test run
        eng = _make_engine()
        self.assertIsNone(eng.prose_stream_cb)


# ---------------------------------------------------------------------------
# 12i. Compact-schema keep-list — _compact_step2_schemas (W7 / T3)
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
        # record_relationship/record_interior: the kind enum survives as
        # structure, but no merged-tool param keeps its description.
        for tool in (
            "record_mood",
            "save_yomi",
            "begin_scene",
            "record_relationship",
            "record_interior",
        ):
            for k, pdef in self._props(tool).items():
                self.assertNotIn(
                    "description", pdef, f"{tool}.{k} kept a description"
                )

    def test_hidden_legacy_tools_absent_from_compact_menu(self):
        # U1: the 19 fine-grained rel/int tools leave the LLM-facing menu but
        # stay registered and dispatchable (safety net for old histories).
        for name in self.engine._COMPACT_HIDDEN_TOOLS:
            self.assertNotIn(name, self.compact)
            self.assertIn(name, self.engine._step2_handlers)

    def test_merged_tools_present_with_kind_enums(self):
        rel_kind = self._props("record_relationship")["kind"]
        int_kind = self._props("record_interior")["kind"]
        self.assertEqual(tuple(rel_kind["enum"]), RPJotEngine._REL_KINDS)
        self.assertEqual(tuple(int_kind["enum"]), RPJotEngine._INT_KINDS)

    def test_compact_menu_is_fifteen_tools(self):
        self.assertEqual(len(self.compact), 15)

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
# 12e-3. Consolidated recorders — record_relationship / record_interior (U1)
# ---------------------------------------------------------------------------


class TestConsolidatedDispatch(unittest.TestCase):
    """The merged kind-enum recorders delegate to the legacy writers.

    The reader-visible contract — tag words and pwd — must be identical to
    the legacy tools' output: readers key off pwd/tag, never tool names, so
    storage stays byte-compatible while only the LLM-facing surface shrinks.
    """

    def setUp(self):
        import tempfile

        self._saved_notefile = Note.NOTEFILE
        fd, self._path = tempfile.mkstemp(suffix=".jot")
        os.close(fd)
        Note.NOTEFILE = self._path
        self.engine = _make_engine(location="ravenwood-manor", people={"player"})

    def tearDown(self):
        Note.NOTEFILE = self._saved_notefile
        try:
            os.remove(self._path)
        except OSError:
            pass

    def _dispatch(self, name, args):
        return self.engine._dispatch_step2(name, json.dumps(args))

    def _last_note(self):
        notes = list(Note.iterate(Note.NOTEFILE))
        self.assertTrue(notes, "no note was written")
        return notes[-1]

    def test_all_rel_kinds_write_legacy_tag_and_pwd(self):
        from rpjot import PWD_REL

        for kind in RPJotEngine._REL_KINDS:
            with self.subTest(kind=kind):
                result = self._dispatch(
                    "record_relationship",
                    {
                        "kind": kind,
                        "char_a": "evie",
                        "char_b": "sam",
                        "description": "a defining fact between them",
                    },
                )
                self.assertNotIn("error", json.loads(result))
                note = self._last_note()
                words = note.tag.split()
                self.assertIn(f"rel:{kind}", words)
                self.assertIn("char:evie", words)
                self.assertIn("char:sam", words)
                self.assertEqual(note.pwd, f"{PWD_REL}/evie-sam")

    def test_all_int_kinds_write_legacy_tag_and_pwd(self):
        from rpjot import PWD_INTERIOR

        for kind in RPJotEngine._INT_KINDS:
            with self.subTest(kind=kind):
                result = self._dispatch(
                    "record_interior",
                    {
                        "kind": kind,
                        "character": "evie",
                        "content": "a private truth",
                        "target": "sam",
                        "detail": "the second layer",
                    },
                )
                self.assertNotIn("error", json.loads(result))
                note = self._last_note()
                words = note.tag.split()
                self.assertIn(f"int:{kind}", words)
                self.assertIn("char:evie", words)
                self.assertEqual(note.pwd, f"{PWD_INTERIOR}/evie")

    def test_merged_bond_matches_legacy_bond_output(self):
        self._dispatch(
            "record_relationship",
            {
                "kind": "bond",
                "char_a": "evie",
                "char_b": "sam",
                "description": "grew up together in the manor",
                "label": "old-friends",
            },
        )
        merged = self._last_note()
        self._dispatch(
            "record_bond",
            {
                "char_a": "evie",
                "char_b": "sam",
                "bond_type": "old-friends",
                "description": "grew up together in the manor",
            },
        )
        legacy = self._last_note()
        self.assertEqual(merged.tag, legacy.tag)
        self.assertEqual(merged.pwd, legacy.pwd)
        self.assertEqual(merged.message, legacy.message)

    def test_merged_subtext_matches_legacy_subtext_output(self):
        self._dispatch(
            "record_interior",
            {
                "kind": "subtext",
                "character": "evie",
                "content": "Lovely weather for a walk.",
                "target": "sam",
                "detail": "come with me, away from the others",
            },
        )
        merged = self._last_note()
        self._dispatch(
            "record_subtext",
            {
                "speaker": "evie",
                "statement": "Lovely weather for a walk.",
                "actual_meaning": "come with me, away from the others",
                "audience": "sam",
            },
        )
        legacy = self._last_note()
        self.assertEqual(merged.tag, legacy.tag)
        self.assertEqual(merged.pwd, legacy.pwd)
        self.assertEqual(merged.message, legacy.message)

    def test_unknown_kind_returns_error_json(self):
        result = self._dispatch(
            "record_relationship",
            {"kind": "nemesis", "char_a": "a", "char_b": "b", "description": "x"},
        )
        payload = json.loads(result)
        self.assertIn("error", payload)
        self.assertIn("bond", payload["hint"])
        # Nothing may be written on a rejected kind.
        self.assertEqual(len(list(Note.iterate(Note.NOTEFILE))), 0)

    def test_omitted_optionals_still_produce_valid_notes(self):
        result = self._dispatch(
            "record_interior",
            {"kind": "longing", "character": "evie", "content": "to be seen"},
        )
        self.assertNotIn("error", json.loads(result))
        note = self._last_note()
        self.assertIn("int:longing", note.tag.split())
        self.assertTrue(note.message.strip())


# ---------------------------------------------------------------------------
# 12e-4. Entry citations — read side over seeded fixtures (TS_CITATIONS, U3)
# ---------------------------------------------------------------------------


class TestEntryCitation(unittest.TestCase):
    """C1/C3/C6/C7/C8 read-side contract over hand-seeded ref: fixtures.

    No writers exist yet at this phase — refs only exist where the fixture
    seeds them, exactly how TestObjectPermanence seeds obj: history.
    """

    T = 1_750_000_000

    def setUp(self):
        import tempfile
        from rpjot import PWD_EVENTS, PWD_INTERIOR

        self._saved_notefile = Note.NOTEFILE
        fd, self._path = tempfile.mkstemp(suffix=".jot")
        os.close(fd)
        Note.NOTEFILE = self._path

        T = self.T
        seeds = [
            # grand-origin note — cited by the event, must NOT surface via
            # depth-1 deref from the interior note (non-transitivity).
            dict(
                message="grand-origin note",
                tag="exp:sam",
                context="origin",
                pwd=f"{PWD_EVENTS}/foyer",
                now=T,
            ),
            # the betrayal event — outside evie's POV terms (exp:sam only).
            dict(
                message="Sam read Evie's letters aloud at the fountain.",
                tag=f"exp:sam ref:{T}",
                context="the betrayal",
                pwd=f"{PWD_EVENTS}/foyer",
                now=T + 100,
            ),
            # citing interior note — visible in evie's POV, cites the event.
            dict(
                message="Evie still carries the fountain betrayal.",
                tag=f"int:secret char:evie ref:{T + 100}",
                context="secret: evie",
                pwd=f"{PWD_INTERIOR}/evie",
                now=T + 200,
            ),
            # same-second twins (C3): one ref must resolve to both.
            dict(
                message="twin-A of the shared second",
                tag="exp:sam",
                context="twin",
                pwd=f"{PWD_EVENTS}/foyer",
                now=T + 300,
            ),
            dict(
                message="twin-B of the shared second",
                tag="exp:sam",
                context="twin",
                pwd=f"{PWD_EVENTS}/foyer",
                now=T + 300,
            ),
            dict(
                message="Evie wants what the twins had.",
                tag=f"int:desire char:evie ref:{T + 300}",
                context="desire: evie",
                pwd=f"{PWD_INTERIOR}/evie",
                now=T + 400,
            ),
            # dangling ref (C8): cites a second no note occupies.
            dict(
                message="Evie longs for a moment no record holds.",
                tag=f"int:longing char:evie ref:{T + 900_000}",
                context="longing: evie",
                pwd=f"{PWD_INTERIOR}/evie",
                now=T + 500,
            ),
        ]
        for s in seeds:
            Note.append(Note.NOTEFILE, Note.jot(**s))
        self.engine = _make_engine(location="ravenwood-manor", people={"player"})

    def tearDown(self):
        Note.NOTEFILE = self._saved_notefile
        try:
            os.remove(self._path)
        except OSError:
            pass

    # --- C1: form ---

    def test_parse_refs_round_trip(self):
        refs = RPJotEngine._parse_refs("int:secret ref:123 char:x ref:456")
        self.assertEqual(refs, [123, 456])
        self.assertEqual(RPJotEngine._format_refs(refs), "ref:123 ref:456")

    def test_garbage_ref_words_skipped(self):
        self.assertEqual(
            RPJotEngine._parse_refs("ref: ref:abc ref:12x ref:-5 reference:9"),
            [],
        )

    # --- flagship: deref pulls the cited entry into the POV ---

    def test_citing_note_pulls_cited_event_into_pov(self):
        bundle = self.engine.gather_pov_context("evie")
        messages = [n.message for n in bundle]
        self.assertTrue(
            any("letters aloud" in m for m in messages),
            "cited event was not dereferenced into the POV bundle",
        )

    def test_rendered_pov_carries_refs_marker(self):
        bundle = self.engine.gather_pov_context("evie")
        rendered = self.engine.render_context(bundle)
        self.assertIn(f"[refs: {self.T + 100}]", rendered)

    # --- C6: depth-1 only ---

    def test_deref_is_not_transitive(self):
        bundle = self.engine.gather_pov_context("evie")
        messages = [n.message for n in bundle]
        self.assertFalse(
            any("grand-origin" in m for m in messages),
            "depth-2 ref was followed — deref must stop at one hop",
        )

    def test_deref_cap_honored(self):
        from rpjot import _REF_DEREF_CAP, PWD_EVENTS, PWD_INTERIOR

        T2 = self.T + 10_000
        for i in range(14):
            Note.append(
                Note.NOTEFILE,
                Note.jot(
                    message=f"capnote-{i}",
                    tag="exp:sam",
                    context="cap target",
                    pwd=f"{PWD_EVENTS}/foyer",
                    now=T2 + i,
                ),
            )
        ref_words = " ".join(f"ref:{T2 + i}" for i in range(14))
        Note.append(
            Note.NOTEFILE,
            Note.jot(
                message="the over-citing note",
                tag=f"int:secret char:overciter {ref_words}",
                context="secret: overciter",
                pwd=f"{PWD_INTERIOR}/overciter",
                now=T2 + 500,
            ),
        )
        bundle = self.engine.gather_pov_context("overciter")
        cap_messages = [n.message for n in bundle if n.message.startswith("capnote-")]
        self.assertEqual(len(cap_messages), _REF_DEREF_CAP)

    # --- C3: multi-resolution ---

    def test_same_second_ref_derefs_all(self):
        bundle = self.engine.gather_pov_context("evie")
        messages = " ".join(n.message for n in bundle)
        self.assertIn("twin-A", messages)
        self.assertIn("twin-B", messages)

    # --- C8: durability / decay ---

    def test_dangling_ref_is_silent_noop(self):
        bundle = self.engine.gather_pov_context("evie")
        rendered = self.engine.render_context(bundle)
        # No crash, and the marker renders as-is for the dangling second.
        self.assertIn(f"[refs: {self.T + 900_000}]", rendered)

    # --- C7: inertness ---

    def test_ref_words_inert_to_object_registry(self):
        from rpjot import PWD_WORLD

        Note.append(
            Note.NOTEFILE,
            Note.jot(
                message="An iron key rests on the sill.",
                tag=f"obj:iron-key ref:{self.T}",
                context="sighting",
                pwd=f"{PWD_WORLD}/ravenwood-manor",
                now=self.T + 600,
            ),
        )
        registry = self.engine._object_registry()
        self.assertIn("iron-key", registry)
        self.assertEqual(
            registry["iron-key"]["residence"].get("room"), "ravenwood-manor"
        )

    # --- backlinks seam ---

    def test_backlinks_finds_citing_note(self):
        backlinks = self.engine._backlinks(self.T + 100)
        self.assertEqual(len(backlinks), 1)
        self.assertIn("fountain betrayal", backlinks[0].message)


# ---------------------------------------------------------------------------
# 12e-5. Citation capture — targeted lookups + cache replay (TS §3.1/§3.4, U4)
# ---------------------------------------------------------------------------


class TestCitationCapture(unittest.TestCase):
    """Targeted step-1 lookups capture provenance; cache hits replay it."""

    T = 1_750_000_000

    def setUp(self):
        import tempfile
        from rpjot import PWD_CHARS, PWD_OBJECTS, PWD_WORLD

        self._saved_notefile = Note.NOTEFILE
        fd, self._path = tempfile.mkstemp(suffix=".jot")
        os.close(fd)
        Note.NOTEFILE = self._path

        T = self.T
        seeds = [
            dict(
                message="Evie, the youngest sister, sharp-eyed and careful.",
                tag="char:evie",
                context="profile",
                pwd=f"{PWD_CHARS}/evie",
                now=T,
            ),
            dict(
                message="The iron key, cold and old.",
                tag="obj:iron-key",
                context="canonical",
                pwd=f"{PWD_OBJECTS}/iron-key",
                now=T + 100,
            ),
            dict(
                message="The key sits on the mantel.",
                tag="obj:iron-key",
                context="sighting",
                pwd=f"{PWD_WORLD}/ravenwood-manor",
                now=T + 200,
            ),
            dict(
                message="Evie pockets the key.",
                tag="obj:iron-key",
                context="sighting",
                pwd=f"{PWD_CHARS}/evie/inventory",
                now=T + 300,
            ),
            dict(
                message="The key changes hands again.",
                tag="obj:iron-key",
                context="sighting",
                pwd=f"{PWD_WORLD}/ravenwood-manor",
                now=T + 400,
            ),
        ]
        for s in seeds:
            Note.append(Note.NOTEFILE, Note.jot(**s))
        self.engine = _make_engine(location="ravenwood-manor", people={"player"})
        self.engine._turn_refs = []

    def tearDown(self):
        Note.NOTEFILE = self._saved_notefile
        try:
            os.remove(self._path)
        except OSError:
            pass

    def test_targeted_lookup_captures_refs(self):
        self.engine._tool_get_character("evie")
        self.assertIn(self.T, self.engine._turn_refs)

    def test_cache_hit_capture_parity(self):
        self.engine._tool_get_character("evie")
        first = list(self.engine._turn_refs)
        self.assertTrue(first)
        self.engine._turn_refs = []
        self.engine._tool_get_character("evie")  # cache hit path
        self.assertEqual(self.engine._turn_refs, first)

    def test_cache_drop_evicts_ref_cache_in_lockstep(self):
        self.engine._tool_get_character("evie")
        self.assertIn("char:evie", self.engine._ref_cache)
        self.engine._cache_drop("char:evie")
        self.assertNotIn("char:evie", self.engine._ref_cache)

    def test_get_object_cites_canonical_plus_two_newest_sightings(self):
        self.engine._tool_get_object("iron-key")
        # canonical (T+100) + the two newest sightings (T+400, T+300);
        # the oldest sighting (T+200) is beyond the per-lookup cap.
        self.assertIn(self.T + 100, self.engine._turn_refs)
        self.assertIn(self.T + 400, self.engine._turn_refs)
        self.assertIn(self.T + 300, self.engine._turn_refs)
        self.assertNotIn(self.T + 200, self.engine._turn_refs)

    def test_ambient_tools_capture_nothing(self):
        import rpjot as rpjot_module

        original = rpjot_module.call_llm
        rpjot_module.call_llm = lambda *a, **k: {
            "content": '{"description": "a quiet room", "mood": "calm"}'
        }
        try:
            self.engine._tool_get_people_present()
            self.engine._tool_examine_location()
        finally:
            rpjot_module.call_llm = original
        self.assertEqual(self.engine._turn_refs, [])

    def test_per_lookup_cap(self):
        from rpjot import _REF_CAP_PER_LOOKUP, PWD_CHARS

        for i in range(6):
            Note.append(
                Note.NOTEFILE,
                Note.jot(
                    message=f"More about Evie, part {i}.",
                    tag="char:evie",
                    context="profile addendum",
                    pwd=f"{PWD_CHARS}/evie",
                    now=self.T + 1000 + i,
                ),
            )
        self.engine._tool_get_character("evie")
        self.assertEqual(len(self.engine._turn_refs), _REF_CAP_PER_LOOKUP)


# ---------------------------------------------------------------------------
# 12e-6. Citation stamps — C4 scope + C5 guard on step-2 writers (U5)
# ---------------------------------------------------------------------------


class TestCitationStamps(unittest.TestCase):
    """Derived-narrative writers stamp _turn_refs; canonical writers never do."""

    T = 1_750_000_000

    def setUp(self):
        import tempfile
        from rpjot import PWD_EVENTS

        self._saved_notefile = Note.NOTEFILE
        fd, self._path = tempfile.mkstemp(suffix=".jot")
        os.close(fd)
        Note.NOTEFILE = self._path
        # A past event other lookups could have surfaced this turn.
        Note.append(
            Note.NOTEFILE,
            Note.jot(
                message="Sam read Evie's letters aloud at the fountain.",
                tag="exp:sam",
                context="the betrayal",
                pwd=f"{PWD_EVENTS}/foyer",
                now=self.T,
            ),
        )
        self.engine = _make_engine(location="ravenwood-manor", people={"player"})
        self.engine._turn_refs = [self.T]

    def tearDown(self):
        Note.NOTEFILE = self._saved_notefile
        try:
            os.remove(self._path)
        except OSError:
            pass

    def _dispatch(self, name, args):
        return self.engine._dispatch_step2(name, json.dumps(args))

    def _notes(self):
        return list(Note.iterate(Note.NOTEFILE))

    def test_record_event_stamps(self):
        self._dispatch(
            "record_event",
            {"description": "Evie confronts Sam.", "tags": "exp:evie exp:sam"},
        )
        self.assertIn(f"ref:{self.T}", self._notes()[-1].tag.split())

    def test_record_knowledge_stamps_both_notes(self):
        self._dispatch(
            "record_knowledge",
            {
                "content": "Evie admits she kept one letter.",
                "witnesses": ["evie"],
                "observable_act": "Evie murmurs something to herself.",
            },
        )
        private, public = self._notes()[-2], self._notes()[-1]
        self.assertIn(f"ref:{self.T}", private.tag.split())
        self.assertIn(f"ref:{self.T}", public.tag.split())

    def test_merged_relationship_stamps(self):
        # I-3: delegation through record_relationship preserves stamping.
        self._dispatch(
            "record_relationship",
            {
                "kind": "wound",
                "char_a": "sam",
                "char_b": "evie",
                "description": "The reading-aloud still stings.",
            },
        )
        words = self._notes()[-1].tag.split()
        self.assertIn("rel:wound", words)
        self.assertIn(f"ref:{self.T}", words)

    def test_canonical_and_structural_writers_stamp_nothing(self):
        self._dispatch(
            "save_character",
            {"name": "winnie", "description": "The steady housekeeper."},
        )
        self._dispatch(
            "save_location",
            {"name": "east-parlor", "description": "Dust and velvet."},
        )
        self._dispatch(
            "begin_scene",
            {"name": "confrontation", "description": "Evie corners Sam."},
        )
        for note in self._notes()[1:]:  # skip the seeded event
            self.assertFalse(
                [w for w in note.tag.split() if w.startswith("ref:")],
                f"canonical/structural note stamped a ref: {note.tag}",
            )

    def test_future_and_same_second_refs_dropped_at_stamp(self):
        import time as _time

        self.engine._turn_refs = [int(_time.time()) + 100]
        self._dispatch(
            "record_event",
            {"description": "Nothing cites the future.", "tags": "exp:evie"},
        )
        self.assertFalse(
            [w for w in self._notes()[-1].tag.split() if w.startswith("ref:")]
        )

    def test_per_note_cap(self):
        from rpjot import _REF_CAP_PER_NOTE

        self.engine._turn_refs = [self.T + i for i in range(_REF_CAP_PER_NOTE + 3)]
        self._dispatch(
            "record_event",
            {"description": "A heavily sourced event.", "tags": "exp:evie"},
        )
        ref_words = [
            w for w in self._notes()[-1].tag.split() if w.startswith("ref:")
        ]
        self.assertEqual(len(ref_words), _REF_CAP_PER_NOTE)

    def test_stamp_dedupes_existing_word(self):
        stamped = self.engine._stamp_refs(f"exp:evie ref:{self.T}")
        self.assertEqual(stamped.split().count(f"ref:{self.T}"), 1)

    def test_no_ref_in_any_schema(self):
        # C2: no tool schema exposes, accepts, or describes citations.
        blob = json.dumps(
            self.engine._step1_schemas
            + self.engine._step2_schemas
            + self.engine._compact_step2_schemas
        )
        self.assertNotIn("ref:", blob)

    def test_stamped_knowledge_derefs_into_pov(self):
        # End-to-end write→read over the mocked pipeline: a stamped private
        # note pulls the cited event into the witness's POV next lookup.
        self._dispatch(
            "record_knowledge",
            {"content": "Evie admits she kept one letter.", "witnesses": ["evie"]},
        )
        bundle = self.engine.gather_pov_context("evie")
        messages = [n.message for n in bundle]
        self.assertTrue(any("letters aloud" in m for m in messages))


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
# 12f-bis. Third-person self-movement (mc_aliases branch)
# ---------------------------------------------------------------------------


class TestThirdPersonMovement(unittest.TestCase):
    """The alias branch: players who write 'Bartholomew enters the gallery'.

    Live finding 2026-07-03: 58/60 turns classified stationary and navigate_to
    fired 0 times, because the classifier only knew first-person "I <verb>".
    Empty alias set must reproduce legacy behavior bit-for-bit.
    """

    ALIASES = frozenset({"bartholomew", "bart"})

    def _stat(self, classified_input, aliases=ALIASES):
        from rpjot import ComplianceStep

        return ComplianceStep._is_stationary_turn(classified_input, aliases)

    def test_third_person_move_is_mobile(self):
        self.assertFalse(
            self._stat("[MC action]: Bartholomew enters the gallery, smirking.")
        )

    def test_third_person_alias_short_form(self):
        self.assertFalse(self._stat("[MC action]: Bart walks to the cellar."))

    def test_empty_aliases_is_legacy_stationary(self):
        # back-compat: without aliases the same input stays stationary.
        self.assertTrue(
            self._stat(
                "[MC action]: Bartholomew enters the gallery.", aliases=frozenset()
            )
        )

    def test_npc_subject_is_stationary(self):
        # an NPC moving is not the MC moving — no alias match, nudge fires.
        self.assertTrue(self._stat("[MC action]: Evie walks toward the door."))

    def test_third_person_without_move_verb_is_stationary(self):
        self.assertTrue(
            self._stat("[MC action]: Bartholomew studies the painting closely.")
        )

    def test_gerund_is_stationary(self):
        # deliberation about movement is not movement (no gerunds in 3P set).
        self.assertTrue(
            self._stat("[MC action]: Bartholomew considers going to the cellar.")
        )

    def test_quoted_body_is_stationary(self):
        self.assertTrue(
            self._stat('[MC action]: "Bartholomew goes wherever he pleases."')
        )

    def test_speech_prefix_still_wins(self):
        # the prefix table is checked first: speech is stationary even if the
        # body reads like third-person movement.
        self.assertTrue(
            self._stat("[MC speaks aloud]: Bartholomew leaves. That's a promise.")
        )

    def test_first_person_branch_unaffected_by_aliases(self):
        self.assertFalse(self._stat("[MC action]: I follow her.", self.ALIASES))

    def test_nudge_omitted_for_third_person_move(self):
        from rpjot import ComplianceStep

        eng = _make_engine(location="ravenwood-manor", people={"mc"})
        eng.mc_aliases = frozenset({"mc", "bartholomew", "bart"})
        eng.init_pipeline()
        step = eng._compliance_step
        moved = step._compose_step2_user_content(
            "[MC action]: Bartholomew enters the gallery.", "WORLD STATE: x"
        )
        self.assertNotIn("DIRECTOR NOTE", moved)

    def test_nudge_still_fires_for_npc_invitation(self):
        # the swept neg_navto shape: an invitation is not movement.
        eng = _make_engine(location="ravenwood-manor", people={"mc"})
        eng.mc_aliases = frozenset({"mc", "bartholomew", "bart"})
        eng.init_pipeline()
        step = eng._compliance_step
        invited = step._compose_step2_user_content(
            '[MC speaks aloud]: "Show me the gallery sometime."', "WORLD STATE: x"
        )
        self.assertIn("DIRECTOR NOTE", invited)


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

    def _run_batching_loop(self, rounds, followup_by_call=None):
        """Drive ComplianceStep.run with scripted rounds; returns messages seen
        by the final LLM call (the fullest view of the loop's transcript)."""
        import rpjot as rpjot_module

        calls = {"i": 0, "last_msgs": None}

        def fake_call_llm(messages, **kwargs):
            calls["last_msgs"] = [dict(m) for m in messages]
            r = rounds[min(calls["i"], len(rounds) - 1)]
            calls["i"] += 1
            return r

        eng = _make_engine(location="ravenwood-manor", people={"player"})
        eng.init_pipeline()
        n = {"i": 0}

        def fake_dispatch(name, args):
            n["i"] += 1
            instruction = (
                followup_by_call[n["i"] - 1]
                if followup_by_call
                else "gather more before narrating"
            )
            payload = {"ok": True}
            if instruction:
                payload["followup_instruction"] = instruction
            return json.dumps(payload)

        eng._dispatch_step2 = fake_dispatch
        original = rpjot_module.call_llm
        rpjot_module.call_llm = fake_call_llm
        try:
            eng._compliance_step.run(
                "[MC action]: I act",
                "WORLD",
                [{"role": "system", "content": "rules"}],
            )
        finally:
            rpjot_module.call_llm = original
        return calls["last_msgs"]

    def _tool_round(self, count):
        return {
            "tool_calls": [
                {
                    "id": f"c{k}",
                    "type": "function",
                    "function": {"name": "record_event", "arguments": "{}"},
                }
                for k in range(count)
            ]
        }

    def test_multi_tool_round_batches_one_directive(self):
        # U6: two tool calls with followups in ONE round → exactly one
        # [DIRECTIVE] message, placed after contiguous tool results.
        msgs = self._run_batching_loop(
            [self._tool_round(2), {"content": "done"}],
            followup_by_call=["first instruction", "second instruction"],
        )
        directives = [
            m for m in msgs
            if m.get("role") == "user"
            and str(m.get("content")).startswith("[DIRECTIVE]")
        ]
        self.assertEqual(len(directives), 1)
        self.assertIn("first instruction", directives[0]["content"])
        self.assertIn("second instruction", directives[0]["content"])
        # Tool results are contiguous: no user message between them.
        roles = [m.get("role") for m in msgs]
        first_tool = roles.index("tool")
        self.assertEqual(roles[first_tool : first_tool + 2], ["tool", "tool"])

    def test_repeated_instruction_deduped_across_rounds(self):
        # The same verbatim followup in round 2 is dropped (per-turn dedupe).
        msgs = self._run_batching_loop(
            [self._tool_round(1), self._tool_round(1), {"content": "done"}],
            followup_by_call=[
                "gather more before narrating",
                "gather more before narrating",
            ],
        )
        directives = [
            m for m in msgs
            if m.get("role") == "user"
            and str(m.get("content")).startswith("[DIRECTIVE]")
        ]
        self.assertEqual(len(directives), 1)


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


class TestTrueResume(unittest.TestCase):
    """True resume: restore last location, cast, and scene from the note file."""

    def setUp(self):
        import tempfile
        from rpjot import PWD_EVENTS, PWD_SCENES, PWD_WORLD, PWD_SUMMARIES

        self._saved_notefile = Note.NOTEFILE
        fd, self._path = tempfile.mkstemp(suffix=".jot")
        os.close(fd)
        Note.NOTEFILE = self._path

        def add(pwd, tag, msg, now, context="ctx"):
            Note.append(
                Note.NOTEFILE,
                Note.jot(message=msg, tag=tag, context=context, pwd=pwd, now=now),
            )

        # Canonical location nodes (so canonicalization/existence checks work).
        for room in ("foyer", "garden", "manor/study"):
            add(f"{PWD_WORLD}/{room}", "", f"{room} node.", now=100)
        # Movement/event trail — newest event lands in manor/study.
        add(f"{PWD_EVENTS}/foyer", "nav", "Arrived in foyer.", now=200)
        add(f"{PWD_EVENTS}/garden", "nav", "Walked to garden.", now=300)
        add(f"{PWD_EVENTS}/manor/study", "nav", "Entered study.", now=400)
        # Scene trail — newest scene is the study conversation.
        add(f"{PWD_SCENES}/opening", "scene:opening", "Opening.", now=210)
        add(f"{PWD_SCENES}/study-talk", "scene:study-talk", "Study talk.", now=410)
        # Turn summaries so infer has something to read.
        for i in range(3):
            add(PWD_SUMMARIES, "summary", f"Turn {i} in the study with Evie.", now=500 + i)

        self.PWD_EVENTS = PWD_EVENTS
        self.PWD_SCENES = PWD_SCENES
        self.PWD_WORLD = PWD_WORLD

    def tearDown(self):
        Note.NOTEFILE = self._saved_notefile
        try:
            os.remove(self._path)
        except OSError:
            pass

    # --- recover_deterministic_state ---------------------------------------

    def test_recover_returns_newest_location_and_scene(self):
        import play

        location, scene = play.recover_deterministic_state()
        self.assertEqual(location, "manor/study")
        self.assertEqual(scene, "study-talk")

    def test_recover_empty_file_is_none(self):
        import play
        import tempfile

        saved = Note.NOTEFILE
        fd, empty = tempfile.mkstemp(suffix=".jot")
        os.close(fd)
        Note.NOTEFILE = empty
        try:
            self.assertEqual(play.recover_deterministic_state(), (None, None))
        finally:
            Note.NOTEFILE = saved
            os.remove(empty)

    def test_recover_normalizes_fragmented_slug(self):
        # a historically fragmented pwd (underscores — the live
        # manor/evie_quarters resume) must recover as the canonical hyphen form.
        import play

        Note.append(
            Note.NOTEFILE,
            Note.jot(
                message="Fragmented event.",
                tag="",
                context="ctx",
                pwd=f"{self.PWD_EVENTS}/manor/evie_quarters",
                now=999,
            ),
        )
        location, _ = play.recover_deterministic_state()
        self.assertEqual(location, "manor/evie-quarters")

    # --- infer_resume_state -------------------------------------------------

    def _engine_at(self, location="manor/study"):
        return _make_engine(location=location, people={"mc"})

    def test_infer_parses_llm_json(self):
        import play
        from unittest.mock import patch

        engine = self._engine_at()
        payload = (
            '{"location": "manor/study", "people_present": ["evie"], '
            '"mood": {"evie": "wary"}, "attention": {"evie": "mc"}}'
        )
        with patch.object(play, "call_llm", return_value={"content": payload}):
            data = play.infer_resume_state(engine, "manor/study")
        self.assertEqual(data["people_present"], ["evie"])
        self.assertEqual(data["mood"], {"evie": "wary"})

    def test_infer_bad_json_returns_none(self):
        import play
        from unittest.mock import patch

        engine = self._engine_at()
        with patch.object(play, "call_llm", return_value={"content": "no json here"}):
            self.assertIsNone(play.infer_resume_state(engine, "manor/study"))

    def test_infer_llm_error_returns_none(self):
        import play
        from unittest.mock import patch

        engine = self._engine_at()
        with patch.object(play, "call_llm", side_effect=RuntimeError("boom")):
            self.assertIsNone(play.infer_resume_state(engine, "manor/study"))

    def test_infer_no_summaries_returns_none(self):
        import play
        import tempfile
        from unittest.mock import patch

        saved = Note.NOTEFILE
        fd, empty = tempfile.mkstemp(suffix=".jot")
        os.close(fd)
        Note.NOTEFILE = empty
        try:
            engine = self._engine_at()
            with patch.object(play, "call_llm") as m:
                self.assertIsNone(play.infer_resume_state(engine, "foyer"))
                m.assert_not_called()  # short-circuits before any LLM call
        finally:
            Note.NOTEFILE = saved
            os.remove(empty)

    # --- apply_resume_state -------------------------------------------------

    def test_apply_commits_deterministic_location(self):
        import play

        engine = self._engine_at(location="foyer")
        play.apply_resume_state(engine, "manor/study", "study-talk", None)
        self.assertEqual(engine.session.location, "manor/study")
        self.assertTrue(
            any("manor/study" in d for d in engine.session.location_context.dirs)
        )
        self.assertTrue(engine._system_refresh_pending)

    def test_apply_restores_cast_mood_attention_filtered(self):
        import play

        engine = self._engine_at(location="manor/study")
        inferred = {
            "location": "manor/study",
            "people_present": ["evie", "MC"],
            "mood": {"evie": "wary", "ghost": "angry"},  # ghost not present
            "attention": {"evie": "mc"},
        }
        play.apply_resume_state(engine, "manor/study", "study-talk", inferred)
        self.assertEqual(engine.session.people_present, {"mc", "evie"})
        self.assertEqual(engine.session.mood, {"evie": "wary"})  # ghost dropped
        self.assertEqual(engine.session.attention, {"evie": "mc"})
        self.assertTrue(engine.npc_tracker.is_registered("evie"))

    def test_apply_reenters_scene_without_new_note(self):
        import play

        def scene_note_count():
            with NoteContext_TREE(self.PWD_SCENES) as n:
                return len(n)

        before = scene_note_count()
        engine = self._engine_at(location="manor/study")
        play.apply_resume_state(engine, "manor/study", "study-talk", None)
        self.assertEqual(engine.session.current_scene, "study-talk")
        self.assertEqual(scene_note_count(), before)  # no scene-header appended

    def test_apply_inferred_location_override_gated_by_node(self):
        import play

        # Existing node → override accepted.
        engine = self._engine_at(location="foyer")
        play.apply_resume_state(
            engine, "foyer", "study-talk", {"location": "garden"}
        )
        self.assertEqual(engine.session.location, "garden")

        # Phantom room → override rejected, deterministic room kept.
        engine2 = self._engine_at(location="foyer")
        play.apply_resume_state(
            engine2, "foyer", "study-talk", {"location": "atlantis"}
        )
        self.assertEqual(engine2.session.location, "foyer")

    # --- end-to-end ---------------------------------------------------------

    def test_resume_engine_end_to_end(self):
        import play
        from unittest.mock import patch

        payload = (
            '{"location": "manor/study", "people_present": ["evie"], '
            '"mood": {"evie": "guarded"}, "attention": {}}'
        )
        with patch.object(play, "call_llm", return_value={"content": payload}):
            engine = play._resume_engine()
        self.assertEqual(engine.session.location, "manor/study")
        self.assertEqual(engine.session.current_scene, "study-talk")
        self.assertEqual(engine.session.people_present, {"mc", "evie"})
        self.assertEqual(engine.session.mood, {"evie": "guarded"})
        self.assertTrue(engine._system_refresh_pending)


def NoteContext_TREE(path):
    """Small helper: open a NoteContext over a directory subtree for counting."""
    from catjot import NoteContext, SearchType

    return NoteContext(Note.NOTEFILE, (SearchType.TREE, path))


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
            seed(f"/story/location/{room}", "", room.split("/")[-1])

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

    def test_lexical_led_fallback_sibling_room(self):
        # garage is a SIBLING of foyer (both children of ravenwood-manor) —
        # the quarters→gallery class the child/root candidate set missed.
        eng = self._engine(self.FOYER)
        eng._remark_location(
            "[MC action]: she pulls me into the garage", "CURRENT ROOM: UNCHANGED"
        )
        self.assertEqual(eng.session.location, "ravenwood-manor/garage")

    # --- record_event explicit-location handling (LM §3.7 two-gate rule) ---

    def _mc_engine(self, location):
        eng = self._engine(location)
        eng.mc_aliases = frozenset({"mc", "bartholomew", "bart"})
        return eng

    def test_record_event_location_canonicalized(self):
        # the live fragmentation class: an underscore variant of an existing
        # room must file under the canonical hyphenated pwd.
        eng = self._mc_engine(self.FOYER)
        eng._tool_record_event(
            "Dust motes swirl.", "exp:evie",
            location="ravenwood-manor/secret_garden",
        )
        self.assertEqual(len(self._events_at("ravenwood-manor/secret-garden")), 1)

    def test_record_event_mc_stationary_auto_moves(self):
        eng = self._mc_engine(self.FOYER)
        eng._tool_record_event(
            "Bartholomew saunters into the garage.",
            "exp:bartholomew exp:evie",
            location="garage",
        )
        self.assertEqual(eng.session.location, "ravenwood-manor/garage")
        # NPC last-seen follows the move (the gap plain remark used to have)
        rec = next(r for r in eng.npc_tracker.all() if r.slug == "evie")
        self.assertEqual(rec.location_last_seen, "ravenwood-manor/garage")
        # subsequent bare record_event files in the new room
        eng._tool_record_event("He leans on the workbench.", "exp:bartholomew")
        self.assertEqual(len(self._events_at("ravenwood-manor/garage")), 2)

    def test_record_event_compound_exp_tag_detects_mc(self):
        eng = self._mc_engine(self.FOYER)
        eng._tool_record_event(
            "They slip into the cottage together.",
            "exp:bartholomew+evie",
            location="cottage",
        )
        self.assertEqual(eng.session.location, "ravenwood-manor/cottage")

    def test_record_event_non_mc_divergent_warns_only(self):
        eng = self._mc_engine(self.FOYER)
        eng._tool_record_event(
            "Evie inspects the roses alone.", "exp:evie", location="garden"
        )
        self.assertEqual(eng.session.location, self.FOYER)  # session unmoved
        self.assertTrue(eng._loc_warnings)
        # the note itself still files at the (canonicalized) explicit room
        self.assertEqual(len(self._events_at("ravenwood-manor/garden")), 2)

    def test_record_event_mobile_defers_then_reconciles(self):
        eng = self._mc_engine(self.FOYER)
        eng._turn_stationary = False  # mobile turn: navigate_to owns the move
        eng._tool_record_event(
            "Bartholomew strides into the garage.",
            "exp:bartholomew",
            location="garage",
        )
        self.assertEqual(eng.session.location, self.FOYER)  # not during step 2
        self.assertEqual(eng._pending_loc_hint, "ravenwood-manor/garage")
        # step 2 ends without navigate_to → the hint commits
        eng._reconcile_loc_hint([("record_event", "ok")])
        self.assertEqual(eng.session.location, "ravenwood-manor/garage")
        self.assertIsNone(eng._pending_loc_hint)

    def test_record_event_hint_dropped_when_navigate_fired(self):
        eng = self._mc_engine(self.FOYER)
        eng._turn_stationary = False
        eng._tool_record_event(
            "Bartholomew heads for the cottage.",
            "exp:bartholomew",
            location="cottage",
        )
        eng._tool_navigate_to("garage")  # the real traversal wins
        eng._reconcile_loc_hint([("record_event", "ok"), ("navigate_to", "ok")])
        self.assertEqual(eng.session.location, "ravenwood-manor/garage")

    def test_record_event_same_room_no_gate(self):
        eng = self._mc_engine(self.FOYER)
        eng._tool_record_event(
            "He paces the foyer.", "exp:bartholomew", location=self.FOYER
        )
        self.assertEqual(eng.session.location, self.FOYER)
        self.assertEqual(eng._loc_warnings, [])

    def test_remark_commit_updates_npc_last_seen(self):
        # the shared _commit_location closes the plain-remark tracker gap.
        eng = self._engine(self.FOYER)
        eng._remark_location(
            "[MC action]: Evie leads me into the cottage",
            "CURRENT ROOM: ravenwood-manor/cottage",
        )
        rec = next(r for r in eng.npc_tracker.all() if r.slug == "evie")
        self.assertEqual(rec.location_last_seen, "ravenwood-manor/cottage")

    # --- scene hint on location change (minimal rotation pressure) ---

    def test_scene_hint_injected_once_after_move(self):
        eng = self._mc_engine(self.FOYER)
        eng.init_pipeline()
        eng.session.current_scene = "arrival"
        eng._commit_location("ravenwood-manor/garage", source="record_event")
        self.assertTrue(eng._scene_hint_pending)
        step = eng._compliance_step
        first = step._compose_step2_user_content("[MC action]: I wait", "WS: x")
        self.assertIn("call begin_scene", first)
        self.assertIn("'arrival'", first)
        second = step._compose_step2_user_content("[MC action]: I wait", "WS: x")
        self.assertNotIn("call begin_scene", second)  # one-shot

    def test_scene_hint_not_set_without_active_scene(self):
        eng = self._mc_engine(self.FOYER)
        eng._commit_location("ravenwood-manor/garage", source="remark")
        self.assertFalse(eng._scene_hint_pending)

    def test_scene_hint_set_by_navigate_to(self):
        eng = self._mc_engine(self.FOYER)
        eng.session.current_scene = "arrival"
        eng._tool_navigate_to("cottage")
        self.assertTrue(eng._scene_hint_pending)

    def test_begin_scene_clears_pending_hint(self):
        eng = self._mc_engine(self.FOYER)
        eng.session.current_scene = "arrival"
        eng._commit_location("ravenwood-manor/garage", source="record_event")
        eng._tool_begin_scene("garage-tinkering", "A new beat in the garage.")
        self.assertFalse(eng._scene_hint_pending)

    def test_stationary_nudge_wording_untouched(self):
        # the swept nudge text is a bakeoff winner — pin it against drift.
        from rpjot import ComplianceStep

        self.assertTrue(
            ComplianceStep._STATIONARY_NUDGE.startswith(
                "DIRECTOR NOTE (this turn): the MC has not moved themselves"
            )
        )

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
        self.assertIn(f"last seen with {self.HOLDER}: {self.SLUG}", lines)

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
                    tag="",
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


class TestConstructReport(unittest.TestCase):
    """Deterministic (non-LLM) coverage for construct_report + the /-trim helpers.

    Seeds a scratch notefile with known notes across distinct now values and
    asserts census math, witness-bar scaling, recency, the miss path, and the
    /location full-vs-default distinction — all without touching the LLM.
    """

    def setUp(self):
        import tempfile

        self._saved_notefile = Note.NOTEFILE
        fd, self._path = tempfile.mkstemp(suffix=".jot")
        os.close(fd)
        Note.NOTEFILE = self._path

    def tearDown(self):
        Note.NOTEFILE = self._saved_notefile
        try:
            os.remove(self._path)
        except OSError:
            pass

    def _jot(self, **kw):
        Note.append(Note.NOTEFILE, Note.jot(**kw))

    def _engine(self, location="hall", people=None):
        eng = RPJotEngine(
            location=location,
            people_present=people if people is not None else {"mc", "evie"},
        )
        eng.register_all_tools()
        return eng

    # ── census math ──────────────────────────────────────────────────────
    def test_person_census_total_dedups_by_identity(self):
        # profile (also carries char:evie) + a conscience note (also char:evie)
        # + a know note + one exp note. The conscience note is returned by BOTH
        # the char:evie source and the conscience source but must count ONCE in
        # the deduped total.
        self._jot(message="Evie.", tag="char:evie", context="p",
                  pwd="/story/character/evie", now=1000)
        self._jot(message="Fears water.", tag="cons:water char:evie", context="c",
                  pwd="/story/conscience/evie", now=1100)
        self._jot(message="Knows the code.", tag="know:evie", context="k",
                  pwd="/story/character/evie", now=1200)
        self._jot(message="A shared beat.", tag="exp:evie exp:mara", context="e",
                  pwd="/story/events/hall", now=1300)
        eng = self._engine()
        out = eng.construct_report("evie")
        # 4 distinct notes; the conscience note is shared across two sources.
        self.assertIn("canon entries (total)", out)
        self.assertRegex(out, r"canon entries \(total\)\s+4\b")

    def test_as_fed_is_census_intersect_last_ctx_now(self):
        self._jot(message="Evie.", tag="char:evie", context="p",
                  pwd="/story/character/evie", now=2000)
        self._jot(message="A beat.", tag="exp:evie", context="e",
                  pwd="/story/events/hall", now=2100)
        self._jot(message="Another beat.", tag="exp:evie", context="e",
                  pwd="/story/events/hall", now=2200)
        eng = self._engine()
        # Simulate "fed last turn": two of the three census nows were fed.
        eng._last_ctx_now = {2000, 2200, 99999}  # 99999 is not in the census
        out = eng.construct_report("evie")
        # as-fed counts census notes whose now is in _last_ctx_now → 2 of 3.
        self.assertRegex(out, r"active — as fed last turn\s+2\b")
        # never exceeds total (V2)
        self.assertRegex(out, r"canon entries \(total\)\s+3\b")

    def test_no_turn_yet_marker(self):
        self._jot(message="Evie.", tag="char:evie", context="p",
                  pwd="/story/character/evie", now=3000)
        eng = self._engine()
        out = eng.construct_report("evie")
        self.assertIn("(no turn yet)", out)

    def test_fresh_recompute_stable_across_repeats(self):
        self._jot(message="Evie.", tag="char:evie", context="p",
                  pwd="/story/character/evie", now=4000)
        self._jot(message="A beat.", tag="exp:evie", context="e",
                  pwd="/story/events/hall", now=4100)
        eng = self._engine()
        a = eng.construct_report("evie")
        b = eng.construct_report("evie")
        self.assertEqual(a, b)  # V1: deterministic, no clock/LLM dependence
        # and construct_report must NOT mutate the as-fed census (V3)
        self.assertEqual(eng._last_ctx_now, set())

    # ── witness bars ─────────────────────────────────────────────────────
    def test_witness_bar_counts_and_scaling(self):
        # evie=4, mara=2, bob=1 co-occurrences across the subject's exp: notes.
        self._jot(message="Evie.", tag="char:evie", context="p",
                  pwd="/story/character/evie", now=4999)
        self._jot(message="b1.", tag="exp:evie exp:mara exp:bob", context="e",
                  pwd="/story/events/hall", now=5000)
        self._jot(message="b2.", tag="exp:evie exp:mara", context="e",
                  pwd="/story/events/hall", now=5100)
        self._jot(message="b3.", tag="exp:evie", context="e",
                  pwd="/story/events/hall", now=5200)
        self._jot(message="b4.", tag="exp:evie", context="e",
                  pwd="/story/events/hall", now=5300)
        eng = self._engine()
        out = eng.construct_report("evie")
        wlines = [l for l in out.splitlines() if l.strip().startswith(("evie", "mara", "bob"))]
        # order desc by count, with correct integer counts in the count column
        joined = "\n".join(wlines)
        self.assertRegex(joined, r"evie\s+█+\s+4\b")
        self.assertRegex(joined, r"mara\s+█+\s+2\b")
        self.assertRegex(joined, r"bob\s+█+\s+1\b")
        # top witness (max count) fills the fixed 24-cell bar; lesser are shorter
        def blocks(name):
            line = next(l for l in wlines if l.strip().startswith(name))
            return line.count("█")
        self.assertEqual(blocks("evie"), 24)
        self.assertLess(blocks("mara"), blocks("evie"))
        self.assertLess(blocks("bob"), blocks("mara"))

    # ── recency / turns-ago ──────────────────────────────────────────────
    def test_newest_oldest_turns_ago_from_summary_clock(self):
        self._jot(message="Evie.", tag="char:evie", context="p",
                  pwd="/story/character/evie", now=6000)  # oldest
        self._jot(message="A beat.", tag="exp:evie", context="e",
                  pwd="/story/events/hall", now=6500)  # newest
        # summary notes = the per-turn clock: two turns after 6500, three after 6000.
        for t in (6100, 6600, 6700):
            self._jot(message="turn.", tag="summary", context="t",
                      pwd="/summaries", now=t)
        eng = self._engine()
        out = eng.construct_report("evie")
        # newest (6500): 2 stamps strictly greater (6600, 6700)
        self.assertRegex(out, r"newest entry\s+2 turns ago")
        # oldest (6000): 3 stamps strictly greater
        self.assertRegex(out, r"oldest entry\s+3 turns ago")

    # ── name resolution / precedence / miss ──────────────────────────────
    def test_object_resolution_and_detail(self):
        self._jot(message="Iron key.", tag="obj:iron-key", context="canon",
                  pwd="/story/object/iron-key", now=7000)
        self._jot(message="In the cellar.", tag="obj:iron-key", context="s",
                  pwd="/story/location/cellar", now=7100)
        eng = self._engine(people={"mc"})
        out = eng.construct_report("the iron key")  # word-overlap canonicalizes
        self.assertIn("CONSTRUCT: iron-key   (object", out)
        self.assertIn("OBJECT DETAIL", out)
        self.assertIn("canonical description: present", out)

    def test_place_resolution_and_detail(self):
        self._jot(message="The hall.", tag="", context="loc",
                  pwd="/story/location/manor/hall", now=8000)
        self._jot(message="A sub-room.", tag="", context="loc",
                  pwd="/story/location/manor/hall/alcove", now=8100)
        eng = self._engine(location="manor/hall", people={"mc"})
        out = eng.construct_report("manor/hall")
        self.assertIn("CONSTRUCT: manor/hall   (place", out)
        self.assertIn("PLACE DETAIL", out)
        self.assertIn("alcove", out)

    def test_miss_prints_roster_no_traceback(self):
        self._jot(message="Iron key.", tag="obj:iron-key", context="canon",
                  pwd="/story/object/iron-key", now=9000)
        self._jot(message="Evie.", tag="char:evie", context="p",
                  pwd="/story/character/evie", now=9100)
        eng = self._engine(people={"mc"})
        out = eng.construct_report("nonexistent-thing-xyz")
        self.assertIn("[no subject matching 'nonexistent-thing-xyz']", out)
        self.assertIn("known objects", out)
        self.assertIn("iron-key", out)
        self.assertIn("known characters", out)
        self.assertIn("evie", out)

    # ── /location full vs default (play.py trim) ─────────────────────────
    def test_location_full_vs_default_distinction(self):
        import play

        # A location with a chunky note body so the full dump is clearly longer.
        big = "The hall is vast. " * 40
        self._jot(message=big, tag="", context="loc",
                  pwd="/story/location/hall", now=10000)
        self._jot(message="An event.", tag="", context="ev",
                  pwd="/story/events/hall", now=10100)
        eng = self._engine(location="hall", people={"mc"})
        default = play.summarize_location(eng)
        full = play.query_location_context(eng)
        # default is materially shorter and never dumps the full body
        self.assertLess(len(default), len(full))
        self.assertNotIn(big.strip(), default)
        self.assertIn(big.strip(), full)
        # default still conveys sufficiency: a count line
        self.assertIn("note(s)", default)


if __name__ == "__main__":
    unittest.main(verbosity=2)
