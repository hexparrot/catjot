#!/usr/bin/env python3
"""rpjot_engine.py -- LLM tooling engine extracted from rpjot.py.

Gameplay logic lives elsewhere. This class owns:
  - session state
  - tool registration and dispatch
  - LLM communication
  - JSON parsing utilities
"""

__author__ = "William Dizon"
__version__ = "0.2.0"

import json
import logging
import re
import sys

from catjot import (
    Note,
    NoteContext,
    SearchType,
    ContextBundle,
    call_llm,
)

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------
# Use the cl100k_base pre-tokenizer regex (same pattern tiktoken uses before
# applying BPE merges).  For English prose this gives token counts within ~5%
# of the real BPE count without needing the vocab file or any network access.
# Falls back to chars//4 if the 'regex' package is absent.

try:
    import regex as _regex

    # Verbatim cl100k_base split pattern from tiktoken source
    _CL100K_PAT = _regex.compile(
        r"""'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}"""
        r"""| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]|\s+(?!\S)|\s+"""
    )

    def _tok(text: str) -> int:
        """Count tokens using cl100k_base pre-tokenizer (no BPE vocab needed)."""
        return len(_CL100K_PAT.findall(text))

    _TOK_SOURCE = "cl100k_base pre-tokenizer (regex)"

except ImportError:

    def _tok(text: str) -> int:  # type: ignore[misc]
        return len(text) // 4

    _TOK_SOURCE = "chars//4 fallback (regex not installed)"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s.%(funcName)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger("rpjot_engine")
if not logger.handlers:
    _h_stderr = logging.StreamHandler(sys.stderr)
    _h_file = logging.FileHandler("debug.log", mode="a")
    _fmt = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)
    _h_stderr.setFormatter(_fmt)
    _h_file.setFormatter(_fmt)
    logger.addHandler(_h_stderr)
    logger.addHandler(_h_file)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
logger.debug("tokenizer: %s", _TOK_SOURCE)

# ---------------------------------------------------------------------------
# Tag and Directory Constants
# ---------------------------------------------------------------------------

TAG_LOC = "loc:"  # loc:ravenwood-manor
TAG_CHAR = "char:"  # char:alice
TAG_EXP = "exp:"  # exp:alice+bob  (shared experience)
TAG_KNOW = "know:"  # know:alice     (private knowledge)
TAG_SCENE = "scene:"  # scene:escort-to-bedroom
TAG_CONS = "cons:"  # cons:aversion-to-water

PWD_RULES = "/system/rules"
PWD_WORLD = "/story/location"
PWD_CHARS = "/story/character"
PWD_EVENTS = "/story/events"
PWD_SCENES = "/story/scenes"
PWD_CONSCIENCE = "/story/conscience"
PWD_SUMMARIES = "/summaries"
PWD_YOMI = "/yomi"

TAG_YOMI = "yomi:"  # yomi:alice

# ---------------------------------------------------------------------------
# Debug flags
# ---------------------------------------------------------------------------

DEBUG_AUDIT_NOTES = (
    False  # write a note for every tool call+result (noisy; for dev only)
)

# ---------------------------------------------------------------------------
# Shared followup instructions for tool results
# ---------------------------------------------------------------------------

FOLLOWUP_QUERY = (
    "Determine if this is a simple user inquiry or knowledge-gathering for "
    "role-playing advancement. If it is a user-triggered inquiry, end additional "
    "knowledge-gathering then record_event."
)

FOLLOWUP_USE_LORE = (
    "Use this world information to ground your narrative in established lore."
)

FOLLOWUP_USE_CHARACTER = (
    "Use this character information to inform your narrative. "
    "If no notes exist, invent consistent details and save them with save_character."
)

FOLLOWUP_SCENE_CONTINUITY = (
    "Use this scene history to maintain continuity. "
    "If the scene is complete, you may summarize it and call "
    "begin_scene to start the next scene."
)

FOLLOWUP_CONSCIENCE_ACTIVE = (
    "Review these constraints before narrating. "
    "Any player action that conflicts with an active conscience "
    "must be reshaped — preserve the player's intent but revise "
    "the execution to respect the constraint."
)

FOLLOWUP_CONTEXT_SNAPSHOT = (
    "Use shared_context for environmental narration. "
    "Use each character's entry in character_contexts to write their "
    "dialogue and reactions authentically — characters do not know "
    "what is in other characters' private context."
)

# ---------------------------------------------------------------------------
# Context size limits
# ---------------------------------------------------------------------------

CONTEXT_MAX_TOKS = (
    2_000  # soft limit in tokens; above this, LLM condensation is triggered
)
CONTEXT_HARD_LIMIT_TOKS = (
    8_000  # hard ceiling in tokens; truncate without LLM above this
)

# ---------------------------------------------------------------------------
# Full-payload guard (MODEL_CONTEXT_LIMIT_TOKS)
# ---------------------------------------------------------------------------
# Set this to your model's actual context window.  _guard_payload() uses it
# to protect every call_llm invocation from overflow.
MODEL_CONTEXT_LIMIT_TOKS = 64_000
_RESPONSE_RESERVE_TOKS = 2_000  # headroom reserved for the model's reply
NARRATIVE_TEMPERATURE = (
    0.75  # temperature for final prose; higher than tool-dispatch calls
)

# Tools whose results contain newly written canonical material worth surfacing to the narrative.
# Query tools (get_character, search_world, prepare_context, etc.) are excluded because
# they return existing lore already visible in the context window.
_WRITE_TOOLS = frozenset(
    {
        "record_event",
        "navigate_to",
        "set_people_present",
        "save_character",
        "save_location",
        "save_object",
        "begin_scene",
        "record_knowledge",
        "record_conscience",
    }
)

# ---------------------------------------------------------------------------
# Tool decorator
# ---------------------------------------------------------------------------


def rp_tool(description, parameters):
    """Mark a method as an LLM-callable tool.

    register_all_tools() discovers all decorated methods and registers them.
    Tool name is derived from the method name by stripping the '_tool_' prefix.
    """

    def decorator(fn):
        fn._rp_tool_meta = (description, parameters)
        return fn

    return decorator


# ---------------------------------------------------------------------------
# SessionState
# ---------------------------------------------------------------------------


class SessionState:
    """Mutable state tracked across one play session."""

    def __init__(self, location, people_present=None):
        self.location = location
        self.location_context = ContextBundle(f"{PWD_WORLD}/{location}")
        self.people_present = set(people_present or [])
        self.current_scene: str = ""
        logger.info(
            "SessionState created: loc=%s, present=%s",
            self.location,
            self.people_present,
        )

    @property
    def location_ancestors(self) -> list[str]:
        """Return all ancestor paths from root to current location, inclusive.

        Example: "manor/foyer/closet" → ["manor", "manor/foyer", "manor/foyer/closet"]
        """
        parts = self.location.split("/")
        return ["/".join(parts[: i + 1]) for i in range(len(parts))]

    def header(self):
        """Return the state header string injected into every user message."""
        present_str = (
            ", ".join(sorted(self.people_present)) if self.people_present else "none"
        )
        scene_str = f" | scene: {self.current_scene}" if self.current_scene else ""
        h = (
            f"[CURRENT STATE | location: {TAG_LOC}{self.location}"
            f" | present: {present_str}{scene_str}]"
        )
        logger.debug("SessionState.header: %s", h)
        return h


# ---------------------------------------------------------------------------
# RPJotEngine
# ---------------------------------------------------------------------------


class RPJotEngine:
    """
    Core engine: owns session state, tool registration, LLM calls,
    and JSON parsing. Does not contain any gameplay logic.

    Usage:
        engine = RPJotEngine(location="ravenwood-manor", people={"mc"})
        engine.register_all_tools()
        response = engine.run_tool_loop(messages)
    """

    def __init__(self, location, people_present=None, main_character="mc"):
        self._tool_schemas: list = []
        self._tool_handlers: dict = {}
        self._last_payload_toks: int = 0  # updated by _guard_payload each iteration
        self.main_character = main_character
        self.session = SessionState(
            location=location,
            people_present=people_present or set(),
        )
        logger.info(
            "RPJotEngine init: loc=%s people=%s mc=%s",
            location,
            self.session.people_present,
            self.main_character,
        )

    # ------------------------------------------------------------------
    # Private tool registry
    # ------------------------------------------------------------------

    def _register_tool(self, name, description, parameters, handler):
        """Register a tool in the instance-local schema + handler dicts."""
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }
        self._tool_schemas = [
            s for s in self._tool_schemas if s["function"]["name"] != name
        ]
        self._tool_schemas.append(schema)
        self._tool_handlers[name] = handler

    def _dispatch(self, name: str, args_json) -> str:
        """Dispatch a tool call using the instance-local handler dict."""
        if name not in self._tool_handlers:
            return json.dumps({"error": f"unknown tool: {name}"})
        args = json.loads(args_json) if isinstance(args_json, str) else args_json
        return self._tool_handlers[name](**args)

    # ------------------------------------------------------------------
    # Utility: think-tag stripping
    # ------------------------------------------------------------------

    @staticmethod
    def strip_think_tags(text):
        """
        Separate <think>...</think> content from the rest of the LLM response.

        Returns:
            (think_content, clean_content) -- both strings.
        """
        think_match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
        think_content = (
            think_match.group(1).strip().replace("\n", " ") if think_match else ""
        )
        clean_content = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return think_content, clean_content

    @classmethod
    def extract_think_contents(cls, messages: list) -> list[str]:
        """
        Iterate through a list of message dicts and return all think content
        found in 'content' fields.

        Returns:
            A list of non-empty think content strings.
        """
        results = []

        for item in messages:
            if not isinstance(item, dict):
                continue

            content = item.get("content", "")
            if not isinstance(content, str):
                continue

            think_content, _ = cls.strip_think_tags(content)
            if think_content:
                results.append(think_content)

        return results

    # ------------------------------------------------------------------
    # Utility: JSON extraction from LLM prose
    # ------------------------------------------------------------------

    @staticmethod
    def extract_json_from_response(text: str) -> dict:
        """
        Pull the first {...} block from an LLM response string and parse it.
        Uses a balanced-brace scan to handle nested JSON correctly.

        Raises:
            ValueError if no JSON object is found or braces are unbalanced.
            json.JSONDecodeError if the matched block is malformed.
        """
        start = text.find("{")
        if start == -1:
            raise ValueError(f"No JSON object found in LLM response: {text!r}")
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
        raise ValueError(f"Unbalanced braces in LLM response: {text!r}")

    # ------------------------------------------------------------------
    # Utility: followup_instruction stripping
    # ------------------------------------------------------------------

    @staticmethod
    def strip_followup_instruction(result_str):
        """
        Remove the followup_instruction key from a JSON tool result string
        before appending it to the message history.

        Returns:
            (instruction_text_or_None, cleaned_json_str)
        """
        try:
            parsed = json.loads(result_str)
            instruction = parsed.pop("followup_instruction", None)
            return instruction, json.dumps(parsed)
        except (json.JSONDecodeError, AttributeError):
            return None, result_str

    # ------------------------------------------------------------------
    # Location traversal
    # ------------------------------------------------------------------

    @staticmethod
    def compute_traversal(from_path: str, to_path: str) -> list[str]:
        """Return the ordered list of locations traversed from from_path to to_path.

        Walks up to the nearest common ancestor then down to the destination.
        Assumes both paths share at least one common prefix component.

        Examples:
            "manor/foyer/kitchen" → "manor/foyer/drawing_room"
            ⟹ ["manor/foyer/kitchen", "manor/foyer", "manor/foyer/drawing_room"]

            "manor/foyer/staircase/elevator" → "manor/foyer"
            ⟹ ["manor/foyer/staircase/elevator", "manor/foyer/staircase", "manor/foyer"]

            "manor/foyer" → "manor/foyer/closet"
            ⟹ ["manor/foyer", "manor/foyer/closet"]
        """
        if from_path == to_path:
            return [to_path]

        from_parts = from_path.split("/")
        to_parts = to_path.split("/")

        common_len = 0
        for a, b in zip(from_parts, to_parts):
            if a == b:
                common_len += 1
            else:
                break

        ascending = [
            "/".join(from_parts[:depth])
            for depth in range(len(from_parts) - 1, common_len - 1, -1)
        ]
        descending = [
            "/".join(to_parts[:depth])
            for depth in range(common_len + 1, len(to_parts) + 1)
        ]
        return [from_path] + ascending + descending

    @staticmethod
    def resolve_destination(from_path: str, destination: str) -> tuple[str, str]:
        """Resolve a raw destination string into a full hierarchical path.

        Returns:
            (resolved_path, nav_type) where nav_type is one of:
            "hierarchical" — shared ancestor found, compute_traversal applies
            "inferred"     — bare name resolved as sibling under current root
            "direct"       — no shared ancestry, different top-level locations
        """
        dest_parts = destination.split("/")
        from_parts = from_path.split("/")

        common_len = 0
        for a, b in zip(from_parts, dest_parts):
            if a == b:
                common_len += 1
            else:
                break

        if common_len > 0:
            return destination, "hierarchical"

        # Bare single-word destination inside a multi-level current location:
        # treat it as a sibling under the top-level root.
        # e.g. current="manor/foyer/corridor", destination="cellar" → "manor/cellar"
        if len(dest_parts) == 1 and len(from_parts) > 1:
            return f"{from_parts[0]}/{destination}", "inferred"

        # Genuinely separate locations — direct transport.
        return destination, "direct"

    # ------------------------------------------------------------------
    # Context assembly
    # ------------------------------------------------------------------

    def gather_context(self, tags_or_dirs) -> ContextBundle:
        """Build a ContextBundle from a list of tags or directory paths."""
        logger.debug(
            "[CTX] gather_context: querying %d term(s): %s",
            len(tags_or_dirs or []),
            tags_or_dirs,
        )
        return ContextBundle(tags_or_dirs) if tags_or_dirs else ContextBundle([])

    def gather_character_knowledge(self, char_name: str) -> ContextBundle:
        """Gather everything known about a specific character."""
        return ContextBundle([f"{PWD_CHARS}/{char_name}"])

    def gather_all_character_knowledge(self, char_names) -> ContextBundle:
        """Gather notes for multiple characters as a single ContextBundle."""
        dirs = [f"{PWD_CHARS}/{n}" for n in char_names]
        return ContextBundle(dirs) if dirs else ContextBundle([])

    def gather_pov_context(self, char_name: str) -> ContextBundle:
        """Build a ContextBundle representing everything char_name would know.

        Combines:
          - location notes from PWD_WORLD/{ancestor} directories for all ancestor paths
          - character profile from PWD_CHARS/{char_name} directory
          - conscience constraints from PWD_CONSCIENCE/{char_name} directory
          - private knowledge tagged know:{char_name}
          - shared experiences tagged exp:{char_name} (partial tag word match)

        Knowledge from other characters' know: notes is NOT included, giving
        each character authentic knowledge gaps about the world.
        """
        terms = []
        for ancestor in self.session.location_ancestors:
            terms.append(f"{PWD_WORLD}/{ancestor}")
        terms.append(f"{PWD_CHARS}/{char_name}")
        terms.append(f"{PWD_CONSCIENCE}/{char_name}")
        terms.append(f"{TAG_KNOW}{char_name}")
        terms.append(f"{TAG_EXP}{char_name}")
        if self.session.current_scene:
            terms.append(f"{TAG_SCENE}{self.session.current_scene}")
        logger.debug(
            "[CTX] gather_pov_context(%s): querying %d terms", char_name, len(terms)
        )
        return ContextBundle(terms)

    def build_scene_context_map(self, focus_hint: str = "") -> dict:
        """Return per-character condensed context plus a shared location snapshot.

        Keys:
          "shared"          — location + world notes only (no character-private info)
          "<char_name>"     — each present character's full POV via gather_pov_context

        Every value is already passed through render_context, so it respects
        CONTEXT_MAX_CHARS and recency ordering.
        """
        shared_terms = [f"{PWD_WORLD}/{a}" for a in self.session.location_ancestors]
        shared_bundle = self.gather_context(shared_terms)

        logger.debug(
            "[CTX] build_scene_context_map: loc=%s people=%s focus=%r",
            self.session.location,
            sorted(self.session.people_present),
            focus_hint or "none",
        )
        shared_str = self.render_context(shared_bundle, focus_hint=focus_hint)
        result = {"shared": shared_str}
        for name in self.session.people_present:
            pov = self.gather_pov_context(name)
            pov_str = self.render_context(pov, focus_hint=focus_hint)
            result[name] = pov_str
            logger.debug("[CTX]   pov(%s): %d tok", name, _tok(pov_str))

        total_toks = sum(_tok(v) for v in result.values())
        logger.debug(
            "[CTX] build_scene_context_map done: shared=%d tok | total=%d tok across %d character(s)",
            _tok(shared_str),
            total_toks,
            len(self.session.people_present),
        )
        return result

    def _gather_yomi_for_scene(self) -> str:
        """Gather stored yomi for all non-protagonist characters in the scene.

        Returns a formatted injection string, or empty string when no yomi exists.
        Yomi notes are sorted newest-first; the most recent insight per character
        leads, reflecting the current arc of the relationship.
        """
        others = sorted(self.session.people_present - {self.main_character})
        if not others:
            return ""

        parts = []
        for char_name in others:
            bundle = ContextBundle(f"{PWD_YOMI}/{char_name}")
            context_str = self.render_context(bundle, focus_hint=char_name)
            if context_str.strip():
                parts.append(f"[{char_name}]\n{context_str.strip()}")
                logger.debug(
                    "[YOMI] loaded yomi for %s: %d tok", char_name, _tok(context_str)
                )

        if not parts:
            return ""

        logger.debug(
            "[YOMI] _gather_yomi_for_scene: %d character(s) with yomi", len(parts)
        )
        return (
            "YOMI — FIRST-PERSON SOCIAL INTUITION\n"
            "The main character's felt sense of how others in this scene perceive "
            "and relate to them. Use these to shape the emotional texture of the "
            "prose: micro-expressions, loaded silences, unspoken tensions, the raw "
            "feeling of being seen or judged. Let this inner knowledge live in "
            "sensory detail and subtext rather than stated plainly:\n\n"
            + "\n\n".join(parts)
        )

    def render_context(self, bundle: ContextBundle, focus_hint: str = "") -> str:
        """Render a ContextBundle as a recency-sorted, size-bounded string.

        Notes are sorted newest-first by Note.now so the most recent description
        of any entity survives truncation.  Suppression set on the bundle is
        respected (uses __iter__ → _visible_notes).

        Size tiers:
          <= CONTEXT_MAX_TOKS      → return as-is
          >  CONTEXT_HARD_LIMIT_TOKS → hard-truncate, skip LLM call
          between the two           → call _condense_context for LLM distillation
        """
        if not isinstance(bundle, ContextBundle):
            text = str(bundle)
            note_count = 0
        else:
            notes = sorted(bundle, key=lambda n: n.now, reverse=True)
            note_count = len(notes)
            parts = [n.context.strip() + "\n\n" + n.message.strip() for n in notes]
            text = "\n\n".join(parts).strip()

        raw_toks = _tok(text)
        logger.debug(
            "[CTX] render_context: %d note(s) → %d tok raw",
            note_count,
            raw_toks,
        )

        # Adaptive headroom passthrough: skip condensation when there is plenty
        # of room in the context window.  Only engage the soft/hard limit logic
        # when this bundle would meaningfully pressure the remaining capacity.
        # Rule: if the bundle fits within half of the currently available tokens
        # (and is below the hard ceiling), return as-is — no LLM call needed.
        _capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        _available = _capacity - self._last_payload_toks
        if raw_toks <= CONTEXT_HARD_LIMIT_TOKS and raw_toks <= _available // 2:
            logger.debug(
                "[CTX] render_context: HEADROOM PASSTHROUGH  %d tok  "
                "[avail=%d  cap=%d  hard=%d]",
                raw_toks,
                _available,
                _capacity,
                CONTEXT_HARD_LIMIT_TOKS,
            )
            return text

        if raw_toks <= CONTEXT_MAX_TOKS:
            logger.debug(
                "[CTX] render_context: PASSTHROUGH  %d tok  [soft=%d  hard=%d]",
                raw_toks,
                CONTEXT_MAX_TOKS,
                CONTEXT_HARD_LIMIT_TOKS,
            )
            return text

        if raw_toks > CONTEXT_HARD_LIMIT_TOKS:
            # Approximate char cut-point via tok→char ratio to stay near the limit
            ratio = len(text) / max(raw_toks, 1)
            cut = int(CONTEXT_HARD_LIMIT_TOKS * ratio)
            truncated = text[:cut]
            logger.warning(
                "[CTX] render_context: HARD-TRUNCATE  %d → %d tok  [soft=%d  hard=%d]",
                raw_toks,
                _tok(truncated),
                CONTEXT_MAX_TOKS,
                CONTEXT_HARD_LIMIT_TOKS,
            )
            return truncated

        logger.debug(
            "[CTX] render_context: CONDENSE  %d tok → target ~%d tok  [soft=%d  hard=%d]",
            raw_toks,
            CONTEXT_MAX_TOKS // 2,
            CONTEXT_MAX_TOKS,
            CONTEXT_HARD_LIMIT_TOKS,
        )
        result = self._condense_context(text, focus_hint=focus_hint)
        logger.debug(
            "[CTX] render_context: CONDENSE done  %d → %d tok",
            raw_toks,
            _tok(result),
        )
        return result

    def _condense_context(self, raw_text: str, focus_hint: str = "") -> str:
        """Call the LLM to distill raw_text to roughly CONTEXT_MAX_TOKS // 2.

        Falls back to hard-truncation at CONTEXT_MAX_TOKS if the LLM call
        fails or returns an empty response.
        """
        target_toks = CONTEXT_MAX_TOKS // 2
        scenario = (
            f"Location: {self.session.location}. "
            f"Present: {', '.join(sorted(self.session.people_present)) or 'none'}."
        )
        focus_clause = f"\nFocus on: {focus_hint}" if focus_hint else ""

        prompt = (
            "You are condensing RPG scene context for use by a storytelling AI.\n"
            f"Scenario: {scenario}{focus_clause}\n\n"
            "Summarize the following notes into the most narratively relevant facts.\n"
            "Preserve: the most recent description of each entity, key events, active "
            "character details, and any story-significant objects or clues.\n"
            f"Target length: {target_toks} tokens. Reply with the condensed context "
            "only — no preamble, no explanation.\n\n"
            f"CONTEXT:\n{raw_text}"
        )

        in_toks = _tok(raw_text)
        logger.debug(
            "[CTX] _condense_context: input=%d tok | target ~%d tok",
            in_toks,
            target_toks,
        )

        try:
            response = call_llm([{"role": "user", "content": prompt}])
            content = response.get("content", "")
            _, condensed = self.strip_think_tags(content)
            if condensed.strip():
                out = condensed.strip()
                out_toks = _tok(out)
                reduction_pct = 100.0 * (1.0 - out_toks / max(in_toks, 1))
                logger.info(
                    "[CTX] _condense_context: %d → %d tok  %.0f%% reduction",
                    in_toks,
                    out_toks,
                    reduction_pct,
                )
                return out
            logger.warning(
                "[CTX] _condense_context: LLM returned empty; falling back to truncation"
            )
        except Exception as exc:
            logger.warning(
                "[CTX] _condense_context: LLM call failed (%s); falling back to truncation",
                exc,
            )

        # Approximate char cut-point to land near CONTEXT_MAX_TOKS
        ratio = len(raw_text) / max(in_toks, 1)
        cut = int(CONTEXT_MAX_TOKS * ratio)
        fallback = raw_text[:cut]
        logger.debug(
            "[CTX] _condense_context: fallback truncation → %d tok",
            _tok(fallback),
        )
        return fallback

    # ------------------------------------------------------------------
    # Scene context extraction (calls LLM)
    # ------------------------------------------------------------------

    def extract_scene_context(self, context):
        """
        Send scene context to the LLM and parse the JSON response into
        noteworthy_objects and established_props lists.

        Args:
            context: string or ContextBundle describing the scene.

        Returns:
            dict with keys "noteworthy_objects" and "established_props".

        Raises:
            ValueError if the LLM response contains no parsable JSON.
        """
        ctx_rendered = (
            self.render_context(context)
            if isinstance(context, ContextBundle)
            else str(context)
        )
        logger.debug(
            "[CTX] extract_scene_context: context=%d tok",
            _tok(ctx_rendered),
        )
        prompt = (
            "You are analyzing scene context. Extract information and respond "
            "ONLY with valid JSON, no other text.\n\n"
            "Context:\n%s\n\n"
            "Rules:\n"
            "- noteworthy_objects: physical objects explicitly mentioned or strongly implied\n"
            "- established_props: objects with confirmed narrative/story significance\n"
            "- Use empty lists if nothing qualifies\n"
            "- No explanation, no markdown, only the JSON object\n"
            "- Each list must contain only plain strings, not objects\n"
            "- Do not include any keys other than "
            '"noteworthy_objects" and "established_props"\n\n'
            "Respond with this exact JSON structure:\n"
            "{\n"
            '  "noteworthy_objects": ["object name and brief detail"],\n'
            '  "established_props": ["prop name and brief detail"]\n'
            "}"
        ) % ctx_rendered

        messages = [{"role": "user", "content": prompt}]
        response = call_llm(messages)

        content = response.get("content", "")
        _think, content = self.strip_think_tags(content)

        parsed = self.extract_json_from_response(content)

        return {
            "noteworthy_objects": parsed.get("noteworthy_objects", []),
            "established_props": parsed.get("established_props", []),
        }

    # ------------------------------------------------------------------
    # Scene debug report
    # ------------------------------------------------------------------

    def scene_debug_report(self) -> str:
        """Token-budget diagnostic for all entities in the current scene.

        Walks the same note sources that prepare_context and gather_pov_context
        use, so the numbers reflect the actual LLM token spend.  A note that
        carries multiple tags (e.g. exp:evie and exp:bartholomew) will be counted
        under each character — the per-category numbers are independent, not
        deduplicated.

        Returns a formatted multi-section string suitable for printing directly.
        """
        cap = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        W = 72  # total line width

        def _note_toks(notes: list) -> int:
            text = "\n\n".join(
                n.context.strip() + "\n\n" + n.message.strip() for n in notes
            ).strip()
            return _tok(text)

        def _row(label: str, n_notes: int, toks: int, indent: int = 4) -> str:
            pct = 100.0 * toks / cap if cap else 0.0
            return (
                f"{'':>{indent}}{label:<34}"
                f"  {n_notes:>4} notes"
                f"  {toks:>6} tok"
                f"  {pct:>5.1f}%"
            )

        SEP = "─" * W
        HDR = "═" * W

        lines: list[str] = []
        grand_n = 0
        grand_toks = 0

        def _accum(notes: list) -> tuple[int, int]:
            nonlocal grand_n, grand_toks
            t = _note_toks(notes)
            grand_n += len(notes)
            grand_toks += t
            return len(notes), t

        lines.append(HDR)
        lines.append(
            f"  SCENE DEBUG REPORT"
            f"  |  location: {self.session.location}"
            f"  |  cap: {cap:,} tok"
        )
        lines.append(HDR)

        # ── LOCATION HIERARCHY ───────────────────────────────────────────
        lines.append("\nLOCATION HIERARCHY")
        for ancestor in self.session.location_ancestors:
            all_notes = list(ContextBundle(f"{PWD_WORLD}/{ancestor}"))
            loc_notes = [
                n
                for n in all_notes
                if not any(t.startswith("obj:") for t in n.tag.split())
            ]
            obj_notes = [
                n for n in all_notes if any(t.startswith("obj:") for t in n.tag.split())
            ]
            ln, lt = _accum(loc_notes)
            on, ot = _accum(obj_notes)
            lines.append(f"\n  {ancestor}")
            lines.append(_row("description notes", ln, lt))
            lines.append(_row("object notes (obj:)", on, ot))

        # ── CHARACTERS IN SCENE ──────────────────────────────────────────
        lines.append(f"\n{SEP}")
        lines.append(
            f"\nCHARACTERS IN SCENE"
            f"  ({len(self.session.people_present)} present:"
            f" {', '.join(sorted(self.session.people_present)) or 'none'})"
        )
        for char in sorted(self.session.people_present):
            profile = list(ContextBundle([f"{PWD_CHARS}/{char}"]))
            conscience = list(ContextBundle([f"{PWD_CONSCIENCE}/{char}"]))
            private = list(ContextBundle([f"{TAG_KNOW}{char}"]))
            shared = list(ContextBundle([f"{TAG_EXP}{char}"]))

            pn, pt = _accum(profile)
            cn, ct = _accum(conscience)
            kn, kt = _accum(private)
            en, et = _accum(shared)
            char_toks = pt + ct + kt + et
            char_pct = 100.0 * char_toks / cap if cap else 0.0

            lines.append(
                f"\n  {char}"
                f"  →  {pn+cn+kn+en} notes  {char_toks} tok  {char_pct:.1f}%"
            )
            lines.append(_row(f"profile (char:{char})", pn, pt))
            lines.append(_row("conscience (cons:)", cn, ct))
            lines.append(_row("private knowledge (know:)", kn, kt))
            lines.append(_row("shared experience (exp:)", en, et))

        # ── ALL OTHER KNOWN CHARACTERS ───────────────────────────────────
        all_known: set[str] = set()
        with NoteContext(Note.NOTEFILE, (SearchType.ALL, "")) as nc:
            for note in nc:
                for word in note.tag.split():
                    if word.startswith(TAG_CHAR):
                        all_known.add(word[len(TAG_CHAR) :])

        others = sorted(all_known - self.session.people_present)
        if others:
            lines.append(f"\n  OTHER KNOWN CHARACTERS (not in scene)")
            for char in others:
                profile = list(ContextBundle([f"{PWD_CHARS}/{char}"]))
                conscience = list(ContextBundle([f"{PWD_CONSCIENCE}/{char}"]))
                pn, pt = len(profile), _note_toks(profile)
                cn, ct = len(conscience), _note_toks(conscience)
                total_t = pt + ct
                pct = 100.0 * total_t / cap if cap else 0.0
                lines.append(
                    f"    {char:<30}  {pn+cn:>4} notes  {total_t:>6} tok  {pct:>5.1f}%"
                    f"  (profile={pn}, conscience={cn})"
                )

        # ── ACTIVE SCENE ─────────────────────────────────────────────────
        lines.append(f"\n{SEP}")
        scene_label = self.session.current_scene or "(none)"
        lines.append(f"\nACTIVE SCENE  {scene_label}")
        if self.session.current_scene:
            scene_notes = list(
                ContextBundle([f"{TAG_SCENE}{self.session.current_scene}"])
            )
            sn, st = _accum(scene_notes)
            lines.append(_row("scene notes", sn, st))
        else:
            lines.append("    (no active scene — call begin_scene to set one)")

        # ── EVENTS AT CURRENT LOCATION ───────────────────────────────────
        lines.append(f"\n{SEP}")
        lines.append(f"\nEVENTS  at {self.session.location}")
        event_notes = list(ContextBundle([f"{PWD_EVENTS}/{self.session.location}"]))
        en2, et2 = _accum(event_notes)
        lines.append(_row("event notes", en2, et2))

        # ── SYSTEM ───────────────────────────────────────────────────────
        lines.append(f"\n{SEP}")
        lines.append("\nSYSTEM")
        for tag in ("system_role", "story_premise", "twist", "backstory"):
            bundle = list(ContextBundle(tag))
            bn, bt = _accum(bundle)
            lines.append(_row(tag, bn, bt))

        # ── SUMMARY ──────────────────────────────────────────────────────
        lines.append(f"\n{HDR}")
        pct_total = 100.0 * grand_toks / cap if cap else 0.0
        lines.append(
            f"  {'TOTAL ENUMERATED':<34}"
            f"  {grand_n:>4} notes"
            f"  {grand_toks:>6} tok"
            f"  {pct_total:>5.1f}%"
        )
        lines.append(
            f"  {'model cap (- response reserve)':<34}"
            f"  {'':>10}"
            f"  {cap:>6} tok"
            f"  100.0%"
        )
        lines.append(
            f"  {'remaining headroom':<34}"
            f"  {'':>10}"
            f"  {cap - grand_toks:>6} tok"
            f"  {100.0 * (cap - grand_toks) / cap:>5.1f}%"
        )
        lines.append(HDR)
        lines.append(
            "  NOTE: notes appearing under multiple characters are counted once per\n"
            "  category entry. The total may exceed a single prepare_context call\n"
            "  because condensation is not applied here."
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    @rp_tool(
        description=(
            "Record a canonical story event: a player action, NPC reaction, "
            "discovery, conversation, combat, or any happening in the narrative. "
            "Use this for WHAT HAPPENED — picking up items, opening doors, "
            "speaking with NPCs, exploring, fighting. "
            "Call this AFTER the story determines what occurs. "
            "Do NOT use this to define world entities — use save_character, "
            "save_location, or save_object to establish those instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What canonically happened",
                },
                "tags": {
                    "type": "string",
                    "description": (
                        "Space-separated tags. Use exp:name for each person present "
                        "(e.g., exp:evie exp:bartholomew). Use know:name for private "
                        "knowledge. Avoid loc: and char: prefixes — location and character "
                        "context is tracked by directory, not tags."
                    ),
                },
                "location": {
                    "type": "string",
                    "description": "Where the event took place (e.g. 'ravenwood-manor/foyer'). Omit to use current session location.",
                },
            },
            "required": ["description", "tags"],
        },
    )
    def _tool_record_event(self, description, tags, location=""):
        """Record a canonical event into the note file."""
        logger.info(
            "ENTER _tool_record_event: description=%r tags=%r location=%r",
            description[:80],
            tags,
            location,
        )

        tag_str = tags.strip()
        loc_clean = (
            location.removeprefix(TAG_LOC) if location else self.session.location
        )
        pwd = f"{PWD_EVENTS}/{loc_clean}"
        context = f"canonical event at {loc_clean}"

        if self.session.current_scene:
            tag_str = f"{tag_str} {TAG_SCENE}{self.session.current_scene}"

        note = Note.jot(
            message=description,
            tag=tag_str,
            context=context,
            pwd=pwd,
        )
        Note.append(Note.NOTEFILE, note)

        logger.info("event recorded: ts=%s tag=%s", note.now, note.tag)
        return f"Event recorded: {description[:80]}..."

    @rp_tool(
        description=(
            "List all characters currently present in the scene, along with "
            "their current disposition (mood, attitude, body language). "
            "Call this when the player asks who is nearby, looks around, "
            "checks who they are with, asks if anyone is present, or "
            "otherwise tries to perceive the people in the current location. "
            "Examples of player intent that should trigger this tool: "
            "'who is here?', 'look around', 'do I see anyone?', "
            "'is anyone else in the room?', 'who am I with?', "
            "'I scan the area for people', 'I check who is present'."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
    )
    def _tool_get_people_present(self):
        """Return who is currently in the scene."""
        logger.info("ENTER _tool_get_people_present")

        state = {
            "people": list(self.session.people_present),
            "followup_instruction": FOLLOWUP_QUERY,
        }
        result = json.dumps(state)
        logger.info("get_people_present result: %s", result)
        return result

    @rp_tool(
        description=(
            "List all objects currently present in the scene, along with "
            "their current location. "
            "Call this when the player asks what is nearby, looks around, "
            "checks what grabs their attention, or "
            "otherwise tries to look for interactable objects to accomplish a goal. "
            "Examples of player intent that should trigger this tool: "
            "'what is here?', 'look around', 'what do I see here?', "
            "'what can i do here?', 'what interests me here?'"
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
    )
    def _tool_examine_location(self):
        """Return the objects and people in the current location."""
        logger.info("ENTER _tool_examine_location")

        location = f"{TAG_LOC}{self.session.location}"
        environment = self.session.location_context
        logger.info(
            "ContextBundle size for %s: %i (%i chars)",
            location,
            len(environment),
            len(str(environment)),
        )

        objects = self.extract_scene_context(environment)

        state = {
            "people": list(self.session.people_present),
            "location": self.session.location,
            "noteworthy_objects": objects.get("noteworthy_objects", []),
            "established_props": objects.get("established_props", []),
            "followup_instruction": FOLLOWUP_QUERY,
        }
        result = json.dumps(state)
        logger.info("examine_location result: %s", result)
        return result

    @rp_tool(
        description=(
            "Move the scene to a new location using its full hierarchical path "
            "(e.g. 'manor/foyer/closet'). The engine computes the traversal through "
            "the location hierarchy and provides context for each intermediate room. "
            "Prefer full hierarchical paths. Single bare names (e.g. 'cellar') are "
            "interpreted as siblings under the current top-level root. "
            "Completely different top-level destinations receive direct transport. "
            "Traversal follows the hierarchy — shortcuts are not yet modeled."
        ),
        parameters={
            "type": "object",
            "properties": {
                "location_name": {
                    "type": "string",
                    "description": (
                        "Full hierarchical destination path (e.g. 'manor/foyer/closet'). "
                        "Use '/' to indicate parent/child relationships. "
                        "A bare name like 'cellar' is placed under the current root."
                    ),
                },
            },
            "required": ["location_name"],
        },
    )
    def _tool_navigate_to(self, location_name: str):
        """Compute traversal path and move to a new location."""
        logger.info("ENTER _tool_navigate_to: location_name=%r", location_name)

        raw_dest = location_name.removeprefix(TAG_LOC)
        from_loc = self.session.location

        resolved_dest, nav_type = self.resolve_destination(from_loc, raw_dest)
        to_loc = resolved_dest

        if nav_type == "direct":
            traversal = [from_loc, to_loc]
        else:
            traversal = self.compute_traversal(from_loc, to_loc)

        logger.info(
            "NAVIGATION [%s]: %s → %s | path: %s",
            nav_type,
            from_loc,
            to_loc,
            " → ".join(traversal),
        )

        # Fetch context for intermediate rooms (exclude departure and destination)
        intermediate_contexts = []
        for stop in traversal[1:-1]:
            ctx_str = self.render_context(ContextBundle(f"{PWD_WORLD}/{stop}"))
            logger.info("intermediate: %s (%d chars of context)", stop, len(ctx_str))
            if ctx_str:
                intermediate_contexts.append({"location": stop, "context": ctx_str})

        # Update session state
        self.session.location = to_loc
        self.session.location_context = ContextBundle(f"{PWD_WORLD}/{to_loc}")

        nav_tag = "nav"
        if self.session.current_scene:
            nav_tag += f" {TAG_SCENE}{self.session.current_scene}"

        note = Note.jot(
            message=f"Traveled from {from_loc} to {to_loc} via: {' → '.join(traversal)}",
            tag=nav_tag,
            context=f"navigation event ({nav_type})",
            pwd=f"{PWD_EVENTS}/{to_loc}",
        )
        Note.append(Note.NOTEFILE, note)

        if nav_type == "direct":
            followup = (
                "Narrate the transition to the new location. Since there is no shared "
                "path with the previous location, briefly note the nature of the "
                "transition if it seems unusual or jarring."
            )
        else:
            followup = (
                "Narrate the journey from the departure location through each "
                "intermediate location to the destination. For each stop in the "
                "traversal, briefly acknowledge any notable people, objects, or "
                "hazards found in its context. Skip featureless empty rooms. "
                "Do not teleport the player — the path matters."
            )

        return json.dumps(
            {
                "from": from_loc,
                "to": to_loc,
                "nav_type": nav_type,
                "traversal": traversal,
                "intermediate_contexts": intermediate_contexts,
                "followup_instruction": followup,
            }
        )

    @rp_tool(
        description=(
            "Update the complete list of people currently in the scene. "
            "Call this when characters enter or leave, or when the scene "
            "population changes. Replaces the entire present-people list."
        ),
        parameters={
            "type": "object",
            "properties": {
                "people": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Complete list of people now in the scene",
                },
            },
            "required": ["people"],
        },
    )
    def _tool_set_people_present(self, people: list):
        """Replace the scene's people list with the provided set."""
        logger.info("ENTER _tool_set_people_present: people=%r", people)
        self.session.people_present = set(people)
        logger.info("people_present updated: %s", self.session.people_present)
        present = ", ".join(sorted(self.session.people_present)) or "none"
        return f"Scene people updated: {present}"

    @rp_tool(
        description=(
            "Save a character's canonical description to the world notes. "
            "Call this when a named character is introduced or their key traits "
            "are established. Saved notes power future character lookups."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Character slug identifier (e.g. 'alice', 'old-guard')",
                },
                "description": {
                    "type": "string",
                    "description": "Canonical description of this character",
                },
                "tags": {
                    "type": "string",
                    "description": "Additional space-separated tags (optional)",
                },
            },
            "required": ["name", "description"],
        },
    )
    def _tool_save_character(self, name: str, description: str, tags: str = ""):
        """Persist a character's traits to notes for future RAG retrieval."""
        logger.info("ENTER _tool_save_character: name=%r", name)

        tag_str = f"{TAG_CHAR}{name}"
        if tags:
            tag_str = f"{tag_str} {tags.strip()}"

        note = Note.jot(
            message=description,
            tag=tag_str,
            context=f"character profile: {name}",
            pwd=f"{PWD_CHARS}/{name}",
        )
        Note.append(Note.NOTEFILE, note)

        logger.info("character saved: %s", name)
        return f"Character saved: {name}"

    @rp_tool(
        description=(
            "Save a location's canonical description to the world notes. "
            "Use hierarchical paths to establish spatial relationships: "
            "'manor/foyer/closet' places the closet as a child of the foyer. "
            "Save parent locations before saving their children. "
            "Saved notes power future location context and traversal descriptions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Full hierarchical location path (e.g. 'manor/foyer/closet'). "
                        "Use '/' to indicate parent/child relationships."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Canonical description of this location",
                },
                "tags": {
                    "type": "string",
                    "description": "Additional space-separated tags (optional)",
                },
            },
            "required": ["name", "description"],
        },
    )
    def _tool_save_location(self, name: str, description: str, tags: str = ""):
        """Persist a location's description to notes."""
        logger.info("ENTER _tool_save_location: name=%r", name)

        tag_str = f"{TAG_LOC}{name}"
        if tags:
            tag_str = f"{tag_str} {tags.strip()}"

        note = Note.jot(
            message=description,
            tag=tag_str,
            context=f"location profile: {name}",
            pwd=f"{PWD_WORLD}/{name}",
        )
        Note.append(Note.NOTEFILE, note)

        logger.info("location saved: %s", name)
        return f"Location saved: {name}"

    @rp_tool(
        description=(
            "Save an object's permanent canonical description to the world notes. "
            "Use this ONLY to define or introduce a new object into the world — "
            "its name, appearance, and fixed properties. "
            "Do NOT use this for player interactions with objects (picking up, "
            "using, examining); those are story events — use record_event instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Object slug identifier (e.g. 'iron-key', 'silver-mirror')",
                },
                "description": {
                    "type": "string",
                    "description": "Canonical description of this object",
                },
                "location": {
                    "type": "string",
                    "description": "Which location this object is in (e.g. 'cellar')",
                },
                "tags": {
                    "type": "string",
                    "description": "Additional space-separated tags (optional)",
                },
            },
            "required": ["name", "description", "location"],
        },
    )
    def _tool_save_object(
        self, name: str, description: str, location: str, tags: str = ""
    ):
        """Persist an object's details to notes within its location."""
        logger.info("ENTER _tool_save_object: name=%r location=%r", name, location)

        loc_clean = location.removeprefix(TAG_LOC)
        tag_str = f"obj:{name}"
        if tags:
            tag_str = f"{tag_str} {tags.strip()}"

        note = Note.jot(
            message=description,
            tag=tag_str,
            context=f"object: {name} at {loc_clean}",
            pwd=f"{PWD_WORLD}/{loc_clean}",
        )
        Note.append(Note.NOTEFILE, note)

        logger.info("object saved: %s at %s", name, loc_clean)
        return f"Object saved: {name} (at {loc_clean})"

    @rp_tool(
        description=(
            "Retrieve saved notes about a specific character. "
            "Call this to recall established traits, history, or details "
            "about a named character before writing them into the narrative."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Character slug to look up (e.g. 'alice')",
                },
            },
            "required": ["name"],
        },
    )
    def _tool_get_character(self, name: str):
        """Return saved character notes as context for the LLM."""
        logger.info("ENTER _tool_get_character: name=%r", name)

        ctx = self.gather_character_knowledge(name)
        context_str = self.render_context(ctx, focus_hint=name)
        result = json.dumps(
            {
                "character": context_str or f"[no notes found for character: {name}]",
                "followup_instruction": FOLLOWUP_USE_CHARACTER,
            }
        )
        logger.info("get_character result: %d chars of context", len(context_str))
        return result

    @rp_tool(
        description=(
            "Search world notes by tag or keyword to retrieve established lore. "
            "Use a loc: tag, char: tag, or a plain keyword to find relevant notes. "
            "Call this when you need to recall established world details before narrating."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Tag or keyword to search "
                        "(e.g. 'exp:evie', 'know:bartholomew', 'magic')"
                    ),
                },
            },
            "required": ["query"],
        },
    )
    def _tool_search_world(self, query: str):
        """Search world notes and return matching context for the LLM."""
        logger.info("ENTER _tool_search_world: query=%r", query)

        # Intentionally unions all world/location notes with the query so the LLM
        # receives both established world lore and the specific match in one bundle.
        ctx = self.gather_context([PWD_WORLD, query])
        context_str = self.render_context(ctx, focus_hint=query)
        result = json.dumps(
            {
                "world_context": context_str or f"[no world notes found for: {query}]",
                "followup_instruction": FOLLOWUP_USE_LORE,
            }
        )
        logger.info("search_world result: %d chars of context", len(context_str))
        return result

    @rp_tool(
        description=(
            "Record information that is known only to a specific subset of actors. "
            "Use this instead of record_event whenever knowledge is selective — "
            "a whispered secret, a private conversation, something only one witness saw, "
            "or information passed between specific characters. "
            "The note is tagged with exp: prefixes for each witness, so only those "
            "actors can recall it via their point-of-view context. "
            "If observable_act is provided, a second public note is written describing "
            "what all present actors can observe (the social act) without revealing the "
            "content — so bystanders know THAT an exchange occurred but not WHAT was said. "
            "Examples: whispered passwords, shared secrets, private confessions, "
            "information one character reads that others cannot see."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The information, secret, or knowledge being shared",
                },
                "witnesses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Names of every actor who learns this information. "
                        "Only these actors will be able to recall it later."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Brief description of the circumstance "
                        "(e.g. 'private whisper', 'secret note passed', 'overheard')."
                    ),
                },
                "observable_act": {
                    "type": "string",
                    "description": (
                        "Optional: what ALL currently present actors can observe "
                        "about this exchange — the visible social act without its content "
                        "(e.g. 'Alice leaned toward Bob and whispered something'). "
                        "When provided, a separate public note is written so non-witnesses "
                        "know an exchange occurred without learning its content."
                    ),
                },
            },
            "required": ["content", "witnesses"],
        },
    )
    def _tool_record_knowledge(
        self,
        content: str,
        witnesses: list,
        context: str = "",
        observable_act: str = "",
    ) -> str:
        """Write a selective-knowledge note and optionally a public observable-act note."""
        logger.info(
            "ENTER _tool_record_knowledge: witnesses=%r content=%r",
            witnesses,
            content[:80],
        )

        clean_witnesses = [w.strip() for w in witnesses if w.strip()]
        witness_tags = " ".join(f"{TAG_EXP}{w}" for w in clean_witnesses)
        if self.session.current_scene:
            witness_tags += f" {TAG_SCENE}{self.session.current_scene}"

        private_note = Note.jot(
            message=content,
            tag=witness_tags,
            context=context or f"private knowledge: {', '.join(clean_witnesses)}",
            pwd=f"{PWD_EVENTS}/{self.session.location}",
        )
        Note.append(Note.NOTEFILE, private_note)
        logger.info(
            "private note written: ts=%s witnesses=%s",
            private_note.now,
            clean_witnesses,
        )

        result: dict = {
            "recorded": True,
            "witnesses": clean_witnesses,
            "content_preview": content[:80],
        }

        if observable_act:
            all_present = sorted(self.session.people_present)
            public_tags = " ".join(f"{TAG_EXP}{p}" for p in all_present)
            if self.session.current_scene:
                public_tags += f" {TAG_SCENE}{self.session.current_scene}"
            non_witnesses = sorted(set(all_present) - set(clean_witnesses))

            public_note = Note.jot(
                message=observable_act,
                tag=public_tags,
                context=(
                    f"observable act — non-witnesses: {', '.join(non_witnesses) or 'none'}"
                ),
                pwd=f"{PWD_EVENTS}/{self.session.location}",
            )
            Note.append(Note.NOTEFILE, public_note)
            logger.info(
                "observable-act note written: ts=%s non_witnesses=%s",
                public_note.now,
                non_witnesses,
            )
            result["observable_act_recorded"] = True
            result["non_witnesses"] = non_witnesses

        return json.dumps(result)

    @rp_tool(
        description=(
            "Start a new narrative scene — a cohesive dramatic unit that may span "
            "multiple locations or involve a changing cast. Call this when: "
            "(1) a character begins escorting or leading Bartholomew across multiple "
            "rooms in one continuous movement; "
            "(2) a distinct new activity begins (a meal, an investigation, a "
            "confrontation, a tour of the estate); "
            "(3) multiple characters arrive or depart in a way that shifts the "
            "scene's dramatic focus. "
            "Once set, the active scene slug is automatically attached to every "
            "subsequent record_event, navigate_to, and record_knowledge call — no "
            "manual tagging needed. "
            "Calling begin_scene again starts a fresh scene; the old scene's notes "
            "remain queryable by name via get_scene. "
            "Err toward calling begin_scene rather than leaving events unanchored."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Short kebab-case slug identifying this scene "
                        "(e.g. 'escort-to-bedroom', 'dinner-with-aurora', "
                        "'investigation-of-study'). Used as the retrieval key."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "One or two sentences describing what makes this a "
                        "cohesive scene — who is involved, where it starts, "
                        "and what dramatic thread it follows."
                    ),
                },
            },
            "required": ["name", "description"],
        },
    )
    def _tool_begin_scene(self, name: str, description: str) -> str:
        """Set the active scene and write a scene-header note."""
        logger.info("ENTER _tool_begin_scene: name=%r", name)

        self.session.current_scene = name

        note = Note.jot(
            message=description,
            tag=f"{TAG_SCENE}{name}",
            context=f"scene: {name}",
            pwd=f"{PWD_SCENES}/{name}",
        )
        Note.append(Note.NOTEFILE, note)

        logger.info("scene started: %s", name)
        return json.dumps({"scene": name, "status": "active"})

    @rp_tool(
        description=(
            "Retrieve all notes belonging to a named scene, or the currently "
            "active scene if no name is provided. "
            "Use this to recall what happened during a conversation thread that "
            "spanned multiple locations, to provide continuity context when "
            "resuming an interrupted scene, or to summarize a completed scene "
            "before beginning the next. "
            "Returns the full rendered scene history sorted newest-first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Scene slug to retrieve (e.g. 'escort-to-bedroom'). "
                        "Omit or pass empty string to retrieve the current active scene."
                    ),
                },
            },
            "required": [],
        },
    )
    def _tool_get_scene(self, name: str = "") -> str:
        """Return all notes for a named (or current) scene."""
        logger.info("ENTER _tool_get_scene: name=%r", name)

        scene_name = name.strip() or self.session.current_scene
        if not scene_name:
            return json.dumps(
                {
                    "scene": None,
                    "context": "[no active scene and no scene name provided]",
                    "followup_instruction": (
                        "No scene is currently active. "
                        "Use begin_scene to start one, or provide a scene name."
                    ),
                }
            )

        bundle = self.gather_context([f"{TAG_SCENE}{scene_name}"])
        context_str = self.render_context(bundle, focus_hint=scene_name)

        logger.info(
            "get_scene: scene=%s notes=%d rendered=%d tok",
            scene_name,
            len(bundle),
            len(context_str) // 4,
        )
        return json.dumps(
            {
                "scene": scene_name,
                "context": context_str or f"[no notes found for scene: {scene_name}]",
                "followup_instruction": FOLLOWUP_SCENE_CONTINUITY,
            }
        )

    @rp_tool(
        description=(
            "Record an immutable behavioral constraint for a character — a hard-coded "
            "personality trait so fundamental to their identity that it must actively "
            "shape all future narration involving them. "
            "Conscience notes are not common story events; they are permanent invariants "
            "derived from a character's backstory: a phobia, a moral absolute, a "
            "deep loyalty, a compulsion, a trauma response. "
            "They should be created when a trait is revealed that would realistically "
            "override or revise the character's actions in an ongoing way — not for "
            "passing moods or situational preferences. "
            "Use sparingly but not rarely: aim for maximum dramatic resonance. "
            "Once recorded, conscience notes are automatically included in that "
            "character's point-of-view context on every prepare_context call, so the "
            "LLM always has them when narrating. The player's stated intent is "
            "preserved, but its execution must be reshaped to respect the constraint. "
            "Examples of appropriate use: aversion to water, inability to harm children, "
            "compulsive honesty with authority figures, refusal to enter confined spaces."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": (
                        "Character slug this constraint belongs to "
                        "(e.g. 'mc', 'evie', 'cassidy'). Defaults to the player character."
                    ),
                },
                "trait": {
                    "type": "string",
                    "description": (
                        "Short kebab-case slug identifying this constraint "
                        "(e.g. 'aversion-to-water', 'protective-of-children', "
                        "'loyalty-to-bellvues'). Used as the retrieval key."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Full canonical description of the trait: what it is, "
                        "where it comes from (backstory basis), and how severe it is."
                    ),
                },
                "behavioral_guidance": {
                    "type": "string",
                    "description": (
                        "Prescriptive narration rule: exactly how this constraint "
                        "should reshape the character's actions when triggered. "
                        "Write as a directive, e.g.: 'When any aquatic activity is "
                        "suggested, Bartholomew must find reasons to avoid it — he "
                        "will become visibly uneasy and redirect the scene.'"
                    ),
                },
            },
            "required": ["character", "trait", "description", "behavioral_guidance"],
        },
    )
    def _tool_record_conscience(
        self,
        character: str,
        trait: str,
        description: str,
        behavioral_guidance: str,
    ) -> str:
        """Persist a conscience constraint for a character."""
        logger.info(
            "ENTER _tool_record_conscience: character=%r trait=%r",
            character,
            trait,
        )

        tag_str = f"{TAG_CONS}{trait}"
        note = Note.jot(
            message=f"{description}\n\nBehavioral guidance: {behavioral_guidance}",
            tag=tag_str,
            context=f"conscience: {character} — {trait}",
            pwd=f"{PWD_CONSCIENCE}/{character}",
        )
        Note.append(Note.NOTEFILE, note)

        logger.info("conscience recorded: %s / %s", character, trait)
        return json.dumps(
            {
                "character": character,
                "trait": trait,
                "status": "recorded",
                "followup_instruction": (
                    f"This constraint is now active for {character}. "
                    "It will be automatically included in their POV context "
                    "on every prepare_context call. Apply it when narrating "
                    "any action that intersects with this trait."
                ),
            }
        )

    @rp_tool(
        description=(
            "Record the main character's intuitive reading of how another character "
            "perceives, feels about, or relates to them — the felt sense of social "
            "dynamics beneath the surface of spoken words. "
            "Yomi captures what the main character senses from micro-expressions, "
            "tone, body language, and subtext: the unspoken intentions, suppressed "
            "emotions, and underlying attitude of the other character toward the main "
            "character. "
            "Call this after a significant character interaction — a first meeting, "
            "a loaded conversation, a moment of tension or warmth — when the main "
            "character has reason to form a felt social read. "
            "Yomi is automatically injected before narrative output to deepen "
            "first-person interiority and vivid social awareness in the prose. "
            "It also tracks the arc of relationships across scenes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "Character slug the yomi is about (e.g. 'evie', 'aurora')",
                },
                "insight": {
                    "type": "string",
                    "description": (
                        "The main character's felt intuition: what they sense about "
                        "how this character sees them, what the character is feeling "
                        "beneath the surface, and what unspoken intentions or tensions "
                        "exist between them. Write in first-person present tense as the "
                        "main character's inner voice."
                    ),
                },
            },
            "required": ["character", "insight"],
        },
    )
    def _tool_save_yomi(self, character: str, insight: str) -> str:
        """Persist the MC's yomi insight about another character."""
        logger.info("ENTER _tool_save_yomi: character=%r", character)

        note = Note.jot(
            message=insight,
            tag=f"{TAG_YOMI}{character}",
            context=f"yomi: {self.main_character} → {character}",
            pwd=f"{PWD_YOMI}/{character}",
        )
        Note.append(Note.NOTEFILE, note)

        logger.info("yomi saved: %s → %s", self.main_character, character)
        return json.dumps(
            {
                "status": "saved",
                "character": character,
                "followup_instruction": (
                    f"Yomi for {character} has been recorded. "
                    "Proceed to the narrative — this intuition will be woven into "
                    "the main character's first-person perspective automatically."
                ),
            }
        )

    @rp_tool(
        description=(
            "Retrieve the main character's stored yomi insight for a specific "
            "character — their accumulated intuitions about how that character "
            "perceives and relates to them. "
            "Call this when the player asks about their feelings toward or "
            "relationship with a character out-of-character, when a conversation "
            "between them becomes central to the action, or when assessing whether "
            "their relationship arc has developed further. "
            "Returns notes sorted newest-first; freshly produced yomi is preferable "
            "when active social dynamics are at play."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "Character slug to retrieve yomi for (e.g. 'evie', 'aurora')",
                },
            },
            "required": ["character"],
        },
    )
    def _tool_get_yomi(self, character: str) -> str:
        """Return stored yomi insights for a character."""
        logger.info("ENTER _tool_get_yomi: character=%r", character)

        bundle = ContextBundle(f"{PWD_YOMI}/{character}")
        context_str = self.render_context(bundle, focus_hint=character)

        logger.info(
            "get_yomi: character=%s notes=%d rendered=%d tok",
            character,
            len(bundle),
            _tok(context_str),
        )
        return json.dumps(
            {
                "character": character,
                "yomi": context_str or f"[no yomi recorded for: {character}]",
            }
        )

    @rp_tool(
        description=(
            "Retrieve all active conscience constraints for a character. "
            "Call this before narrating any action that might intersect with "
            "a known or suspected behavioral constraint — especially for the "
            "player character. Returns all cons: notes for the given character "
            "sorted newest-first. "
            "Conscience notes are also automatically included in prepare_context "
            "output; this tool is for direct inspection or when you need to "
            "verify a specific constraint before writing a scene."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": (
                        "Character slug to look up (e.g. 'mc', 'evie'). "
                        "Defaults to the player character 'mc'."
                    ),
                },
            },
            "required": [],
        },
    )
    def _tool_get_conscience(self, character: str = "mc") -> str:
        """Return all conscience constraints recorded for a character."""
        logger.info("ENTER _tool_get_conscience: character=%r", character)

        bundle = self.gather_context([f"{PWD_CONSCIENCE}/{character}"])
        context_str = self.render_context(bundle, focus_hint=character)

        logger.info(
            "get_conscience: character=%s notes=%d rendered=%d tok",
            character,
            len(bundle),
            _tok(context_str),
        )
        return json.dumps(
            {
                "character": character,
                "conscience": context_str
                or f"[no conscience constraints recorded for: {character}]",
                "followup_instruction": FOLLOWUP_CONSCIENCE_ACTIVE,
            }
        )

    @rp_tool(
        description=(
            "Gather, sort, and condense all context relevant to the current scene "
            "into a single structured snapshot. Returns shared location knowledge "
            "and per-character knowledge views — each character's context includes "
            "their private knowledge (know:name) and shared experiences (exp:name) "
            "but NOT other characters' private information, preserving authentic "
            "knowledge gaps. "
            "Call this before writing narrative for a new scene, after navigation, "
            "or when you need an authoritative, token-safe summary of current world "
            "state. Supply focus_hint to bias condensation toward a specific topic "
            "or entity (e.g. 'alice', 'the iron key', 'recent combat')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "focus_hint": {
                    "type": "string",
                    "description": (
                        "Optional topic or entity name to bias condensation toward. "
                        "Leave empty for a balanced snapshot of everything relevant."
                    ),
                },
            },
            "required": [],
        },
    )
    def _tool_prepare_context(self, focus_hint: str = "") -> str:
        """Assemble and condense per-character POV context for the current scene."""
        logger.info("ENTER _tool_prepare_context: focus_hint=%r", focus_hint)

        context_map = self.build_scene_context_map(focus_hint=focus_hint)

        shared = context_map.pop("shared", "")
        character_contexts = context_map

        total_ctx_toks = _tok(shared) + sum(
            _tok(v) for v in character_contexts.values()
        )
        logger.info(
            "[CTX] prepare_context: shared=%d tok | per-char=%s | total=%d tok",
            _tok(shared),
            {k: f"{_tok(v)} tok" for k, v in character_contexts.items()},
            total_ctx_toks,
        )

        return json.dumps(
            {
                "location": self.session.location,
                "people_present": sorted(self.session.people_present),
                "shared_context": shared or "[no shared location context found]",
                "character_contexts": {
                    name: ctx or f"[no context found for {name}]"
                    for name, ctx in character_contexts.items()
                },
                "followup_instruction": FOLLOWUP_CONTEXT_SNAPSHOT,
            }
        )

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def register_all_tools(self):
        """
        Discover and register all @rp_tool-decorated methods.
        Must be called before run_tool_loop.
        """
        logger.info("ENTER register_all_tools")

        for attr_name in dir(self.__class__):
            fn = getattr(self.__class__, attr_name)
            if callable(fn) and hasattr(fn, "_rp_tool_meta"):
                description, parameters = fn._rp_tool_meta
                bound = getattr(self, attr_name)
                tool_name = attr_name.removeprefix("_tool_")
                self._register_tool(tool_name, description, parameters, bound)

        logger.info("registered %d tool(s)", len(self._tool_schemas))

    # ------------------------------------------------------------------
    # Message construction helpers
    # ------------------------------------------------------------------

    def build_user_message(self, user_input):
        """
        Augment raw user input with the current session state header.

        Returns:
            dict suitable for appending to a messages list.
        """
        augmented = f"{self.session.header()}\n{user_input}"
        return {"role": "user", "content": augmented}

    def build_tool_result_message(self, tool_call_id, result_str):
        """
        Build the tool result message appended after dispatch.
        Strips followup_instruction before storing in message history.

        Returns:
            (instruction_or_None, message_dict)
        """
        instruction, cleaned = self.strip_followup_instruction(result_str)
        msg = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": cleaned,
        }
        return instruction, msg

    # ---------------------------------------------------------------------------
    # Note creation from tool calls
    # ---------------------------------------------------------------------------

    def create_notes_from_tool_calls(self, tool_calls, tool_results):
        """
        After each tool-call round, write an audit note for every tool call
        and its result. Notes are stored at /story/tool-notes/{location}.

        Args:
            tool_calls:   list of tool_call dicts from the LLM response.
            tool_results: dict mapping tool_call id -> raw result string.
        """
        PWD_NOTES = "/story/tool-notes"

        loc = self.session.location
        people = self.session.people_present

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            fn_args_raw = tc["function"]["arguments"]
            tool_id = tc.get("id", fn_name)

            try:
                fn_args = (
                    json.loads(fn_args_raw)
                    if isinstance(fn_args_raw, str)
                    else fn_args_raw
                )
            except json.JSONDecodeError:
                fn_args = {}

            raw_result = tool_results.get(tool_id, "")

            note_body = (
                f"tool_call: {fn_name}\n"
                f"args: {json.dumps(fn_args, indent=2)}\n"
                f"result: {raw_result[:500]}"
            )

            tags = f"tool:{fn_name}"

            note = Note.jot(
                message=note_body,
                tag=tags,
                context=f"tool call log at loc:{loc}",
                pwd=f"{PWD_NOTES}/{loc}",
            )
            Note.append(Note.NOTEFILE, note)
            logger.debug("tool-call note written: %s -> %s", fn_name, tool_id)

    # ------------------------------------------------------------------
    # Narrative synthesis helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _summarize_write_result(fn_name: str, result_str: str) -> str:
        """Return a short human-readable summary of a write-tool result.

        Used to build the canonical-facts section of the narrative synthesis
        injection.  The followup_instruction key is silently ignored since it
        is directives, not content.
        """
        try:
            parsed = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            return f"{fn_name}: {result_str[:150]}"

        if fn_name == "navigate_to":
            frm = parsed.get("from", "?")
            to = parsed.get("to", "?")
            via = " → ".join(parsed.get("traversal", [frm, to]))
            return f"Traveled: {via}"
        if fn_name == "begin_scene":
            return f"Scene started: {parsed.get('scene', '?')}"
        if fn_name == "record_knowledge":
            witnesses = ", ".join(parsed.get("witnesses", []))
            preview = parsed.get("content_preview", "")
            return f"Private knowledge ({witnesses}): {preview}"
        if fn_name == "record_conscience":
            char = parsed.get("character", "?")
            trait = parsed.get("trait", "?")
            return f"Conscience ({char}): {trait}"
        # All other cases: the result was not JSON-parsable into something useful;
        # fall back to the raw result string (e.g. "Event recorded: …")
        return f"{fn_name}: {str(parsed)[:150]}"

    def _build_narrative_synthesis(
        self,
        accumulated_think: list[str],
        canonical_results: list[tuple[str, str]],
    ) -> str:
        """Build a pre-narrative injection from think blocks and canonical write results.

        Returns an empty string when there is nothing meaningful to inject.
        The caller appends this as a role=user message before the final
        narrative call, giving the LLM explicit access to:

          - The story advances it reasoned about (think blocks from tool turns)
          - The canonical facts it just established via write tools

        Both sources are framed as directives, not summaries, so the LLM
        incorporates them into the prose rather than restating them.
        """
        parts: list[str] = []

        if accumulated_think:
            combined = "\n---\n".join(accumulated_think)
            parts.append(
                "NARRATOR PLANNING NOTES — story advances you reasoned about this turn.\n"
                "These belong in the narrative. Weave them into the prose naturally; "
                "do not list or announce them separately:\n\n" + combined
            )

        if canonical_results:
            lines = [
                f"  • {self._summarize_write_result(fn, res)}"
                for fn, res in canonical_results
            ]
            parts.append(
                "CANONICAL FACTS ESTABLISHED THIS TURN — these just happened "
                "and must be present in the narrative:\n" + "\n".join(lines)
            )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # LLM tool loop
    # ------------------------------------------------------------------

    def _guard_payload(self, messages: list) -> list:
        """Pre-call safety guard against exceeding MODEL_CONTEXT_LIMIT_TOKS.

        Tiers (capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS):
          < 85%  — debug log only, no action
          85-99% — WARNING log, no mutation
          ≥ 100% — WARNING log + targeted reduction:
                   pass 1: trim oldest tool-result messages proportionally
                   pass 2: drop oldest non-system messages if still over
        Never touches role="system" or the final message in the list.
        """
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        total = sum(_tok(str(m.get("content") or "")) for m in messages)
        self._last_payload_toks = total
        pct = 100.0 * total / capacity

        if pct < 85.0:
            logger.debug("[CTX] guard: %d tok (%.1f%% of cap=%d)", total, pct, capacity)
            return messages

        if total <= capacity:
            logger.warning(
                "[CTX] guard: APPROACHING LIMIT — %d tok (%.1f%% of cap=%d)",
                total,
                pct,
                capacity,
            )
            return messages

        # Over limit — reduce
        logger.warning(
            "[CTX] guard: OVER LIMIT — %d tok > cap=%d (%.1f%%) — reducing",
            total,
            capacity,
            pct,
        )
        messages = [dict(m) for m in messages]
        excess = total - capacity

        # Pass 1: trim tool-result messages oldest-first, never the last message
        for i in range(len(messages) - 1):
            if excess <= 0:
                break
            m = messages[i]
            if m.get("role") != "tool":
                continue
            content = str(m.get("content") or "")
            ctoks = _tok(content)
            if ctoks < 20:
                continue
            target_toks = max(10, ctoks - excess - 50)
            ratio = len(content) / max(ctoks, 1)
            cut = int(target_toks * ratio)
            messages[i]["content"] = content[:cut] + "\n[payload guard: truncated]"
            shed = ctoks - _tok(messages[i]["content"])
            excess -= shed
            logger.warning(
                "[CTX] guard: trimmed tool result msg[%d] by ~%d tok (was %d tok)",
                i,
                shed,
                ctoks,
            )

        # Pass 2: drop oldest non-system messages if still over
        if excess > 0:
            for i in range(1, len(messages) - 1):
                if excess <= 0:
                    break
                m = messages[i]
                if m is None or m.get("role") == "system":
                    continue
                ctoks = _tok(str(m.get("content") or ""))
                logger.warning(
                    "[CTX] guard: dropped msg[%d] role=%s ~%d tok",
                    i,
                    m.get("role"),
                    ctoks,
                )
                messages[i] = None
                excess -= ctoks
            messages = [m for m in messages if m is not None]

        new_total = sum(_tok(str(m.get("content") or "")) for m in messages)
        logger.warning(
            "[CTX] guard: reduction complete — %d → %d tok (saved %d)",
            total,
            new_total,
            total - new_total,
        )
        return messages

    def run_tool_loop(self, messages, max_iterations=8):
        """
        Send the conversation to the LLM with registered tools.
        Loop until the LLM returns a narrative (no tool calls) or
        max_iterations is reached.

        Before the final narrative call the loop injects a synthesis message
        that consolidates two sources of material the vanilla response would
        otherwise under-use:

          1. Think blocks from tool-calling turns — the narrator's unspoken
             planning notes, which often contain story advances that never
             make it to the prose.
          2. Results from write-type tools (record_event, navigate_to, etc.)
             — canonical facts just established this turn that must appear in
             the narrative.

        The narrative call itself uses NARRATIVE_TEMPERATURE (not the lower
        temperature used for tool dispatch), adding entropy that counteracts
        the repetition caused by stationary lore in the context window.
        When no tools were called at all (iter 0), the original response is
        returned unchanged to avoid a redundant LLM call.

        Args:
            messages:       list of message dicts (mutated in place).
            max_iterations: safety cap on tool-call rounds.

        Returns:
            The final LLM response dict (always has a "content" key).
        """
        logger.info(
            "ENTER run_tool_loop: max_iterations=%d message_count=%d",
            max_iterations,
            len(messages),
        )

        accumulated_think: list[str] = []
        canonical_results: list[tuple[str, str]] = []

        for i in range(max_iterations):
            messages = self._guard_payload(messages)
            logger.debug(
                "[CTX] run_tool_loop iter %d/%d: %d message(s), %d tok total payload",
                i + 1,
                max_iterations,
                len(messages),
                self._last_payload_toks,
            )
            response_msg = call_llm(
                messages, tools=self._tool_schemas, tool_choice="auto"
            )

            tool_calls = response_msg.get("tool_calls")
            if not tool_calls:
                # ── Yomi + narrative synthesis injection ───────────────────
                # Gather yomi for present characters (empty string when none saved).
                # If tools ran at least once OR yomi exists, re-call the LLM so it
                # generates clean prose primed by social intuition and canonical facts.
                yomi_text = self._gather_yomi_for_scene()

                if i > 0 or yomi_text:
                    synthesis = self._build_narrative_synthesis(
                        accumulated_think, canonical_results
                    )

                    injection_parts = []
                    if yomi_text:
                        injection_parts.append(yomi_text)
                    if synthesis:
                        injection_parts.append(synthesis)

                    injection = "\n\n".join(injection_parts)
                    if injection:
                        logger.debug(
                            "[YOMI/SYNTH] injection: yomi=%s think=%d canonical=%d",
                            bool(yomi_text),
                            len(accumulated_think),
                            len(canonical_results),
                        )
                        messages.append({"role": "user", "content": injection})
                        messages = self._guard_payload(messages)
                    response_msg = call_llm(messages, temperature=NARRATIVE_TEMPERATURE)

                content = response_msg.get("content", "")
                history_toks = self._last_payload_toks
                logger.info(
                    "[CTX] run_tool_loop EXIT: narrative=%d tok | history=%d tok "
                    "across %d messages | think_blocks=%d canonical_writes=%d",
                    _tok(content),
                    history_toks,
                    len(messages),
                    len(accumulated_think),
                    len(canonical_results),
                )
                return response_msg

            # ── Extract think from this tool-calling response ──────────────
            think, _ = self.strip_think_tags(response_msg.get("content", "") or "")
            if think.strip():
                accumulated_think.append(think.strip())
                logger.debug("[SYNTH] captured think block (%d tok)", _tok(think))

            messages.append(response_msg)

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"]["arguments"]
                tool_id = tc.get("id", fn_name)

                logger.info("TOOL CALL [iter %d]: %s(%s)", i + 1, fn_name, fn_args)
                result = self._dispatch(fn_name, fn_args)

                # Track canonical write results for synthesis injection
                if fn_name in _WRITE_TOOLS:
                    canonical_results.append((fn_name, result))
                    logger.debug("[SYNTH] captured canonical result from %s", fn_name)

                if DEBUG_AUDIT_NOTES:
                    self.create_notes_from_tool_calls(
                        tool_calls=[tc],
                        tool_results={tool_id: result},
                    )

                instruction, tool_msg = self.build_tool_result_message(tool_id, result)
                if instruction:
                    logger.debug(
                        "followup_instruction from %s: %s", fn_name, instruction[:120]
                    )

                logger.debug("TOOL RESULT [%s]: %s", fn_name, tool_msg["content"][:200])
                messages.append(tool_msg)

                if instruction:
                    messages.append({"role": "user", "content": instruction})

        logger.warning("max iterations (%d) reached", max_iterations)
        return {"content": "(The narrator deliberated too long and could not decide.)"}
