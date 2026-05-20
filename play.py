#!/usr/bin/env python3
"""play.py -- Main game loop for the text-based RP engine."""

import argparse
import json
import logging
import os
import shutil
from datetime import datetime

import rpjot as _rpjot_module
from rpjot import (
    RPJotEngine,
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
_BELLVUE_JOT = os.path.join(_HERE, "tests", "bellvue_canonical.jot")
_SESSIONS_DIR = os.path.join(_HERE, "sessions")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("play")

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
      story_premise — who Bartholomew is and why he is at Ravenswood
      twist        — additional narrator secrets (belt-and-suspenders query
                     in case a twist note loses the system_role tag)
      backstory    — every named character's public profile (8 notes), giving
                     the narrator the canonical character roster upfront so it
                     never invents people who do not exist
    """
    parts = []

    for tag in ("system_role", "story_premise", "twist", "backstory"):
        bundle = ContextBundle(tag)
        text = str(bundle).strip()
        if text:
            parts.append(text)

    return [{"role": "system", "content": "\n\n".join(parts)}]


def game_loop(engine):
    """Main eternal game loop."""
    messages = build_initial_messages()

    print(
        "Welcome. Type your action. "
        "Commands: /quit, /people, /location, /objects, /stats, /mood, /attn, /yomi <name>"
    )
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

        # --- Normal player input -> LLM ---
        messages.append(engine.build_user_message(user_input))

        response = engine.run_tool_loop(messages)

        think, narrative = RPJotEngine.strip_think_tags(response.get("content", ""))

        display_think(think)
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
    """Copy bellvue.jot into a fresh timestamped session file and return its path."""
    os.makedirs(_SESSIONS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_SESSIONS_DIR, f"session_{stamp}.jot")
    shutil.copy2(_BELLVUE_JOT, path)
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
        description="Bellvue RP — omit SESSION to start a new game."
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
