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
    _STEP3_SYSTEM,
    MODEL_CONTEXT_LIMIT_TOKS,
    _RESPONSE_RESERVE_TOKS,
    _msg_toks,
)
from catjot import Note, ContextBundle, call_llm

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

_SLASH_EXACT = frozenset(
    ["/quit", "/people", "/location", "/stats", "/mood", "/attn", "/prompt"]
)
_SLASH_PREFIX = ("/objects", "/yomi")

_SYSTEM_REFRESH_TEMPERATURE = 0.9

_PARAPHRASE_INSTRUCTION = (
    "You are a narrator-briefing editor. "
    "Restate the following in fresh phrasing. "
    "Preserve every proper noun, character name, game rule, secret, "
    "relationship, and factual detail exactly — do not add or omit anything. "
    "Vary only sentence structure and word choice."
)

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
    "/stats, /mood, /attn, /yomi <name>, /prompt [text]"
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


def build_dynamic_context(engine) -> str:
    """Return /story/* context relevant to the current scene.

    Strategy (all naive, no LLM calls):
      1. Location notes for the current location hierarchy — always included.
      2. "Domain tags" extracted from those location notes (descriptive tags
         like 'bellvue_family', 'manor', excluding structural/prefixed tags).
         Character backstory notes and premise/twist notes that share any
         domain tag are included.
      3. Characters explicitly present in the scene (people_present) are
         always included regardless of tag overlap.
      4. Text-based fallback: notes whose text contains a present NPC name
         or the current scene slug are also included.
      5. The MC profile is always included (domain_tags seeded with 'mc').
    """
    _STRUCTURAL_PREFIXES = (
        "loc:",
        "scene:",
        "char:",
        "exp:",
        "know:",
        "cons:",
        "yomi:",
        "rel:",
        "int:",
    )
    _STRUCTURAL_TAGS = frozenset(
        {
            "backstory",
            "system_role",
            "story_premise",
            "twist",
            "hardcoded",
            "fixed_story",
            "alternate_story",
        }
    )

    parts = []

    # 1. Location hierarchy notes (always included)
    ancestor_dirs = [f"{PWD_WORLD}/{a}" for a in engine.session.location_ancestors]
    loc_bundle = engine.gather_context(ancestor_dirs) if ancestor_dirs else None
    if loc_bundle:
        loc_text = str(loc_bundle).strip()
        if loc_text:
            parts.append(loc_text)

    # Seed domain tags with MC sentinel so the MC's backstory is always loaded
    domain_tags: set = {"mc", "player"}
    if loc_bundle:
        for note in loc_bundle:
            for word in note.tag.split():
                if (
                    not any(word.startswith(p) for p in _STRUCTURAL_PREFIXES)
                    and word not in _STRUCTURAL_TAGS
                ):
                    domain_tags.add(word)

    # Text-based fallback terms: present NPCs + current scene slug
    present_npcs = {c for c in engine.session.people_present if c != "mc"}
    text_terms: set = set(present_npcs)
    if engine.session.current_scene:
        text_terms.add(engine.session.current_scene)

    # 2. Character backstory notes: domain-tag overlap OR present in scene OR text match
    char_matched: list = []
    seen_chars: set = set()
    for note in ContextBundle("backstory"):
        char_name = next(
            (
                w[5:]
                for w in note.tag.split()
                if w.startswith("char:") and w != "char:mc"
            ),
            None,
        )
        if not char_name or char_name in seen_chars:
            continue
        note_tags = set(note.tag.split())
        note_text = f"{note.context} {note.message}".lower()
        if (
            char_name in present_npcs
            or bool(note_tags & domain_tags)
            or any(t.lower() in note_text for t in text_terms)
        ):
            seen_chars.add(char_name)
            char_matched.append(f"{note.context.strip()}\n\n{note.message.strip()}")
    if char_matched:
        parts.append("\n\n".join(char_matched))

    # 3. Premise/twist notes: domain-tag overlap OR text match
    premise_matched: list = []
    for note in ContextBundle(["story_premise", "twist"]):
        note_tags = set(note.tag.split())
        note_text = f"{note.context} {note.message}".lower()
        if bool(note_tags & domain_tags) or any(
            t.lower() in note_text for t in text_terms
        ):
            premise_matched.append(f"{note.context.strip()}\n\n{note.message.strip()}")
    if premise_matched:
        parts.append("\n\n".join(premise_matched))

    return "\n\n".join(parts)


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


def build_step2_initial_messages():
    """Return the Step 2 (ComplianceStep) initial message list.

    System message = gameplay rules (system_role notes).
    Story/character content is injected per-turn by WorldStateStep.
    """
    text = str(ContextBundle("system_role")).strip()
    return [{"role": "system", "content": text}]


def build_step3_initial_messages():
    """Return the Step 3 (ProseStep) initial message list.

    System message = prose craft prompt. No gameplay rules — pure narrative.
    """
    return [{"role": "system", "content": _STEP3_SYSTEM}]


def refresh_system_message(engine, step2_messages):
    """Paraphrase step2_messages[0] (gameplay rules) to break token-sequence repetition.

    No-ops with a warning if the LLM call fails so the game continues.
    """
    original = str(ContextBundle("system_role")).strip()
    if not original:
        logger.warning("[REFRESH] no system content found; skipping refresh")
        engine._system_refresh_pending = False
        return
    prompt_messages = [
        {"role": "system", "content": _PARAPHRASE_INSTRUCTION},
        {"role": "user", "content": original},
    ]

    try:
        response = call_llm(prompt_messages, temperature=_SYSTEM_REFRESH_TEMPERATURE)
        refreshed = response.get("content", "").strip()
        if not refreshed:
            raise ValueError("empty paraphrase response")
    except Exception as exc:
        logger.warning(
            "[REFRESH] paraphrase call failed (%s); keeping old system message", exc
        )
        engine._system_refresh_pending = False
        return

    step2_messages[0] = {"role": "system", "content": refreshed}
    engine._system_refresh_pending = False
    logger.info("[REFRESH] system message refreshed (%d tok)", len(refreshed.split()))


# ---------------------------------------------------------------------------
# Persistent-history compaction (R1 / W4)
# ---------------------------------------------------------------------------
# The per-call guard trims a *copy* at send time; it never shrinks the
# persistent step2/step3 histories, so a long session grows them without
# bound until the guard silently starts dropping story turns every call.
# compact_history folds the oldest exchanges into a single "STORY SO FAR"
# digest well before the guard's 85% tier, keeping history sawtooth-bounded.

HISTORY_SOFT_TOKS = int(0.5 * (MODEL_CONTEXT_LIMIT_TOKS - _RESPONSE_RESERVE_TOKS))
KEEP_RECENT_PAIRS = 8  # last N user/assistant pairs stay verbatim


def _history_toks(messages) -> int:
    """Total token cost of a persistent history list (content + tool_calls)."""
    return sum(_msg_toks(m) for m in messages)


def _split_history(messages):
    """Return (head, digest_prefix, body) for a history list.

    head is [system]; digest_prefix is the content of an existing STORY SO FAR
    digest at index 1 (removed from body) or "" if none; body is the remaining
    user/assistant pairs.
    """
    head, body = messages[:1], messages[1:]
    digest_prefix = ""
    if (
        body
        and body[0].get("role") == "user"
        and str(body[0].get("content", "")).startswith("STORY SO FAR:")
    ):
        digest_prefix = body.pop(0)["content"]
    return head, digest_prefix, body


def compact_history(
    engine, step2_messages, step3_messages, keep_pairs=KEEP_RECENT_PAIRS
):
    """Fold the oldest turns of both histories into one shared STORY SO FAR digest.

    Triggers when either list exceeds HISTORY_SOFT_TOKS. The two lists carry
    identical user/assistant pairs (only their system message at index 0
    differs), so the digest is computed once and installed into both (D1). An
    existing digest is folded into the new one — the "STORY SO FAR" slot never
    stacks. Mutates both lists in place; never touches the final message
    (compaction runs after the post-turn appends, so the newest pair is kept
    verbatim by keep_pairs anyway).
    """
    if (
        _history_toks(step2_messages) <= HISTORY_SOFT_TOKS
        and _history_toks(step3_messages) <= HISTORY_SOFT_TOKS
    ):
        return

    head2, prefix2, body2 = _split_history(step2_messages)
    head3, prefix3, body3 = _split_history(step3_messages)

    cut = max(0, len(body2) - 2 * keep_pairs)
    old, recent2 = body2[:cut], body2[cut:]
    if not old:
        return
    cut3 = max(0, len(body3) - 2 * keep_pairs)
    recent3 = body3[cut3:]

    digest_prefix = prefix2 or prefix3
    raw = (digest_prefix + "\n\n" if digest_prefix else "") + "\n\n".join(
        f"[{m.get('role')}] {m.get('content', '')}" for m in old
    )
    digest = engine._condense_context(raw, focus_hint="")
    digest_content = f"STORY SO FAR:\n{digest}"

    step2_messages[:] = (
        head2 + [{"role": "user", "content": digest_content}] + recent2
    )
    step3_messages[:] = (
        head3 + [{"role": "user", "content": digest_content}] + recent3
    )

    logger.info(
        "[HIST] compacted %d msgs → digest (step2=%d tok, step3=%d tok now)",
        len(old),
        _history_toks(step2_messages),
        _history_toks(step3_messages),
    )


def game_loop(engine):
    """Main game loop using the 3-step pipeline."""
    step2_messages = build_step2_initial_messages()
    step3_messages = build_step3_initial_messages()

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

        if user_input.lower().startswith("/prompt"):
            parts = user_input.split(maxsplit=1)
            simulated_input = parts[1] if len(parts) > 1 else "[player input here]"
            classified_sim = classify_input(simulated_input)
            divider = "─" * 60
            print(f"\n{'═'*60}")
            print("  PROMPT PREVIEW (3-step architecture)")
            print(f"{'═'*60}")
            print(f"\n[STEP 2 SYSTEM (gameplay rules)]\n{divider}")
            print(
                step2_messages[0]["content"][:800]
                + ("..." if len(step2_messages[0]["content"]) > 800 else "")
            )
            print(f"{divider}\n[STEP 3 SYSTEM (prose craft)]\n{divider}")
            print(step3_messages[0]["content"])
            print(f"{divider}\n[NPC TRACKER]\n{divider}")
            print(engine.npc_tracker.roster_summary())
            print(f"{divider}\n[CLASSIFIED INPUT]\n{divider}")
            print(classified_sim)
            print(f"{divider}\n")
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

        # --- Normal player input → 3-step pipeline ---
        classified = classify_input(user_input)

        try:
            narrative = engine.run_turn(classified, step2_messages, step3_messages)
        except LLMError as exc:
            print(f"[LLM error: {exc}]")
            continue

        if not narrative:
            logger.warning("[NARRATIVE] empty narrative from step 3")
            display_narrative("(The narrator fell silent.)")
            step2_messages.append({"role": "user", "content": classified})
            step2_messages.append({"role": "assistant", "content": "(no response)"})
            step3_messages.append({"role": "user", "content": classified})
            step3_messages.append({"role": "assistant", "content": "(no response)"})
            compact_history(engine, step2_messages, step3_messages)
            continue

        display_narrative(narrative)

        note = Note.jot(
            message=narrative,
            tag="summary",
            context=user_input,
            pwd=PWD_SUMMARIES,
        )
        Note.append(Note.NOTEFILE, note)

        # Both histories get the same player input + narrative output
        step2_messages.append({"role": "user", "content": classified})
        step2_messages.append({"role": "assistant", "content": narrative})
        step3_messages.append({"role": "user", "content": classified})
        step3_messages.append({"role": "assistant", "content": narrative})

        # Compaction runs BEFORE the system refresh: refresh only replaces
        # index 0 (system), so the digest installed at index 1 is preserved.
        compact_history(engine, step2_messages, step3_messages)

        if engine._system_refresh_pending:
            refresh_system_message(engine, step2_messages)


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
    engine.init_pipeline()

    # Bootstrap the opening scene so current_scene is never empty from turn one.
    engine._tool_begin_scene(
        "opening",
        "The story opens at the exterior of Ravenwood Manor. The player character has just arrived "
        "having been dropped off and the taxi now fully out of sight.",
    )
    engine._system_refresh_pending = False

    game_loop(engine)


if __name__ == "__main__":
    main()
