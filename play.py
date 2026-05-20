#!/usr/bin/env python3
"""play.py -- Main game loop for the text-based RP engine."""

import argparse
import logging
import os
import shutil
from datetime import datetime

import rpjot as _rpjot_module
from rpjot import (
    RPJotEngine,
    LLMError,
    PWD_SUMMARIES,
    TAG_LOC,
    PWD_WORLD,
    PWD_YOMI,
    PWD_REL,
    PWD_INTERIOR,
)
from catjot import Note, ContextBundle

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEED_JOT = os.path.join(_HERE, "tests", "bellvue_canonical.jot")
_SESSIONS_DIR = os.path.join(_HERE, "sessions")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("play")

# ---------------------------------------------------------------------------
# Known slash commands (for unknown-command guard)
# ---------------------------------------------------------------------------

_SLASH_EXACT = frozenset(["/quit", "/people", "/location", "/stats", "/mood", "/attn"])
_SLASH_PREFIX = ("/objects", "/yomi")

# ---------------------------------------------------------------------------
# Input classification
# ---------------------------------------------------------------------------

_SIGILS = ('"', "*", "@", "^")

_HELP_TEXT = (
    "Input sigils:\n"
    '  "text   — MC speaks aloud (default if no sigil)\n'
    "  *text   — MC performs an action\n"
    "  @name   — shift MC focus to person/object; tail is intent toward it\n"
    "  ^text   — MC inner monologue (can seed backstory)\n"
    "Commands: /quit, /people, /location, /objects [name], "
    "/stats, /mood, /attn, /yomi <name>"
)


def classify_input(raw: str) -> str:
    """Translate player sigils into explicit LLM directives."""
    if raw.startswith('"'):
        return f"[MC speaks aloud]: {raw}"

    if raw.startswith("*"):
        content = raw[1:].strip()
        return f"[MC action]: {content}"

    if raw.startswith("@"):
        rest = raw[1:]
        tokens = rest.split(maxsplit=1)
        target = tokens[0]
        tail = tokens[1].strip() if len(tokens) > 1 else ""
        lines = [
            f'[MC attention → "{target}"]: '
            f"Shift MC's focus to '{target}'. "
            f"If '{target}' is not yet in the scene but could plausibly exist here, "
            f"introduce it as a discovered world detail. "
            f"If it cannot plausibly exist here, do not force it — "
            f"narrate the absence or redirect naturally."
        ]
        if tail:
            lines.append(f'[MC intent regarding "{target}"]: {tail}')
        return "\n".join(lines)

    if raw.startswith("^"):
        content = raw[1:].strip()
        return (
            f"[MC inner monologue — private, unspoken]: {content}\n"
            "Narrate this as interior experience only, never as spoken dialogue. "
            "If it reveals a meaningful feeling, preference, aversion, memory, or "
            "developing emotional arc, use record_conscience or record_secret to "
            "preserve it as MC backstory."
        )

    # Default: treat as spoken dialogue; note inference is acceptable
    return f"[MC — likely spoken aloud, interpret as dialogue unless clearly an action]: {raw}"


# ---------------------------------------------------------------------------
# Context query helpers (placeholders -- wire to real ContextBundle logic)
# ---------------------------------------------------------------------------


def query_people_context(engine):
    """Return notes about all characters currently in the scene."""
    people = list(engine.session.people_present)
    if not people:
        return "[no characters in scene]"
    ctx = engine.gather_all_character_knowledge(people)
    return str(ctx) or f"[no character notes found for: {', '.join(people)}]"


def query_location_context(engine):
    """Return notes for the current location and all ancestor locations."""
    ancestor_tags = [f"{TAG_LOC}{a}" for a in engine.session.location_ancestors]
    ctx = engine.gather_context(ancestor_tags + [PWD_WORLD])
    return str(ctx) or f"[no location notes found for: {engine.session.location}]"


def query_object_context(engine, object_name=None):
    """Return notes about objects in the scene or a specific named object."""
    if object_name:
        ctx = engine.gather_context([f"obj:{object_name}"])
        return str(ctx) or f"[no notes found for object: {object_name}]"
    ctx = engine.gather_context([f"{TAG_LOC}{engine.session.location}"])
    return str(ctx) or "[no object notes found in current scene]"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def display_narrative(narrative):
    """Print the narrator's response to the player."""
    print("\n" + "=" * 60)
    print(narrative)
    print("=" * 60 + "\n")


def display_think(think):
    """Optionally print the LLM's internal reasoning (debug aid)."""
    if think:
        logger.debug("[THINK] %s", think[:300])


# ---------------------------------------------------------------------------
# Game loop
# ---------------------------------------------------------------------------


def build_initial_messages():
    """Return the base system message list, seeded with Bellvue campaign data.

    Loads four groups from the canonical seed into the system prompt so the
    LLM narrator has authoritative context from turn one without tool calls:

      system_role  — RP/gameplay rules, story arcs, hardcoded world facts,
                     and narrator-only twist secrets
      story_premise — who mc is and why theyre where they are
      twist        — additional narrator secrets (belt-and-suspenders query
                     in case a twist note loses the system_role tag)
      backstory    — every named character's public profile (8 notes), giving
                     the narrator the canonical character roster upfront so it
                     never invents people who do not exist
    """
    parts = []
    backstory_bundle = None

    for tag in ("system_role", "story_premise", "twist", "backstory"):
        bundle = ContextBundle(tag)
        if tag == "backstory":
            backstory_bundle = bundle
        text = str(bundle).strip()
        if text:
            parts.append(text)

    # Derive a roster lock from backstory so the LLM never invents new named NPCs.
    # Extract every "char:name" token from backstory note tags, skip "mc" (engine alias).
    if backstory_bundle:
        names = sorted(
            {
                word[5:]
                for note in backstory_bundle
                for word in note.tag.split()
                if word.startswith("char:") and word != "char:mc"
            }
        )
        if names:
            parts.append(
                "NARRATOR RULE — Character Roster Check:\n"
                f"There are many pre-named characters in this story: {', '.join(names)}.\n"
                "Verify whether not-yet-named NPCs should adopt any of these characters "
                "since they might correspond to a created person, but just unknown to mc."
            )

    return [{"role": "system", "content": "\n\n".join(parts)}]


def game_loop(engine):
    """Main eternal game loop."""
    messages = build_initial_messages()

    print("Welcome.\n")
    print(_HELP_TEXT)
    print("-" * 60)

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nFarewell.")
            break

        if not user_input:
            continue

        # --- Meta commands (not sent to LLM) ---
        if user_input.lower() == "/quit":
            print("Farewell.")
            break

        if user_input.lower() == "/people":
            print(query_people_context(engine))
            continue

        if user_input.lower() == "/location":
            print(query_location_context(engine))
            continue

        if user_input.lower().startswith("/objects"):
            parts = user_input.split(maxsplit=1)
            obj = parts[1] if len(parts) > 1 else None
            print(query_object_context(engine, object_name=obj))
            continue

        if user_input.lower() == "/stats":
            print(engine.scene_debug_report())
            continue

        if user_input.lower() == "/mood":
            mood = engine.session.mood
            if mood:
                for char, state in sorted(mood.items()):
                    print(f"  {char} → {state}")
            else:
                print("[no mood state set this turn]")
            continue

        if user_input.lower() == "/attn":
            attn = engine.session.attention
            if attn:
                for char, focus in sorted(attn.items()):
                    print(f"  {char} → {focus}")
            else:
                print("[no attention state set this turn]")
            continue

        if user_input.lower().startswith("/yomi"):
            parts = user_input.split(maxsplit=1)
            if len(parts) > 1:
                char_name = parts[1].strip()
                bundle = ContextBundle(f"{PWD_YOMI}/{char_name}")
                text = str(bundle).strip()
                print(text or f"[no yomi found for: {char_name}]")
            else:
                print("Usage: /yomi <character_name>")
            continue

        # --- Unknown slash command guard ---
        if user_input.startswith("/"):
            cmd = user_input.lower().split()[0]
            if cmd not in _SLASH_EXACT and not any(
                cmd.startswith(p) for p in _SLASH_PREFIX
            ):
                print(f"Unknown command: {cmd}")
                print(_HELP_TEXT)
                continue

        # --- Normal player input -> LLM ---
        messages.append(engine.build_user_message(classify_input(user_input)))

        try:
            response = engine.run_tool_loop(messages)
        except LLMError as exc:
            print(f"[LLM error: {exc}]")
            messages.pop()
            continue

        think, narrative = RPJotEngine.strip_think_tags(response.get("content", ""))

        display_think(think)

        if not narrative:
            logger.warning(
                "[NARRATIVE] empty narrative after stripping — model produced no prose"
            )
            messages.append({"role": "assistant", "content": "(no response)"})
            display_narrative("(The narrator fell silent.)")
            continue

        display_narrative(narrative)

        note = Note.jot(
            message=narrative,
            tag="summary",
            context=think or user_input,
            pwd=PWD_SUMMARIES,
        )
        Note.append(Note.NOTEFILE, note)

        # Append the final assistant message to history
        messages.append({"role": "assistant", "content": narrative})


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


def create_session() -> str:
    """Copy seed.jot into a fresh timestamped session file and return its path."""
    os.makedirs(_SESSIONS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_SESSIONS_DIR, f"session_{stamp}.jot")
    shutil.copy2(_SEED_JOT, path)
    return path


def set_session_file(path: str):
    """Wire both catjot and rpjot to use *path* as the active note file."""
    Note.NOTEFILE = path
    _rpjot_module.NOTEFILE = path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="RP — omit SESSION to start a new game."
    )
    parser.add_argument(
        "session",
        nargs="?",
        help="Path to an existing session file to continue (e.g. sessions/session_20240101_120000.jot)",
    )
    args = parser.parse_args()

    if args.session:
        session_file = os.path.abspath(args.session)
        if not os.path.isfile(session_file):
            print(f"Session file not found: {session_file}")
            raise SystemExit(1)
        print(f"Continuing session: {session_file}")
    else:
        session_file = create_session()
        print(f"New session started: {session_file}")

    set_session_file(session_file)

    engine = RPJotEngine(
        location="ravenwood-manor",
        people_present={"mc"},
    )
    engine.register_all_tools()
    game_loop(engine)


if __name__ == "__main__":
    main()
