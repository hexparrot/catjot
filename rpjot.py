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

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

import json
import logging
import re
import sys
import time

import requests
from dataclasses import dataclass, field as _dc_field

from catjot import (
    Note,
    NoteContext,
    SearchType,
    ContextBundle,
    call_llm,
)


class LLMError(RuntimeError):
    """Raised when the LLM endpoint returns an error or is unreachable."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Tokenizer
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


def _msg_toks(m: dict) -> int:
    """Token cost of one chat message: content plus any tool_calls payload.

    The step-2 loop appends assistant messages whose function-call arguments
    live in ``m["tool_calls"]`` with ``content`` often empty or None. Those
    argument JSONs are real payload the guard must account for; a content-only
    sum undercounts exactly the messages most likely to push a call over the
    window. Shared by the payload guard, history compaction, and the REPL
    token panel so every subsystem measures the same way.
    """
    t = _tok(str(m.get("content") or ""))
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function", {}) or {}
        t += _tok(str(fn.get("name", ""))) + _tok(str(fn.get("arguments", "")))
    return t


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s.%(funcName)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger("rpjot_engine")
_h_file = None  # module-level ref to the current file handler (swappable per session)
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


def configure_logging(stamp: str) -> None:
    """Re-point the debug file handler to debug_{stamp}.log for this session.

    Segments the log per session so a runaway investigation doesn't grep one
    ever-growing shared file (R6). Falls back to the module-load default
    (debug.log) when never called, so imports and the test suite keep working.
    """
    global _h_file
    if not stamp:
        return
    new_path = f"debug_{stamp}.log"
    new_handler = logging.FileHandler(new_path, mode="a")
    new_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    if _h_file is not None and _h_file in logger.handlers:
        logger.removeHandler(_h_file)
        _h_file.close()
    logger.addHandler(new_handler)
    _h_file = new_handler
    logger.info("[LOG] per-session debug log: %s", new_path)

# ---------------------------------------------------------------------------
# Tag and Directory Constants
# ---------------------------------------------------------------------------

TAG_CHAR = "char:"  # char:alice
TAG_EXP = "exp:"  # exp:alice+bob  (shared experience)
TAG_KNOW = "know:"  # know:alice     (private knowledge)
TAG_SCENE = "scene:"  # scene:escort-to-bedroom
TAG_CONS = "cons:"  # cons:aversion-to-water
TAG_OBJ = "obj:"  # obj:iron-key    (object identity; OBJECT_TOOLING §2)

PWD_RULES = "/system/rules"
PWD_WORLD = "/story/location"
PWD_CHARS = "/story/character"
PWD_EVENTS = "/story/events"
PWD_SCENES = "/story/scenes"
PWD_CONSCIENCE = "/story/conscience"
PWD_SUMMARIES = "/summaries"
PWD_YOMI = "/yomi"
PWD_REL = "/story/relationship"
PWD_INTERIOR = "/story/interior"
PWD_OBJECTS = "/story/object"  # canonical object descriptions (OBJECT_TOOLING §2)

TAG_YOMI = "yomi:"  # yomi:alice
TAG_REL = "rel:"  # rel:bond, rel:history, rel:wound …
TAG_INT = "int:"  # int:secret, int:desire, int:mask …

# ref:1750000100 — direct entry citation (TS_CITATIONS C1). Engine-stamped
# retrieval provenance ONLY: a targeted step-1 lookup surfaced the cited entry
# this turn. Never model-emitted; no tool schema exposes it (C2). The key is
# the cited note's epoch int verbatim and may resolve to every note sharing
# that second (C3) — dereference rides the existing TIMESTAMP match.
TAG_REF = "ref:"

# Citation caps (TS_CITATIONS C5/C6): refs captured per targeted lookup,
# ref words stamped per written note, timestamps dereferenced per bundle.
_REF_CAP_PER_LOOKUP = 3
_REF_CAP_PER_NOTE = 6
_REF_DEREF_CAP = 12

# LOCATION_MARKING §3.1 — the gated lexical fallback for a led/passive move.
# A passive-arrival cue = a _LED_VERBS word co-occurring with a directional
# preposition. Mirrors the _MOVE_VERBS pattern for third-person/passive movement.
# The gate is load-bearing: it keeps the neg_navto mention≠movement failure out
# of the metadata side-door (a mere "meet me in the garage" must NOT re-mark).
_LED_VERBS = frozenset({
    "lead", "leads", "led", "bring", "brings", "brought", "pull", "pulls",
    "pulled", "carry", "carries", "carried", "drag", "drags", "dragged",
    "escort", "escorts", "escorted", "arrive", "arrives", "arrived",
    "take", "takes", "took", "usher", "ushers", "ushered",
})
_LED_PREPS = frozenset({"into", "to", "inside", "in", "through", "toward", "towards"})
# First line of the step-1 world doc: "CURRENT ROOM: <canonical path>" or UNCHANGED.
_CURRENT_ROOM_RE = re.compile(r"(?im)^[ \t]*CURRENT ROOM:[ \t]*(.+?)[ \t]*$")

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

# Human-readable labels for write-tool results in the narrative synthesis (T5).
# Every step-2 tool has an entry so the fallback never renders a raw dict (with
# braces) into the prose-facing synthesis.
_WRITE_TOOL_LABELS = {
    "record_event": "Event",
    "record_knowledge": "Private knowledge",
    "record_conscience": "Conscience",
    "record_mood": "Mood",
    "update_attn": "Attention",
    "save_character": "Character saved",
    "save_location": "Location saved",
    "save_object": "Object saved",
    "place_object": "Object placed",
    "save_yomi": "Yomi",
    "set_people_present": "Cast updated",
    "begin_scene": "Scene",
    "navigate_to": "Travel",
    "record_bond": "Bond",
    "record_history": "Shared history",
    "record_dynamic": "Dynamic",
    "record_power_dynamic": "Power dynamic",
    "record_wound": "Wound",
    "record_promise": "Promise",
    "record_debt": "Debt",
    "record_lie": "Lie",
    "record_leverage": "Leverage",
    "record_impression": "Impression",
    "record_secret": "Secret",
    "record_desire": "Desire",
    "record_longing": "Longing",
    "record_jealousy": "Jealousy",
    "record_mask": "Mask",
    "record_subtext": "Subtext",
    "record_reputation": "Reputation",
    "record_trigger": "Trigger",
    "record_unspoken": "Unspoken",
}

# ---------------------------------------------------------------------------
# Context size limits
# ---------------------------------------------------------------------------

# Max distinct entries retained in SessionState._query_cache before FIFO eviction.
_QUERY_CACHE_MAX = 256

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
MODEL_CONTEXT_LIMIT_TOKS = 30_000
_RESPONSE_RESERVE_TOKS = 2_000  # headroom reserved for the model's reply
NARRATIVE_TEMPERATURE = (
    0.75  # temperature for final prose; higher than tool-dispatch calls
)

# ---------------------------------------------------------------------------
# Per-call output token budgets
# ---------------------------------------------------------------------------
# Cap each call_llm invocation so that thinking-heavy models cannot exhaust
# the output budget on internal reasoning before producing usable content.
# Each limit covers thinking overhead + the actual output tokens needed.
# For models without thinking, these are simple output length caps.
# Tune per model: a local non-thinking model can use smaller values.
MAX_TOKENS_CONDENSE = 2_048  # context distillation / scene-object extraction
MAX_TOKENS_STEP2 = 4_096  # ComplianceStep — more room for multi-tool chains
MAX_TOKENS_NARRATIVE = 2_048  # final prose generation

# ---------------------------------------------------------------------------
# Entropy: how often the system message is paraphrased regardless of scenes
# ---------------------------------------------------------------------------
# begin_scene sets _system_refresh_pending=True on scene transitions.
# SYSTEM_REFRESH_INTERVAL provides a fallback so the system message is
# paraphrased at least every N player turns even if the scene never changes.
SYSTEM_REFRESH_INTERVAL = 8

# System prompt for Step 1: World State Resolution
_STEP1_SYSTEM = (
    "You are a scene intelligence system for a text-based RPG. "
    "Your only job is to retrieve facts — not to narrate or decide what happens. "
    "Read the player's input (note the [MC ...] directive prefix and who is "
    "acting) and call lookup tools to gather everything relevant: character "
    "profiles, relationships, location details, story context, yomi, and "
    "conscience constraints. Then output a structured WORLD STATE document "
    "summarizing what you found. Be comprehensive — it feeds both the "
    "compliance and prose steps.\n"
    "The VERY FIRST line of your output must be `CURRENT ROOM: <path>` naming "
    "the room the scene is in NOW as a canonical slug path — reuse an exact slug "
    "from [ROOMS KNOWN HERE] when the scene is in or moving into one of them. If "
    "the room has not changed since the current location, write "
    "`CURRENT ROOM: UNCHANGED`. Base this on scene understanding (who moved whom, "
    "where), not on places merely mentioned, offered, or thought about."
)

# Placeholder input for the idle-window speculative step-1 run. Explicit
# no-op directive (not empty): classified_input feeds both the PLAYER INPUT
# block and the baseline focus_hint ranking, and a blank there sends the
# model hunting for a phantom input.
_SPECULATIVE_INPUT = (
    "[no player input yet — speculative pre-turn lookup: assemble the WORLD "
    "STATE for the scene exactly as it stands; do not invent new events]"
)

# System prompt for Step 3: Narrative Prose
_STEP3_SYSTEM = (
    "You are a vivid literary narrator for an immersive text-based roleplay experience. "
    "Write in second person. Respond to exactly what the player just did — no more, no less. "
    "Use the World State for atmospheric detail, sensory imagery, and character voice. "
    "Use the Narrative Facts as the factual skeleton: these things happened this turn and "
    "must appear in your prose. Do not call tools. Do not plan. Only narrate. "
    "Write prose that is immersive, sensory, and character-voiced. "
    "Vary sentence structure and rhythm. Show, do not tell."
)

MAX_TOKENS_STEP1 = 4_096  # encyclopedic lookup — needs room to gather
MAX_ITER_STEP1_DELTA = 3  # seeded delta run — most lookups already in the seed
MAX_TOKENS_STEP3 = 3_072  # prose — invest output budget here
STEP3_TEMPERATURE = 1.2  # higher than legacy NARRATIVE_TEMPERATURE for richer variance

# ---------------------------------------------------------------------------
# Tool decorator
# ---------------------------------------------------------------------------


def rp_tool(description, parameters, *, step: int = 2):
    """Mark a method as an LLM-callable tool.

    register_all_tools() discovers all decorated methods and registers them.
    Tool name is derived from the method name by stripping the '_tool_' prefix.

    step=1: read-only world lookup (WorldStateStep)
    step=2: write / state-change (ComplianceStep) — default
    """

    def decorator(fn):
        fn._rp_tool_meta = (description, parameters, step)
        return fn

    return decorator


# ---------------------------------------------------------------------------
# NPC Tracker
# ---------------------------------------------------------------------------


@dataclass
class NPCRecord:
    """In-memory record for one NPC encountered during a play session."""

    slug: str
    display_name: str
    named: bool = True
    central: bool = False
    location_introduced: str = ""
    location_last_seen: str = ""
    intro_purpose: str = ""
    interacted: bool = False
    mentioned: bool = False
    saved: bool = False
    turns_present: int = 0
    turns_mentioned: int = 0
    turn_introduced: int = 0
    turn_last_active: int = 0


class NPCTracker:
    """In-memory NPC roster for one play session. Not persisted to disk."""

    def __init__(self) -> None:
        self._records: dict[str, NPCRecord] = {}

    def get(self, slug: str) -> NPCRecord | None:
        return self._records.get(slug)

    def all(self) -> list[NPCRecord]:
        return list(self._records.values())

    def named_npcs(self) -> list[NPCRecord]:
        return [r for r in self._records.values() if r.named]

    def is_registered(self, slug: str) -> bool:
        return slug in self._records

    def register(
        self,
        slug: str,
        display_name: str,
        *,
        named: bool = True,
        central: bool = False,
        location: str = "",
        intro_purpose: str = "",
        turn: int = 0,
    ) -> NPCRecord:
        if slug in self._records:
            return self._records[slug]
        rec = NPCRecord(
            slug=slug,
            display_name=display_name,
            named=named,
            central=central,
            location_introduced=location,
            location_last_seen=location,
            intro_purpose=intro_purpose,
            turn_introduced=turn,
        )
        self._records[slug] = rec
        return rec

    def mark_interacted(self, slug: str, turn: int = 0) -> None:
        if r := self._records.get(slug):
            r.interacted = True
            r.turn_last_active = turn

    def mark_mentioned(self, slug: str, turn: int = 0) -> None:
        if r := self._records.get(slug):
            r.mentioned = True
            r.turns_mentioned += 1
            r.turn_last_active = turn

    def mark_saved(self, slug: str) -> None:
        if r := self._records.get(slug):
            r.saved = True

    def mark_present(self, slug: str, location: str, turn: int = 0) -> None:
        if r := self._records.get(slug):
            r.turns_present += 1
            r.location_last_seen = location
            r.turn_last_active = turn

    def mark_central(self, slug: str) -> None:
        if r := self._records.get(slug):
            r.central = True

    def roster_summary(self) -> str:
        if not self._records:
            return "(no NPCs registered yet)"
        lines = []
        for r in sorted(self._records.values(), key=lambda x: x.turn_introduced):
            flags = []
            if r.central:
                flags.append("central")
            if r.interacted:
                flags.append("interacted")
            if r.mentioned and not r.interacted:
                flags.append("mentioned-only")
            if not r.named:
                flags.append("unnamed")
            if not r.saved:
                flags.append("not-yet-saved")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            purpose = r.intro_purpose[:60].replace("\n", " ")
            lines.append(
                f"- {r.slug} ({r.display_name}){flag_str}"
                f" | last: {r.location_last_seen}"
                f" | {purpose}"
            )
        return "\n".join(lines)


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
        self.attention: dict[str, str] = {}  # char → gaze/focus (transient)
        self.mood: dict[str, str] = {}  # char → emotional state (transient)
        self._query_cache: dict[str, str] = (
            {}
        )  # render_context results; keyed, invalidated on write
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
        attn_seg = (
            " | attn: "
            + " ".join(f"{c}→{f}" for c, f in sorted(self.attention.items()))
            if self.attention
            else ""
        )
        mood_seg = (
            " | mood: " + " ".join(f"{c}:{m}" for c, m in sorted(self.mood.items()))
            if self.mood
            else ""
        )
        h = (
            f"[CURRENT STATE | location: {self.location}"
            f" | present: {present_str}{scene_str}{attn_seg}{mood_seg}]"
        )
        logger.debug("SessionState.header: %s", h)
        return h


# ---------------------------------------------------------------------------
# 3-Step pipeline classes
# ---------------------------------------------------------------------------


class WorldStateStep:
    """Step 1: Read-only world lookup using step=1 tools.

    Uses its own isolated message list — never touches the main conversation.
    Output is a WorldStateDoc string injected into ComplianceStep and ProseStep.
    """

    def __init__(self, engine: "RPJotEngine") -> None:
        self.engine = engine
        # LLM rounds consumed by the most recent run(); read by run_turn's
        # [TIMING] line. Set on every exit path.
        self.last_rounds = 0
        # True only when the most recent loop exited via the clean final-text
        # path (not fallback/exception). Read by speculate_step1 to reject
        # unusable docs.
        self.last_ok = False
        # True when the most recent run() produced its doc via the seeded
        # delta path. Read by run_turn to finalize seed hit/miss status.
        self.last_seed_used = False

    def _warning_block(self) -> str:
        """Cast + location drift warnings for the SCENE STATE header, or ''."""
        warnings = [
            w
            for w in (
                self.engine._cast_warning_line(),
                self.engine._loc_warning_line(),
            )
            if w
        ]
        return "\n" + "\n".join(warnings) + "\n" if warnings else ""

    def _build_initial_message(self, classified_input: str) -> str:
        sess = self.engine.session
        present = ", ".join(sorted(sess.people_present)) or "none"
        roster = self.engine.npc_tracker.roster_summary()

        baseline = self._build_baseline_context(classified_input)

        cast_block = self._warning_block()

        return (
            f"SCENE STATE:\n"
            f"  location: {sess.location}\n"
            f"  scene: {sess.current_scene or '(none)'}\n"
            f"  people_present: {present}\n"
            f"{cast_block}\n"
            f"NPC TRACKER (session memory — every named character on file):\n{roster}\n\n"
            f"{baseline}\n"
            f"PLAYER INPUT:\n{classified_input}\n\n"
            "The BASELINE CONTEXT above is a deterministic snapshot of the current scene "
            "and the characters present. Use it as the foundation of your WORLD STATE "
            "document. Then enrich it via tool calls: look up any character mentioned in "
            "the player input but not yet present, fetch yomi/conscience/relationships as "
            "needed, search for relevant lore. Output a WORLD STATE document covering "
            "every character in the scene, location atmosphere, and story/scene context."
        )

    def _build_seeded_message(self, classified_input: str, seed_doc: str) -> str:
        """Delta-mode initial message: precomputed doc instead of baseline.

        Reuses the cheap in-memory pieces of _build_initial_message (scene
        state, cast warning, NPC roster) but replaces the expensive
        _build_baseline_context call — the seed doc already covers the scene.
        """
        sess = self.engine.session
        present = ", ".join(sorted(sess.people_present)) or "none"
        roster = self.engine.npc_tracker.roster_summary()

        cast_block = self._warning_block()

        return (
            f"SCENE STATE:\n"
            f"  location: {sess.location}\n"
            f"  scene: {sess.current_scene or '(none)'}\n"
            f"  people_present: {present}\n"
            f"{cast_block}\n"
            f"NPC TRACKER (session memory — every named character on file):\n{roster}\n\n"
            f"PRECOMPUTED WORLD STATE (assembled moments ago for this exact "
            f"scene and cast):\n{seed_doc}\n\n"
            f"PLAYER INPUT:\n{classified_input}\n\n"
            "The PRECOMPUTED WORLD STATE above already covers this scene, its "
            "lore, and every character present. Update it for the player input: "
            "use tool calls ONLY to look up characters, objects, or lore newly "
            "mentioned in the player input and not already covered above. If "
            "nothing new is needed, output the updated WORLD STATE document "
            "directly. Follow the same output conventions (including the "
            "CURRENT ROOM line)."
        )

    def _build_baseline_context(self, classified_input: str) -> str:
        """Deterministic per-scene context — pulled directly from notes.

        Guarantees that the LLM always sees the present characters' full
        backstories and the shared location snapshot exactly once, even if
        every tool call is skipped.
        """
        engine = self.engine
        sess = engine.session
        focus = classified_input or None

        parts = ["BASELINE CONTEXT (deterministic — already loaded from notes):"]

        try:
            shared_terms = [f"{PWD_WORLD}/{a}" for a in sess.location_ancestors]
            shared_bundle = engine.gather_context(shared_terms)
            shared = engine.render_context(
                shared_bundle, focus_hint=focus or ""
            ).strip()
        except Exception as exc:
            logger.warning("[STEP1] baseline shared-context build failed: %s", exc)
            shared = ""
        if shared:
            parts.append(f"\n[LOCATION & SHARED LORE]\n{shared}")

        # [ROOMS KNOWN HERE] — canonical child-room vocabulary (LM §3.4). Lets the
        # step-1 CURRENT ROOM line emit canonical slugs instead of prose.
        try:
            child_slugs = engine._child_room_slugs(sess.location)
        except Exception as exc:
            logger.warning("[STEP1] baseline rooms-known build failed: %s", exc)
            child_slugs = []
        if child_slugs:
            parts.append(
                "\n[ROOMS KNOWN HERE] (canonical child slugs — reuse these exact "
                "names):\n" + ", ".join(child_slugs)
            )

        # [EVENTS IN THIS ROOM & SUB-ROOMS] — down-walk recall (LM §3.6),
        # complementing the ancestor up-walk in [LOCATION & SHARED LORE].
        try:
            loc_events = engine.gather_location_events(
                sess.location, focus_hint=focus or ""
            ).strip()
        except Exception as exc:
            logger.warning("[STEP1] baseline location-events build failed: %s", exc)
            loc_events = ""
        if loc_events:
            parts.append(f"\n[EVENTS IN THIS ROOM & SUB-ROOMS]\n{loc_events}")

        empty = []
        for name in sorted(sess.people_present):
            try:
                bundle = engine.gather_character_knowledge(name)
                pov = engine.render_context(bundle, focus_hint=name).strip()
            except Exception as exc:
                logger.warning(
                    "[STEP1] baseline char-context build failed (%s): %s", name, exc
                )
                pov = ""
            if pov:
                parts.append(f"\n[{name.upper()} — CHARACTER PROFILE]\n{pov}")
            else:
                empty.append(name)
        if empty:
            parts.append(f"\n[NO CHARACTER NOTES ON FILE FOR]: {', '.join(empty)}")

        # [KNOWN OBJECTS] — deterministic canonical-slug vocabulary (OBJECT_TOOLING
        # §3.6): objects whose newest residence is this room, plus those held by
        # present cast. Authoritative over stale room-blob sightings (I7). Framed
        # as a name registry + last-known locations rather than "OBJECTS HERE" —
        # the "currently here" reading suppressed place_object on strong models in
        # the Tier-3 sweep (qwen3-235b objects_here pos 9/12→0/12).
        try:
            obj_lines = engine._objects_here_lines(sess.location, sess.people_present)
        except Exception as exc:
            logger.warning("[STEP1] baseline objects-here build failed: %s", exc)
            obj_lines = []
        if obj_lines:
            parts.append(
                "\n[KNOWN OBJECTS] (canonical slugs — reuse these exact spellings; "
                "last-known locations for continuity, not a record of what happened "
                "this turn):\n" + "\n".join(obj_lines)
            )
        return "\n".join(parts) + "\n"

    def run(self, classified_input: str, seed_doc: str | None = None) -> str:
        """Run Step 1 and return the WorldStateDoc string.

        With seed_doc (a precomputed WorldStateDoc for the current scene and
        cast), attempt a short delta run first — the seed replaces the baseline
        and the model only folds in the player input (MAX_ITER_STEP1_DELTA
        rounds). On any delta failure, fall back to the normal full rebuild;
        last_rounds accumulates across both so [TIMING] counts every real call.
        """
        self.last_seed_used = False

        if seed_doc is not None:
            messages = [
                {"role": "system", "content": _STEP1_SYSTEM},
                {
                    "role": "user",
                    "content": self._build_seeded_message(classified_input, seed_doc),
                },
            ]
            doc = self._run_loop(messages, MAX_ITER_STEP1_DELTA, [])
            if doc:
                self.last_seed_used = True
                return doc
            delta_rounds = self.last_rounds
            logger.warning(
                "[SEED] delta step-1 failed after %d rounds; full rebuild",
                delta_rounds,
            )
            doc = self._run_full(classified_input)
            self.last_rounds += delta_rounds
            return doc

        return self._run_full(classified_input)

    def _run_full(self, classified_input: str) -> str:
        """The full (unseeded) Step 1 path — behavior identical to legacy run()."""
        messages = [
            {"role": "system", "content": _STEP1_SYSTEM},
            {"role": "user", "content": self._build_initial_message(classified_input)},
        ]
        tool_results_collected: list[str] = []
        doc = self._run_loop(messages, 8, tool_results_collected)
        if doc is not None:
            # Clean text exit; empty doc → deterministic fallback (legacy `or`).
            return doc or self._fallback_doc()
        logger.warning("[STEP1] max iterations reached; using collected results")
        return self._fallback_doc(tool_results_collected)

    def _run_loop(
        self,
        messages: list,
        max_iter: int,
        tool_results_collected: list[str],
    ) -> str | None:
        """Shared Step-1 tool loop.

        Returns the stripped WorldStateDoc on a clean final-text exit, or None
        on RequestException / max-iteration exhaustion. Sets last_ok (clean
        final-text exit) and last_rounds on every exit path.
        """
        engine = self.engine
        self.last_rounds = 0
        self.last_ok = False

        for i in range(max_iter):
            messages = engine._guard_payload(
                messages, schema_overhead=engine._cached_step1_schema_toks
            )
            t_call = time.perf_counter()
            try:
                response_msg = call_llm(
                    messages,
                    tools=engine._step1_schemas,
                    tool_choice="auto",
                    max_tokens=MAX_TOKENS_STEP1,
                    temperature=0.1,
                )
            except requests.exceptions.RequestException as exc:
                logger.warning("[STEP1] LLM call failed: %s", exc)
                self.last_rounds = i
                return None
            logger.debug(
                "[TIMING] step1 call %d: %.1fs", i + 1, time.perf_counter() - t_call
            )

            tool_calls = response_msg.get("tool_calls")
            if not tool_calls:
                # Final text output from Step 1 is the WorldStateDoc
                content = response_msg.get("content", "")
                _, world_doc = engine.strip_think_tags(content)
                self.last_rounds = i + 1  # i tool rounds + the final text call
                logger.info(
                    "[STEP1] complete: %d tool rounds, world_doc=%d tok",
                    i,
                    _tok(world_doc),
                )
                self.last_ok = True
                return world_doc.strip()

            messages.append(response_msg)
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"]["arguments"]
                tool_id = tc.get("id", fn_name)

                result = engine._safe_dispatch(
                    engine._step1_handlers, fn_name, fn_args
                )

                logger.debug("[STEP1] tool %s → %s", fn_name, result[:120])
                tool_results_collected.append(f"[{fn_name}]: {result}")
                messages.append(
                    {"role": "tool", "tool_call_id": tool_id, "content": result}
                )
            self.last_rounds = i + 1

        return None

    def _fallback_doc(self, collected: list[str] | None = None) -> str:
        """Deterministic worst-case WorldStateDoc when the LLM fails or stalls.

        Always includes the baseline scene context so downstream steps have at
        least the established character POVs and location lore for present NPCs.
        """
        sess = self.engine.session
        parts = [
            f"WORLD STATE — {sess.location} | scene: {sess.current_scene or 'none'}",
        ]
        baseline = self._build_baseline_context(classified_input="")
        if baseline.strip():
            parts.append(baseline)
        if collected:
            parts.append("COLLECTED TOOL RESULTS:\n" + "\n\n".join(collected))
        return "\n\n".join(parts)


class ComplianceStep:
    """Step 2: Narrative decisions using step=2 (write) tools.

    Receives the WorldStateDoc from Step 1 as context.
    Operates on the main step2_messages history (system=gameplay rules).
    Returns (canonical_results, accumulated_think) for ProseStep.
    """

    # Corrective directive for the zero-canonical nudge (T2/D4). Prefixed
    # [DIRECTIVE] so the model does not confuse it with player input (T5).
    _ZERO_CANONICAL_NUDGE = (
        "[DIRECTIVE] No canonical record was written this turn. If anything "
        "happened — an action, dialogue, or discovery — call record_event or "
        "record_knowledge now. If truly nothing canonical occurred, reply "
        "exactly: DONE."
    )

    # Classified-input prefixes that warrant a nudge when the turn wrote no
    # canon. Actions and spoken/likely-spoken lines usually establish canon;
    # inner monologue and pure attention shifts often do not (D4).
    _NUDGE_PREFIXES = (
        "[MC action]",
        "[MC speaks aloud]",
        "[MC — likely spoken aloud",
    )

    # Turn-scoped DIRECTOR NOTE injected on stationary turns to stop navigate_to
    # over-firing on an NPC's invitation or a merely-mentioned place (the
    # neg_navto 0/18 miss). Kept CONDITIONAL — appears only on stationary turns —
    # so it stays salient instead of habituating like an always-on rule would.
    # The escape hatch ("physically carry the MC") is load-bearing: forced
    # movement (dragged/carried/moving vehicle) classifies stationary yet must
    # still move, which a hard tool-gate cannot express. Wording = the winning
    # `nudge_positive_v2` arm from bakeoff_navnudge.py.
    _STATIONARY_NUDGE = (
        "DIRECTOR NOTE (this turn): the MC has not moved themselves this turn. "
        "An NPC's invitation, or a place merely spoken or thought about, is not "
        "movement — the story continues at the current location. Only if events "
        "physically carry the MC elsewhere (dragged, carried, a moving vehicle) "
        "does the scene move."
    )

    # Classified-input prefixes that are ALWAYS stationary: the MC spoke,
    # thought, or shifted attention but did not physically move (§3.5 table).
    # [MC action] is the only ambiguous prefix; it is resolved by _MOVE_VERBS.
    _STATIONARY_PREFIXES = (
        "[MC speaks aloud]",
        "[MC — likely spoken aloud",
        "[MC inner monologue",
        "[MC attention",
    )

    # First-person movement verbs that turn an [MC action] into real travel —
    # the MC's own body moving. Ported verbatim from bakeoff_navnudge's
    # classify_heuristic so production classification matches the swept harness.
    # PARITY DEBT: the third-person alias branch below does NOT exist in
    # classify_heuristic — port it there before any nudge re-sweep, or the
    # harness will mislabel third-person arms as stationary.
    _MOVE_VERBS = frozenset({
        "follow", "go", "walk", "head", "step", "enter", "climb", "cross",
        "descend", "ascend", "run", "stride", "move", "leave", "exit",
        "return", "approach",
    })

    # Third-person conjugations for the alias branch. Explicit forms (mirrors
    # _LED_VERBS style) — never derived with +"s", and deliberately no gerunds:
    # "Bartholomew considers going to the cellar" must stay stationary.
    _MOVE_VERBS_3P = frozenset({
        "follows", "goes", "walks", "heads", "steps", "enters", "climbs",
        "crosses", "descends", "ascends", "runs", "strides", "moves",
        "leaves", "exits", "returns", "approaches",
    })

    def __init__(self, engine: "RPJotEngine") -> None:
        self.engine = engine
        # LLM rounds consumed by the most recent run(); read by run_turn's
        # [TIMING] line. Set on every exit path.
        self.last_rounds = 0

    @classmethod
    def _should_nudge_zero_canonical(cls, classified_input: str) -> bool:
        """True when an empty-canonical turn should get one corrective round."""
        s = (classified_input or "").lstrip()
        return s.startswith(cls._NUDGE_PREFIXES)

    @classmethod
    def _is_stationary_turn(
        cls, classified_input: str, mc_aliases: frozenset = frozenset()
    ) -> bool:
        """True when the MC did not physically move this turn (→ inject the nudge).

        Implements the sigil→mobility table (§3.5). Speech, likely-speech, inner
        monologue and attention prefixes are stationary unconditionally. Only
        [MC action] is ambiguous: it is *mobile* iff its content is a first-person
        movement — an unquoted line beginning "I ..." carrying a _MOVE_VERBS verb
        — mirroring classify_heuristic exactly, including the "I follow her down
        the corridor" collision and the quoted-dialogue guard. Any UNRECOGNIZED
        prefix fails OPEN to not-stationary (no injection = baseline behavior),
        so a future sigil can never silently suppress navigate_to.

        mc_aliases (lowercase) adds a third-person branch for players who write
        "Bartholomew enters the gallery": mobile iff the first body token is an
        MC alias AND a movement verb (either conjugation set) is present AND the
        body is unquoted. Empty alias set = legacy behavior exactly; the same
        "wants to go" collision class as first person is accepted (§3.5 note).
        """
        s = (classified_input or "").lstrip()
        if s.startswith(cls._STATIONARY_PREFIXES):
            return True
        if not s.startswith("[MC action]"):
            return False  # unrecognized prefix → fail open (treat as mobile)
        body = s.split("]", 1)[-1].lstrip(": ").strip()
        low = body.lower()
        quoted = body[:1] in {'"', "'"}
        words = low.split()
        first_person_move = low.startswith("i ") and any(
            w.strip('.,;:"') in cls._MOVE_VERBS for w in words
        )
        first_token = words[0].strip('.,;:!?"\'*') if words else ""
        third_person_move = first_token in mc_aliases and any(
            w.strip('.,;:"') in cls._MOVE_VERBS or w.strip('.,;:"') in cls._MOVE_VERBS_3P
            for w in words
        )
        return not ((first_person_move or third_person_move) and not quoted)

    def _compose_step2_user_content(
        self, classified_input: str, world_doc: str
    ) -> str:
        """Build the step-2 user message exactly as production sends it.

        Order (recency-favored): WORLD STATE BRIEFING → NARRATOR RULE →
        classified_input → [conditional] DIRECTOR NOTE. The stationary nudge is
        the LAST block so it sits in the strongest attention position, after the
        player input it qualifies. Shared by ComplianceStep.run and the
        production-shape selection tests so both exercise the same composition.
        """
        parts = []
        if world_doc.strip():
            parts.append(f"WORLD STATE BRIEFING:\n{world_doc}")
        parts.append(f"NARRATOR RULE: {self.engine._NARRATOR_RULE}")
        parts.append(classified_input)
        if self._is_stationary_turn(classified_input, self.engine.mc_aliases):
            parts.append(self._STATIONARY_NUDGE)
        return "\n\n".join(parts)

    def run(
        self,
        classified_input: str,
        world_doc: str,
        step2_messages: list,
        max_iterations: int = 10,
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Run Step 2 tool loop. Returns (canonical_results, accumulated_think)."""
        engine = self.engine
        canonical_results: list[tuple[str, str]] = []
        accumulated_think: list[str] = []

        if self._is_stationary_turn(classified_input, engine.mc_aliases):
            logger.info("[STEP2] stationary nudge → injected")
        user_content = self._compose_step2_user_content(classified_input, world_doc)

        messages = list(step2_messages) + [
            {"role": "user", "content": user_content}
        ]

        # `bound` is bumped by exactly one if the zero-canonical nudge fires, so
        # the corrective round always has room even on the last iteration (D4).
        i = 0
        bound = max_iterations
        nudged = False
        self.last_rounds = 0
        # Followup dedupe (TOOL_UNIFY U6): the FOLLOWUP_* constants repeat
        # verbatim across rounds; each repeat is a fresh "do more" prompt that
        # pressures the loop into another round. One injection per turn each.
        seen_instructions: set = set()
        while i < bound:
            messages = engine._guard_payload(
                messages, schema_overhead=engine._cached_compact_step2_schema_toks
            )
            t_call = time.perf_counter()
            try:
                response_msg = call_llm(
                    messages,
                    tools=engine._compact_step2_schemas,
                    tool_choice="auto",
                    max_tokens=MAX_TOKENS_STEP2,
                )
            except requests.exceptions.RequestException as exc:
                logger.warning("[STEP2] LLM call failed: %s", exc)
                self.last_rounds = i
                break
            logger.debug(
                "[TIMING] step2 call %d: %.1fs", i + 1, time.perf_counter() - t_call
            )

            tool_calls = response_msg.get("tool_calls")
            if not tool_calls:
                think, _ = engine.strip_think_tags(
                    response_msg.get("content", "") or ""
                )
                if think.strip():
                    accumulated_think.append(think.strip())

                if (
                    not canonical_results
                    and not nudged
                    and self._should_nudge_zero_canonical(classified_input)
                ):
                    nudged = True
                    bound += 1
                    messages.append(response_msg)
                    messages.append(
                        {"role": "user", "content": self._ZERO_CANONICAL_NUDGE}
                    )
                    logger.info("[STEP2] zero-canonical nudge → injected")
                    i += 1
                    self.last_rounds = i
                    continue

                if nudged:
                    outcome = (
                        f"recorded {len(canonical_results)}"
                        if canonical_results
                        else "acknowledged (no canon)"
                    )
                    logger.info("[STEP2] zero-canonical nudge → %s", outcome)
                self.last_rounds = i + 1  # i tool rounds + the final text call
                logger.info(
                    "[STEP2] complete: %d tool rounds, canonical=%d think=%d",
                    i,
                    len(canonical_results),
                    len(accumulated_think),
                )
                return canonical_results, accumulated_think

            think, _ = engine.strip_think_tags(response_msg.get("content", "") or "")
            if think.strip():
                accumulated_think.append(think.strip())

            messages.append(response_msg)
            round_instructions: list = []
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"]["arguments"]
                tool_id = tc.get("id", fn_name)

                logger.info("[STEP2] tool %s", fn_name)
                result = engine._dispatch_step2(fn_name, fn_args)

                # All step=2 tools mutate state — surface every result to the prose step.
                canonical_results.append((fn_name, result))

                instruction, tool_msg = engine.build_tool_result_message(
                    tool_id, result
                )
                messages.append(tool_msg)
                if instruction and instruction not in seen_instructions:
                    seen_instructions.add(instruction)
                    round_instructions.append(instruction)

            # Tool-result messages stay contiguous under their assistant
            # tool_calls block (standard ordering); one batched directive per
            # round instead of one per tool call. Prefixed so the model does
            # not mistake a system-issued followup for player input (T5).
            if round_instructions:
                messages.append(
                    {
                        "role": "user",
                        "content": "[DIRECTIVE] " + " ".join(round_instructions),
                    }
                )

            i += 1
            self.last_rounds = i

        logger.warning("[STEP2] max iterations reached")
        return canonical_results, accumulated_think


class _StreamThinkGate:
    """Stateful display filter for streamed prose (RPJOT_STREAM).

    Thinking models may emit <think>…</think> (and, as a prose fallback,
    <tool_call>…</tool_call>) before the narrative; the canonical path strips
    them post-hoc via strip_think_tags. For live display we must withhold
    those blocks as tokens arrive: feed(text) returns only the displayable
    portion, holding back any suffix that could be a partial tag split across
    token boundaries.

    The stored narrative is always the strip_think_tags result of the full
    accumulated text — this gate shapes ONLY what is shown live. Accepted
    edge: an orphaned </think> with no opener (strip_think_tags' third case)
    streams the pre-tag text to the display while the stored form drops it.
    Rare, and the stored form is authoritative.
    """

    _OPENERS = ("<think>", "<tool_call>")
    _CLOSERS = {"<think>": "</think>", "<tool_call>": "</tool_call>"}

    def __init__(self):
        self._buf = ""
        self._swallow_until = None  # closing tag we are inside, or None
        self._emitted_any = False

    def _held_tail_len(self, s: str) -> int:
        """Longest suffix of s that is a proper prefix of an opener tag."""
        max_probe = max(len(op) for op in self._OPENERS) - 1
        for k in range(min(len(s), max_probe), 0, -1):
            tail = s[-k:]
            if any(op.startswith(tail) for op in self._OPENERS):
                return k
        return 0

    def feed(self, text: str) -> str:
        self._buf += text
        out = []
        while True:
            if self._swallow_until is not None:
                idx = self._buf.find(self._swallow_until)
                if idx == -1:
                    # Retain just enough tail to catch a split closing tag.
                    keep = len(self._swallow_until) - 1
                    if len(self._buf) > keep:
                        self._buf = self._buf[-keep:]
                    break
                self._buf = self._buf[idx + len(self._swallow_until) :]
                self._swallow_until = None
                continue

            # Scanning/prose mode: emit up to the earliest full opener.
            earliest = None
            for op in self._OPENERS:
                i = self._buf.find(op)
                if i != -1 and (earliest is None or i < earliest[0]):
                    earliest = (i, op)
            if earliest is not None:
                i, op = earliest
                out.append(self._buf[:i])
                self._buf = self._buf[i + len(op) :]
                self._swallow_until = self._CLOSERS[op]
                continue

            hold = self._held_tail_len(self._buf)
            cut = len(self._buf) - hold
            out.append(self._buf[:cut])
            self._buf = self._buf[cut:]
            break

        display = "".join(out)
        if not self._emitted_any:
            display = display.lstrip()
        if display:
            self._emitted_any = True
        return display


class ProseStep:
    """Step 3: Pure narrative prose generation. No tools."""

    def __init__(self, engine: "RPJotEngine") -> None:
        self.engine = engine

    def run(
        self,
        classified_input: str,
        world_doc: str,
        canonical_results: list[tuple[str, str]],
        accumulated_think: list[str],
        step3_messages: list,
    ) -> str:
        """Generate final narrative prose. Returns narrative string."""
        engine = self.engine

        synthesis = engine._build_narrative_synthesis(
            accumulated_think, canonical_results
        )
        attn_text = engine._gather_attn_for_scene()
        mood_text = engine._gather_mood_for_scene()

        injection_parts = [
            "PROSE PHASE — write the narrative response now. "
            "Do not plan or invoke any tools; this is the final prose generation step."
        ]
        if world_doc.strip():
            injection_parts.append(
                f"WORLD STATE (for atmospheric detail):\n{world_doc}"
            )
        if attn_text:
            injection_parts.append(attn_text)
        if mood_text:
            injection_parts.append(mood_text)
        if synthesis:
            injection_parts.append(synthesis)
        injection_parts.append(classified_input)

        prose_messages = list(step3_messages) + [
            {"role": "user", "content": "\n\n".join(injection_parts)}
        ]

        prose_messages = engine._guard_payload(
            prose_messages, schema_overhead=engine._cached_bare_schema_toks
        )

        # Live streaming (RPJOT_STREAM): tokens flow through a think gate to
        # the play loop's printer as they arrive. The canonical narrative is
        # still the strip_think_tags result of the FULL accumulated text —
        # streaming shapes only what is shown live. On a mid-stream
        # RequestException, partial prose may already be on screen; the
        # LLMError surfaces after it, same contract as today.
        engine._prose_streamed = False
        on_token = None
        if engine.prose_stream_cb is not None:
            gate = _StreamThinkGate()
            t_start = time.perf_counter()

            def on_token(text, _gate=gate, _t0=t_start):
                display = _gate.feed(text)
                if display:
                    if not engine._prose_streamed:
                        logger.debug(
                            "[STREAM] first token: %.1fs",
                            time.perf_counter() - _t0,
                        )
                    # Callback BEFORE the flag flips: the printer keys the
                    # turn's top border off _prose_streamed being False.
                    engine.prose_stream_cb(display)
                    engine._prose_streamed = True

        try:
            response_msg = call_llm(
                prose_messages,
                tools=engine._bare_tool_schemas,
                tool_choice="none",
                temperature=STEP3_TEMPERATURE,
                max_tokens=MAX_TOKENS_STEP3,
                on_token=on_token,
            )
        except requests.exceptions.RequestException as exc:
            raise LLMError(str(exc)) from None

        content = response_msg.get("content", "")
        _, narrative = engine.strip_think_tags(content)
        logger.info("[STEP3] prose: %d tok", _tok(narrative))
        return narrative.strip()


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
        narrative = engine.run_turn(classified_input, step2_messages, step3_messages)
    """

    # ===================================================================
    # === 1. Construction & Initialization ===
    # ===================================================================

    def __init__(self, location, people_present=None, main_character="mc"):
        # Step-partitioned tool registries
        self._step1_schemas: list = []  # read-only: WorldStateStep
        self._step2_schemas: list = []  # write: ComplianceStep
        self._step1_handlers: dict = {}
        self._step2_handlers: dict = {}
        self._last_payload_toks: int = 0
        self._cached_schema_toks: int = 0
        self._cached_bare_schema_toks: int = 0
        self._cached_step1_schema_toks: int = 0
        self._cached_compact_step2_schema_toks: int = 0
        self._system_refresh_pending: bool = False
        self._turn_count: int = 0
        # Citation capture (TS_CITATIONS §3.1): `now` timestamps of notes
        # surfaced by this turn's TARGETED step-1 lookups, in capture order.
        # Reset at the top of run_turn; stamped onto C4-scoped step-2 writes.
        self._turn_refs: list = []
        # Cache-hit replay (§3.4): the query cache returns rendered strings,
        # so note timestamps are destroyed at the render boundary — this maps
        # cache key → captured refs, evicted in lockstep with _query_cache.
        self._ref_cache: dict = {}
        # Cast-drift warnings (T1): NPC slugs named in the turn's text but absent
        # from people_present. Detection only; surfaced in /stats and the next
        # step-1 SCENE STATE header, cleared when the discrepancy resolves.
        self._cast_warnings: list = []
        # Location-drift warnings (LM follow-up, cast-drift pattern): filed-vs-
        # session divergences that were NOT auto-committed. Reset at step-2
        # entry; surfaced in /stats and both step-1 headers via
        # _loc_warning_line. [LOCDRIFT] log tag.
        self._loc_warnings: list = []
        # Per-turn mobility verdict stashed by _remark_location; True default
        # so a bare tool call outside run_turn gets the conservative
        # (stationary → auto-move eligible) branch of the LM §3.7 gate.
        self._turn_stationary: bool = True
        # Mobile-turn MC location from record_event, reconciled after step 2
        # iff navigate_to never fired (never races a real traversal).
        self._pending_loc_hint: str | None = None
        # Idle-window precompute (background seed). seed_enabled is set by the
        # play loop from RPJOT_BG_SEED; the engine never reads env. _seed holds
        # the speculative step-1 result {doc, refs, state, turn, rounds,
        # elapsed}; single-use, validated by _consume_seed. _seed_status feeds
        # the [TIMING] line; _bg_stats is written by the play loop's idle
        # worker (spec_s/refresh_s/wait_s) and cleared after logging.
        self.seed_enabled: bool = False
        self._seed: dict | None = None
        self._seed_status: str = "off"
        self._bg_stats: dict | None = None
        # Prose streaming (RPJOT_STREAM): the play loop sets prose_stream_cb
        # to a str-consuming printer; ProseStep streams the step-3 call
        # through a _StreamThinkGate into it. _prose_streamed tells the play
        # loop whether anything was actually displayed live this turn (False
        # → it falls back to the normal full display).
        self.prose_stream_cb = None
        self._prose_streamed: bool = False
        self.main_character = main_character
        # MC alias set (lowercase) for third-person self-movement detection
        # and the record_event MC-present gate. The play loop extends it from
        # RPJOT_MC_ALIASES; it always contains the mc slug itself. Empty env =
        # legacy behavior (first-person only, exp:mc only) — safe but
        # under-fires on third-person players.
        self.mc_aliases: frozenset = frozenset({main_character.lower()})
        self.session = SessionState(
            location=location,
            people_present=people_present or set(),
        )
        # NPC tracker — pre-populated from established character notes,
        # then grows further as new characters appear via tool calls.
        self.npc_tracker = NPCTracker()
        self._preload_npc_tracker_from_notes(location)
        for slug in people_present or set():
            if not self.npc_tracker.is_registered(slug):
                self.npc_tracker.register(slug, slug, location=location, turn=0)
            self.npc_tracker.mark_present(slug, location=location, turn=0)
        logger.info(
            "RPJotEngine init: loc=%s people=%s mc=%s npcs_preloaded=%d",
            location,
            self.session.people_present,
            self.main_character,
            len(self.npc_tracker.all()),
        )

    def _preload_npc_tracker_from_notes(self, location: str) -> None:
        """Scan PWD_CHARS in the notes file and register every established character.

        This guarantees the NPC roster_summary reflects all known characters from
        turn 1, so WorldStateStep's LLM never has to guess who exists.
        """
        seen_pwds = set()
        with NoteContext(Note.NOTEFILE, (SearchType.TREE, PWD_CHARS)) as nc:
            for note in nc:
                if note.pwd in seen_pwds:
                    continue
                seen_pwds.add(note.pwd)
                # First component under PWD_CHARS — an object-possession subpath
                # like /story/character/evie/inventory must resolve to "evie", not
                # mint a phantom "inventory" character (OT §3.1).
                if not note.pwd.startswith(PWD_CHARS + "/"):
                    continue
                char_name = note.pwd[len(PWD_CHARS) + 1:].split("/")[0]
                if char_name and not self.npc_tracker.is_registered(char_name):
                    self.npc_tracker.register(
                        char_name,
                        char_name,
                        named=True,
                        location=location,
                        turn=0,
                    )
                    self.npc_tracker.mark_saved(char_name)

    # ===================================================================
    # === 2. Tool Registry ===
    # ===================================================================

    # ------------------------------------------------------------------
    # Private tool registry
    # ------------------------------------------------------------------

    @property
    def _bare_tool_schemas(self) -> list:
        """Minimal tool stubs (names only) for tool_choice='none' synthesis calls."""
        return [
            {
                "type": "function",
                "function": {
                    "name": s["function"]["name"],
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for s in self._step2_schemas
        ]

    @property
    def _bare_step1_schemas(self) -> list:
        return [
            {
                "type": "function",
                "function": {
                    "name": s["function"]["name"],
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for s in self._step1_schemas
        ]

    # Parameter descriptions that survive step-2 schema compaction (T3/D3).
    # These carry load-bearing argument contracts — the exp:/know: tag grammar,
    # witness/observable-act semantics, hierarchical path rules — whose loss
    # silently corrupts the knowledge-asymmetry engine. Ordered so the keep-list
    # can be trimmed from the bottom if the compact budget ever exceeds ~3,000
    # tok (never drop the first three entries).
    _COMPACT_KEEP_PARAM_DESCRIPTIONS = [
        ("record_event", "tags"),
        ("record_knowledge", "witnesses"),
        ("record_knowledge", "observable_act"),
        ("navigate_to", "location_name"),
        ("save_location", "name"),
        # place_object: the holder-XOR-room and possession-is-residence contracts
        # are load-bearing (OT §3.7). Appended at the bottom — trimmed first if the
        # compact budget ever exceeds ~3,000 tok.
        ("place_object", "holder"),
        ("place_object", "room"),
    ]

    # Legacy fine-grained rel:/int: tools hidden from the LLM-facing compact
    # menu (TOOL_UNIFY U1). They stay registered and dispatchable — an old
    # resumed history or a habituated model can still call them by name — but
    # the model's step-2 menu offers record_relationship / record_interior
    # instead. Census basis: across all real sessions the rel:* tags fired
    # zero times and int:* once, while these 19 schemas cost 56% of the
    # compact step-2 budget and diluted every selection with distractors.
    _COMPACT_HIDDEN_TOOLS = frozenset({
        "record_bond", "record_history", "record_dynamic",
        "record_power_dynamic", "record_wound", "record_promise",
        "record_debt", "record_lie", "record_leverage", "record_impression",
        "record_secret", "record_desire", "record_longing", "record_jealousy",
        "record_mask", "record_subtext", "record_reputation", "record_trigger",
        "record_unspoken",
    })

    # Function-level (tool) descriptions that survive step-2 schema compaction.
    # Compaction strips ALL function descriptions by default (§3.1), so the model
    # selects among 31 step-2 tools essentially by name. This map is EMPTY unless
    # a description is load-bearing for tool *selection* (not argument shape,
    # which _COMPACT_KEEP_PARAM_DESCRIPTIONS covers). Each entry costs ~30 tok;
    # test_compact_budget_under_3000 guards the ceiling.
    #
    # navigate_to — the `nudge_pos_desc` upgrade. The 2026-07-03 re-sweep
    # (bakeoff_navnudge) showed this one-liner ON TOP OF the stationary nudge
    # strictly beats the nudge alone: vs the shipped v2 it lifts stationary
    # 97%→100% (closes the neg_invite residual) and forced 33%→58% with no
    # per-model regression — the "or is physically carried" clause reinforces the
    # nudge's dragged/carried escape hatch. Text kept identical to
    # bakeoff_navnudge.NAV_FUNCTION_DESC so production == the swept harness.
    #
    # place_object — the nudge_pos_desc-style positive one-liner (OT §3.7). Text
    # kept identical to _tool_place_object's full description so production ==
    # the Tier-3 swept harness. Phase 4's bakeoff_objperm sweep validates the
    # ship/hold decision (§4.3): ship iff it wins mention-negative without losing
    # pickup/handover. Shipped by default per OT §3.7's budget projection.
    _COMPACT_KEEP_FUNCTION_DESCRIPTIONS: dict = {
        "navigate_to": (
            "Move the scene when the MC's own body travels (or is physically "
            "carried) to a new place; never for places merely mentioned, "
            "offered, or thought about."
        ),
        "place_object": (
            "Log an object changing hands or rooms — picked up, handed over, "
            "dropped, stowed, left behind; also to note a lasting change to its "
            "condition."
        ),
        # The merged recorders' names are broader than any legacy tool name,
        # so a selection one-liner is load-bearing (TOOL_UNIFY U1); the
        # kind-enum itself survives compaction as parameter structure.
        "record_relationship": (
            "Record a durable fact BETWEEN two characters — kind picks bond, "
            "history, dynamic, power, wound, promise, debt, lie, leverage, or "
            "impression; char_a is the actor/source, char_b the target."
        ),
        "record_interior": (
            "Record one character's hidden interior — kind picks secret, "
            "desire, longing, jealousy, mask, subtext, reputation, trigger, or "
            "unspoken; content is the private material itself."
        ),
        # U2 restorations, priority-ordered — trim from the bottom if the
        # budget ever tightens. Each targets a selection boundary a name
        # alone cannot carry (the nudge_pos_desc precedent: one restored
        # one-liner took a 17% selection miss to 0%).
        "record_knowledge": (
            "Record information only a specific subset of those present learn "
            "— whispers, private talk, an act with limited witnesses; use "
            "record_event for openly witnessed happenings."
        ),
        "set_people_present": (
            "Replace the full who-is-here list the moment anyone enters or "
            "leaves the scene; a present character omitted from this list "
            "silently drops out of play."
        ),
        "save_character": (
            "Save a character's canonical profile when a named character "
            "first appears or their lasting traits are established — this is "
            "what future lookups recall."
        ),
        "save_object": (
            "Define a new object's permanent canonical record — name, "
            "appearance, fixed properties; interactions with it are events, "
            "not saves (use place_object)."
        ),
    }

    @property
    def _compact_step2_schemas(self) -> list:
        """Step 2 schemas with most parameter descriptions stripped.

        Preserves tool name, parameter names, types, and required fields so the
        model can still call tools correctly — just without the verbose English
        descriptions that inflate schema overhead from ~7,700 tok to ~2,000 tok.
        The critical argument contracts in _COMPACT_KEEP_PARAM_DESCRIPTIONS are
        retained (T3): without them the model has to guess the exp:/know: tag
        grammar and witness semantics, silently producing notes POV queries
        cannot find.
        """
        keep = set(self._COMPACT_KEEP_PARAM_DESCRIPTIONS)
        keep_fn = self._COMPACT_KEEP_FUNCTION_DESCRIPTIONS
        result = []
        for s in self._step2_schemas:
            fn = s["function"]
            name = fn["name"]
            # Hidden legacy tools stay dispatchable but leave the menu (U1).
            if name in self._COMPACT_HIDDEN_TOOLS:
                continue
            params = fn.get("parameters", {})
            stripped_props = {}
            for k, pdef in params.get("properties", {}).items():
                if (name, k) in keep:
                    stripped_props[k] = dict(pdef)
                else:
                    stripped_props[k] = {
                        pk: pv for pk, pv in pdef.items() if pk != "description"
                    }
            compact_fn = {
                "name": fn["name"],
                "parameters": {
                    "type": "object",
                    "properties": stripped_props,
                    "required": params.get("required", []),
                },
            }
            # Function-level description survives only for keep-listed tools
            # whose selection contract is load-bearing (default: none — §3.1).
            if name in keep_fn:
                compact_fn["description"] = keep_fn[name]
            result.append({"type": "function", "function": compact_fn})
        return result

    @property
    def _tool_schemas(self) -> list:
        """Flat view of all registered tool schemas (step1 + step2).

        Computed on demand from the step-partitioned registries so there is
        only one source of truth. Retained for legacy callers/tests.
        """
        return list(self._step1_schemas) + list(self._step2_schemas)

    @property
    def _tool_handlers(self) -> dict:
        """Flat view of all registered tool handlers (step1 + step2).

        Computed on demand from the step-partitioned registries. Retained for
        legacy callers/tests that dispatch by tool name without caring which
        step the tool belongs to.
        """
        return {**self._step1_handlers, **self._step2_handlers}

    def _register_tool(self, name, description, parameters, handler, *, step: int = 2):
        """Register a tool into the step-partitioned schema + handler dicts."""
        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }
        if step == 1:
            self._step1_schemas = [
                s for s in self._step1_schemas if s["function"]["name"] != name
            ]
            self._step1_schemas.append(schema)
            self._step1_handlers[name] = handler
        else:
            self._step2_schemas = [
                s for s in self._step2_schemas if s["function"]["name"] != name
            ]
            self._step2_schemas.append(schema)
            self._step2_handlers[name] = handler

    def _required_params(self, name: str) -> list:
        """Return the ``required`` param list for a tool schema, or []."""
        for s in self._tool_schemas:
            if s["function"]["name"] == name:
                return s["function"].get("parameters", {}).get("required", []) or []
        return []

    def _safe_dispatch(self, handlers: dict, name: str, args_json) -> str:
        """Dispatch a tool call defensively — a bad call must never crash the turn.

        Unknown tool names, non-JSON arguments, missing required keys, and any
        exception raised by the handler are all converted to an error-JSON
        string (logged at WARNING) instead of propagating. The model receives
        the error inside its tool loop and can self-correct on the next round.
        """
        if name not in handlers:
            return json.dumps({"error": f"unknown tool: {name}"})

        try:
            args = (
                json.loads(args_json) if isinstance(args_json, str) else args_json
            )
        except json.JSONDecodeError as exc:
            logger.warning("[DISPATCH] %s: malformed JSON arguments: %s", name, exc)
            return json.dumps(
                {
                    "error": f"tool {name} failed: {type(exc).__name__}: {exc}",
                    "hint": "check argument names and types against the tool schema",
                }
            )

        if not isinstance(args, dict):
            logger.warning("[DISPATCH] %s: arguments are not an object: %r", name, args)
            return json.dumps(
                {
                    "error": f"tool {name} failed: arguments must be a JSON object",
                    "hint": "check argument names and types against the tool schema",
                }
            )

        missing = [k for k in self._required_params(name) if k not in args]
        if missing:
            logger.warning(
                "[DISPATCH] %s: missing required argument(s) %s", name, missing
            )
            return json.dumps(
                {
                    "error": (
                        f"tool {name} failed: missing required argument(s): "
                        f"{', '.join(missing)}"
                    ),
                    "hint": "check argument names and types against the tool schema",
                }
            )

        try:
            return handlers[name](**args)
        except Exception as exc:
            logger.warning(
                "[DISPATCH] %s raised %s: %s",
                name,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return json.dumps(
                {
                    "error": f"tool {name} failed: {type(exc).__name__}: {exc}",
                    "hint": "check argument names and types against the tool schema",
                }
            )

    def _dispatch(self, name: str, args_json) -> str:
        """Dispatch a tool call by name across step1 + step2 handlers."""
        return self._safe_dispatch(self._tool_handlers, name, args_json)

    def _dispatch_step2(self, name: str, args_json) -> str:
        """Dispatch a Step 2 (write) tool call."""
        return self._safe_dispatch(self._step2_handlers, name, args_json)

    # ===================================================================
    # === 3. Run Orchestration ===
    # ===================================================================

    def register_all_tools(self):
        """Discover and register all @rp_tool-decorated methods into step partitions.

        step=1 tools → _step1_schemas/_step1_handlers  (WorldStateStep)
        step=2 tools → _step2_schemas/_step2_handlers  (ComplianceStep)
        `_tool_schemas`/`_tool_handlers` are computed properties that combine
        the two partitions on demand — there is no separate flat registry.
        """
        logger.info("ENTER register_all_tools")

        for attr_name in dir(self.__class__):
            fn = getattr(self.__class__, attr_name)
            if callable(fn) and hasattr(fn, "_rp_tool_meta"):
                description, parameters, step = fn._rp_tool_meta
                bound = getattr(self, attr_name)
                tool_name = attr_name.removeprefix("_tool_")
                self._register_tool(
                    tool_name, description, parameters, bound, step=step
                )

        self._cached_schema_toks = _tok(json.dumps(self._tool_schemas))
        self._cached_bare_schema_toks = _tok(json.dumps(self._bare_tool_schemas))
        self._cached_step1_schema_toks = _tok(json.dumps(self._step1_schemas))
        self._cached_compact_step2_schema_toks = _tok(
            json.dumps(self._compact_step2_schemas)
        )
        logger.info(
            "registered step1=%d step2=%d tools | "
            "schema overhead: step1=%d tok, step2_compact=%d tok (full=%d), bare=%d tok",
            len(self._step1_schemas),
            len(self._step2_schemas),
            self._cached_step1_schema_toks,
            self._cached_compact_step2_schema_toks,
            self._cached_schema_toks,
            self._cached_bare_schema_toks,
        )

    def _has_led_cue(self, text: str) -> bool:
        """True when `text` carries a passive-arrival cue (LM §3.1 fallback gate).

        A _LED_VERBS word co-occurring with a directional preposition — the
        third-person/passive analogue of _MOVE_VERBS. The gate is what keeps a
        mere mention ("meet me in the garage") from re-marking the location.
        """
        words = {w.strip('.,;:!?"\'').lower() for w in (text or "").split()}
        return bool(words & _LED_VERBS) and bool(words & _LED_PREPS)

    def _extract_led_room(self, text: str) -> str | None:
        """Match a KNOWN room named in a led-move input (LM §3.1 fallback).

        Only returns a room that already exists (a known child of the current
        location, a known sibling under the same parent, or a known root) —
        never create-new, so a mention of an unknown word cannot mint a room.
        Returns None if nothing known is named.
        """
        current = self.session.location
        children = {c: f"{current}/{c}" for c in self._child_room_slugs(current)}
        siblings = {}
        if "/" in current:
            parent = current.rsplit("/", 1)[0]
            siblings = {
                s: f"{parent}/{s}" for s in self._sibling_room_slugs(current)
            }
        roots = self._known_location_roots()
        toks = [
            re.sub(r"[^a-z0-9-]+", "-", w.strip('.,;:!?"\'').lower()).strip("-")
            for w in (text or "").split()
        ]
        toks = [t for t in toks if t]
        candidates = list(toks) + [f"{a}-{b}" for a, b in zip(toks, toks[1:])]
        for cand in candidates:
            if cand in children:
                return children[cand]
            if cand in siblings:
                return siblings[cand]
            if cand in roots:
                return cand
        return None

    def _remark_location(self, classified_input: str, world_doc: str) -> str:
        """Commit a precise session.location before step-2 recording (LM §3.1).

        Returns world_doc with any `CURRENT ROOM:` line stripped, so it never
        leaks into step-2/step-3 context. Runs only on stationary turns — mobile
        self-moves defer to navigate_to, which moves session.location itself in
        step 2 (pre-committing would collapse compute_traversal to from==to and
        erase multi-room journey narration). Never guesses: an undeterminable room
        leaves session.location untouched (the last precise room). Metadata only —
        deliberately does NOT reset attention/mood (that is navigate_to's job).
        """
        # Parse + strip the step-1 CURRENT ROOM line regardless of gating.
        proposed = None
        m = _CURRENT_ROOM_RE.search(world_doc)
        if m:
            raw = m.group(1).strip()
            world_doc = _CURRENT_ROOM_RE.sub("", world_doc, count=1).lstrip("\n")
            if raw and raw.upper() != "UNCHANGED":
                proposed = raw

        # One always-emitted structured line per turn: the live-session
        # blindness was not knowing whether step 1 said UNCHANGED, omitted the
        # line, or proposed a room the canonicalizer rejected.
        def _log(action, path=None):
            logger.info(
                "[REMARK] line=%s proposed=%r canonical=%s action=%s",
                "present" if m else "absent",
                proposed,
                path,
                action,
            )

        # Decoupling gate: defer to navigate_to on mobile self-moves. The
        # verdict is stashed for the turn — record_event's auto-move gate
        # (LM §3.7) keys off it during step 2.
        self._turn_stationary = ComplianceStep._is_stationary_turn(
            classified_input, self.mc_aliases
        )
        if not self._turn_stationary:
            _log("mobile-defer")
            return world_doc

        path = None
        source = "committed"
        # 1. Step-1 structured line (primary — scene understanding, not lexical).
        if proposed is not None:
            path = self._canonicalize_room(proposed, self.session.location)
        # 2. Gated deterministic extraction (fallback) — only on a led cue.
        if path is None and self._has_led_cue(classified_input):
            path = self._extract_led_room(classified_input)
            source = "lexical-committed" if path else source

        # 3. Fail-safe — nothing confident, or already there.
        if not path or path == self.session.location:
            if path:
                _log("same-room", path)
            elif proposed is not None:
                _log("canon-none")
            elif m:
                _log("unchanged")
            else:
                _log("no-line")
            return world_doc

        self._commit_location(path, source="remark")
        _log(source, path)
        return world_doc

    def _commit_location(self, path: str, source: str) -> None:
        """Move session.location with metadata-only bookkeeping (LM §3.1/§3.7).

        Shared by the step-1 re-mark, the record_event auto-move, and the
        post-step-2 reconciliation. Ensures the node, rebinds the context
        bundle, drops the social map, and updates NPC last-seen (which the
        plain re-mark used to strand) — but does NOT reset attention/mood:
        scene-move semantics stay owned by navigate_to.
        """
        if self._ensure_location_node(path):
            # Stub minting is worth surfacing (LM §5 audit advisory) — and the
            # header line doubles as a save_location prompt for the model.
            self._loc_warn(
                f"location node auto-created for {path} (source={source}) — "
                "call save_location to describe it"
            )
        self.session.location = path
        self.session.location_context = ContextBundle(f"{PWD_WORLD}/{path}")
        self._cache_drop("social_map")
        for slug in self.session.people_present:
            if slug != self.main_character:
                self.npc_tracker.mark_present(slug, path, self._turn_count)
        logger.info("[COMMIT-LOC] session.location → %s (source=%s)", path, source)

    def _reconcile_loc_hint(self, canonical_results: list) -> None:
        """Post-step-2 join of the mobile-turn location hint (LM §3.7).

        The hint only exists when a mobile turn's record_event named an MC
        location; navigate_to owns mobile moves, so the hint commits only if
        navigate_to never fired this turn — never racing a real traversal.
        """
        if not self._pending_loc_hint:
            return
        hint, self._pending_loc_hint = self._pending_loc_hint, None
        navigated = any(fn == "navigate_to" for fn, _ in canonical_results)
        if not navigated and hint != self.session.location:
            self._commit_location(hint, source="reconcile")

    def run_turn(
        self,
        classified_input: str,
        step2_messages: list,
        step3_messages: list,
    ) -> str:
        """Orchestrate the 3-step pipeline for one player turn.

        Step 1: WorldStateStep  — encyclopedic read-only lookup
        Step 2: ComplianceStep  — write tools, state changes
        Step 3: ProseStep       — pure narrative prose

        Returns the narrative string (Step 3 output). Advances turn counter.
        The caller is responsible for appending the narrative to both
        step2_messages and step3_messages as the assistant turn.

        Auto-initializes the pipeline (and registers tools, if needed) so
        callers do not have to remember the construction sequence.
        """
        if not self._step1_schemas and not self._step2_schemas:
            self.register_all_tools()
        if not hasattr(self, "_world_state_step"):
            self.init_pipeline()

        logger.info("[TURN %d] run_turn: START", self._turn_count + 1)
        t0 = time.perf_counter()
        # Citation capture is strictly turn-scoped (TS_CITATIONS §3.1).
        self._turn_refs = []

        # Step 1 — seeded delta when the idle-window precompute is valid.
        seed = self._consume_seed()
        world_doc = self._world_state_step.run(
            classified_input, seed_doc=seed["doc"] if seed else None
        )
        if seed and self._world_state_step.last_seed_used:
            self._seed_status = "hit"
            # Adopt speculative provenance first (capture order), the delta
            # run's own lookups after. On a delta failure the full rebuild's
            # captures already stand — seed refs are NOT adopted (the doc
            # content did not come from those lookups).
            self._turn_refs = seed["refs"] + [
                t for t in self._turn_refs if t not in seed["refs"]
            ]
        elif seed:
            self._seed_status = "miss"
        logger.info("[TURN] step1 done: world_doc=%d tok", _tok(world_doc))

        # Location re-mark (LM §3.1): commit a precise session.location before any
        # step-2 record tool runs, and strip the CURRENT ROOM line from world_doc.
        world_doc = self._remark_location(classified_input, world_doc)
        t1 = time.perf_counter()

        # Step 2. Location-drift warnings clear here, not at run_turn start:
        # producers live in step 2, so a warning must survive through the NEXT
        # turn's step-1 header (and the idle seed build) before going stale.
        self._loc_warnings = []
        self._pending_loc_hint = None
        canonical_results, accumulated_think = self._compliance_step.run(
            classified_input, world_doc, step2_messages
        )
        t2 = time.perf_counter()
        logger.info(
            "[TURN] step2 done: canonical=%d think=%d",
            len(canonical_results),
            len(accumulated_think),
        )
        # Per-turn tool census (T6/D7): makes under-used tools measurable before
        # any decision to consolidate the rel/int taxonomy. Empty turns log too.
        logger.info(
            "[TOOLS] turn=%d step2=%s",
            self._turn_count + 1,
            [fn for fn, _ in canonical_results],
        )

        # Reconciliation (LM §3.7): a mobile-turn record_event carried an MC
        # location that step 2 never consummated with navigate_to — commit it
        # now so step 3 and the idle seed stop keying off the stale room.
        self._reconcile_loc_hint(canonical_results)

        # Step 3
        narrative = self._prose_step.run(
            classified_input,
            world_doc,
            canonical_results,
            accumulated_think,
            step3_messages,
        )
        t3 = time.perf_counter()
        logger.info("[TURN] step3 done: narrative=%d tok", _tok(narrative))
        # Per-turn wall-clock accounting (TOOL_UNIFY U0): one parseable line per
        # turn so latency work is measured, not assumed. `it` counts LLM calls
        # consumed by each step's loop (step1 includes the re-mark in its span).
        # seed/bg/wait (idle-window precompute): seed = this turn's precompute
        # outcome; bg = LLM seconds the idle worker moved off the critical
        # path; wait = seconds the player actually stalled at the join.
        bg_stats = self._bg_stats if isinstance(self._bg_stats, dict) else {}
        self._bg_stats = None
        logger.info(
            "[TIMING] turn=%d step1=%.1fs/%dit step2=%.1fs/%dit step3=%.1fs "
            "total=%.1fs seed=%s bg=%.1fs wait=%.1fs",
            self._turn_count + 1,
            t1 - t0,
            self._world_state_step.last_rounds,
            t2 - t1,
            self._compliance_step.last_rounds,
            t3 - t2,
            t3 - t0,
            self._seed_status,
            bg_stats.get("spec_s", 0.0) + bg_stats.get("refresh_s", 0.0),
            bg_stats.get("wait_s", 0.0),
        )

        # Cast-drift detection (T1): compare who the turn's text names against
        # who the session thinks is present. Detection only — never mutates cast.
        self._scan_cast_drift(classified_input, narrative)

        # Advance turn counter; schedule entropy refresh
        self._turn_count += 1
        if (
            not self._system_refresh_pending
            and self._turn_count % SYSTEM_REFRESH_INTERVAL == 0
        ):
            self._system_refresh_pending = True
            logger.info(
                "[ENTROPY] turn %d: scheduling system message refresh", self._turn_count
            )

        return narrative

    def _seed_state_snapshot(self) -> tuple:
        """The invariants a seed is valid against: location, cast, scene."""
        return (
            self.session.location,
            frozenset(self.session.people_present),
            self.session.current_scene,
        )

    def speculate_step1(self) -> None:
        """Idle-window speculative step-1: precompute the next turn's seed.

        Called by the play loop's background worker AFTER all post-turn
        bookkeeping, while the player is at the input() prompt (engine state
        cannot drift there — meta-commands are read-only). Runs a full step-1
        against the post-turn world with a placeholder input and stores the
        doc for _consume_seed. Never raises; on any failure the seed is
        simply absent and the next turn takes the normal full path.
        """
        if not self._step1_schemas and not self._step2_schemas:
            self.register_all_tools()
        if not hasattr(self, "_world_state_step"):
            self.init_pipeline()
        if not self.seed_enabled:
            return
        self._seed = None

        state = self._seed_state_snapshot()
        # Citation isolation (TS_CITATIONS §3.1): speculative lookups must not
        # pollute the just-completed turn's refs. Captured refs travel with
        # the seed and are adopted by run_turn only on a seed hit.
        saved_refs = self._turn_refs
        self._turn_refs = []
        t0 = time.perf_counter()
        try:
            doc = self._world_state_step.run(_SPECULATIVE_INPUT)
        except Exception as exc:
            # call_llm can raise beyond RequestException (JSON decode etc.);
            # a background failure must never surface.
            logger.warning("[SEED] speculative step-1 failed: %s", exc)
            return
        finally:
            refs = self._turn_refs
            self._turn_refs = saved_refs

        if not self._world_state_step.last_ok or not doc.strip():
            # Exhaustion/exception produced a fallback doc — seeding with it
            # would be worse than a fresh rebuild next turn.
            logger.debug("[SEED] speculative doc unusable; discarded")
            return

        # The speculative CURRENT ROOM line was judged against the placeholder
        # input; strip it so it cannot leak into the seeded prompt. The delta
        # call emits its own, which _remark_location processes normally.
        doc = _CURRENT_ROOM_RE.sub("", doc, count=1).lstrip("\n")

        self._seed = {
            "doc": doc,
            "refs": refs,
            "state": state,
            "turn": self._turn_count,
            "rounds": self._world_state_step.last_rounds,
            "elapsed": time.perf_counter() - t0,
        }
        logger.info(
            "[SEED] speculative doc ready (%d tok, %d rounds, %.1fs)",
            _tok(doc),
            self._seed["rounds"],
            self._seed["elapsed"],
        )

    def _consume_seed(self) -> dict | None:
        """Pop and validate the speculative seed (single-use, hit or miss).

        Valid iff produced after the previous completed turn (turn counter
        matches) and the state snapshot still holds — guards first-turn/resume,
        a prior run_turn that raised mid-turn after partially mutating state,
        and programmer error. Player-input novelty is NOT an invalidation
        concern: the delta call's own tool budget handles new lookups.
        """
        seed, self._seed = self._seed, None
        if not self.seed_enabled:
            self._seed_status = "off"
            return None
        if not seed:
            self._seed_status = "miss"
            logger.debug("[SEED] no seed available (first turn/resume/failure)")
            return None
        if seed["turn"] != self._turn_count:
            self._seed_status = "miss"
            logger.debug(
                "[SEED] stale seed rejected: turn %d != %d",
                seed["turn"],
                self._turn_count,
            )
            return None
        if seed["state"] != self._seed_state_snapshot():
            self._seed_status = "miss"
            logger.debug("[SEED] stale seed rejected: state snapshot changed")
            return None
        return seed

    def _scan_cast_drift(self, classified_input: str, narrative: str) -> list:
        """Detect NPCs named in the turn's text but absent from people_present.

        The highest-stakes silent failure in the system: if the narrative
        introduces or dismisses a character without set_people_present firing,
        every downstream deterministic guarantee (baseline profiles, POV
        contexts, exp: witness tagging) keys off the wrong cast. This only
        *detects* the drift — it never mutates the cast, because a wrong guess
        would corrupt canon exactly the way a miss does (T1 rationale).

        Matching is case-insensitive and word-boundary anchored on each known
        NPC's display_name and slug; the main character is skipped. Results
        replace self._cast_warnings, so the warning clears automatically once
        the named character is added to the cast or stops being mentioned.
        """
        text = f"{classified_input}\n{narrative}".lower()
        present = {p.lower() for p in self.session.people_present}
        mentioned_absent: list = []
        for rec in self.npc_tracker.all():
            if rec.slug == self.main_character:
                continue
            if rec.slug.lower() in present:
                continue
            for name in {rec.display_name, rec.slug}:
                if not name:
                    continue
                if re.search(r"\b" + re.escape(name.lower()) + r"\b", text):
                    mentioned_absent.append(rec.slug)
                    break
        self._cast_warnings = sorted(set(mentioned_absent))
        if self._cast_warnings:
            logger.warning(
                "[CAST] mentioned-but-absent: %s", ", ".join(self._cast_warnings)
            )
        return self._cast_warnings

    def _cast_warning_line(self) -> str:
        """One-line cast-drift warning for headers/reports, or '' if none."""
        if not self._cast_warnings:
            return ""
        return (
            "CAST WARNING: narrative mentions "
            f"{', '.join(self._cast_warnings)} who "
            f"{'is' if len(self._cast_warnings) == 1 else 'are'} "
            "not in people_present — call set_people_present if the cast changed."
        )

    def _loc_warn(self, message: str) -> None:
        """Record a location-drift warning (cast-drift pattern, LOCDRIFT tag)."""
        self._loc_warnings.append(message)
        logger.warning("[LOCDRIFT] %s", message)

    def _loc_warning_line(self) -> str:
        """One-line location-drift warning for headers/reports, or '' if none."""
        if not self._loc_warnings:
            return ""
        return "LOCATION WARNING: " + "; ".join(self._loc_warnings)

    def init_pipeline(self) -> None:
        """Instantiate the 3-step pipeline objects. Call after register_all_tools()."""
        self._world_state_step = WorldStateStep(self)
        self._compliance_step = ComplianceStep(self)
        self._prose_step = ProseStep(self)
        logger.info(
            "[PIPELINE] WorldStateStep + ComplianceStep + ProseStep initialized"
        )

    # ===================================================================
    # === 4. Static Utilities ===
    # ===================================================================

    # ------------------------------------------------------------------
    # Utility: think-tag stripping
    # ------------------------------------------------------------------

    @staticmethod
    def strip_think_tags(text):
        """
        Separate <think>...</think> content from the rest of the LLM response.
        Also strips <tool_call> blocks that the LLM emits as prose fallback.

        Handles unclosed tags (truncated responses) for both tag types.

        Returns:
            (think_content, clean_content) -- both strings.
        """
        think_match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
        if not think_match:
            think_match = re.search(r"<think>(.*)", text, flags=re.DOTALL)
        if not think_match:
            # Orphaned closing tag: opening was missing/truncated; treat everything before </think> as think content
            think_match = re.search(r"(.*?)</think>", text, flags=re.DOTALL)
        think_content = (
            think_match.group(1).strip().replace("\n", " ") if think_match else ""
        )
        # Strip closed then unclosed think tags
        clean_content = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        clean_content = re.sub(r"<think>.*", "", clean_content, flags=re.DOTALL)
        # Strip orphaned closing tag and everything before it
        clean_content = re.sub(
            r".*?</think>", "", clean_content, count=1, flags=re.DOTALL
        )
        # Strip closed then unclosed tool_call blocks
        clean_content = re.sub(
            r"<tool_call>.*?</tool_call>", "", clean_content, flags=re.DOTALL
        )
        clean_content = re.sub(r"<tool_call>.*", "", clean_content, flags=re.DOTALL)
        return think_content, clean_content.strip()

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

    # ===================================================================
    # === 5. Cache ===
    # ===================================================================

    # ------------------------------------------------------------------
    # Query result cache (session-scoped, invalidated on write)
    # ------------------------------------------------------------------

    def _cache_get(self, key: str) -> str | None:
        hit = self.session._query_cache.get(key)
        if hit is not None:
            logger.debug("[CACHE] HIT  %s (%d tok)", key, _tok(hit))
            # Replay captured provenance (TS_CITATIONS §3.4): a cache hit must
            # capture the same refs the original lookup did, or hits silently
            # produce citation-free turns. No-op for keys that never captured.
            for t in self._ref_cache.get(key, []):
                if t not in self._turn_refs:
                    self._turn_refs.append(t)
        return hit

    def _cache_put(self, key: str, value: str, refs: list | None = None) -> None:
        cache = self.session._query_cache
        # FIFO eviction cap (R6): the cache is invalidated on writes but never
        # globally evicted, so bound it to keep long sessions from leaking RAM.
        if key not in cache and len(cache) >= _QUERY_CACHE_MAX:
            oldest = next(iter(cache))
            del cache[oldest]
            self._ref_cache.pop(oldest, None)
            logger.debug("[CACHE] EVICT(FIFO) %s (cap %d)", oldest, _QUERY_CACHE_MAX)
        cache[key] = value
        # Lockstep with the rendered string — stale refs must never outlive it.
        if refs:
            self._ref_cache[key] = list(refs)
        else:
            self._ref_cache.pop(key, None)
        logger.debug("[CACHE] SET  %s (%d tok)", key, _tok(value))

    def _cache_drop(self, *keys: str) -> None:
        for k in keys:
            if k in self.session._query_cache:
                del self.session._query_cache[k]
                self._ref_cache.pop(k, None)
                logger.debug("[CACHE] EVICT %s", k)

    def _cite(self, notes, cap: int = _REF_CAP_PER_LOOKUP) -> list:
        """Capture retrieval provenance from one targeted lookup (C2/C5).

        Records the `now` of the newest `cap` notes the lookup actually
        surfaced into the per-turn ref set, newest-first. Returns the captured
        list so cached lookups can store it beside the rendered string (§3.4).
        Ambient tools (get_people_present, examine_location, prepare_context)
        and the deterministic baseline blocks never call this — citing
        everything in context would make every ref meaningless.
        """
        newest = sorted(notes, key=lambda n: n.now, reverse=True)[:cap]
        captured = [n.now for n in newest]
        for t in captured:
            if t not in self._turn_refs:
                self._turn_refs.append(t)
        return captured

    def _stamp_refs(self, tag_str: str) -> str:
        """Append this turn's captured refs to a derived-note tag (C4/C5).

        The C5 guard: any ref with ts >= now-at-stamp-time is dropped — a
        note can never cite itself, a same-turn sibling, or the future. Live
        turns always pass (LLM latency >> 1 s); tests seed past `now=`
        values. At most _REF_CAP_PER_NOTE ref words per note, capture order,
        deduped against words already present. Canonical and structural
        writers never call this (C4) — identity nodes carry no provenance.
        """
        if not self._turn_refs:
            return tag_str
        existing = set((tag_str or "").split())
        cutoff = int(time.time())
        stamped = []
        for t in self._turn_refs:
            if t >= cutoff:
                continue
            word = f"{TAG_REF}{t}"
            if word in existing:
                continue
            existing.add(word)
            stamped.append(word)
            if len(stamped) >= _REF_CAP_PER_NOTE:
                break
        if not stamped:
            return tag_str
        return ((tag_str or "").strip() + " " + " ".join(stamped)).strip()

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
    def resolve_destination(
        from_path: str, destination: str, known_roots=None
    ) -> tuple[str, str]:
        """Resolve a raw destination string into a full hierarchical path.

        Args:
            known_roots: optional set/collection of depth-1 root slugs that have a
                saved node (LOCATION_MARKING §3.3). Passed by _tool_navigate_to so a
                bare destination naming a real sibling root resolves "direct" instead
                of being nested. When None (pure unit-test callers) the safe default
                nests a bare single-component destination as a child (the G4 fix) —
                never a detached top-level location that breaks location_ancestors.

        Returns:
            (resolved_path, nav_type) where nav_type is one of:
            "hierarchical" — shared ancestor found, compute_traversal applies
            "inferred"     — bare name resolved as sibling under current root, or
                             nested as a child of the current location (G4)
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

        # Bare single-component destination: resolve relative to the current
        # location rather than as a detached top-level place (G4).
        if len(dest_parts) == 1:
            # Inside a multi-level location → sibling under the top-level root.
            # e.g. current="manor/foyer/corridor", destination="cellar" → "manor/cellar"
            if len(from_parts) > 1:
                return f"{from_parts[0]}/{destination}", "inferred"
            # From a single-component root: only a KNOWN saved sibling root is a
            # direct top-level move; anything else nests as a child of the current
            # location so its notes stay inside the current ancestry (G4 fix).
            if known_roots is not None and destination in known_roots:
                return destination, "direct"
            return f"{from_path}/{destination}", "inferred"

        # Genuinely separate multi-segment locations — direct transport.
        return destination, "direct"

    # ===================================================================
    # === 5b. Location & object canonicalization substrate ===
    #   (OBJECT_PERMANENCE Phase 0 — all read-only or strictly-safer writes)
    # ===================================================================

    def _known_location_roots(self) -> set:
        """Depth-1 room slugs directly under PWD_WORLD that have a saved node.

        Passed to resolve_destination (LM §3.3) so a bare destination naming a
        real sibling root resolves "direct" instead of nesting as a child.
        TREE prefix has no boundary check, so guard with the '/'-boundary rule.
        """
        prefix = PWD_WORLD + "/"
        roots = set()
        with NoteContext(Note.NOTEFILE, (SearchType.TREE, PWD_WORLD)) as nc:
            for note in nc:
                pwd = note.pwd
                if not pwd.startswith(prefix):
                    continue  # excludes PWD_WORLD itself and prefix-sharing siblings
                roots.add(pwd[len(prefix):].split("/")[0])
        roots.discard("")
        return roots

    def _child_room_slugs(self, parent: str) -> list:
        """One-level child room slugs of `parent` (LM §3.4), boundary-filtered (§3.6).

        SearchType.TREE is a raw pwd.startswith with no path-boundary check, so
        `/story/location/garden` would match `/story/location/garden-east`. Post-
        filter with `pwd.startswith(prefix + "/")` (strictly deeper) and take the
        first component below the prefix. Deduped, order-preserving.
        """
        if not parent:
            return []
        prefix = f"{PWD_WORLD}/{parent}"
        slugs = []
        seen = set()
        with NoteContext(Note.NOTEFILE, (SearchType.TREE, prefix)) as nc:
            for note in nc:
                pwd = note.pwd
                if not pwd.startswith(prefix + "/"):
                    continue  # excludes parent's own node AND prefix-sharing siblings
                child = pwd[len(prefix) + 1:].split("/")[0]
                if child and child not in seen:
                    seen.add(child)
                    slugs.append(child)
        return slugs

    def _sibling_room_slugs(self, current: str) -> list:
        """One-level sibling room slugs of `current` — children of its parent.

        A single-component room has no parent under PWD_WORLD and therefore no
        siblings here (top-level neighbors are _known_location_roots territory).
        Deduped, order-preserving, excludes current's own leaf.
        """
        if not current or "/" not in current:
            return []
        parent, leaf = current.rsplit("/", 1)
        return [s for s in self._child_room_slugs(parent) if s != leaf]

    def _canonicalize_room(self, proposed: str, current: str) -> str | None:
        """Resolve a proposed room string to a canonical slug (LM §3.2).

        Precedence: exact node → known child of current → known sibling of
        current → known root → resolve_destination (only if it lands on an
        existing node) → create-new (nested under current, never a far-away
        guess). Returns None on empty or undeterminable input, feeding
        _remark_location's fail-safe.
        """
        if not proposed:
            return None
        slug = proposed.strip().lower()
        slug = re.sub(r"[^a-z0-9/-]+", "-", slug).strip("-/")
        if not slug:
            return None
        leaf = slug.split("/")[-1]

        # 1. Exact existing full-path node.
        with NoteContext(
            Note.NOTEFILE, (SearchType.DIRECTORY, f"{PWD_WORLD}/{slug}")
        ) as nc:
            if len(nc):
                return slug

        # 2. Known child of the current location (match on last component).
        if current:
            for child in self._child_room_slugs(current):
                if child == leaf:
                    return f"{current}/{child}"

        # 2.5. Known sibling of the current location (match on last component) —
        # the quarters→gallery class: a move between rooms sharing a parent,
        # which neither the child walk nor the root check can ever resolve.
        if current and "/" in current:
            parent = current.rsplit("/", 1)[0]
            for sib in self._sibling_room_slugs(current):
                if sib == leaf:
                    return f"{parent}/{sib}"

        # 3. Known top-level root (match on first component).
        roots = self._known_location_roots()
        if slug.split("/")[0] in roots:
            return slug

        # 4. resolve_destination fallback — trust it only if the node exists.
        if current:
            resolved, _ = self.resolve_destination(current, slug, known_roots=roots)
            with NoteContext(
                Note.NOTEFILE, (SearchType.DIRECTORY, f"{PWD_WORLD}/{resolved}")
            ) as nc:
                if len(nc):
                    return resolved

        # 5. Create-new — nest under current (never a far-away guess).
        return f"{current}/{leaf}" if current else leaf

    def _ensure_location_node(self, path: str) -> bool:
        """Idempotently ensure a canonical /story/location/{path} node exists (LM §3.5).

        Returns True if a node was created, False if one already existed.
        Existence check uses DIRECTORY (exact) — TREE would false-positive whenever
        a child already has a node. A later real save_location supersedes the stub
        via newest-first render.
        """
        if not path:
            return False
        with NoteContext(
            Note.NOTEFILE, (SearchType.DIRECTORY, f"{PWD_WORLD}/{path}")
        ) as nc:
            if len(nc):
                return False
        leaf = path.rstrip("/").split("/")[-1]
        Note.append(
            Note.NOTEFILE,
            Note.jot(
                message=(
                    f"{leaf.capitalize()}. "
                    "(Auto-created location node; awaiting description.)"
                ),
                tag="",
                context=f"location node (auto): {path}",
                pwd=f"{PWD_WORLD}/{path}",
            ),
        )
        return True

    def _ensure_object_node(self, slug: str) -> bool:
        """Idempotently ensure a canonical /story/object/{slug} node exists (OT §3.4).

        Auto-genesis stub ("awaiting description") mirroring _ensure_location_node;
        superseded by a later real save_object (newest-first render). Doubles as
        the lazy-migration hook: a legacy sighting-only object gains its canonical
        node on the first place_object touch. Returns True if a node was created.
        """
        if not slug:
            return False
        with NoteContext(
            Note.NOTEFILE, (SearchType.DIRECTORY, f"{PWD_OBJECTS}/{slug}")
        ) as nc:
            if len(nc):
                return False
        Note.append(
            Note.NOTEFILE,
            Note.jot(
                message=f"{slug} (awaiting description).",
                tag=f"{TAG_OBJ}{slug}",
                context=f"object node (auto): {slug}",
                pwd=f"{PWD_OBJECTS}/{slug}",
            ),
        )
        return True

    def _parse_residence(self, pwd: str):
        """Map a sighting note's pwd to a residence dict (OBJECT_TOOLING §3.5).

            /story/character/{h}/inventory → {"held_by": h}
            /story/location/{room}         → {"room": room}
            /story/events/{room}           → {"room": room}  (record_event fallback)

        Returns None if pwd is not a recognizable residence (e.g. a canonical
        /story/object note, already excluded by the registry pass).
        """
        if not pwd:
            return None
        char_prefix = PWD_CHARS + "/"
        inv_suffix = "/inventory"
        if pwd.startswith(char_prefix) and pwd.endswith(inv_suffix):
            holder = pwd[len(char_prefix):-len(inv_suffix)]
            if holder and "/" not in holder:
                return {"held_by": holder}
        if pwd.startswith(PWD_WORLD + "/"):
            return {"room": pwd[len(PWD_WORLD) + 1:]}
        if pwd.startswith(PWD_EVENTS + "/"):
            return {"room": pwd[len(PWD_EVENTS) + 1:]}
        return None

    def _object_registry(self) -> dict:
        """One pass over the notefile collecting per-object residence evidence.

        Returns {slug: {"residence": <dict|None>, "canonical": <bool>}}.

        Residence = the newest NON-canonical obj:-tagged note, with equal Note.now
        resolved by later-in-file order (>= while scanning append order, OT §3.1) —
        NOT a bare sorted(reverse=True). Notes under PWD_OBJECTS are excluded from
        residence but flip the canonical flag. Cached under "obj_registry"; dropped
        by the object writers and record_event (event tags are sightings). /story as
        a TREE prefix is unambiguous here, so no boundary filter is needed.
        """
        cached = self._cache_get("obj_registry")
        if cached is not None:
            return json.loads(cached)

        # slug -> running best (newest non-canonical) + canonical-exists flag
        acc: dict = {}
        with NoteContext(Note.NOTEFILE, (SearchType.TREE, "/story")) as nc:
            for note in nc:
                for word in note.tag.split():
                    if not word.startswith(TAG_OBJ):
                        continue
                    slug = word[len(TAG_OBJ):]
                    if not slug:
                        continue
                    entry = acc.setdefault(
                        slug, {"best_now": None, "residence": None, "canonical": False}
                    )
                    if note.pwd == f"{PWD_OBJECTS}/{slug}":
                        entry["canonical"] = True
                    if note.pwd.startswith(PWD_OBJECTS + "/"):
                        continue  # canonical namespace is never residence evidence
                    # newest non-canonical wins; tie → later-in-file (>=)
                    if entry["best_now"] is None or note.now >= entry["best_now"]:
                        res = self._parse_residence(note.pwd)
                        if res is not None:
                            entry["best_now"] = note.now
                            entry["residence"] = res
        result = {
            slug: {"residence": e["residence"], "canonical": e["canonical"]}
            for slug, e in acc.items()
        }
        self._cache_put("obj_registry", json.dumps(result))
        return result

    def _canonicalize_object(self, name: str) -> str:
        """Resolve an object name to a canonical FLAT slug (OBJECT_TOOLING §3.2).

        Slugs are flat ([a-z0-9-]; '/' stripped — hierarchy belongs to residence,
        not identity). Precedence: exact canonical node → exact registry slug →
        word-overlap (token-set containment) against registry slugs → create-new.
        Create-new is the NORMAL path, not a failure — a deliberate asymmetry with
        _canonicalize_room's never-guess room resolution (do not "fix" it).
        """
        slug = (name or "").strip().lower().removeprefix(TAG_OBJ)
        slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
        if not slug:
            return slug  # empty in → empty out; never raises

        # 1. Exact canonical node.
        with NoteContext(
            Note.NOTEFILE, (SearchType.DIRECTORY, f"{PWD_OBJECTS}/{slug}")
        ) as nc:
            if len(nc):
                return slug

        registry = self._object_registry()
        # 2. Exact slug in registry (covers legacy objects with no canonical node).
        if slug in registry:
            return slug

        # 3. Word-overlap: token-set containment either direction ("the iron key"
        #    → iron-key). First stable (file-order) match wins.
        tokens = set(slug.split("-"))
        for known in registry:
            known_tokens = set(known.split("-"))
            if tokens <= known_tokens or known_tokens <= tokens:
                return known

        # 4. Create-new — the normal path.
        return slug

    @staticmethod
    def _newest_by_now(notes):
        """Return the newest note (max Note.now; equal-now → later-in-file).

        `notes` must be in file/append order (NoteContext yields that order), so
        the >= scan reproduces the OT §3.1 tie-break without an unstable sort.
        """
        best = None
        for n in notes:
            if best is None or n.now >= best.now:
                best = n
        return best

    @staticmethod
    def _parse_refs(tag_str: str) -> list:
        """Extract cited epoch ints from a tag string (TS_CITATIONS C1).

        Only well-formed `ref:{digits}` words parse; garbage (`ref:`,
        `ref:abc`) is skipped silently — a malformed word is inert, never an
        error. Order of appearance is preserved.
        """
        refs = []
        for word in (tag_str or "").split():
            if not word.startswith(TAG_REF):
                continue
            body = word[len(TAG_REF):]
            if body.isdigit():
                refs.append(int(body))
        return refs

    @staticmethod
    def _format_refs(refs) -> str:
        """Render ref ints as tag words — the C1 verbatim form, no padding."""
        return " ".join(f"{TAG_REF}{int(t)}" for t in refs)

    def _bundle_from_notes(self, notes) -> ContextBundle:
        """Wrap an explicit note list in a transient ContextBundle (LM §3.6).

        ContextBundle term-matching cannot express "exactly these notes", so build
        an empty bundle and assign .notes directly — used to route an object's
        timeline through render_context's recency/size caps.
        """
        bundle = ContextBundle([])
        bundle.notes = list(notes)
        return bundle

    def _object_sightings(self, slug: str) -> list:
        """Non-canonical obj:{slug} sighting notes, in file/append order."""
        with NoteContext(Note.NOTEFILE, (SearchType.TAG, f"{TAG_OBJ}{slug}")) as nc:
            return [n for n in nc if not n.pwd.startswith(PWD_OBJECTS + "/")]

    def _backlinks(self, ts: int) -> list:
        """Notes citing entry `ts` — one TAG search for the literal ref word.

        Named seam for tests and future tooling ("what cites this entry");
        zero new semantics (TS_CITATIONS §3.3).
        """
        with NoteContext(
            Note.NOTEFILE, (SearchType.TAG, f"{TAG_REF}{int(ts)}")
        ) as nc:
            return list(nc)

    def _deref_citations(self, bundle: ContextBundle) -> ContextBundle:
        """Depth-1 citation dereference (TS_CITATIONS C6), mutating `bundle`.

        Collects ref ints from the bundle's visible notes, adds at most
        _REF_DEREF_CAP timestamps, and regenerates ONCE — never per-term
        __iadd__ (each call rescans the whole notefile). Refs carried by the
        dereferenced notes are NOT followed — cycle-safe by construction, no
        visited-set. A dangling ref resolves to an empty TIMESTAMP match — a
        silent no-op (C8). Wired into gather_pov_context only.
        """
        ref_ts = []
        seen = set()
        for n in bundle:
            for t in self._parse_refs(n.tag):
                if t not in seen:
                    seen.add(t)
                    ref_ts.append(t)
        if not ref_ts:
            return bundle
        bundle.ts.update(ref_ts[:_REF_DEREF_CAP])
        bundle._regen_notes()
        return bundle

    def _object_canonical_description(self, slug: str) -> str:
        """Canonical description: newest note at the canonical pwd, with a legacy
        fallback to the newest sighting message (OBJECT_TOOLING §3.5 / §4)."""
        with NoteContext(
            Note.NOTEFILE, (SearchType.DIRECTORY, f"{PWD_OBJECTS}/{slug}")
        ) as nc:
            canon = self._newest_by_now(list(nc))
        if canon is not None:
            return canon.message.strip()
        sighting = self._newest_by_now(self._object_sightings(slug))
        return sighting.message.strip() if sighting is not None else ""

    def _objects_here(self, location: str, present):
        """Structured registry residents of `location` (OBJECT_TOOLING §3.6).

        Returns (in_room_slugs, {holder: [slugs]}). Only the NEWEST residence
        counts — an object moved out of `location` is excluded even though its
        stale sighting note still sits in the room's ContextBundle blob; this is
        the authoritative correction layer (invariant I7).
        """
        registry = self._object_registry()
        in_room = []
        held: dict = {}
        present_set = set(present or [])
        for slug in sorted(registry):
            res = registry[slug]["residence"]
            if not res:
                continue
            if res.get("room") == location:
                in_room.append(slug)
            elif res.get("held_by") in present_set:
                held.setdefault(res["held_by"], []).append(slug)
        return in_room, held

    def _objects_here_lines(self, location: str, present) -> list:
        """[KNOWN OBJECTS] body lines (in-room slugs, then held-by-present).

        Framed as "last seen" rather than "is here": residence is derived from
        the newest sighting, so it is genuinely a last-known location, not a
        current-turn state assertion. The framing matters for tool selection —
        a "currently here" reading suppressed place_object on strong models in
        the Tier-3 sweep (the qwen3-235b objects_here collapse); "last seen"
        reads as continuity reference, not "already handled".
        """
        in_room, held = self._objects_here(location, present)
        lines = []
        if in_room:
            lines.append(f"last seen in this room: {', '.join(in_room)}")
        for holder in sorted(held):
            lines.append(f"last seen with {holder}: {', '.join(sorted(held[holder]))}")
        return lines

    # ===================================================================
    # === 6. Context Building ===
    # ===================================================================

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
        terms.append(f"{PWD_INTERIOR}/{char_name}")
        if self.session.current_scene:
            terms.append(f"{TAG_SCENE}{self.session.current_scene}")
        logger.debug(
            "[CTX] gather_pov_context(%s): querying %d terms", char_name, len(terms)
        )
        # The one deref site (TS_CITATIONS §3.3): a note in this POV citing a
        # prior entry pulls that entry verbatim into the character's context —
        # provenance instead of a decaying paraphrase.
        return self._deref_citations(ContextBundle(terms))

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
        # Location-scoped event recall — this room AND its sub-rooms (LM §3.6);
        # complements the ancestor (walk-up) recall in `shared`.
        result["location_events"] = self.gather_location_events(
            self.session.location, focus_hint=focus_hint
        )
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

    def gather_location_events(self, room: str, focus_hint: str = "") -> str:
        """Location-scoped event recall for `room` AND its sub-rooms (LM §3.6).

        ContextBundle routes directory terms through DIRECTORY (exact), so TREE
        (prefix) recall is only reachable via a direct NoteContext call. TREE is a
        raw pwd.startswith with no path-boundary check, so post-filter with the
        '/'-boundary rule (else /story/events/garden pulls garden-east). Contract:
        DIRECTORY-exact = this room's own notes; TREE (boundary-filtered) = this
        room + all sub-rooms; ancestor recall walks *up*, this walks *down*.
        """
        if not room:
            return ""
        prefix = f"{PWD_EVENTS}/{room}"
        with NoteContext(Note.NOTEFILE, (SearchType.TREE, prefix)) as nc:
            notes = [
                n for n in nc if n.pwd == prefix or n.pwd.startswith(prefix + "/")
            ]
        return self.render_context(
            self._bundle_from_notes(notes), focus_hint=focus_hint
        )

    # ===================================================================
    # === 7. Scene State Queries ===
    # ===================================================================

    def _gather_attn_for_scene(self) -> str:
        """Format the current in-memory attention state for pre-narrative injection.

        Returns an empty string when no attention has been set this turn.
        Not persisted — attention is lost between turns and rebuilt from events.
        """
        if not self.session.attention:
            return ""

        lines = [
            f"  {char} → {focus}"
            for char, focus in sorted(self.session.attention.items())
        ]
        logger.debug("[ATTN] _gather_attn_for_scene: %d entries", len(lines))
        return (
            "SCENE ATTENTION — who is looking at what right now.\n"
            "Let this shape physical staging, eyeline, and what each character "
            "can plausibly notice. Characters not looking at each other may miss "
            "reactions; shared gaze creates shared witness:\n\n" + "\n".join(lines)
        )

    def _gather_mood_for_scene(self) -> str:
        """Format the current in-memory mood state for pre-narrative injection.

        Returns an empty string when no moods have been set this turn.
        Not persisted — cleared on cast and location changes.
        """
        if not self.session.mood:
            return ""
        lines = [
            f"  {char} → {state}" for char, state in sorted(self.session.mood.items())
        ]
        logger.debug("[MOOD] _gather_mood_for_scene: %d entries", len(lines))
        return (
            "SCENE MOOD — current emotional states (transient, not persisted).\n"
            "Let these color dialogue delivery, physical tells, and what each "
            "character notices or ignores:\n\n" + "\n".join(lines)
        )

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
            cache_key = f"yomi:{char_name}"
            context_str = self._cache_get(cache_key)
            if context_str is None:
                bundle = ContextBundle(f"{PWD_YOMI}/{char_name}")
                context_str = self.render_context(bundle, focus_hint=char_name)
                if context_str.strip():
                    self._cache_put(cache_key, context_str)
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
            parts = []
            for n in notes:
                part = n.context.strip() + "\n\n" + n.message.strip()
                # Provenance marker (TS_CITATIONS §3.3): tags are otherwise
                # invisible at render time, so without this line a citation
                # buys nothing when the prose model reads the note.
                refs = self._parse_refs(n.tag)
                if refs:
                    part += "\n[refs: " + " ".join(str(t) for t in refs) + "]"
                parts.append(part)
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
            "If a fact carries a bracketed [refs: ...] marker, keep the marker "
            "attached to that fact.\n"
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
            response = call_llm(
                [{"role": "user", "content": prompt}],
                max_tokens=MAX_TOKENS_CONDENSE,
            )
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
        response = call_llm(messages, max_tokens=MAX_TOKENS_CONDENSE)

        content = response.get("content", "")
        _think, content = self.strip_think_tags(content)

        parsed = self.extract_json_from_response(content)

        return {
            "noteworthy_objects": parsed.get("noteworthy_objects", []),
            "established_props": parsed.get("established_props", []),
        }

    # ===================================================================
    # === 8. Step 1 Tools: World Lookup ===
    # ===================================================================

    # ===================================================================
    # === 9. Step 2 Tools: Write / State Change ===
    # ===================================================================

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
                        "knowledge. Do not add location or character-name tags — that "
                        "context is tracked automatically by directory. "
                        "Add obj:slug for any significant object handled."
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
        loc_clean = self.session.location
        explicit = (location or "").strip()
        if explicit:
            loc_clean = (
                self._canonicalize_room(explicit, self.session.location)
                or self.session.location
            )
        if explicit and loc_clean != self.session.location:
            # LM §3.7: an explicit location param is a structured source on par
            # with step-1's CURRENT ROOM line — and on the live 60-turn session
            # it was the ONLY movement signal the model ever emitted.
            mc_present = any(
                self.mc_aliases & set(t[len(TAG_EXP):].lower().split("+"))
                for t in tag_str.split()
                if t.startswith(TAG_EXP)
            )
            if mc_present and self._turn_stationary:
                # Stationary turn: navigate_to is nudge-suppressed by design,
                # so nothing else will move the session — commit immediately.
                self._commit_location(loc_clean, source="record_event")
            elif mc_present:
                # Mobile turn: navigate_to owns the move; reconcile after
                # step 2 iff it never fires (weak-model bucket).
                self._pending_loc_hint = loc_clean
                self._loc_warn(
                    f"record_event filed at {loc_clean} but session is "
                    f"{self.session.location} (mc-tagged, mobile turn — "
                    "deferred to navigate_to)"
                )
            else:
                # Off-screen event: filing elsewhere is correct, session stays.
                self._loc_warn(
                    f"record_event filed at {loc_clean} but session is "
                    f"{self.session.location} (no MC tag — session unmoved)"
                )
        pwd = f"{PWD_EVENTS}/{loc_clean}"
        context = f"canonical event at {loc_clean}"

        if self.session.current_scene:
            tag_str = f"{tag_str} {TAG_SCENE}{self.session.current_scene}"
        tag_str = self._stamp_refs(tag_str)

        note = Note.jot(
            message=description,
            tag=tag_str,
            context=context,
            pwd=pwd,
        )
        Note.append(Note.NOTEFILE, note)

        # Event tags can be object sightings (the weak-model fallback channel,
        # OT §2/§3.5), so the object registry must drop or those sightings stay
        # invisible until an unrelated invalidation (OP §5 cache-coherence note).
        if any(t.startswith(TAG_OBJ) for t in tag_str.split()):
            self._cache_drop("obj_registry")

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
        step=1,
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
        step=1,
    )
    def _tool_examine_location(self):
        """Return the objects and people in the current location."""
        logger.info("ENTER _tool_examine_location")

        location = self.session.location
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
            # Deterministic registry residents (OBJECT_TOOLING §3.6): wins over the
            # LLM-extracted noteworthy_objects on conflict.
            "objects_here": self._objects_here_lines(
                self.session.location, self.session.people_present
            ),
            "noteworthy_objects": objects.get("noteworthy_objects", []),
            "established_props": objects.get("established_props", []),
            "followup_instruction": FOLLOWUP_QUERY,
        }
        result = json.dumps(state)
        logger.info("examine_location result: %s", result)
        return result

    @rp_tool(
        description=(
            "Move the scene to a new location. Call this when the player's own input "
            "moves them there — an [MC action] whose subject is the player (I/MC) "
            "with a move verb: go, walk, follow, head, step, enter, climb, cross. "
            "The directive prefix decides: [MC action] where the player moves = "
            "navigate; [MC speaks aloud] or [MC — likely spoken aloud] = dialogue, "
            "never navigate. An NPC's invitation, beckoning, or escort becomes "
            "navigation only once the player's next [MC action] takes it up. Paths "
            "are hierarchical (e.g. 'manor/foyer/closet'); a bare name is a sibling "
            "under the current root."
        ),
        parameters={
            "type": "object",
            "properties": {
                "location_name": {
                    "type": "string",
                    "description": (
                        "Hierarchical destination path (e.g. 'manor/foyer/closet'); '/' marks "
                        "parent/child, a bare name sits under the current root. Call this ONLY "
                        "when the player's own [MC action] moves them — subject is the player "
                        "(I/MC) with a move verb (go/walk/follow/head/step/enter). [MC speaks "
                        "aloud] is dialogue, never navigation, even if movement is mentioned; an "
                        "NPC's invite, beckon, or escort becomes navigation only when the "
                        "player's next [MC action] takes it up."
                    ),
                },
            },
            "required": ["location_name"],
        },
    )
    def _tool_navigate_to(self, location_name: str):
        """Compute traversal path and move to a new location."""
        logger.info("ENTER _tool_navigate_to: location_name=%r", location_name)

        raw_dest = location_name
        from_loc = self.session.location

        resolved_dest, nav_type = self.resolve_destination(
            from_loc, raw_dest, known_roots=self._known_location_roots()
        )
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

        # Update session state; attention and mood reset on location change
        self.session.location = to_loc
        self.session.location_context = ContextBundle(f"{PWD_WORLD}/{to_loc}")
        self.session.attention = {}
        self.session.mood = {}
        self._cache_drop("social_map")
        self._ensure_location_node(to_loc)  # LM §3.5: guarantee a node for the arrival room

        # NPC tracker: update last-seen location for all present NPCs
        for slug in self.session.people_present:
            if slug != self.main_character:
                self.npc_tracker.mark_present(slug, to_loc, self._turn_count)

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
        self.session.attention = {}
        self.session.mood = {}
        self._cache_drop("social_map")

        # NPC tracker: register any new characters and mark them as present
        for slug in people:
            if slug != self.main_character:
                if not self.npc_tracker.is_registered(slug):
                    self.npc_tracker.register(
                        slug,
                        slug,
                        location=self.session.location,
                        turn=self._turn_count,
                    )
                self.npc_tracker.mark_present(
                    slug, self.session.location, self._turn_count
                )

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
        self._cache_drop(f"char:{name}")

        # NPC tracker: register if new, mark as saved and central
        if not self.npc_tracker.is_registered(name):
            self.npc_tracker.register(
                name, name, location=self.session.location, turn=self._turn_count
            )
        self.npc_tracker.mark_saved(name)
        self.npc_tracker.mark_central(name)

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

        tag_str = tags.strip() if tags else ""

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
            "using, examining); those are story events — use place_object instead."
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
                    "description": "Which location this object is in (e.g. 'cellar'). Omit to use current session location.",
                },
                "tags": {
                    "type": "string",
                    "description": "Additional space-separated tags (optional)",
                },
            },
            "required": ["name", "description"],
        },
    )
    def _tool_save_object(
        self, name: str, description: str, location: str = "", tags: str = ""
    ):
        """Persist an object's canonical identity + a genesis sighting (OT §3.3).

        Dual write: the canonical description at PWD_OBJECTS/{slug} (identity,
        never shadowed) AND a genesis sighting carrying the same text at the room
        residence (byte-for-byte the old write, so room rendering is unchanged for
        models that never call get_object). location defaults to session.location,
        inheriting _remark_location's precision for free (LM). Return string keeps
        the old shape so existing tests stay green.
        """
        logger.info("ENTER _tool_save_object: name=%r location=%r", name, location)

        slug = self._canonicalize_object(name)
        room = location or self.session.location
        extra = f" {tags.strip()}" if tags else ""
        tag_str = f"{TAG_OBJ}{slug}{extra}"

        # Canonical node (identity) — its own pwd namespace, never shadowed.
        Note.append(
            Note.NOTEFILE,
            Note.jot(
                message=description,
                tag=tag_str,
                context=f"object canon: {slug}",
                pwd=f"{PWD_OBJECTS}/{slug}",
            ),
        )
        # Genesis sighting (residence) — the room, byte-for-byte the old write.
        Note.append(
            Note.NOTEFILE,
            Note.jot(
                message=description,
                tag=tag_str,
                context=f"object sighting: {slug} at {room}",
                pwd=f"{PWD_WORLD}/{room}",
            ),
        )
        self._cache_drop("obj_registry", f"{TAG_OBJ}{slug}")
        logger.info("object saved: %s (canon + genesis sighting at %s)", slug, room)
        return f"Object saved: {slug} (at {room})"

    @rp_tool(
        description=(
            "Log an object changing hands or rooms — picked up, handed over, "
            "dropped, stowed, left behind; also to note a lasting change to its "
            "condition."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The object being placed or updated (e.g. 'iron-key').",
                },
                "holder": {
                    "type": "string",
                    "description": (
                        "Character who now holds the object — possession is a "
                        "residence. Provide holder OR room, never both."
                    ),
                },
                "room": {
                    "type": "string",
                    "description": (
                        "Room the object is now in. Provide holder OR room, never "
                        "both; omit both to restate the object where it already is."
                    ),
                },
                "state": {
                    "type": "string",
                    "description": "Optional lasting change to its condition (e.g. 'now cracked').",
                },
            },
            "required": ["name"],
        },
    )
    def _tool_place_object(
        self, name: str, holder: str = "", room: str = "", state: str = ""
    ):
        """Record an object's residence change or state change (OT §3.4).

        One tool for pick-up / hand-over / drop / stow / move / state-change.
        holder XOR room; neither restates at the current residence. Never raises —
        both-given returns an error string. Auto-creates the canonical node
        (lazy-migration hook). Newest sighting wins on read.
        """
        logger.info(
            "ENTER _tool_place_object: name=%r holder=%r room=%r", name, holder, room
        )
        slug = self._canonicalize_object(name)
        if not slug:
            return json.dumps({"error": "place_object: empty object name"})
        if holder and room:
            return json.dumps(
                {"error": "place_object: provide holder OR room, not both"}
            )

        self._ensure_object_node(slug)  # auto-genesis; lazy migration hook

        if holder:
            h = re.sub(
                r"[^a-z0-9-]+", "-", holder.strip().lower().removeprefix(TAG_CHAR)
            ).strip("-")
            if h and not self.npc_tracker.is_registered(h):
                logger.warning("[PLACE] holder %r not tracked — recording anyway", h)
            residence = f"held by {h}"
            pwd = f"{PWD_CHARS}/{h}/inventory"
        elif room:
            r = (
                self._canonicalize_room(room, self.session.location)
                or self.session.location
            )
            residence, pwd = r, f"{PWD_WORLD}/{r}"
        else:
            # Neither → restate at the current residence; no prior sighting → the
            # session location (a room, never a guessed holder).
            entry = self._object_registry().get(slug)
            res = entry["residence"] if entry else None
            if res and res.get("held_by"):
                residence = f"held by {res['held_by']}"
                pwd = f"{PWD_CHARS}/{res['held_by']}/inventory"
            elif res and res.get("room"):
                residence, pwd = res["room"], f"{PWD_WORLD}/{res['room']}"
            else:
                residence = self.session.location
                pwd = f"{PWD_WORLD}/{self.session.location}"

        Note.append(
            Note.NOTEFILE,
            Note.jot(
                message=state or f"{slug} is here.",
                tag=f"{TAG_OBJ}{slug}",
                context=f"object sighting: {slug} at {residence}",
                pwd=pwd,
            ),
        )
        self._cache_drop("obj_registry", f"{TAG_OBJ}{slug}")
        logger.info("object placed: %s → %s", slug, residence)
        return f"Object placed: {slug} (at {residence})"

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
        step=1,
    )
    def _tool_get_character(self, name: str):
        """Return saved character notes as context for the LLM."""
        logger.info("ENTER _tool_get_character: name=%r", name)

        cache_key = f"char:{name}"
        context_str = self._cache_get(cache_key)
        if context_str is None:
            ctx = self.gather_character_knowledge(name)
            captured = self._cite(ctx)
            context_str = self.render_context(ctx, focus_hint=name)
            self._cache_put(cache_key, context_str, refs=captured)
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
            "Retrieve a saved object's canonical description, its current residence "
            "(the room it is in, or the character holding it), and its sighting "
            "history newest-first. Call this to recall where an established object "
            "is and what state it is in before writing it into the narrative."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Object name or slug to look up "
                        "(e.g. 'iron-key', 'the iron key')"
                    ),
                },
            },
            "required": ["name"],
        },
        step=1,
    )
    def _tool_get_object(self, name: str):
        """Return an object's residence + description + timeline as step-1 context.

        Newest-wins residence read deterministically from the object registry;
        never invents a residence on a miss (invariant I5) — returns the known
        roster with a find_character-style "use an existing name" followup.
        """
        logger.info("ENTER _tool_get_object: name=%r", name)

        slug = self._canonicalize_object(name)
        cache_key = f"{TAG_OBJ}{slug}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        registry = self._object_registry()
        entry = registry.get(slug)
        if not slug or entry is None:
            result = json.dumps(
                {
                    "object": f"[no object on file matching: {name!r}]",
                    "known_objects": sorted(registry),
                    "followup_instruction": (
                        "No saved object matches. Use an existing name from "
                        "known_objects, or introduce it with save_object. Do NOT "
                        "invent where it is."
                    ),
                }
            )
            self._cache_put(cache_key, result)
            return result

        sightings = self._object_sightings(slug)
        timeline = self.render_context(
            self._bundle_from_notes(sightings), focus_hint=slug
        )
        # Cite the canonical node + the 2 newest sightings (TS §4 Phase 2) —
        # together they are exactly the per-lookup cap of 3.
        with NoteContext(
            Note.NOTEFILE, (SearchType.DIRECTORY, f"{PWD_OBJECTS}/{slug}")
        ) as nc:
            canon_note = self._newest_by_now(list(nc))
        newest_sightings = sorted(sightings, key=lambda n: n.now, reverse=True)[:2]
        captured = self._cite(
            ([canon_note] if canon_note is not None else []) + newest_sightings
        )
        result = json.dumps(
            {
                "object": slug,
                "canonical_description": self._object_canonical_description(slug),
                "residence": entry["residence"],
                "timeline": timeline or "[no sightings recorded]",
                "followup_instruction": (
                    "Ground the object in its newest residence and state; a later "
                    "sighting supersedes an earlier one."
                ),
            }
        )
        self._cache_put(cache_key, result, refs=captured)
        logger.info("get_object result: %s → %s", slug, entry["residence"])
        return result

    @rp_tool(
        description=(
            "Scan every character in /story/character to find an established character "
            "who fits a role you are about to introduce. "
            "Call this BEFORE naming any new NPC — pass a description of the role "
            "(e.g. 'head waitstaff', 'personal assistant', 'housekeeper', 'driver'). "
            "Returns the full roster of known character names and complete profiles "
            "for any whose tags or backstory contain words from your role description. "
            "If a match is found, use that character instead of inventing a new name."
        ),
        parameters={
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": (
                        "Description of the role or character type you want to introduce "
                        "(e.g. 'head waitstaff', 'butler', 'eldest daughter', 'family assistant')"
                    ),
                },
            },
            "required": ["role"],
        },
        step=1,
    )
    def _tool_find_character(self, role: str):
        """Scan /story/character for established characters matching a role description."""
        logger.info("ENTER _tool_find_character: role=%r", role)

        role_words = {w.lower() for w in re.split(r"\W+", role) if len(w) > 2}

        roster = []  # all known character slugs
        matches = []  # full profiles for role-matching characters
        match_notes = []  # matched profile notes — cited; the roster is not

        seen_pwds = set()
        seen_names = set()
        with NoteContext(Note.NOTEFILE, (SearchType.TREE, PWD_CHARS)) as nc:
            for note in nc:
                if note.pwd in seen_pwds:
                    continue
                seen_pwds.add(note.pwd)

                # First component under PWD_CHARS — an object-possession subpath
                # (/story/character/evie/inventory) resolves to "evie", never a
                # phantom "inventory" roster entry (OT §3.1).
                if not note.pwd.startswith(PWD_CHARS + "/"):
                    continue
                char_name = note.pwd[len(PWD_CHARS) + 1:].split("/")[0]
                if not char_name or char_name in seen_names:
                    continue
                seen_names.add(char_name)
                roster.append(char_name)

                combined = (note.tag + " " + note.message).lower()
                if role_words and all(w in combined for w in role_words):
                    matches.append(
                        {
                            "name": char_name,
                            "tags": note.tag,
                            "profile": note.message.strip(),
                        }
                    )
                    match_notes.append(note)

        # NPC tracker: register any roster characters not yet tracked
        for char_name in roster:
            if not self.npc_tracker.is_registered(char_name):
                self.npc_tracker.register(
                    char_name,
                    char_name,
                    named=True,
                    location=self.session.location,
                    turn=self._turn_count,
                )
            self.npc_tracker.mark_saved(char_name)

        if match_notes:
            self._cite(match_notes)

        logger.info(
            "find_character: role=%r roster=%d matches=%d",
            role,
            len(roster),
            len(matches),
        )

        followup = (
            "A matching established character was found — use them instead of "
            "inventing a new name. Load their full profile with get_character if needed."
            if matches
            else "No established character matches this role. "
            "You may introduce an unnamed background figure, but do NOT give them a "
            "name unless they are already on the roster."
        )

        return json.dumps(
            {
                "roster": roster,
                "matches": matches,
                "followup_instruction": followup,
            }
        )

    @rp_tool(
        description=(
            "Search world notes by tag or keyword to retrieve established lore. "
            "Use an exp:/know: tag, an obj: slug, or a plain keyword to find relevant notes. "
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
        step=1,
    )
    def _tool_search_world(self, query: str):
        """Search world notes and return matching context for the LLM."""
        logger.info("ENTER _tool_search_world: query=%r", query)

        # Intentionally unions all world/location notes with the query so the LLM
        # receives both established world lore and the specific match in one bundle.
        ctx = self.gather_context([PWD_WORLD, query])
        self._cite(ctx)
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
            tag=self._stamp_refs(witness_tags),
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
                tag=self._stamp_refs(public_tags),
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
            "Start a new named narrative scene — a cohesive dramatic unit that "
            "anchors later retrieval. Call this: (1) on the first player turn to open "
            "the story, or after navigate_to arrives somewhere with no active scene; "
            "(2) when the player's input clearly enters a new dramatic context (a "
            "meal, an investigation, a confrontation); (3) when one beat has ended "
            "and the next clearly begins. Like navigate_to, a scene opens on the "
            "player's own [MC action], not on an NPC's invitation or escort. Call at "
            "most ONCE per player turn, and not again within the same tool-call "
            "sequence. The scene slug then attaches to later record_event and "
            "record_knowledge calls automatically; old scenes stay queryable via "
            "get_scene."
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
        self._cache_drop("social_map")
        self._system_refresh_pending = True

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
        step=1,
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
        self._cite(bundle)
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
            tag=self._stamp_refs(tag_str),
            context=f"conscience: {character} — {trait}",
            pwd=f"{PWD_CONSCIENCE}/{character}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"conscience:{character}")

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
            "Establish what each character in the current scene is focused on right "
            "now — their gaze, object of attention, or mental preoccupation — based "
            "on the most recent events. "
            "Call this as the FIRST tool when processing any player action that "
            "involves social interaction, observation, or physical activity. "
            "Setting attention early is critical: divergent gazes create asymmetric "
            "knowledge. If mc is watching aurora while evie's eyes dart between them, "
            "mc may catch evie's reaction; if mc is staring at the floor, they miss it. "
            "Attention informs what each character can plausibly observe before "
            "record_knowledge or prepare_context is called. "
            "Attention resets automatically on navigation and cast changes. "
            "Examples of what to capture: a character watching the door after a knock, "
            "the MC following a taxi out of sight, two guests exchanging a sidelong "
            "glance, someone studying their own hands in embarrassment."
        ),
        parameters={
            "type": "object",
            "properties": {
                "attention": {
                    "type": "array",
                    "description": "Current focus state for every character in the scene",
                    "items": {
                        "type": "object",
                        "properties": {
                            "character": {
                                "type": "string",
                                "description": "Character slug (e.g. 'mc', 'evie', 'aurora')",
                            },
                            "focus": {
                                "type": "string",
                                "description": (
                                    "What or whom they are currently looking at or thinking "
                                    "about (e.g. 'evie', 'the door', 'their own feet', "
                                    "'the window', 'the taxi pulling away')"
                                ),
                            },
                        },
                        "required": ["character", "focus"],
                    },
                },
            },
            "required": ["attention"],
        },
    )
    def _tool_update_attn(self, attention: list) -> str:
        """Store the scene's current attention state in session memory (not persisted)."""
        logger.info("ENTER _tool_update_attn: %d entries", len(attention))

        self.session.attention = {
            item["character"]: item["focus"] for item in attention
        }

        summary = ", ".join(
            f"{c}→{f}" for c, f in sorted(self.session.attention.items())
        )
        logger.info("attention updated: %s", summary)

        return json.dumps(
            {
                "attention": self.session.attention,
                "followup_instruction": (
                    "Attention state captured. Use this map when deciding what each "
                    "character can observe about the others: a character whose focus is "
                    "on person X has clear line-of-sight to X's expressions and reactions; "
                    "a character looking elsewhere may miss subtle signals from others. "
                    "Let divergent gazes produce asymmetric observations before calling "
                    "record_knowledge or prepare_context."
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
            tag=self._stamp_refs(f"{TAG_YOMI}{character}"),
            context=f"yomi: {self.main_character} → {character}",
            pwd=f"{PWD_YOMI}/{character}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"yomi:{character}")

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
        step=1,
    )
    def _tool_get_yomi(self, character: str) -> str:
        """Return stored yomi insights for a character."""
        logger.info("ENTER _tool_get_yomi: character=%r", character)

        cache_key = f"yomi:{character}"
        context_str = self._cache_get(cache_key)
        if context_str is None:
            bundle = ContextBundle(f"{PWD_YOMI}/{character}")
            captured = self._cite(bundle)
            context_str = self.render_context(bundle, focus_hint=character)
            if context_str.strip():
                self._cache_put(cache_key, context_str, refs=captured)
        logger.info(
            "get_yomi: character=%s rendered=%d tok", character, _tok(context_str)
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
        step=1,
    )
    def _tool_get_conscience(self, character: str = "mc") -> str:
        """Return all conscience constraints recorded for a character."""
        logger.info("ENTER _tool_get_conscience: character=%r", character)

        cache_key = f"conscience:{character}"
        context_str = self._cache_get(cache_key)
        if context_str is None:
            bundle = self.gather_context([f"{PWD_CONSCIENCE}/{character}"])
            captured = self._cite(bundle)
            context_str = self.render_context(bundle, focus_hint=character)
            if context_str.strip():
                self._cache_put(cache_key, context_str, refs=captured)
        logger.info(
            "get_conscience: character=%s rendered=%d tok", character, _tok(context_str)
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
        step=1,
    )
    def _tool_prepare_context(self, focus_hint: str = "") -> str:
        """Assemble and condense per-character POV context for the current scene."""
        logger.info("ENTER _tool_prepare_context: focus_hint=%r", focus_hint)

        context_map = self.build_scene_context_map(focus_hint=focus_hint)

        shared = context_map.pop("shared", "")
        # location_events is a reserved (non-character) key — fold it into the
        # shared environmental snapshot, never into character_contexts (LM §3.6).
        location_events = context_map.pop("location_events", "")
        if location_events:
            shared = (
                f"{shared}\n\nEVENTS IN THIS ROOM & SUB-ROOMS:\n{location_events}"
                if shared
                else f"EVENTS IN THIS ROOM & SUB-ROOMS:\n{location_events}"
            )
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

    # ===================================================================
    # === 10. Step 2 Tools: Relationships ===
    # ===================================================================

    # ------------------------------------------------------------------
    # Interpersonal tools — relationships, interior life, social query
    # ------------------------------------------------------------------

    @staticmethod
    def _rel_key(char_a: str, char_b: str) -> str:
        """Return a canonical alphabetically-sorted key for a character pair."""
        return "-".join(sorted([char_a, char_b]))

    # ── Relationship-pair record tools ────────────────────────────────

    @rp_tool(
        description=(
            "Record the named relationship type between two characters — what they "
            "are to each other and how that bond came to be. "
            "Use this for durable relationships independent of the current scene: "
            "old friends, estranged siblings, rivals, former lovers, mentor and student. "
            "Call this when a relationship is established or revealed for the first time."
        ),
        parameters={
            "type": "object",
            "properties": {
                "char_a": {"type": "string", "description": "First character slug"},
                "char_b": {"type": "string", "description": "Second character slug"},
                "bond_type": {
                    "type": "string",
                    "description": "Short label (e.g. 'rivals', 'old-friends', 'former-lovers', 'mentor-student', 'estranged-siblings')",
                },
                "description": {
                    "type": "string",
                    "description": "How the bond formed, what defines it, and how it shapes both characters today",
                },
            },
            "required": ["char_a", "char_b", "bond_type", "description"],
        },
    )
    def _tool_record_bond(
        self, char_a: str, char_b: str, bond_type: str, description: str
    ) -> str:
        logger.info("ENTER _tool_record_bond: %r ↔ %r [%s]", char_a, char_b, bond_type)
        pair = self._rel_key(char_a, char_b)
        note = Note.jot(
            message=f"Bond type: {bond_type}\n\n{description}",
            tag=self._stamp_refs(f"{TAG_REL}bond {TAG_CHAR}{char_a} {TAG_CHAR}{char_b}"),
            context=f"bond: {char_a} ↔ {char_b} ({bond_type})",
            pwd=f"{PWD_REL}/{pair}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"rel:{pair}", "social_map")
        logger.info("bond recorded: %s ↔ %s [%s]", char_a, char_b, bond_type)
        return json.dumps(
            {
                "char_a": char_a,
                "char_b": char_b,
                "bond_type": bond_type,
                "status": "recorded",
            }
        )

    @rp_tool(
        description=(
            "Record a specific shared past event between two characters that still "
            "echoes in how they relate to each other — a betrayal, a rescue, a shared "
            "loss, a night neither discusses. "
            "History informs callbacks, half-sentences, and the weight of unsaid things. "
            "Call once per significant backstory event when it is established or revealed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "char_a": {"type": "string", "description": "First character slug"},
                "char_b": {"type": "string", "description": "Second character slug"},
                "event": {
                    "type": "string",
                    "description": "What happened — the concrete incident",
                },
                "significance": {
                    "type": "string",
                    "description": "How this event shaped both characters and what it still means to each of them",
                },
            },
            "required": ["char_a", "char_b", "event", "significance"],
        },
    )
    def _tool_record_history(
        self, char_a: str, char_b: str, event: str, significance: str
    ) -> str:
        logger.info("ENTER _tool_record_history: %r ↔ %r", char_a, char_b)
        pair = self._rel_key(char_a, char_b)
        note = Note.jot(
            message=f"{event}\n\nSignificance: {significance}",
            tag=self._stamp_refs(f"{TAG_REL}history {TAG_CHAR}{char_a} {TAG_CHAR}{char_b}"),
            context=f"shared history: {char_a} ↔ {char_b}",
            pwd=f"{PWD_REL}/{pair}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"rel:{pair}", "social_map")
        logger.info("history recorded: %s ↔ %s", char_a, char_b)
        return json.dumps({"char_a": char_a, "char_b": char_b, "status": "recorded"})

    @rp_tool(
        description=(
            "Record the recurring push-pull behavioral pattern between two characters — "
            "the habitual way their interaction unfolds regardless of content: who leads, "
            "who defers, who deflects with humor, who always needs the last word. "
            "This is not WHAT they do but HOW they do it, replayed across encounters. "
            "Use this when a pattern becomes clear after multiple interactions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "char_a": {"type": "string", "description": "First character slug"},
                "char_b": {"type": "string", "description": "Second character slug"},
                "pattern": {
                    "type": "string",
                    "description": "Short kebab-case label (e.g. 'aurora-leads-mc-defers', 'mutual-deflection', 'competitive-performance')",
                },
                "description": {
                    "type": "string",
                    "description": "How the pattern manifests in dialogue and behavior — specific tells, recurring moves",
                },
            },
            "required": ["char_a", "char_b", "pattern", "description"],
        },
    )
    def _tool_record_dynamic(
        self, char_a: str, char_b: str, pattern: str, description: str
    ) -> str:
        logger.info("ENTER _tool_record_dynamic: %r ↔ %r [%s]", char_a, char_b, pattern)
        pair = self._rel_key(char_a, char_b)
        note = Note.jot(
            message=f"Pattern: {pattern}\n\n{description}",
            tag=self._stamp_refs(f"{TAG_REL}dynamic {TAG_CHAR}{char_a} {TAG_CHAR}{char_b}"),
            context=f"dynamic: {char_a} ↔ {char_b} ({pattern})",
            pwd=f"{PWD_REL}/{pair}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"rel:{pair}", "social_map")
        logger.info("dynamic recorded: %s ↔ %s [%s]", char_a, char_b, pattern)
        return json.dumps(
            {
                "char_a": char_a,
                "char_b": char_b,
                "pattern": pattern,
                "status": "recorded",
            }
        )

    @rp_tool(
        description=(
            "Record who holds situational or social power over another character and why — "
            "the asymmetry that makes one character careful around the other. "
            "Power can come from status, a secret held, leverage, charm, institutional "
            "authority, or financial dependency. "
            "Use this when the power differential becomes narratively active."
        ),
        parameters={
            "type": "object",
            "properties": {
                "holder": {
                    "type": "string",
                    "description": "Character who holds power",
                },
                "subject": {
                    "type": "string",
                    "description": "Character subject to that power",
                },
                "basis": {
                    "type": "string",
                    "description": "What the power is based on (e.g. 'status', 'blackmail', 'financial-dependency', 'emotional-hold', 'institutional-authority')",
                },
                "description": {
                    "type": "string",
                    "description": "How this manifests — what the subject does differently when the holder is present",
                },
            },
            "required": ["holder", "subject", "basis", "description"],
        },
    )
    def _tool_record_power_dynamic(
        self, holder: str, subject: str, basis: str, description: str
    ) -> str:
        logger.info(
            "ENTER _tool_record_power_dynamic: %r over %r [%s]", holder, subject, basis
        )
        pair = self._rel_key(holder, subject)
        note = Note.jot(
            message=f"Holder: {holder} | Subject: {subject} | Basis: {basis}\n\n{description}",
            tag=self._stamp_refs(f"{TAG_REL}power {TAG_CHAR}{holder} {TAG_CHAR}{subject}"),
            context=f"power dynamic: {holder} over {subject} ({basis})",
            pwd=f"{PWD_REL}/{pair}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"rel:{pair}", "social_map")
        logger.info("power dynamic recorded: %s over %s [%s]", holder, subject, basis)
        return json.dumps(
            {"holder": holder, "subject": subject, "basis": basis, "status": "recorded"}
        )

    @rp_tool(
        description=(
            "Record an emotional injury one character inflicted on another — "
            "deliberately or inadvertently — that still shapes how the wounded "
            "character responds. "
            "Wounds explain inexplicable coldness, overreaction, or the flinch at "
            "a particular word. Unlike conscience (a self-contained trait), wounds "
            "are relational: they came from a specific source. "
            "Use known_to_inflicter to capture whether the wound-giver is aware."
        ),
        parameters={
            "type": "object",
            "properties": {
                "inflicter": {"type": "string", "description": "Who caused the wound"},
                "wounded": {"type": "string", "description": "Who carries it"},
                "description": {
                    "type": "string",
                    "description": "The act, its impact, and how the wound manifests now",
                },
                "known_to_inflicter": {
                    "type": "boolean",
                    "description": "Whether the inflicter knows they caused this (default false)",
                },
            },
            "required": ["inflicter", "wounded", "description"],
        },
    )
    def _tool_record_wound(
        self,
        inflicter: str,
        wounded: str,
        description: str,
        known_to_inflicter: bool = False,
    ) -> str:
        logger.info("ENTER _tool_record_wound: %r → %r", inflicter, wounded)
        pair = self._rel_key(inflicter, wounded)
        awareness = "known to inflicter" if known_to_inflicter else "inflicter unaware"
        note = Note.jot(
            message=f"Inflicter: {inflicter} | Wounded: {wounded} | {awareness}\n\n{description}",
            tag=self._stamp_refs(f"{TAG_REL}wound {TAG_CHAR}{inflicter} {TAG_CHAR}{wounded}"),
            context=f"wound: {inflicter} → {wounded}",
            pwd=f"{PWD_REL}/{pair}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"rel:{pair}", "social_map")
        logger.info(
            "wound recorded: %s → %s (known=%s)", inflicter, wounded, known_to_inflicter
        )
        return json.dumps(
            {
                "inflicter": inflicter,
                "wounded": wounded,
                "known_to_inflicter": known_to_inflicter,
                "status": "recorded",
            }
        )

    @rp_tool(
        description=(
            "Record a commitment one character made to another — explicit or implied — "
            "that now exists as a narrative obligation. "
            "Promises create forward tension: they can be kept, broken, or weaponized. "
            "Use stakes to describe what is at risk if the promise is not honored. "
            "Call this when a character commits to something in a way that will matter."
        ),
        parameters={
            "type": "object",
            "properties": {
                "promiser": {
                    "type": "string",
                    "description": "Who made the commitment",
                },
                "recipient": {"type": "string", "description": "To whom it was made"},
                "commitment": {
                    "type": "string",
                    "description": "The precise obligation — what was promised",
                },
                "stakes": {
                    "type": "string",
                    "description": "What is at risk if the promise is broken (optional)",
                },
            },
            "required": ["promiser", "recipient", "commitment"],
        },
    )
    def _tool_record_promise(
        self, promiser: str, recipient: str, commitment: str, stakes: str = ""
    ) -> str:
        logger.info("ENTER _tool_record_promise: %r → %r", promiser, recipient)
        pair = self._rel_key(promiser, recipient)
        body = (
            f"Promiser: {promiser} | Recipient: {recipient}\n\nCommitment: {commitment}"
        )
        if stakes:
            body += f"\n\nStakes: {stakes}"
        note = Note.jot(
            message=body,
            tag=self._stamp_refs(f"{TAG_REL}promise {TAG_CHAR}{promiser} {TAG_CHAR}{recipient}"),
            context=f"promise: {promiser} → {recipient}",
            pwd=f"{PWD_REL}/{pair}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"rel:{pair}", "social_map")
        logger.info("promise recorded: %s → %s", promiser, recipient)
        return json.dumps(
            {"promiser": promiser, "recipient": recipient, "status": "recorded"}
        )

    @rp_tool(
        description=(
            "Record that one character owes another something — a favor, a secret "
            "kept, a life saved, a financial obligation. "
            "Debts create behavioral asymmetry: the debtor is more careful, more "
            "accommodating, more alert to the creditor's moods. "
            "The creditor may choose not to collect yet, which is its own kind of power."
        ),
        parameters={
            "type": "object",
            "properties": {
                "debtor": {"type": "string", "description": "Who owes"},
                "creditor": {"type": "string", "description": "Who is owed"},
                "what_is_owed": {
                    "type": "string",
                    "description": "The nature of the debt — what must be repaid or returned",
                },
                "origin": {
                    "type": "string",
                    "description": "How the debt arose — the event or circumstance that created it",
                },
            },
            "required": ["debtor", "creditor", "what_is_owed", "origin"],
        },
    )
    def _tool_record_debt(
        self, debtor: str, creditor: str, what_is_owed: str, origin: str
    ) -> str:
        logger.info("ENTER _tool_record_debt: %r owes %r", debtor, creditor)
        pair = self._rel_key(debtor, creditor)
        note = Note.jot(
            message=f"Debtor: {debtor} | Creditor: {creditor}\n\nOwed: {what_is_owed}\n\nOrigin: {origin}",
            tag=self._stamp_refs(f"{TAG_REL}debt {TAG_CHAR}{debtor} {TAG_CHAR}{creditor}"),
            context=f"debt: {debtor} owes {creditor}",
            pwd=f"{PWD_REL}/{pair}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"rel:{pair}", "social_map")
        logger.info("debt recorded: %s owes %s", debtor, creditor)
        return json.dumps(
            {"debtor": debtor, "creditor": creditor, "status": "recorded"}
        )

    @rp_tool(
        description=(
            "Record a specific false statement one character made to another, "
            "alongside the actual truth the narrator holds. "
            "Lies create fault lines: the liar must maintain them, and the deceived "
            "character operates on false premises. When the topic surfaces again, the "
            "liar must choose to double down, deflect, or confess."
        ),
        parameters={
            "type": "object",
            "properties": {
                "liar": {"type": "string", "description": "Who told the lie"},
                "target": {"type": "string", "description": "Who was lied to"},
                "statement": {
                    "type": "string",
                    "description": "What was said — the false statement as delivered",
                },
                "truth": {
                    "type": "string",
                    "description": "The actual truth being concealed",
                },
            },
            "required": ["liar", "target", "statement", "truth"],
        },
    )
    def _tool_record_lie(
        self, liar: str, target: str, statement: str, truth: str
    ) -> str:
        logger.info("ENTER _tool_record_lie: %r → %r", liar, target)
        pair = self._rel_key(liar, target)
        note = Note.jot(
            message=f"Liar: {liar} | Target: {target}\n\nStatement: {statement}\n\nTruth: {truth}",
            tag=self._stamp_refs(f"{TAG_REL}lie {TAG_CHAR}{liar} {TAG_CHAR}{target}"),
            context=f"lie: {liar} → {target}",
            pwd=f"{PWD_REL}/{pair}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"rel:{pair}", "social_map")
        logger.info("lie recorded: %s → %s", liar, target)
        return json.dumps({"liar": liar, "target": target, "status": "recorded"})

    @rp_tool(
        description=(
            "Record information or circumstances one character holds over another — "
            "the kind of thing that makes the subject careful, conciliatory, or quietly "
            "afraid. Leverage is not always deployed; sometimes power lies in its "
            "existence. Can be blackmail material, knowledge of vulnerability, or any "
            "asymmetric information that could cause harm."
        ),
        parameters={
            "type": "object",
            "properties": {
                "holder": {"type": "string", "description": "Who holds the leverage"},
                "subject": {
                    "type": "string",
                    "description": "Who it can be used against",
                },
                "description": {
                    "type": "string",
                    "description": "What the leverage is, and what harm its deployment would cause",
                },
            },
            "required": ["holder", "subject", "description"],
        },
    )
    def _tool_record_leverage(self, holder: str, subject: str, description: str) -> str:
        logger.info("ENTER _tool_record_leverage: %r over %r", holder, subject)
        pair = self._rel_key(holder, subject)
        note = Note.jot(
            message=f"Holder: {holder} | Subject: {subject}\n\n{description}",
            tag=self._stamp_refs(f"{TAG_REL}leverage {TAG_CHAR}{holder} {TAG_CHAR}{subject}"),
            context=f"leverage: {holder} over {subject}",
            pwd=f"{PWD_REL}/{pair}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"rel:{pair}", "social_map")
        logger.info("leverage recorded: %s over %s", holder, subject)
        return json.dumps({"holder": holder, "subject": subject, "status": "recorded"})

    @rp_tool(
        description=(
            "Record one character's assessment of another after an interaction — "
            "what impression formed, how it revised a prior view, and what caused it. "
            "Impressions diverge between characters and create dramatic irony: "
            "mc's warmth may land on someone already cold toward them. "
            "Call this after a meaningful interaction when a character's view has "
            "shifted or solidified."
        ),
        parameters={
            "type": "object",
            "properties": {
                "observer": {
                    "type": "string",
                    "description": "Who formed the impression",
                },
                "subject": {"type": "string", "description": "Who is being assessed"},
                "impression": {
                    "type": "string",
                    "description": "What the observer now thinks or feels — as specific as possible",
                },
                "trigger": {
                    "type": "string",
                    "description": "What caused this impression to form or shift (optional)",
                },
            },
            "required": ["observer", "subject", "impression"],
        },
    )
    def _tool_record_impression(
        self, observer: str, subject: str, impression: str, trigger: str = ""
    ) -> str:
        logger.info("ENTER _tool_record_impression: %r of %r", observer, subject)
        pair = self._rel_key(observer, subject)
        body = f"Observer: {observer} | Subject: {subject}\n\nImpression: {impression}"
        if trigger:
            body += f"\n\nTriggered by: {trigger}"
        note = Note.jot(
            message=body,
            tag=self._stamp_refs(f"{TAG_REL}impression {TAG_CHAR}{observer} {TAG_CHAR}{subject}"),
            context=f"impression: {observer} of {subject}",
            pwd=f"{PWD_REL}/{pair}",
        )
        Note.append(Note.NOTEFILE, note)
        self._cache_drop(f"rel:{pair}", "social_map")
        logger.info("impression recorded: %s of %s", observer, subject)
        return json.dumps(
            {"observer": observer, "subject": subject, "status": "recorded"}
        )

    # ── Character interior record tools ───────────────────────────────

    @rp_tool(
        description=(
            "Record something a character is actively concealing — not merely unknown, "
            "but deliberately hidden. Unlike record_knowledge (which captures facts), "
            "this captures the ACT of hiding: the character deflects, changes subject, "
            "or overcompensates when the topic comes near. "
            "The narrator can write the seams around a secret without ever naming it. "
            "concealed_from names who must not learn it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "Who holds this secret"},
                "secret": {
                    "type": "string",
                    "description": "The full truth as the narrator knows it",
                },
                "concealed_from": {
                    "type": "string",
                    "description": "Who must not learn this (comma-separated, optional)",
                },
            },
            "required": ["character", "secret"],
        },
    )
    def _tool_record_secret(
        self, character: str, secret: str, concealed_from: str = ""
    ) -> str:
        logger.info("ENTER _tool_record_secret: character=%r", character)
        body = secret
        if concealed_from:
            body += f"\n\nConcealed from: {concealed_from}"
        note = Note.jot(
            message=body,
            tag=self._stamp_refs(f"{TAG_INT}secret {TAG_CHAR}{character}"),
            context=f"secret: {character}",
            pwd=f"{PWD_INTERIOR}/{character}",
        )
        Note.append(Note.NOTEFILE, note)
        logger.info("secret recorded: %s", character)
        return json.dumps({"character": character, "status": "recorded"})

    @rp_tool(
        description=(
            "Record what a character wants from the current interaction or from "
            "another character — their hidden agenda, the thing driving behavior "
            "beneath what they say they want. "
            "Desire is almost always private. Every action becomes legible as pursuit "
            "or concealment of the desire. "
            "Use subtext to record what the desire really means at a deeper level."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "Who holds this desire"},
                "desire": {
                    "type": "string",
                    "description": "What they want — the immediate object or outcome",
                },
                "target": {
                    "type": "string",
                    "description": "Who they want it from, if applicable (optional)",
                },
                "subtext": {
                    "type": "string",
                    "description": "The deeper meaning — what this desire really represents (optional)",
                },
            },
            "required": ["character", "desire"],
        },
    )
    def _tool_record_desire(
        self, character: str, desire: str, target: str = "", subtext: str = ""
    ) -> str:
        logger.info("ENTER _tool_record_desire: character=%r", character)
        body = desire
        if target:
            body = f"Directed at: {target}\n\n{body}"
        if subtext:
            body += f"\n\nSubtext: {subtext}"
        note = Note.jot(
            message=body,
            tag=self._stamp_refs(f"{TAG_INT}desire {TAG_CHAR}{character}"),
            context=f"desire: {character}",
            pwd=f"{PWD_INTERIOR}/{character}",
        )
        Note.append(Note.NOTEFILE, note)
        logger.info("desire recorded: %s", character)
        return json.dumps({"character": character, "status": "recorded"})

    @rp_tool(
        description=(
            "Record a suppressed desire for another person — romantic, parental, the "
            "longing to be truly seen or understood. "
            "Longing is almost always hidden; the character may deny it to themselves. "
            "It produces the richest prose: glances held a beat too long, the careful "
            "not-touching, the specific thing they notice that no one else would."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "Who experiences the longing",
                },
                "subject": {
                    "type": "string",
                    "description": "Who or what is longed for",
                },
                "description": {
                    "type": "string",
                    "description": "What the longing feels like and how it manifests — specific, sensory, behavioral",
                },
            },
            "required": ["character", "subject", "description"],
        },
    )
    def _tool_record_longing(
        self, character: str, subject: str, description: str
    ) -> str:
        logger.info("ENTER _tool_record_longing: %r for %r", character, subject)
        note = Note.jot(
            message=f"Longing for: {subject}\n\n{description}",
            tag=self._stamp_refs(f"{TAG_INT}longing {TAG_CHAR}{character}"),
            context=f"longing: {character} for {subject}",
            pwd=f"{PWD_INTERIOR}/{character}",
        )
        Note.append(Note.NOTEFILE, note)
        logger.info("longing recorded: %s for %s", character, subject)
        return json.dumps(
            {"character": character, "subject": subject, "status": "recorded"}
        )

    @rp_tool(
        description=(
            "Record the envy one character feels toward another over a specific thing: "
            "the MC's attention, a social position, an inheritance, a talent. "
            "The jealous character conceals or rationalizes it; others sense something "
            "is off without naming it. "
            "Jealousy leaks into tone, small sabotages, and the wrong kind of helpfulness."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "Who feels the jealousy",
                },
                "target": {"type": "string", "description": "Who they are envious of"},
                "subject_of_competition": {
                    "type": "string",
                    "description": "What is being competed over (e.g. 'mc's attention', 'the inheritance', 'Ravenswood standing')",
                },
                "description": {
                    "type": "string",
                    "description": "How the jealousy manifests in behavior and thought",
                },
            },
            "required": [
                "character",
                "target",
                "subject_of_competition",
                "description",
            ],
        },
    )
    def _tool_record_jealousy(
        self, character: str, target: str, subject_of_competition: str, description: str
    ) -> str:
        logger.info("ENTER _tool_record_jealousy: %r of %r", character, target)
        note = Note.jot(
            message=f"Envious of: {target} | Over: {subject_of_competition}\n\n{description}",
            tag=self._stamp_refs(f"{TAG_INT}jealousy {TAG_CHAR}{character}"),
            context=f"jealousy: {character} of {target}",
            pwd=f"{PWD_INTERIOR}/{character}",
        )
        Note.append(Note.NOTEFILE, note)
        logger.info("jealousy recorded: %s of %s", character, target)
        return json.dumps(
            {"character": character, "target": target, "status": "recorded"}
        )

    @rp_tool(
        description=(
            "Record the persona a character presents publicly versus who they actually "
            "are in private — the gap between their performed self and their real one. "
            "A mask may be conscious (deliberate performance) or unconscious (a self "
            "they have come to believe in). "
            "Prose can show the cracks: the mask slipping, the over-correction, what "
            "the real person almost let through."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "Character slug"},
                "public_persona": {
                    "type": "string",
                    "description": "The face shown to the world",
                },
                "private_self": {
                    "type": "string",
                    "description": "Who they actually are beneath the performance",
                },
            },
            "required": ["character", "public_persona", "private_self"],
        },
    )
    def _tool_record_mask(
        self, character: str, public_persona: str, private_self: str
    ) -> str:
        logger.info("ENTER _tool_record_mask: character=%r", character)
        note = Note.jot(
            message=f"Public persona: {public_persona}\n\nPrivate self: {private_self}",
            tag=self._stamp_refs(f"{TAG_INT}mask {TAG_CHAR}{character}"),
            context=f"mask: {character}",
            pwd=f"{PWD_INTERIOR}/{character}",
        )
        Note.append(Note.NOTEFILE, note)
        logger.info("mask recorded: %s", character)
        return json.dumps({"character": character, "status": "recorded"})

    @rp_tool(
        description=(
            "Record what a character actually meant by something they said — "
            "the real message running beneath the stated words. "
            "Dialogue written with captured subtext operates on two tracks: what was "
            "said and what was meant, what was heard and what will be understood. "
            "Call this when a significant exchange has a second layer worth preserving."
        ),
        parameters={
            "type": "object",
            "properties": {
                "speaker": {"type": "string", "description": "Who said it"},
                "statement": {
                    "type": "string",
                    "description": "What was actually said",
                },
                "actual_meaning": {
                    "type": "string",
                    "description": "The real message beneath the surface",
                },
                "audience": {
                    "type": "string",
                    "description": "Who the statement was aimed at, if not the whole room (optional)",
                },
            },
            "required": ["speaker", "statement", "actual_meaning"],
        },
    )
    def _tool_record_subtext(
        self, speaker: str, statement: str, actual_meaning: str, audience: str = ""
    ) -> str:
        logger.info("ENTER _tool_record_subtext: speaker=%r", speaker)
        body = f'Said: "{statement}"\n\nMeant: {actual_meaning}'
        if audience:
            body += f"\n\nAimed at: {audience}"
        note = Note.jot(
            message=body,
            tag=self._stamp_refs(f"{TAG_INT}subtext {TAG_CHAR}{speaker}"),
            context=f"subtext: {speaker}",
            pwd=f"{PWD_INTERIOR}/{speaker}",
        )
        Note.append(Note.NOTEFILE, note)
        logger.info("subtext recorded: %s", speaker)
        return json.dumps({"speaker": speaker, "status": "recorded"})

    @rp_tool(
        description=(
            "Record how a character is generally perceived by others versus who they "
            "actually are — the gap between reputation and reality. "
            "Other characters arrive with preconceptions; they may be surprised, "
            "confirmed, or deceived by what they find. "
            "Use in_context to specify which social group holds this view."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "Character slug"},
                "perceived_as": {
                    "type": "string",
                    "description": "Their reputation — how others see them",
                },
                "reality": {
                    "type": "string",
                    "description": "Who they actually are, in tension with the reputation",
                },
                "in_context": {
                    "type": "string",
                    "description": "Among whom this reputation holds (e.g. 'Ravenswood staff', 'the nobility') — optional",
                },
            },
            "required": ["character", "perceived_as", "reality"],
        },
    )
    def _tool_record_reputation(
        self, character: str, perceived_as: str, reality: str, in_context: str = ""
    ) -> str:
        logger.info("ENTER _tool_record_reputation: character=%r", character)
        body = f"Perceived as: {perceived_as}\n\nReality: {reality}"
        if in_context:
            body += f"\n\nContext: {in_context}"
        note = Note.jot(
            message=body,
            tag=self._stamp_refs(f"{TAG_INT}reputation {TAG_CHAR}{character}"),
            context=f"reputation: {character}",
            pwd=f"{PWD_INTERIOR}/{character}",
        )
        Note.append(Note.NOTEFILE, note)
        logger.info("reputation recorded: %s", character)
        return json.dumps({"character": character, "status": "recorded"})

    @rp_tool(
        description=(
            "Record a specific word, topic, gesture, or event that causes a strong "
            "involuntary reaction in a character. "
            "Unlike conscience (a broad behavioral invariant), a trigger is precise: "
            "a single phrase, a smell, a name, a gesture. When it appears, the reaction "
            "is immediate and visceral. Others may stumble onto it without knowing. "
            "origin explains where the trigger came from."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "Who is triggered"},
                "trigger": {
                    "type": "string",
                    "description": "The specific word, topic, gesture, sound, or situation",
                },
                "reaction": {
                    "type": "string",
                    "description": "What happens when triggered — the immediate involuntary response",
                },
                "origin": {
                    "type": "string",
                    "description": "Where the trigger came from — the underlying wound or memory (optional)",
                },
            },
            "required": ["character", "trigger", "reaction"],
        },
    )
    def _tool_record_trigger(
        self, character: str, trigger: str, reaction: str, origin: str = ""
    ) -> str:
        logger.info(
            "ENTER _tool_record_trigger: character=%r trigger=%r", character, trigger
        )
        body = f"Trigger: {trigger}\n\nReaction: {reaction}"
        if origin:
            body += f"\n\nOrigin: {origin}"
        note = Note.jot(
            message=body,
            tag=self._stamp_refs(f"{TAG_INT}trigger {TAG_CHAR}{character}"),
            context=f"trigger: {character}",
            pwd=f"{PWD_INTERIOR}/{character}",
        )
        Note.append(Note.NOTEFILE, note)
        logger.info("trigger recorded: %s", character)
        return json.dumps({"character": character, "status": "recorded"})

    @rp_tool(
        description=(
            "Record something a character desperately wants to say to another person "
            "but has not said — pent-up feeling, unsent confession, accusation being "
            "swallowed. "
            "The unspoken thing bends every interaction around it without appearing. "
            "Conversations feel pressurized; pauses carry weight. "
            "why_unsaid captures the obstacle: fear, timing, social cost, or inability "
            "to find the words."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "Who is holding it in"},
                "target": {
                    "type": "string",
                    "description": "Who they want to say it to",
                },
                "what_they_want_to_say": {
                    "type": "string",
                    "description": "The unsaid thing — as specifically as possible",
                },
                "why_unsaid": {
                    "type": "string",
                    "description": "Why they haven't said it — fear, timing, pride, love, the wrong audience",
                },
            },
            "required": ["character", "target", "what_they_want_to_say", "why_unsaid"],
        },
    )
    def _tool_record_unspoken(
        self, character: str, target: str, what_they_want_to_say: str, why_unsaid: str
    ) -> str:
        logger.info("ENTER _tool_record_unspoken: %r → %r", character, target)
        note = Note.jot(
            message=f"To: {target}\n\nUnsaid: {what_they_want_to_say}\n\nWhy unsaid: {why_unsaid}",
            tag=self._stamp_refs(f"{TAG_INT}unspoken {TAG_CHAR}{character}"),
            context=f"unspoken: {character} → {target}",
            pwd=f"{PWD_INTERIOR}/{character}",
        )
        Note.append(Note.NOTEFILE, note)
        logger.info("unspoken recorded: %s → %s", character, target)
        return json.dumps(
            {"character": character, "target": target, "status": "recorded"}
        )

    # ── Consolidated relationship/interior recorders (TOOL_UNIFY U1) ──
    #
    # The 19 fine-grained rel:/int: tools above remain the storage
    # implementations, but are hidden from the LLM-facing compact menu
    # (_COMPACT_HIDDEN_TOOLS): the sessions tag census measured them at
    # near-zero activation while costing 56% of the compact step-2 schema
    # and acting as 19 distractors in every selection. These two kind-enum
    # tools are the model's surface; they delegate so tags, pwd, cache
    # drops, and reader visibility are byte-identical to the legacy tools.
    # `power` (not `power_dynamic`) so the enum value matches the stored
    # rel:power tag.

    _REL_KINDS = (
        "bond", "history", "dynamic", "power", "wound",
        "promise", "debt", "lie", "leverage", "impression",
    )
    _INT_KINDS = (
        "secret", "desire", "longing", "jealousy", "mask",
        "subtext", "reputation", "trigger", "unspoken",
    )

    @rp_tool(
        description=(
            "Record a durable fact about the relationship BETWEEN two characters. "
            "kind selects what it is: bond (what they are to each other), history "
            "(shared past event), dynamic (recurring push-pull pattern), power "
            "(who holds power and why), wound (emotional injury inflicted), "
            "promise, debt, lie, leverage, or impression (one's assessment of the "
            "other). char_a is always the actor or source — the holder, promiser, "
            "debtor, liar, inflicter, or observer; char_b is the target or "
            "recipient. description carries the main content; label is a short "
            "kebab-case type tag (bond type / pattern / power basis); detail is "
            "the second layer (significance, stakes, origin, the concealed truth)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(_REL_KINDS),
                    "description": "Which relationship fact is being recorded",
                },
                "char_a": {
                    "type": "string",
                    "description": "Actor/source character slug (holder, promiser, debtor, liar, inflicter, observer)",
                },
                "char_b": {
                    "type": "string",
                    "description": "Target/recipient character slug",
                },
                "description": {
                    "type": "string",
                    "description": "The main content — what is true, what happened, what is owed or claimed",
                },
                "label": {
                    "type": "string",
                    "description": "Short kebab-case type label (bond type, pattern, power basis) — optional",
                },
                "detail": {
                    "type": "string",
                    "description": "Second layer: significance, stakes, origin, or the concealed truth — optional",
                },
            },
            "required": ["kind", "char_a", "char_b", "description"],
        },
    )
    def _tool_record_relationship(
        self,
        kind: str,
        char_a: str,
        char_b: str,
        description: str,
        label: str = "",
        detail: str = "",
    ) -> str:
        logger.info(
            "ENTER _tool_record_relationship: kind=%r %r ↔ %r", kind, char_a, char_b
        )
        delegates = {
            "bond": lambda: self._tool_record_bond(
                char_a, char_b,
                bond_type=label or "unspecified",
                description=description,
            ),
            "history": lambda: self._tool_record_history(
                char_a, char_b,
                event=description,
                significance=detail or "(unspecified)",
            ),
            "dynamic": lambda: self._tool_record_dynamic(
                char_a, char_b,
                pattern=label or "unspecified",
                description=description,
            ),
            "power": lambda: self._tool_record_power_dynamic(
                holder=char_a, subject=char_b,
                basis=label or "unspecified",
                description=description,
            ),
            "wound": lambda: self._tool_record_wound(
                inflicter=char_a, wounded=char_b, description=description,
            ),
            "promise": lambda: self._tool_record_promise(
                promiser=char_a, recipient=char_b,
                commitment=description, stakes=detail,
            ),
            "debt": lambda: self._tool_record_debt(
                debtor=char_a, creditor=char_b,
                what_is_owed=description,
                origin=detail or "(unspecified)",
            ),
            "lie": lambda: self._tool_record_lie(
                liar=char_a, target=char_b,
                statement=description,
                truth=detail or "(truth unrecorded)",
            ),
            "leverage": lambda: self._tool_record_leverage(
                holder=char_a, subject=char_b, description=description,
            ),
            "impression": lambda: self._tool_record_impression(
                observer=char_a, subject=char_b,
                impression=description, trigger=detail,
            ),
        }
        delegate = delegates.get(kind)
        if delegate is None:
            return json.dumps(
                {
                    "error": f"unknown kind: {kind}",
                    "hint": "one of: " + ", ".join(self._REL_KINDS),
                }
            )
        return delegate()

    @rp_tool(
        description=(
            "Record one character's hidden interior life. kind selects what it "
            "is: secret (actively concealed truth), desire (hidden agenda), "
            "longing (suppressed yearning for someone), jealousy (envy over "
            "something specific), mask (public persona vs private self), subtext "
            "(what a statement really meant), reputation (how they are perceived "
            "vs reality), trigger (stimulus causing involuntary reaction), or "
            "unspoken (what they want to say but cannot). content is the private "
            "material itself; target is the other person involved (concealed-"
            "from, desired, longed-for, envied, or the audience); detail is the "
            "counterpart layer (private self, actual meaning, reality, reaction, "
            "why it stays unsaid)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(_INT_KINDS),
                    "description": "Which interior fact is being recorded",
                },
                "character": {
                    "type": "string",
                    "description": "Whose interior this is (the speaker, for subtext)",
                },
                "content": {
                    "type": "string",
                    "description": "The primary material — the secret, desire, persona, statement, trigger, or unsaid thing",
                },
                "target": {
                    "type": "string",
                    "description": "Other person involved: concealed-from, desired, longed-for, envied, or audience — optional",
                },
                "detail": {
                    "type": "string",
                    "description": "Counterpart layer: private self, actual meaning, reality, reaction, why-unsaid — optional",
                },
            },
            "required": ["kind", "character", "content"],
        },
    )
    def _tool_record_interior(
        self,
        kind: str,
        character: str,
        content: str,
        target: str = "",
        detail: str = "",
    ) -> str:
        logger.info(
            "ENTER _tool_record_interior: kind=%r character=%r", kind, character
        )
        delegates = {
            "secret": lambda: self._tool_record_secret(
                character, secret=content, concealed_from=target,
            ),
            "desire": lambda: self._tool_record_desire(
                character, desire=content, target=target, subtext=detail,
            ),
            "longing": lambda: self._tool_record_longing(
                character,
                subject=target or "(unnamed)",
                description=content,
            ),
            "jealousy": lambda: self._tool_record_jealousy(
                character,
                target=target or "(unnamed)",
                subject_of_competition=detail or "(unspecified)",
                description=content,
            ),
            "mask": lambda: self._tool_record_mask(
                character,
                public_persona=content,
                private_self=detail or "(unspecified)",
            ),
            "subtext": lambda: self._tool_record_subtext(
                speaker=character,
                statement=content,
                actual_meaning=detail or "(unspecified)",
                audience=target,
            ),
            "reputation": lambda: self._tool_record_reputation(
                character,
                perceived_as=content,
                reality=detail or "(unspecified)",
                in_context=target,
            ),
            "trigger": lambda: self._tool_record_trigger(
                character,
                trigger=content,
                reaction=detail or "(unspecified)",
            ),
            "unspoken": lambda: self._tool_record_unspoken(
                character,
                target=target or "(unnamed)",
                what_they_want_to_say=content,
                why_unsaid=detail or "(unspecified)",
            ),
        }
        delegate = delegates.get(kind)
        if delegate is None:
            return json.dumps(
                {
                    "error": f"unknown kind: {kind}",
                    "hint": "one of: " + ", ".join(self._INT_KINDS),
                }
            )
        return delegate()

    @rp_tool(
        description=(
            "Set a character's current emotional state for this scene — their mood, "
            "disposition, or dominant feeling right now. "
            "This is TRANSIENT: it lives in session memory only and is NOT persisted "
            "to notes. It clears automatically on cast changes and location changes. "
            "Mood colors dialogue delivery, physical tells, and what the character "
            "notices or ignores. "
            "Call update_attn for gaze and focus; call record_mood for emotional state."
        ),
        parameters={
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "Character slug"},
                "emotional_state": {
                    "type": "string",
                    "description": "Current emotional state (e.g. 'guarded', 'elated but hiding it', 'exhausted and bitter', 'nervously performing calm')",
                },
            },
            "required": ["character", "emotional_state"],
        },
    )
    def _tool_record_mood(self, character: str, emotional_state: str) -> str:
        logger.info("ENTER _tool_record_mood: %r → %r", character, emotional_state)
        self.session.mood[character] = emotional_state
        logger.info("mood set: %s = %r", character, emotional_state)
        return json.dumps(
            {
                "character": character,
                "emotional_state": emotional_state,
                "persisted": False,
                "followup_instruction": (
                    f"{character}'s mood is now '{emotional_state}'. "
                    "Let this color their dialogue delivery, physical tells, and what "
                    "they choose to notice or ignore in the scene."
                ),
            }
        )

    # ── Relationship query tools ───────────────────────────────────────

    @rp_tool(
        description=(
            "Retrieve all stored relationship notes for a specific pair of characters: "
            "bonds, shared history, dynamics, power, wounds, promises, debts, lies, "
            "leverage, and impressions. "
            "Call this at scene open when two characters first interact, or when the "
            "player asks out-of-character about a relationship. "
            "Results are cached — repeat calls within the same scene are free. "
            "Avoid calling this every beat; once per scene open is the right cadence "
            "unless new notes have been written (cache invalidates automatically on writes). "
            "Returns all notes sorted newest-first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "char_a": {"type": "string", "description": "First character slug"},
                "char_b": {"type": "string", "description": "Second character slug"},
            },
            "required": ["char_a", "char_b"],
        },
        step=1,
    )
    def _tool_get_relationship_arc(self, char_a: str, char_b: str) -> str:
        logger.info("ENTER _tool_get_relationship_arc: %r ↔ %r", char_a, char_b)
        pair = self._rel_key(char_a, char_b)
        cache_key = f"rel:{pair}"
        context_str = self._cache_get(cache_key)
        if context_str is None:
            bundle = ContextBundle(f"{PWD_REL}/{pair}")
            context_str = self.render_context(bundle, focus_hint=f"{char_a}+{char_b}")
            if context_str.strip():
                self._cache_put(cache_key, context_str)
        logger.info(
            "get_relationship_arc: pair=%s rendered=%d tok", pair, _tok(context_str)
        )
        return json.dumps(
            {
                "char_a": char_a,
                "char_b": char_b,
                "relationship_arc": context_str
                or f"[no relationship notes found for {char_a} ↔ {char_b}]",
                "followup_instruction": (
                    "Use this relationship history to inform how these characters interact — "
                    "their dialogue, what they avoid, what they reach for, and what they "
                    "cannot bring themselves to say."
                ),
            }
        )

    @rp_tool(
        description=(
            "Retrieve a relationship map for all characters currently present in the "
            "scene — bonds, history, dynamics, wounds, and impressions for every pair. "
            "Call this once at scene open when multiple characters are present, to "
            "understand the full web before writing group dynamics. "
            "Results are cached — repeat calls within the same scene are free. "
            "Avoid calling this on every beat; once per scene open is the right cadence "
            "unless the cast changes or new relationship notes have been written "
            "(cache invalidates automatically on writes and cast changes). "
            "Returns pair-by-pair summaries sorted newest-first within each pair."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        step=1,
    )
    def _tool_get_social_map(self) -> str:
        logger.info("ENTER _tool_get_social_map")
        people = sorted(self.session.people_present)
        if len(people) < 2:
            return json.dumps(
                {
                    "social_map": "[fewer than 2 characters present — no relationships to map]",
                }
            )

        cache_key = "social_map"
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.info("get_social_map: cache hit")
            return json.dumps(
                {
                    "cast": people,
                    "social_map": cached,
                    "followup_instruction": (
                        "Use this relationship web to write group dynamics authentically: "
                        "who defers to whom, who avoids whose gaze, which alliances are "
                        "hidden, and who is performing for whose benefit."
                    ),
                }
            )

        map_parts = []
        for i, char_a in enumerate(people):
            for char_b in people[i + 1 :]:
                pair = self._rel_key(char_a, char_b)
                bundle = ContextBundle(f"{PWD_REL}/{pair}")
                ctx = self.render_context(bundle, focus_hint=f"{char_a}+{char_b}")
                if ctx.strip():
                    map_parts.append(f"[{char_a} ↔ {char_b}]\n{ctx.strip()}")

        social_map_str = (
            "\n\n".join(map_parts)
            if map_parts
            else "[no relationship notes found for current cast]"
        )
        self._cache_put(cache_key, social_map_str)
        logger.info(
            "get_social_map: %d character(s), %d pair(s) with notes",
            len(people),
            len(map_parts),
        )
        return json.dumps(
            {
                "cast": people,
                "social_map": social_map_str,
                "followup_instruction": (
                    "Use this relationship web to write group dynamics authentically: "
                    "who defers to whom, who avoids whose gaze, which alliances are "
                    "hidden, and who is performing for whose benefit."
                ),
            }
        )

    # ===================================================================
    # === 11. Message Construction ===
    # ===================================================================

    # ------------------------------------------------------------------
    # Message construction helpers
    # ------------------------------------------------------------------

    _NARRATOR_RULE = (
        "Respond to exactly what the player's input describes — the single beat "
        "in front of you — and advance only as its direct consequence. Do not "
        "skip ahead or resolve an NPC's offer for the player. When an NPC "
        "invites, beckons, or leads, narrate the offer and let it hang; the "
        "player answers on their own next turn."
    )

    def build_user_message(self, user_input, dynamic_context=""):
        """
        Augment raw user input with the current session state header.

        Args:
            user_input:       classified player input string.
            dynamic_context:  optional per-turn story context from build_dynamic_context();
                              prepended as a SCENE CONTEXT block when non-empty.

        Returns:
            dict suitable for appending to a messages list.
        """
        parts = []
        if dynamic_context:
            parts.append(f"SCENE CONTEXT:\n{dynamic_context}")
        parts.append(self.session.header())
        parts.append(self._NARRATOR_RULE)
        parts.append(user_input)
        return {"role": "user", "content": "\n\n".join(parts)}

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
                context=f"tool call log at {loc}",
                pwd=f"{PWD_NOTES}/{loc}",
            )
            Note.append(Note.NOTEFILE, note)
            logger.debug("tool-call note written: %s -> %s", fn_name, tool_id)

    # ===================================================================
    # === 12. Narrative Synthesis ===
    # ===================================================================

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
        if fn_name == "record_bond":
            a, b = parsed.get("char_a", "?"), parsed.get("char_b", "?")
            bt = parsed.get("bond_type", "?")
            return f"Bond: {a} ↔ {b} ({bt})"
        if fn_name == "record_history":
            a, b = parsed.get("char_a", "?"), parsed.get("char_b", "?")
            return f"Shared history recorded: {a} ↔ {b}"
        if fn_name == "record_wound":
            inf, wnd = parsed.get("inflicter", "?"), parsed.get("wounded", "?")
            return f"Wound: {inf} → {wnd}"
        if fn_name == "record_promise":
            p, r = parsed.get("promiser", "?"), parsed.get("recipient", "?")
            return f"Promise: {p} → {r}"
        if fn_name == "record_impression":
            obs, sub = parsed.get("observer", "?"), parsed.get("subject", "?")
            return f"Impression: {obs} of {sub}"
        # Generic brace-free summary for every remaining tool (the whole rel/int
        # taxonomy and any future tool): a human label plus its meaningful scalar
        # values — never str(dict), which would leak braces into the prose synthesis.
        label = _WRITE_TOOL_LABELS.get(fn_name, fn_name.replace("_", " "))
        skip_keys = {"status", "recorded", "followup_instruction"}
        vals = []
        for k, v in parsed.items():
            if k in skip_keys or isinstance(v, bool):
                continue
            if isinstance(v, (str, int, float)) and str(v).strip():
                vals.append(str(v))
            elif isinstance(v, list) and all(isinstance(x, str) for x in v):
                if v:
                    vals.append(", ".join(v))
        preview = " — ".join(vals)[:150]
        return f"{label}: {preview}" if preview else label

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

    # ===================================================================
    # === 13. Debug ===
    # ===================================================================

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

        # ── CAST / LOCATION DRIFT WARNINGS ───────────────────────────────
        cast_warning = self._cast_warning_line()
        if cast_warning:
            lines.append(f"\n⚠  {cast_warning}")
        loc_warning = self._loc_warning_line()
        if loc_warning:
            lines.append(f"\n⚠  {loc_warning}")

        # ── LOCATION HIERARCHY ───────────────────────────────────────────
        lines.append("\nLOCATION HIERARCHY")
        for ancestor in self.session.location_ancestors:
            all_notes = list(ContextBundle(f"{PWD_WORLD}/{ancestor}"))
            loc_notes = [
                n
                for n in all_notes
                if not any(t.startswith(TAG_OBJ) for t in n.tag.split())
            ]
            obj_notes = [
                n for n in all_notes if any(t.startswith(TAG_OBJ) for t in n.tag.split())
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

        # ── NPC TRACKER ──────────────────────────────────────────────────
        lines.append(f"\n{HDR}")
        lines.append("  NPC TRACKER (session memory)")
        lines.append(HDR)
        tracker_records = self.npc_tracker.all()
        if tracker_records:
            for rec in sorted(tracker_records, key=lambda r: r.turn_introduced):
                flags = []
                if rec.central:
                    flags.append("central")
                if rec.interacted:
                    flags.append("interacted")
                if rec.mentioned and not rec.interacted:
                    flags.append("mentioned-only")
                if not rec.named:
                    flags.append("unnamed")
                if not rec.saved:
                    flags.append("not-yet-saved")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                # Rough backstory token cost
                char_bundle = list(ContextBundle(f"{PWD_CHARS}/{rec.slug}"))
                char_toks = _note_toks(char_bundle)
                lines.append(
                    f"    {rec.slug:<20}{flag_str:<30}"
                    f"  last: {rec.location_last_seen:<25}"
                    f"  backstory: {char_toks:>5} tok"
                )
        else:
            lines.append("    (no NPCs registered yet)")
        lines.append(HDR)

        # ── STEP TOOL COUNTS ─────────────────────────────────────────────
        lines.append(f"\n  Step 1 tools: {len(self._step1_schemas)}")
        lines.append(f"  Step 2 tools: {len(self._step2_schemas)}")

        return "\n".join(lines)

    def history_report(
        self, step2_messages, step3_messages, avg_pair_toks=None
    ) -> str:
        """Live token panel for the persistent conversation histories (R5/W10).

        The histories are the number that actually grows over a session, yet
        neither /prompt nor /stats surfaced it. Shows per-history totals and %,
        message counts, digest presence, the three cached schema overheads, the
        last measured payload, and an estimate of turns until the guard's 85%
        tier (from a trailing-average pair size supplied by the caller).
        """
        cap = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS

        def _has_digest(msgs):
            return any(
                str(m.get("content", "")).startswith("STORY SO FAR:") for m in msgs
            )

        s2 = sum(_msg_toks(m) for m in step2_messages)
        s3 = sum(_msg_toks(m) for m in step3_messages)
        lp = self._last_payload_toks

        lines = [
            f"{'[TOKEN BUDGET]':<30}"
            f"cap = {cap:,} (MODEL {MODEL_CONTEXT_LIMIT_TOKS:,} − "
            f"RESERVE {_RESPONSE_RESERVE_TOKS:,})",
            f"step2 history : {s2:>6,} tok ({100.0 * s2 / cap:>4.0f}%)  "
            f"[n={len(step2_messages)} msgs, digest present: "
            f"{'yes' if _has_digest(step2_messages) else 'no'}]",
            f"step3 history : {s3:>6,} tok ({100.0 * s3 / cap:>4.0f}%)  "
            f"[n={len(step3_messages)} msgs, digest present: "
            f"{'yes' if _has_digest(step3_messages) else 'no'}]",
            f"schema ovh    : step1 {self._cached_step1_schema_toks:,} · "
            f"step2(compact) {self._cached_compact_step2_schema_toks:,} · "
            f"step3(bare) {self._cached_bare_schema_toks:,}",
            f"last payload  : {lp:>6,} tok ({100.0 * lp / cap:>4.1f}%)"
            f"          (engine._last_payload_toks)",
        ]

        if avg_pair_toks and avg_pair_toks > 0:
            current = s2 + self._cached_compact_step2_schema_toks
            remaining = 0.85 * cap - current
            turns = int(remaining / avg_pair_toks) if remaining > 0 else 0
            lines.append(
                f"est. headroom : ~{turns} turns until 85% at "
                f"~{int(avg_pair_toks)} tok/turn (trailing-5 avg)"
            )
        else:
            lines.append(
                "est. headroom : (need ≥1 completed turn to estimate)"
            )

        return "\n".join(lines)

    # ===================================================================
    # === 14. LLM Payload Management ===
    # ===================================================================

    @staticmethod
    def _is_digest_message(m: dict) -> bool:
        """True for the compaction digest (a user message starting 'STORY SO FAR:').

        The guard treats it like a system message so oldest-first dropping never
        deletes the compacted story memory before ordinary history (D2).
        """
        c = m.get("content")
        return (
            m.get("role") == "user"
            and isinstance(c, str)
            and c.startswith("STORY SO FAR:")
        )

    @staticmethod
    def _tool_unit_indices(messages: list, i: int) -> set:
        """Indices of the atomic tool-call unit anchored at (or containing) index i.

        An assistant message with tool_calls forms one unit with every later
        role="tool" message answering one of its call ids. A role="tool" message
        belongs to the unit anchored by its parent assistant. Returns just {i}
        for a plain message or an orphan tool with no locatable parent.
        """
        m = messages[i]
        if m is None:
            return set()
        if m.get("tool_calls"):
            ids = {tc.get("id") for tc in m["tool_calls"] if tc.get("id")}
            unit = {i}
            for j in range(i + 1, len(messages)):
                mj = messages[j]
                if mj is None:
                    continue
                if mj.get("role") == "tool" and mj.get("tool_call_id") in ids:
                    unit.add(j)
            return unit
        if m.get("role") == "tool":
            tid = m.get("tool_call_id")
            for p in range(i - 1, -1, -1):
                mp = messages[p]
                if mp is None:
                    continue
                if mp.get("tool_calls") and any(
                    tc.get("id") == tid for tc in mp["tool_calls"]
                ):
                    return RPJotEngine._tool_unit_indices(messages, p)
            return {i}
        return {i}

    def _guard_payload(
        self, messages: list, schema_overhead: int | None = None
    ) -> list:
        """Pre-call safety guard against exceeding MODEL_CONTEXT_LIMIT_TOKS.

        Tiers (capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS):
          < 85%  — debug log only, no action
          85-89% — WARNING log, no mutation
          90-99% — WARNING log + pass 1 (trim oldest tool results)
          ≥ 100% — WARNING log + pass 1 + pass 2 (drop oldest non-system msgs)
        Never touches role="system" or the final message in the list.

        schema_overhead: token cost of the tools array that will be sent with
          this call.  Defaults to self._cached_schema_toks (full schemas).
          Pass self._cached_bare_schema_toks for narrative calls that use stubs.
        """
        capacity = MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS
        if schema_overhead is None:
            schema_overhead = self._cached_schema_toks
        msg_toks = sum(_msg_toks(m) for m in messages)
        total = msg_toks + schema_overhead
        self._last_payload_toks = total
        pct = 100.0 * total / capacity

        if pct < 85.0:
            logger.debug(
                "[CTX] guard: %d tok (msg=%d schema=%d, %.1f%% of cap=%d)",
                total,
                msg_toks,
                schema_overhead,
                pct,
                capacity,
            )
            return messages

        # 85-89%: warn only
        if pct < 90.0:
            logger.warning(
                "[CTX] guard: APPROACHING LIMIT — %d tok (%.1f%% of cap=%d)",
                total,
                pct,
                capacity,
            )
            return messages

        # 90-99%: pass 1 only (trim tool results) — reduce proactively before overflow
        # ≥ 100%: pass 1 + pass 2 (drop messages)
        if pct < 100.0:
            logger.warning(
                "[CTX] guard: NEAR LIMIT — %d tok (%.1f%% of cap=%d) — reducing",
                total,
                pct,
                capacity,
            )
        else:
            logger.warning(
                "[CTX] guard: OVER LIMIT — %d tok > cap=%d (%.1f%%) — reducing",
                total,
                capacity,
                pct,
            )
        messages = [dict(m) for m in messages]
        excess = max(total - int(capacity * 0.88), 0)  # trim to ~88% to give headroom

        # Pass 1: trim tool-result messages oldest-first, never the last message
        shed_pass1 = 0
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
            shed_pass1 += shed
            logger.warning(
                "[CTX] guard: trimmed tool result msg[%d] by ~%d tok (was %d tok)",
                i,
                shed,
                ctoks,
            )

        # Honest pass-1 report (D8): in the 90-99% band the payload is often all
        # history and no tool results exist to trim — say so rather than implying
        # a trim happened. (Above 100% pass 2 does the real work; reported later.)
        if pct < 100.0:
            if shed_pass1 <= 0:
                logger.warning(
                    "[CTX] guard: no trimmable tool results — payload is "
                    "conversation history, not tool output (%d tok, %.1f%%)",
                    total,
                    pct,
                )
            else:
                logger.warning(
                    "[CTX] guard: pass 1 shed %d tok from tool results", shed_pass1
                )

        # Pass 2: drop oldest non-system messages only when actually over limit.
        # Tool-call units are dropped atomically: an assistant carrying
        # tool_calls leaves together with every role="tool" reply to its ids,
        # and a role="tool" victim takes its parent assistant and siblings with
        # it. Dropping a partial unit yields an API-invalid sequence that
        # several OpenAI-compatible backends hard-reject. The STORY SO FAR
        # digest (index 1) is protected like a system message (D2) so the
        # oldest-first sweep never deletes compacted memory first.
        if excess > 0 and pct >= 100.0:
            n = len(messages)
            i = 1
            while i < n - 1 and excess > 0:
                m = messages[i]
                if (
                    m is None
                    or m.get("role") == "system"
                    or self._is_digest_message(m)
                ):
                    i += 1
                    continue
                unit = self._tool_unit_indices(messages, i)
                # Never drop the final (active-prompt) message, even as part of a unit.
                if (n - 1) in unit:
                    i += 1
                    continue
                unit_toks = sum(
                    _msg_toks(messages[k]) for k in unit if messages[k] is not None
                )
                for k in unit:
                    messages[k] = None
                excess -= unit_toks
                logger.warning(
                    "[CTX] guard: dropped %d-msg tool unit anchored at [%d] ~%d tok",
                    len(unit),
                    i,
                    unit_toks,
                )
                i += 1
            messages = [m for m in messages if m is not None]

        new_total = sum(_msg_toks(m) for m in messages)
        logger.warning(
            "[CTX] guard: reduction complete — %d → %d tok (saved %d)",
            total,
            new_total,
            total - new_total,
        )
        return messages
