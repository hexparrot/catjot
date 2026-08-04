#!/usr/bin/env python3
"""Rewrite MC aliases to the `mc` slug in an existing .jot save.

Sessions written before the alias jot recorded the player character under the
name the model used for them (`exp:bartholomew`, `/story/character/bartholomew`)
while the engine keyed every MC lookup on the `mc` slug — so those notes were
invisible to the MC's own POV context. This walks a notefile, detects which
names are aliases of the MC from evidence in the file itself, rewrites cast
references to `mc`, and appends the alias jot so the running engine keeps
collapsing them.

Detection (no hardcoded names), from two independent signals:
  * a note at /story/character/T carrying the bare `mc`/`player`/`protagonist`
    tag — how the current seed marks the player character; and
  * the rules/premise prose naming them ("the user controls X", "X is the
    player character") — the only signal older saves carry, since their
    profiles were minted by the model with tags of its own choosing.
Short forms come from the same text: a "(Bart)" parenthetical, the full name,
and each name part on its own.

Only PREFIXED cast tags are rewritten (exp:/know:/char:/yomi:/cons:). A bare
topic tag like `story_premise bellvue_family bartholomew` is prose subject
matter, not cast identity, and is left alone.

Usage:  migrate_mc_aliases.py [--apply] FILE [FILE ...]
Without --apply it reports what would change and writes nothing.
"""
import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catjot import Note  # noqa: E402

MC = "mc"
CAST_PREFIXES = ("exp:", "know:", "char:", "yomi:", "cons:")
# pwd roots keyed by a single character slug
CHAR_ROOTS = ("/story/character", "/story/conscience", "/story/interior", "/yomi")
REL_ROOT = "/story/relationship"


def _slug(name):
    return re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower()).strip("-")


NAME = r"[A-Z][a-z]+(?: [A-Z][a-z]+)*"
# Prose that names the player character. Written by a human (the seed), not the
# model, so the phrasings are few and stable.
# NOTE: never case-insensitive — NAME leans on the capital to find the name,
# and re.I let it match "the" out of "controls the ...".
_MC_PROSE = (
    re.compile(rf"[Pp]layer controls ({NAME})"),
    re.compile(rf"[Uu]ser controls ({NAME})"),
    re.compile(rf"({NAME})[^.]{{0,60}} is the player character"),
)


def _names_from(text, strong, weak):
    """Fold a display name into the alias sets.

    The whole name and any "(Bart)" nickname are strong evidence; a single name
    part on its own is weak — a surname can belong to a whole family.
    """
    strong.add(_slug(text))
    for nick in re.findall(r"\(([^)]{2,20})\)", text):
        if nick.replace(" ", "").isalpha():
            strong.add(_slug(nick))
    for part in text.split():
        weak.add(_slug(part))


def detect_aliases(notes):
    """Return the MC alias slugs this file's own contents imply."""
    strong, weak = set(), set()
    for n in notes:
        if n.pwd.startswith("/story/character/"):
            slug = n.pwd[len("/story/character/"):].split("/")[0]
            bare = {w.lower() for w in n.tag.split() if ":" not in w}
            if _slug(slug) in ("", MC) or not (
                {"mc", "player", "protagonist"} & bare
            ):
                continue
            strong.add(_slug(slug))
            # "Bartholomew Wentworth (Bart), 35, is the player character."
            # The opening name only counts when it corroborates the slug it is
            # filed under — a profile that opens "The protagonist, arriving at
            # Ravenwood…" must not mint `the` as an alias.
            head = n.message.strip().splitlines()[0] if n.message.strip() else ""
            if m := re.match(rf"({NAME})", head):
                if _slug(slug) in {_slug(p) for p in m.group(1).split()}:
                    _names_from(m.group(1), strong, weak)
            for nick in re.findall(r"\(([^)]{2,20})\)", head):
                if nick.replace(" ", "").isalpha():
                    strong.add(_slug(nick))
        elif n.pwd.startswith("/system/rules") or n.pwd.startswith("/story/premises"):
            for pattern in _MC_PROSE:
                for m in pattern.finditer(n.message):
                    _names_from(m.group(1), strong, weak)
    if not strong:
        return set()
    # A bare name part that is also somebody else's slug (a family surname)
    # would swallow that character's notes — drop it.
    others = {
        _slug(n.pwd[len("/story/character/"):].split("/")[0])
        for n in notes
        if n.pwd.startswith("/story/character/")
    } - strong
    return (strong | (weak - others)) - {MC, ""}


def rewrite_tag(tag, aliases):
    """Collapse alias tokens inside prefixed cast tags to the mc slug."""
    out = []
    for word in tag.split():
        prefix = next((p for p in CAST_PREFIXES if word.startswith(p)), None)
        if not prefix:
            out.append(word)
            continue
        seen, parts = set(), []
        for tok in word[len(prefix):].split("+"):
            t = MC if _slug(tok) in aliases else tok
            if t and t not in seen:
                seen.add(t)
                parts.append(t)
        out.append(prefix + "+".join(parts))
    return " ".join(out)


def rewrite_pwd(pwd, aliases):
    """Repoint MC-keyed directories (character, conscience, interior, pair) at mc."""
    for root in CHAR_ROOTS:
        if pwd.startswith(root + "/"):
            rest = pwd[len(root) + 1:].split("/")
            if _slug(rest[0]) in aliases:
                rest[0] = MC
                return "/".join([root] + rest)
            return pwd
    if pwd.startswith(REL_ROOT + "/"):
        pair = pwd[len(REL_ROOT) + 1:]
        # _rel_key is "-".join(sorted([a, b])); either half may itself contain
        # dashes, so anchor on the alias at one end and keep the remainder whole.
        for alias in sorted(aliases, key=len, reverse=True):
            other = None
            if _slug(pair).startswith(alias + "-"):
                other = pair[len(alias) + 1:]
            elif _slug(pair).endswith("-" + alias):
                other = pair[: -(len(alias) + 1)]
            if other is not None:
                if _slug(other) == MC:
                    # bartholomew-mc: the MC paired with itself. Collapsing it
                    # would be a lie and deleting it would be data loss, so it
                    # stays put and gets reported.
                    return pwd
                return f"{REL_ROOT}/{'-'.join(sorted([MC, other]))}"
    return pwd


def is_self_pair(pwd, aliases):
    """True for a relationship pwd pairing the mc slug with one of its aliases."""
    if not pwd.startswith(REL_ROOT + "/"):
        return False
    pair = _slug(pwd[len(REL_ROOT) + 1:])
    return any(
        pair in (f"{a}-{MC}", f"{MC}-{a}") for a in aliases
    )


def cast_conflicts(notes, aliases):
    """Notes that tag the mc slug AND an alias — evidence they are two people.

    In one session the story had the MC impersonating Bartholomew ("views
    Bartholomew as a vessel for identity theft"), so the model was tagging them
    apart on purpose. Collapsing that file would erase a real distinction, so
    any hit here disqualifies the whole file from automatic migration.
    """
    hits = []
    for n in notes:
        cast = set()
        for word in n.tag.split():
            prefix = next((p for p in CAST_PREFIXES if word.startswith(p)), None)
            if prefix:
                cast |= {_slug(t) for t in word[len(prefix):].split("+")}
        if MC in cast and cast & aliases:
            hits.append(n)
    return hits


def migrate(path, apply, force=False):
    notes = list(Note.iterate(path))
    aliases = detect_aliases(notes)
    if not aliases:
        print(f"{path}: no MC aliases detected — skipped")
        return
    print(f"{path}: aliases {sorted(aliases)}")

    if conflicts := cast_conflicts(notes, aliases):
        print(
            f"  !! {len(conflicts)} note(s) tag mc AND an alias together — this "
            "story may treat them as two characters:"
        )
        for n in conflicts[:3]:
            print(f"       {n.tag}")
        if not force:
            print("  skipped (pass --force to migrate anyway)")
            return

    changed_tag = changed_pwd = 0
    records = []
    for n in notes:
        tag, pwd = rewrite_tag(n.tag, aliases), rewrite_pwd(n.pwd, aliases)
        if is_self_pair(n.pwd, aliases):
            print(f"    keep  {n.pwd}  (MC paired with itself — left alone)")
        if tag != n.tag:
            changed_tag += 1
            print(f"    tag   {n.tag}\n       -> {tag}")
        if pwd != n.pwd:
            changed_pwd += 1
            print(f"    pwd   {n.pwd}\n       -> {pwd}")
        records.append((pwd, n.now, tag, n.context, n.message))

    print(f"  {changed_tag} tags, {changed_pwd} pwds")
    if not apply:
        print("  (dry run — nothing written)")
        return

    backup = path + ".pre-alias.bak"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    tmp = path + ".migrating"
    with open(tmp, "wt") as fh:
        for pwd, now, tag, context, message in records:
            fh.write(f"{Note.LABEL_SEP}\n")
            fh.write(f"{Note.LABEL_PWD}{pwd}\n")
            fh.write(f"{Note.LABEL_NOW}{now}\n")
            fh.write(f"{Note.LABEL_TAG}{tag}\n")
            fh.write(f"{Note.LABEL_CTX}{context}\n")
            fh.write(f"{Note.LABEL_ARG}{message}\n\n")
    os.replace(tmp, path)

    full = sorted(aliases | {MC})
    Note.append(
        path,
        Note.jot(
            message="Player-facing names for the main character: " + ", ".join(full),
            tag=" ".join(f"alias:{a}" for a in full),
            context="mc aliases",
            pwd="/story/character/mc",
        ),
    )
    print(f"  written (backup: {backup}); alias jot: {full}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes")
    ap.add_argument(
        "--force",
        action="store_true",
        help="migrate even when mc and an alias are tagged together",
    )
    ap.add_argument("files", nargs="+")
    for path in (a := ap.parse_args()).files:
        migrate(path, a.apply, a.force)
