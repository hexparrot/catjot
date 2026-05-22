#!/usr/bin/env python3
__author__ = "William Dizon"
__license__ = "MIT"
__version__ = "0.0.1"
__maintainer__ = "William Dizon"
__email__ = "wdchromium@gmail.com"
__status__ = "Development"

"""
catjot — a directory-aware, cat-themed note-taking tool
========================================================

Every note is stamped with the directory you were in when you wrote it, so
your notes live where your work lives.  Jot something down, wander away,
come back, and the cat remembers exactly where you left things.

  jot              — show notes for the current directory
  jot <text>       — write a note (pipe text in, or type after the command)
  jot s <term>     — search all notes, case-insensitive
  jot llm          — ask the cat to dig through your notes naturally
  jot convo        — start a persistent LLM conversation stored as notes

The on-disk format is a plain-text record file delimited by "^-^" separators
(the cat face), making it human-readable and trivially grep-able without
this tool.  See Note.LABEL_SEP and Note.FIELDS_TO_PARSE for the exact layout.

Architecture at a glance
─────────────────────────
  Note            — a single jotted thought; knows how to read/write itself
  NoteContext     — `with` wrapper that materialises a filtered Note list
  ContextBundle   — a live, set-algebra view over many notes; used by the
                    LLM roleplay / conversation system
  catjot_graphql  — optional GraphQL interface over the same note file
  run_tool_loop   — agentic LLM loop that searches notes via tool calls
  main()          — the CLI; all user-facing commands land here
"""

import requests
import json
from functools import partial
from typing import Callable, List
from os import environ, getcwd, getenv
from enum import Enum, auto

# ENVIRONMENT VARIABLES
#
# Variable          Default                  Description
# ─────────────────────────────────────────────────────────────────────────────
# CATJOT_FILE       $HOME/.catjot            Path to the note storage file.
# EDITOR            vim                      Editor launched by `jot scoop`.
# openai_api_key    (none)                   Bearer token for the LLM endpoint.
# openai_api_url    (none)                   Full URL to an OpenAI-compatible
#                                            chat-completions endpoint.
# openai_api_model  (none)                   Model name sent in each request.
# openai_api_sysrole (none)                  System-role prompt prepended to
#                                            every LLM conversation. Appended
#                                            to the built-in cat-assistant
#                                            prompt in `jot llm`; replaces it
#                                            entirely in `jot chat`/`jot convo`.
#
# ── Bash / Zsh ────────────────────────────────────────────────────────────────
# Persist in ~/.bash_profile, ~/.bashrc, or ~/.zshrc:
#
#   export CATJOT_FILE="$HOME/.myjot"
#   export EDITOR="nano"
#   export openai_api_key="sk-proj...8EEF"
#   export openai_api_url="https://localhost:5000/v1/chat/completions"
#   export openai_api_model="catgpt-nano"
#   export openai_api_sysrole="You are a pleasant cat assistant. Be playful."
#
# Override for a single command:
#
#   openai_api_sysrole="Be brief." jot chat explain recursion
#   CATJOT_FILE=/tmp/scratch.jot jot
#
# ── Nushell ───────────────────────────────────────────────────────────────────
# Persist in config.nu (or env.nu):
#
#   $env.CATJOT_FILE       = ($env.HOME | path join ".myjot")
#   $env.EDITOR            = "hx"
#   $env.openai_api_key    = "sk-proj...8EEF"
#   $env.openai_api_url    = "https://localhost:5000/v1/chat/completions"
#   $env.openai_api_model  = "catgpt-nano"
#   $env.openai_api_sysrole = "You are a cat assistant. Be playful and punny."
#
# Override for a single command using with-env:
#
#   with-env { openai_api_sysrole: "Be brief." } { jot chat explain recursion }
#   with-env { CATJOT_FILE: "/tmp/scratch.jot", openai_api_model: "gpt-4o" } { jot llm }


def supports_color():
    """Return True if the current terminal can display ANSI colour codes.

    Checks two things: the platform is not plain Windows (unless ANSICON or
    Windows Terminal are present), and stdout is an actual TTY rather than a
    pipe or file redirect.  When piping output to `grep`, `less`, or a file
    the cat quietly drops all the pretty colours so the raw text stays clean.
    """
    import os
    import sys

    supported_platform = (
        os.name != "nt" or "ANSICON" in os.environ or "WT_SESSION" in os.environ
    )
    is_a_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    return supported_platform and is_a_tty


class AnsiColor(Enum):
    """Raw ANSI escape sequences for terminal colour output.

    Values are strings that can be embedded directly in f-strings.
    Always pair a colour with RESET at the end of the coloured span so the
    cat's coat doesn't bleed onto unintended text.
    """

    RESET = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"


class SearchType(Enum):
    """Selects which field of a Note to match against in Note.match().

    Pass one of these together with a search value as a (SearchType, value)
    tuple.  Multiple tuples can be combined with AND or OR logic.

      ALL        — match every note regardless of content; value is ignored
      TAG        — exact word match within the space-separated tag field
      MESSAGE    — case-sensitive substring search of the message body
      MESSAGE_I  — case-insensitive version of MESSAGE
      CONTEXT    — case-sensitive substring search of the context field
      CONTEXT_I  — case-insensitive version of CONTEXT
      TIMESTAMP  — match the exact integer epoch timestamp (Note.now)
      DIRECTORY  — exact match on the stored directory path (Note.pwd)
      TREE       — prefix match on pwd; returns the note and all children
    """

    ALL = auto()
    TAG = auto()
    MESSAGE = auto()
    MESSAGE_I = auto()
    CONTEXT = auto()
    CONTEXT_I = auto()
    TIMESTAMP = auto()
    DIRECTORY = auto()
    TREE = auto()


class OutputColors(Enum):
    """Semantic colour aliases used when rendering chat and cat-image output.

    Keeps the display logic decoupled from raw ANSI values so the colour
    scheme can be changed in one place.

      IMG_CAT  — colour of the ASCII cat art
      CHAT_ME  — colour of the user's prompt echo
      CHAT_CAT — colour of the assistant's reply header
      CHAT_END — colour of the status/footer line ("stop. model=…")
    """

    IMG_CAT = AnsiColor.YELLOW.value
    CHAT_ME = AnsiColor.CYAN.value
    CHAT_CAT = AnsiColor.GREEN.value
    CHAT_END = AnsiColor.MAGENTA.value


class Note(object):
    """A single catjot note — one thought, one place, one moment in time.

    Each Note stores five fields:

      pwd      — the working directory the note was written from
      now      — Unix epoch timestamp (int) of when it was written
      tag      — space-separated labels, e.g. "project1 urgent"
      context  — a free-form string giving the note extra situational meaning;
                 typically the command that produced the output being noted, or
                 a summary of the surrounding conversation turn in LLM mode
      message  — the actual note body (always ends with a newline)

    On disk each note is stored in a plain-text "record" delimited by the
    "^-^" separator (the cat face).  The serialised form looks like:

      ^-^
      Directory:/home/user
      Date:1695002470
      Tag:project1
      Context:ls /home/user
      Message:note_goes_here
      note_continued

    When printed to the terminal the same note renders as:

      ^-^
      > cd /home/user
      # date 2023-09-17 19:01:10 (1695002470)
      [project1]
      % ls /home/user
      note_goes_here
      note_continued

    The on-disk format is intentionally grep-friendly.  The terminal format
    uses colour when the terminal supports it (see USE_COLORIZATION).

    Class-level label constants (LABEL_SEP, LABEL_PWD, …) are the canonical
    source of truth for both reading and writing; change them here if you
    ever need to migrate the file format.

    File lifecycle
    ──────────────
    Writes always append; destructive edits (delete, amend, pop) follow a
    safe two-phase pattern:

      1. Note.delete / Note.amend writes a <src>.new shadow file.
      2. Note.commit renames <src> → <src>.old, then <src>.new → <src>.

    This keeps a one-step rollback available at all times — the cat always
    lands on its feet.
    """

    # ── On-disk field label constants ────────────────────────────────────────
    # Label Name | File internally looks like:
    # -----------|----------------------------------
    # LABEL_SEP  | ^-^
    # LABEL_PWD  | Directory:/home/user
    # LABEL_NOW  | Date:1695002470
    # LABEL_TAG  | Tag:project1
    # LABEL_CTX  | Context:ls /home/user
    # LABEL_ARG  | Message:note_goes_here
    #            | note_continued
    #            |

    # ── Terminal display label constants ─────────────────────────────────────
    # Label Name | Default pattern shown at runtime:
    # -----------|----------------------------------
    # REC_TOP    | ^-^
    # LABEL_DIR  | > cd /home/user
    # LABEL_DATE | # date 2023-09-17 19:01:10 (1695002470)
    #            | [project1]
    #            | % ls /home/user
    # LABEL_DATA | note_goes_here
    #            | note_continued
    #            |
    # REC_BOT    | (empty — override to add a footer)

    # Runtime display presentation
    LABEL_DIR = "> cd "
    LABEL_DATE = "# date "
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S (%s)"
    LABEL_DATA = ""  # line has no add'l prefix as default

    # Top and bottom record separators
    REC_TOP = "^-^"
    REC_BOT = ""

    # .catjot file formatting
    LABEL_SEP = "^-^"  # record separator
    LABEL_PWD = "Directory:"
    LABEL_NOW = "Date:"
    LABEL_TAG = "Tag:"
    LABEL_CTX = "Context:"
    LABEL_ARG = "Message:"

    # All required fields to exist before note data
    FIELDS_TO_PARSE = [
        ("pwd", LABEL_PWD),
        ("now", LABEL_NOW),
        ("tag", LABEL_TAG),
        ("context", LABEL_CTX),
    ]

    # Filepath to save to, saves in $HOME
    NOTEFILE = f"{environ['HOME']}/.catjot"
    # Use colorization if terminal supports
    USE_COLORIZATION = True and supports_color()

    def __init__(self, values_dict=None):
        """Initialise a Note from a plain dictionary of field values.

        All fields are optional; missing ones fall back to sensible defaults
        so that Note() with no arguments creates a valid (if empty) note
        anchored to the current directory and current time.

        The message field is stripped of a leading "Message:" prefix when
        the dict comes directly from the file parser — that way the same
        constructor handles both parsed records and hand-built dicts without
        needing separate factory paths.

        Args:
            values_dict: dict with any subset of keys:
                "pwd"     — absolute path string (asserted to start with "/")
                "now"     — int epoch timestamp
                "tag"     — str, space-separated tag words
                "context" — str, situational annotation
                "message" — str, the note body
        """
        from time import time

        if values_dict is None:
            values_dict = {}
        now = int(time())
        self.pwd = values_dict.get("pwd", getcwd())
        assert self.pwd.startswith("/")
        self.now = int(values_dict.get("now", now))
        assert isinstance(self.now, int)
        self.tag = values_dict.get("tag", "")
        assert isinstance(self.tag, str)
        self.context = values_dict.get("context", "")
        self.message = values_dict.get("message", "")
        # Strip the on-disk "Message:" label prefix if present so the stored
        # text and the in-memory text are always identical.
        if self.message.startswith(Note.LABEL_ARG):
            self.message = self.message[len(Note.LABEL_ARG) :]

    def __str__(self):
        """Render the note for human eyes (terminal display format).

        The output format is intentionally different from the on-disk storage
        format — it's meant to be read by people, not parsed by machines.
        Optional fields (tag, context) are omitted entirely when empty so
        the display stays tidy.  Colour codes are applied when
        Note.USE_COLORIZATION is True (i.e. the output is a real TTY).

        Returns:
            A multi-line string starting with the directory and timestamp
            header, followed by optional tag and context lines, then the
            message body.  Does NOT include the REC_TOP/REC_BOT separators —
            the caller (printout in main) wraps those around the str() call.
        """
        from datetime import datetime

        dt = datetime.fromtimestamp(self.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)

        tagline = ""
        context = ""

        if Note.USE_COLORIZATION:
            if self.context:
                context = (
                    f"{AnsiColor.GREEN.value}% {self.context}{AnsiColor.RESET.value}\n"
                )
            if self.tag:
                tagline = (
                    f"[{AnsiColor.YELLOW.value}{self.tag}{AnsiColor.RESET.value}]\n"
                )

            return (
                f"{AnsiColor.CYAN.value}{Note.LABEL_DIR}{self.pwd}{AnsiColor.RESET.value}\n"
                + f"{AnsiColor.RED.value}{Note.LABEL_DATE}{friendly_date}{AnsiColor.RESET.value}\n"
                + tagline
                + context
                + f"{Note.LABEL_DATA}{self.message}"
            )
        else:
            if self.context:
                context = f"% {self.context}\n"
            if self.tag:
                tagline = f"[{self.tag}]\n"

            return (
                f"{Note.LABEL_DIR}{self.pwd}\n"
                + f"{Note.LABEL_DATE}{friendly_date}\n"
                + tagline
                + context
                + f"{Note.LABEL_DATA}{self.message}"
            )

    def __repr__(self):
        """Compact unambiguous representation for debugging and test output."""
        return f"Note(context='{self.context}', message='{self.message}')"

    def __eq__(self, other):
        """Compare two notes for logical equality, ignoring insignificant whitespace.

        Trailing newlines and leading/trailing spaces in message and context
        are stripped before comparison, so a note written with a trailing
        blank line is considered the same note as one without.  The timestamp
        (now) must match exactly — two otherwise identical notes written at
        different times are different notes.

        Returns False (not NotImplemented) when compared to a non-Note so
        that list membership tests work correctly without surprises.
        """

        if isinstance(other, Note):
            return (
                self.message.strip() == other.message.strip()
                and self.pwd == other.pwd
                and self.now == other.now
                and self.context.strip() == other.context.strip()
                and self.tag == other.tag
            )
        else:
            return False

    @classmethod
    def jot(cls, message, tag="", context="", pwd=None, now=None):
        """Create a new Note without touching the filesystem.

        This is the preferred factory for constructing notes in code.  It
        strips and re-adds a trailing newline so the message is always
        consistently terminated, and raises immediately on an empty message
        rather than letting a silent no-op propagate to disk.

        Args:
            message: the note body text; must be non-empty.
            tag:     space-separated tag words, e.g. "project1 urgent".
            context: situational annotation (command, conversation turn, etc.).
            pwd:     directory to stamp on the note; defaults to getcwd().
            now:     epoch timestamp int; defaults to the current time.

        Returns:
            A new Note object ready to be passed to Note.append().

        Raises:
            ValueError: if message is empty or whitespace-only.
        """
        from time import time

        if not message:
            raise ValueError

        return Note(
            {
                "pwd": pwd or getcwd(),
                "now": now or int(time()),
                "tag": tag,
                "context": context,
                "message": message.strip() + "\n",
            }
        )

    @classmethod
    def append(cls, src, note):
        """Serialise a Note and append it to the note file.

        Writes one complete record — separator, all four header fields, and
        the message body — in a single open/write/close cycle.  The file is
        opened in append mode ("at") so concurrent writers don't clobber each
        other's notes (though concurrent *deletes* are not safe).

        Args:
            src:  path to the .catjot file (created if it doesn't exist).
            note: a Note object; its message must be non-empty.

        Raises:
            ValueError: if note.message is falsy (empty string).
        """
        if not note.message:
            raise ValueError("Cannot append a note with an empty message")

        with open(src, "at") as file:
            file.write(f"{Note.LABEL_SEP}\n")
            file.write(f"{Note.LABEL_PWD}{note.pwd}\n")
            file.write(f"{Note.LABEL_NOW}{note.now}\n")
            file.write(f"{Note.LABEL_TAG}{note.tag}\n")
            file.write(f"{Note.LABEL_CTX}{note.context}\n")
            file.write(f"{Note.LABEL_ARG}{note.message}\n\n")

    @classmethod
    def delete(cls, src, timestamp):
        """Write a shadow copy of the note file with matching notes omitted.

        Does NOT modify src in place.  Instead it creates <src>.new containing
        every note whose timestamp does NOT equal `timestamp`.  Call
        Note.commit(src) afterward to atomically replace src with the new file
        and keep a backup at <src>.old.

        If multiple notes share the same timestamp (unusual but possible if
        notes are constructed with an explicit `now` value) all of them will be
        omitted — timestamp is the only identity key the format provides.

        Args:
            src:       path to the source note file.
            timestamp: int epoch value of the note(s) to remove.
        """
        newpath = src + ".new"
        with open(newpath, "wt") as trunc_file:
            for inst in cls.iterate(src):
                if int(inst.now) != int(timestamp):
                    trunc_file.write(f"{Note.LABEL_SEP}\n")
                    trunc_file.write(f"{Note.LABEL_PWD}{inst.pwd}\n")
                    trunc_file.write(f"{Note.LABEL_NOW}{inst.now}\n")
                    trunc_file.write(f"{Note.LABEL_TAG}{inst.tag}\n")
                    trunc_file.write(f"{Note.LABEL_CTX}{inst.context}\n")
                    trunc_file.write(f"{Note.LABEL_ARG}{inst.message}\n\n")

    @classmethod
    def amend(cls, src, context=None, pwd=None, tag=None):
        """Rewrite the *last* note in the file with updated field values.

        Like delete(), this is a two-phase operation: it produces <src>.new and
        requires a subsequent Note.commit() call to finalise.  All notes other
        than the last are copied verbatim; the last note gets whichever fields
        are provided as non-None arguments.

        Tag handling is additive by default — passing tag="new_label" appends
        to the existing tag string rather than replacing it.  To *remove* a
        tag, prefix it with "~": tag="~old_label" strips "old_label" from
        the existing tags.

        Args:
            src:     path to the source note file.
            context: new context string for the last note, or None to keep
                     the existing value.
            pwd:     new directory path for the last note, or None to keep
                     the existing value.
            tag:     tag to add (plain string) or remove ("~tagname"), or None
                     to leave the tag field untouched.
        """
        last_record = None
        for inst in cls.iterate(src):
            last_record = inst

        newpath = src + ".new"
        with open(newpath, "wt") as trunc_file:
            for inst in cls.iterate(src):
                trunc_file.write(f"{Note.LABEL_SEP}\n")

                if pwd and int(inst.now) == int(last_record.now):
                    # new pwd provided and this is the matching record time
                    trunc_file.write(f"{Note.LABEL_PWD}{pwd}\n")
                else:
                    trunc_file.write(f"{Note.LABEL_PWD}{inst.pwd}\n")

                trunc_file.write(f"{Note.LABEL_NOW}{inst.now}\n")

                if tag and int(inst.now) == int(last_record.now):
                    all_tags = inst.tag.split(" ")
                    if tag.startswith("~"):
                        try:
                            all_tags.remove(tag[1:])
                        except ValueError:
                            pass  # don't care if its not in there
                    else:
                        if tag not in all_tags:
                            all_tags.append(tag)
                    tag_str = " ".join(all_tags)
                    trunc_file.write(f"{Note.LABEL_TAG}{tag_str}\n")
                else:
                    trunc_file.write(f"{Note.LABEL_TAG}{inst.tag}\n")

                if context and int(inst.now) == int(last_record.now):
                    # new contxt provided and this is the matching record time
                    trunc_file.write(f"{Note.LABEL_CTX}{context}\n")
                else:
                    trunc_file.write(f"{Note.LABEL_CTX}{inst.context}\n")

                trunc_file.write(f"{Note.LABEL_ARG}{inst.message}\n\n")

    @classmethod
    def pop(cls, src, path):
        """Delete the most recently written note for a given directory.

        Iterates every note that matches the exact directory path and tracks
        the last one seen.  After the loop completes, that final timestamp is
        passed to Note.delete() to produce the <src>.new shadow file.

        Still requires Note.commit(src) to apply the deletion.

        Args:
            src:  path to the note file.
            path: exact directory string to match (SearchType.DIRECTORY).

        Raises:
            TypeError: if no notes are found for `path` (last_record stays
                       None and delete(src, None) will raise).
        """
        last_record = None
        for inst in Note.match(src, [(SearchType.DIRECTORY, path)]):
            last_record = inst.now
        cls.delete(src, last_record)

    @classmethod
    def commit(cls, src):
        """Atomically replace the note file with the pending shadow copy.

        The two-step rename sequence is:
          1. src        → src.old   (previous version kept as one-step backup)
          2. src.new    → src       (shadow copy becomes the live file)

        Always pair this with a prior Note.delete(), Note.amend(), or
        Note.pop() call that produced the src.new file.  Calling commit()
        without a preceding write-phase will raise FileNotFoundError because
        src.new won't exist — the cat doesn't like committing to nothing.
        """
        import shutil

        shutil.move(src, src + ".old")
        shutil.move(src + ".new", src)

    @classmethod
    def iterate(cls, src):
        """Yield every Note in the file, in order of appearance.

        This is the foundation of all read operations.  Note.match() calls
        this generator and filters its output; nothing else should need to
        open the note file directly.

        The parser recognises a record boundary as a blank line immediately
        followed by a "^-^" line (LABEL_SEP).  Records do not need a trailing
        separator at the end of the file — the final record is flushed when
        EOF is reached.

        Fault tolerance
        ───────────────
        If the first line of a new record doesn't look like a Directory:
        header (which happens when a raw "^-^" separator ends up inside a
        previous note's message body, e.g. from `cat file.jot | jot`), the
        malformed fragment is silently discarded so it doesn't poison the
        rest of the file.  Parsing resumes at the next valid record boundary.

        Yields:
            Note objects, one per valid record.
        """

        def parse(record):
            """Convert a list of raw lines into a Note-constructor dict.

            Pops lines from the front of `record` in FIELDS_TO_PARSE order,
            strips the field label prefix, and accumulates the remainder as
            the message body.  Returns None (implicitly) if the header lines
            don't match the expected labels, causing the caller to skip the
            malformed record silently.

            Args:
                record: list of raw file lines for one record (mutable;
                        lines are pop(0)'ed during parsing).

            Returns:
                dict suitable for Note(**d), or None on parse failure.
            """
            current_read = {}
            for field, label in cls.FIELDS_TO_PARSE:  # enforce header ordering
                try:
                    current_read[field] = record.pop(0).split(label, 1)[1].strip()
                except IndexError:
                    break  # header line missing or out of order — skip record
            else:
                # `for…else` fires only when the loop completed without a break,
                # meaning all four header fields were parsed successfully.
                message = "".join(record).rstrip() + "\n"
                current_read["message"] = message
                return current_read

        current_record = []
        last_line = ""

        with open(src, "r") as file:
            for line in file:
                if last_line == "" and line.strip() == Note.LABEL_SEP:
                    # Blank line + separator = end of previous record.
                    # Flush whatever we accumulated and start fresh.
                    if len(current_record):
                        yield Note(parse(current_record))
                    current_record = []
                else:
                    if current_record and Note.LABEL_PWD not in current_record[0]:
                        # We're mid-record but the first line doesn't look like
                        # a Directory: header — a separator landed inside the
                        # previous note's data.  Drop this fragment silently
                        # and wait for the next valid record boundary.
                        current_record = []
                        last_line = ""
                        continue

                    current_record.append(line)
                    last_line = line.strip()
            # End of file: no trailing separator, so flush the last record
            # manually if one is in progress.
            if last_line == "" and len(current_record):
                yield Note(parse(current_record))

    @classmethod
    def match(cls, src, criteria, logic="and", time_only=False):
        """Yield notes from src that satisfy the given search criteria.

        Criteria are expressed as (SearchType, value) tuples.  For
        convenience a single tuple may be passed directly (without wrapping
        it in a list) — this method normalises it automatically.

        AND mode (default)
        ──────────────────
        A note is yielded only when it satisfies *every* criterion.  The
        yield happens as soon as the running match count reaches len(criteria),
        which means the generator is safe to partially consume (e.g. next()).

        OR mode
        ───────
        A note is yielded as soon as *any single* criterion is satisfied,
        then the inner loop breaks so the note is never yielded twice.

        Empty criteria list always yields nothing — an explicit match
        against nothing is treated as "no results", not "everything".

        Falsy values (empty string, 0) are skipped and never increment the
        match counter, EXCEPT for SearchType.ALL which always matches and
        always increments regardless of its value.

        Args:
            src:       path to the note file.
            criteria:  list of (SearchType, value) tuples, or a single tuple.
            logic:     "and" (all must match) or "or" (any must match).
            time_only: if True, yield note.now (int) instead of the Note object.

        Yields:
            Note objects (or int timestamps if time_only=True) in file order.
        """
        if isinstance(criteria, tuple):
            criteria = [criteria]  # normalise bare tuple → single-element list

        if logic == "and":
            for inst in cls.iterate(src):
                CRITERIA_MET = 0
                for s_type, s_text in criteria:
                    if s_type is SearchType.ALL:
                        CRITERIA_MET += 1  # ALL, match all
                    elif not s_text:
                        pass  # no matching, no incrementing
                    elif s_type is SearchType.DIRECTORY:
                        CRITERIA_MET += 1 if inst.pwd == s_text else 0
                    elif s_type is SearchType.TREE:
                        CRITERIA_MET += 1 if inst.pwd.startswith(s_text) else 0
                    elif s_type is SearchType.MESSAGE:
                        CRITERIA_MET += 1 if s_text in inst.message else 0
                    elif s_type is SearchType.MESSAGE_I:
                        CRITERIA_MET += (
                            1 if s_text.lower() in inst.message.lower() else 0
                        )
                    elif s_type is SearchType.CONTEXT:
                        CRITERIA_MET += 1 if s_text in inst.context else 0
                    elif s_type is SearchType.CONTEXT_I:
                        CRITERIA_MET += (
                            1 if s_text.lower() in inst.context.lower() else 0
                        )
                    elif s_type is SearchType.TIMESTAMP:
                        CRITERIA_MET += 1 if inst.now == s_text else 0
                    elif s_type is SearchType.TAG:
                        CRITERIA_MET += 1 if s_text in inst.tag.split() else 0

                    if CRITERIA_MET == len(criteria):
                        if time_only:
                            yield inst.now
                        else:
                            yield inst
        else:
            for inst in cls.iterate(src):
                CRITERIA_MET = 0
                for s_type, s_text in criteria:
                    if s_type is SearchType.ALL:
                        CRITERIA_MET += 1
                    elif not s_text:
                        pass  # no matching, no incrementing
                    elif s_type is SearchType.DIRECTORY:
                        CRITERIA_MET += 1 if inst.pwd == s_text else 0
                    elif s_type is SearchType.TREE:
                        CRITERIA_MET += 1 if inst.pwd.startswith(s_text) else 0
                    elif s_type is SearchType.MESSAGE:
                        CRITERIA_MET += 1 if s_text in inst.message else 0
                    elif s_type is SearchType.MESSAGE_I:
                        CRITERIA_MET += (
                            1 if s_text.lower() in inst.message.lower() else 0
                        )
                    elif s_type is SearchType.CONTEXT:
                        CRITERIA_MET += 1 if s_text in inst.context else 0
                    elif s_type is SearchType.CONTEXT_I:
                        CRITERIA_MET += (
                            1 if s_text.lower() in inst.context.lower() else 0
                        )
                    elif s_type is SearchType.TIMESTAMP:
                        CRITERIA_MET += 1 if inst.now == s_text else 0
                    elif s_type is SearchType.TAG:
                        CRITERIA_MET += 1 if s_text in inst.tag.split() else 0

                    if CRITERIA_MET:
                        if time_only:
                            yield inst.now
                        else:
                            yield inst
                        break


class ContextBundle(object):
    """A live, set-algebra view over a collection of notes.

    ContextBundle is primarily used by the LLM conversation and roleplay
    system to assemble curated groups of notes as context windows.  It
    supports natural Python operators for building and manipulating that
    context:

      ctx  = ContextBundle("my_tag")          # start with all notes tagged "my_tag"
      ctx += "/home/user/project"             # union in notes from a directory
      ctx += 1726009125                       # union in a note by timestamp
      ctx -= "unwanted_tag"                   # remove all notes with that tag
      ctx2 = ctx - ContextBundle("spoilers")  # new object, "spoilers" notes suppressed
      str(ctx)                                # context+message pairs for LLM consumption

    Matching terms (tags, dirs, timestamps) determine which notes are loaded
    from disk into self.notes.  Suppression is a separate, non-destructive
    layer: suppressed notes stay in self.notes but are excluded from
    iteration and str() output.  This lets you temporarily hide parts of
    the context without losing the underlying data.

    The distinction between -= and suppress():
      -=           removes the matching term and reloads notes from disk;
                   the note may disappear if no other term covers it.
      suppress()   adds the term to a blocklist; the note stays in self.notes
                   but is invisible to callers — until unsuppress() is called.

    Term type dispatch (used by +=, -=, suppress, unsuppress):
      int           → treated as a timestamp
      str starting with "/" → treated as a directory path
      any other str → treated as a tag
    """

    def __init__(self, tags_dirs_ts):
        """Initialise a ContextBundle and immediately load matching notes.

        Accepts a single term (str or int) or a list of mixed terms.  Each
        term is dispatched to the appropriate set (self.tags, self.dirs, or
        self.ts) and notes are loaded from Note.NOTEFILE for each.

        Args:
            tags_dirs_ts: a single tag/dir/timestamp, or a list of them.
        """
        self.tags = set()
        self.dirs = set()
        self.ts = set()
        self.notes = []
        self.blocks = {"directory": set(), "tag": set(), "timestamp": set()}

        if isinstance(tags_dirs_ts, list):
            for t in tags_dirs_ts:
                self += t
        else:
            self += tags_dirs_ts

    def __str__(self):
        """Render the bundle as a flat string for LLM context injection.

        Each visible note contributes two paragraphs: its context field (the
        situational annotation) followed by its message body, separated by a
        blank line.  Notes are emitted in the order they were added to the
        bundle.  Suppressed notes are silently skipped.

        The result is suitable for inserting directly into an LLM message as
        prior-conversation context or world-state background.
        """
        combined_str = ""
        for note in self._visible_notes():
            combined_str += (
                note.context.strip() + "\n\n" + note.message.strip() + "\n\n"
            )
        return (
            combined_str.strip()
        )  # Remove the trailing newline from the final concatenation

    def __repr__(self):
        """Full developer representation showing all internal state."""
        return (
            f"ContextBundle(tags={self.tags}, dirs={self.dirs}, ts={self.ts}, "
            f"notes={self.notes}, blocks={self.blocks})"
        )

    def __iter__(self):
        """Yield each visible (non-suppressed) note in addition order."""
        for n in self._visible_notes():
            yield n

    def __add__(self, item):
        """Return a new bundle that is the union of this bundle and `item`.

        When `item` is another ContextBundle, the matching terms (tags, dirs,
        ts) of both are merged into the new object.  Suppressions from either
        side are NOT carried over — the returned bundle starts with a clean
        block list.

        When `item` is a plain str or int it is added via __iadd__ on a deep
        copy of self.

        Returns a new ContextBundle; self is unchanged.
        """
        import copy

        # Create a deep copy of the current instance
        new_obj = copy.deepcopy(self)

        if isinstance(item, ContextBundle):
            # returned object has values from both a + b
            new_obj.tags.update(item.tags)
            new_obj.dirs.update(item.dirs)
            new_obj.ts.update(item.ts)
        else:
            new_obj += item

        # Reread file on disk and repopulate self.notes list
        new_obj._regen_notes()

        return new_obj

    def __iadd__(self, item):
        """Add a matching term in place and reload notes from disk.

        Dispatches by type/prefix:
          int  → self.ts    (timestamp match)
          str starting with "/" → self.dirs  (exact directory match)
          other str → self.tags  (tag word match)

        After updating the appropriate set, _regen_notes() re-reads the file
        so self.notes reflects the new union of all matching terms.
        """
        # adds notes if not existing
        if isinstance(item, int):
            self.ts.add(item)
        elif item.startswith("/"):
            self.dirs.add(item)
        else:
            self.tags.add(item)

        # Reread file on disk and repopulate self.notes list
        self._regen_notes()

        return self

    def __isub__(self, item):
        """Remove a matching term in place and reload notes from disk.

        Removes `item` from the appropriate set (ts, dirs, or tags).  If the
        item isn't present the operation is a silent no-op.  After updating
        the set, _regen_notes() re-reads the file — notes that were only
        covered by the removed term will disappear from self.notes; notes
        covered by remaining terms will stay.
        """
        # identifies and removes matching notes
        if isinstance(item, int):
            if item in self.ts:
                self.ts.remove(item)
        elif item.startswith("/"):
            if item in self.dirs:
                self.dirs.remove(item)
        elif item in self.tags:
            self.tags.remove(item)

        # Reread file on disk and repopulate self.notes list
        self._regen_notes()

        return self

    def __sub__(self, item):
        """Return a new bundle with `item` removed or suppressed.

        When `item` is a ContextBundle: returns a deep copy of self with all
        of item's tags, timestamps, and directories added to the block list
        (suppression).  The underlying notes are preserved in memory; they
        simply become invisible to iteration and str() until unsuppressed.

        When `item` is a plain str or int: returns a deep copy with that term
        removed from the matching sets (equivalent to -= on a copy).

        Returns a new ContextBundle; self is unchanged.
        """
        import copy

        # Create a deep copy of the current instance
        new_obj = copy.deepcopy(self)

        if isinstance(item, ContextBundle):
            for t in item.tags:
                new_obj.suppress(t)
            for t in item.ts:
                new_obj.suppress(t)
            for t in item.dirs:
                new_obj.suppress(t)
        else:
            # Identifies and removes matching notes
            new_obj -= item

            # Reread file on disk and repopulate self.notes list
            new_obj._regen_notes()

        return new_obj

    def __len__(self):
        """Return the count of currently visible (non-suppressed) notes."""
        return len(list(self._visible_notes()))

    def _visible_notes(self):
        """Yield notes that pass all suppression filters, without duplicates.

        A note is hidden if any of the following is true:
          • one of its tag words appears in self.blocks["tag"]
          • its pwd is in self.blocks["directory"]
          • its now timestamp is in self.blocks["timestamp"]

        This is used by __iter__, __len__, and __str__ — everything that
        needs to respect the current suppression state goes through here.
        """
        seen = set()
        for n in self.notes:
            if id(n) in seen:
                continue
            if not set(n.tag.split()).isdisjoint(self.blocks["tag"]):
                continue
            if n.pwd in self.blocks["directory"]:
                continue
            if n.now in self.blocks["timestamp"]:
                continue
            seen.add(id(n))
            yield n

    def _regen_notes(self):
        """Rebuild self.notes by re-reading Note.NOTEFILE from disk.

        Called every time a matching term is added or removed (via +=/-=) to
        keep the in-memory note list consistent with the declared terms.
        Uses NoteContext (which in turn calls Note.match) so the same search
        logic applies here as everywhere else.

        Notes are de-duplicated: a note that matches on both a tag and a
        directory is only stored once.
        """
        self.notes = []

        def add_notes(search_type, values):
            for value in values:
                with NoteContext(Note.NOTEFILE, (search_type, value)) as notes:
                    for n in notes:
                        if n not in self.notes:
                            self.notes.append(n)

        # Regenerate notes based on tags, directories, and timestamps
        add_notes(SearchType.TAG, self.tags)
        add_notes(SearchType.DIRECTORY, self.dirs)
        add_notes(SearchType.TIMESTAMP, self.ts)

    @property
    def active_tags(self):
        """Return the set of all tag words across every note in self.notes.

        Suppression is intentionally NOT applied here — this reflects the
        full tag vocabulary of all loaded notes, not just the visible ones.
        Useful for inspecting what tags are present before deciding what
        to suppress or add as new matching terms.
        """
        all_tags = set()
        for n in self.notes:
            all_tags.update(n.tag.split())
        return all_tags

    def suppress(self, item):
        """Add `item` to the block list so matching notes are hidden from iteration.

        Does NOT remove notes from self.notes or touch the matching-term sets.
        The notes remain in memory and can be revealed again via unsuppress().

        Args:
            item: int → blocks by timestamp;
                  str starting with "/" → blocks by directory;
                  other str → blocks by tag word.
        """
        if isinstance(item, int):
            self.blocks["timestamp"].add(item)
        elif item.startswith("/"):
            self.blocks["directory"].add(item)
        else:
            self.blocks["tag"].add(item)

    def unsuppress(self, item):
        """Remove `item` from the block list, making matching notes visible again.

        Silent no-op if `item` was not suppressed — the cat doesn't complain
        about unsuppressing things that weren't suppressed in the first place.

        Args:
            item: int, directory string, or tag string (same dispatch as suppress).
        """
        try:
            if isinstance(item, int):
                self.blocks["timestamp"].remove(item)
            elif item.startswith("/"):
                self.blocks["directory"].remove(item)
            else:
                self.blocks["tag"].remove(item)
        except KeyError:
            pass


class NoteContext:
    """Context manager that materialises a filtered list of Notes.

    Wraps Note.match() in a `with` statement so callers get a concrete list
    (not a lazy generator) without having to write their own try/except for
    the "file doesn't exist yet" first-run case.

    Usage:
        with NoteContext(NOTEFILE, (SearchType.DIRECTORY, "/home/user")) as nc:
            for note in nc:
                print(note)

    The `with` block receives a plain list, so len(), indexing (nc[0]),
    and multiple passes all work without rewinding a generator.

    First-run behaviour
    ───────────────────
    If the note file doesn't exist yet, NoteContext prints a friendly ASCII
    cat, creates the empty file, and exits with code 1 — prompting the user
    to try again.  On the second run the file exists and everything proceeds
    normally.  The cat wanted a warm place to sleep before it started taking
    notes.
    """

    # ASCII cat shown on first-run file creation (credit: felix lee)
    NEWCAT = r"""-------------------------------------
     ("`-/")_.-'"``-._
      . . `; -._    )-;-,_`)
     (v_,)'  _  )`-.\  ``-'
    _.- _..-_/ / ((.'
  ((,.-'   ((,/
   ((,-'    ((,|
"""

    def __init__(self, notefile, search_criteria):
        """Store the file path and search criteria for use in __enter__.

        Args:
            notefile:        path to the .catjot note file.
            search_criteria: (SearchType, value) tuple, or list of tuples,
                             or an empty list (yields zero results).
        """
        self.notefile = notefile
        self.criteria = search_criteria

    def __enter__(self):
        """Execute the search and return the result as a list.

        Returns:
            list of Note objects matching self.criteria.

        Side effects on error:
            FileNotFoundError → prints ASCII cat, creates the file, sys.exit(1)
            ValueError        → prints type-mismatch message, sys.exit(3)
        """
        import sys

        try:
            return list(Note.match(self.notefile, self.criteria))
        except FileNotFoundError:
            print(f"Waking up the cat at {self.notefile}. Now, try again.")
            for line in self.NEWCAT.split("\n")[0:-2]:
                print(line)
            open(self.notefile, "a").close()
            sys.exit(1)
        except ValueError:
            print(
                f"Value provided does not match expected type, like having a character in an int."
            )
            sys.exit(3)

    def __exit__(self, exc_type, exc_value, traceback):
        """No cleanup needed — the list was fully materialised in __enter__."""
        pass


class catjot_graphql(object):
    """Optional GraphQL interface over the catjot note file.

    Exposes the same Note fields available through Note.match() as a GraphQL
    query endpoint.  Useful when you want to drive catjot from tooling that
    speaks GraphQL (dashboards, notebooks, external scripts).

    All filtering runs in O(n) time over the note file — there is no index.
    For interactive use this is fine; for very large note files consider
    batching queries or pre-filtering with Note.match() directly.

    Quickstart:
        gql = catjot_graphql()
        result = gql.execute_query({"pwdtree": "/home/user/project"})
        for note in result.data["notes"]:
            print(note["message"])

    The default query (QUERY) returns all five note fields.  Pass a custom
    query string to execute_query() if you only need a subset.
    """

    # Default GraphQL query — returns all five note fields.
    # Use as a template; narrow the field selection if you only need a subset.
    QUERY = """
    query ($pwd: String, $now: Int, $tag: [String], $context: String, $message: String, $pwdtree: String, $logic: String) {
      notes(pwd: $pwd, now: $now, tag: $tag, context: $context, message: $message, pwdtree: $pwdtree, logic: $logic) {
        pwd
        now
        tag
        context
        message
      }
    }
    """

    def __init__(self, notefile=Note.NOTEFILE):
        self.schema = self._create_schema()
        self.NOTEFILE = notefile

    def _create_schema(self):
        """Build and return the GraphQL schema for the Note type.

        Defines one root query field ("notes") that accepts the same filter
        arguments as Note.match() and delegates to resolve_notes().  The
        schema is constructed once in __init__ and reused across queries.
        """
        from graphql import (
            graphql_sync,
            GraphQLSchema,
            GraphQLObjectType,
            GraphQLList,
            GraphQLField,
            GraphQLInt,
            GraphQLString,
        )

        # Define the GraphQL schema for the Note type
        NoteType = GraphQLObjectType(
            name="Note",
            fields={
                "pwd": GraphQLField(GraphQLString),
                "now": GraphQLField(GraphQLInt),
                "tag": GraphQLField(GraphQLString),
                "context": GraphQLField(GraphQLString),
                "message": GraphQLField(GraphQLString),
            },
        )

        # Update the root query type to allow filtering by context
        QueryType = GraphQLObjectType(
            name="Query",
            fields={
                "notes": GraphQLField(
                    GraphQLList(NoteType),  # The query returns a list of Note objects
                    args={  # Query arguments for filtering
                        "pwd": GraphQLString,
                        "now": GraphQLInt,
                        "tag": GraphQLList(GraphQLString),
                        "context": GraphQLString,
                        "message": GraphQLString,
                        "pwdtree": GraphQLString,
                        "logic": GraphQLString,
                    },
                    resolve=self.resolve_notes,
                ),
            },
        )

        return GraphQLSchema(query=QueryType)

    def execute_query(self, variables, query=QUERY):
        """Execute a GraphQL query against the note file.

        Args:
            variables: dict of query variables, e.g.:
                {"tag": ["project", "urgent"], "logic": "and"}
                {"pwdtree": "/home/user/project"}
                {"message": "deployment", "context": "prod"}
            query: GraphQL query string; defaults to QUERY (returns all fields).

        Returns:
            graphql.ExecutionResult with a .data dict and optional .errors list.

        Examples:
            from pprint import pprint
            gql = catjot_graphql()

            # AND logic: notes tagged "predator" AND "kitten" mentioning "meow"
            pprint(gql.execute_query({
                "tag": ["predator", "kitten"],
                "message": "meow",
                "logic": "and",
            }).data)

            # All notes under any path
            pprint(gql.execute_query({"pwdtree": "/"}).data)
        """
        from graphql import parse, execute_sync

        parsed_query = parse(query)
        result = execute_sync(self.schema, parsed_query, variable_values=variables)
        return result

    def resolve_notes(
        self,
        _,
        info,
        pwd=None,
        now=None,
        tag=None,
        context=None,
        message=None,
        pwdtree=None,
        logic="or",
    ):
        """GraphQL resolver: translate query args into Note.match() criteria.

        Each non-None argument becomes a (SearchType, value) tuple in the
        criteria list.  `tag` may be a list of strings — each becomes its
        own TAG criterion (combined with the chosen logic).  String fields
        use case-insensitive search (CONTEXT_I, MESSAGE_I).

        Args:
            _:       root value (unused, required by graphql-core signature).
            info:    resolver context (unused).
            pwd:     exact directory match.
            now:     exact timestamp match.
            tag:     single tag string or list of tag strings.
            context: case-insensitive context substring.
            message: case-insensitive message substring.
            pwdtree: directory prefix match (note and all children).
            logic:   "or" (default) or "and".

        Returns:
            list of Note objects satisfying the criteria.
        """

        criteria = []

        if pwd:
            criteria.append((SearchType.DIRECTORY, pwd))

        if pwdtree:
            criteria.append((SearchType.TREE, pwdtree))

        if now:
            criteria.append((SearchType.TIMESTAMP, now))

        if tag:
            if isinstance(tag, list):
                for i in tag:
                    criteria.append((SearchType.TAG, i))
            else:
                criteria.append((SearchType.TAG, tag))

        if context:
            criteria.append((SearchType.CONTEXT_I, context))

        if message:
            criteria.append((SearchType.MESSAGE_I, message))

        return list(Note.match(self.NOTEFILE, criteria, logic))


# END: CLASSES
# START: LLM/MCP FUNCTIONS


def call_llm(
    messages, tools=None, temperature=0.2, tool_choice="auto", max_tokens=None
):
    """Fire a single chat-completion request at the configured LLM endpoint.

    Reads credentials and model from environment variables:
        openai_api_key   – Bearer token sent in the Authorization header.
        openai_api_url   – Full completion endpoint URL.
        openai_api_model – Model identifier string.

    When *tools* is provided the payload gains ``tools`` and ``tool_choice``
    fields so the LLM can request function calls.  Some provider implementations
    attach ``tool_calls`` at the choice level rather than inside the message
    dict; this function normalises that quirk before returning.

    *max_tokens* caps the output budget for the call.  Pass it to prevent
    thinking-heavy models from consuming the full output allowance on internal
    reasoning before producing any prose or tool call.

    Returns the ``message`` dict from ``choices[0]``.  Raises
    ``requests.HTTPError`` on a non-2xx response.
    """
    api_key = getenv("openai_api_key")
    api_url = getenv("openai_api_url")
    api_model = getenv("openai_api_model")

    payload = {
        "model": api_model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    resp = requests.post(
        api_url,
        headers=headers,
        json=payload,
    )
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    msg = choice["message"]

    # Some servers put tool_calls at the choice level, not inside message
    if "tool_calls" not in msg and "tool_calls" in choice:
        msg["tool_calls"] = choice["tool_calls"]

    return msg


# Maps tool name -> handler function
TOOL_HANDLERS = {}

# Maps tool name -> OpenAI-style tool schema (for the LLM)
TOOL_SCHEMAS = []


def register_tool(name, description, parameters, handler):
    """Register a tool so the LLM can call it.

    name:        string identifier
    description: what the tool does (shown to LLM)
    parameters:  JSON Schema dict for the tool's arguments
    handler:     callable(kwargs) -> str  (returns result as string)
    """
    TOOL_HANDLERS[name] = handler
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }
    for i, existing in enumerate(TOOL_SCHEMAS):
        if existing["function"]["name"] == name:
            TOOL_SCHEMAS[i] = schema
            return
    TOOL_SCHEMAS.append(schema)


def dispatch_tool_call(tool_name, arguments_json):
    """Look up a registered tool by name and invoke its handler.

    *arguments_json* may arrive as a raw JSON string (as the LLM sends it) or
    as an already-parsed dict — both forms are accepted.

    Returns the handler's string result, or an error string if the tool name
    is not found in ``TOOL_HANDLERS``.
    """
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return f"Error: unknown tool '{tool_name}'"

    if isinstance(arguments_json, str):
        args = json.loads(arguments_json)
    else:
        args = arguments_json

    return handler(**args)


# --- per-field search handlers, each returns a JSON list of Note.now IDs ---


def make_tag_search_handler():
    """Return a handler that searches notes by the *tag* field.

    The returned callable splits *query* on whitespace and runs an OR search
    against ``Note.NOTEFILE`` for each word.  It collects the ``note.now``
    timestamps of every matching note (deduplicating order-preservingly) and
    returns them as a JSON array string — the format expected by
    ``aggregate_note_ids``.

    Factory pattern used so the handler closes over nothing mutable and can
    be safely registered at import time via ``register_tool``.
    """

    def handler(query: str) -> str:
        seen = []
        for word in query.split():
            for note in Note.match(Note.NOTEFILE, [(SearchType.TAG, word)], logic="or"):
                if note.now not in seen:
                    seen.append(note.now)
        return json.dumps(seen)

    return handler


def make_context_search_handler():
    """Return a handler that searches notes by the *context* field (case-insensitive).

    Identical contract to ``make_tag_search_handler`` but targets
    ``SearchType.CONTEXT_I``.  Each word in *query* is searched independently
    and the union of matching note IDs is returned as a JSON array.
    """

    def handler(query: str) -> str:
        seen = []
        for word in query.split():
            for note in Note.match(
                Note.NOTEFILE, [(SearchType.CONTEXT_I, word)], logic="or"
            ):
                if note.now not in seen:
                    seen.append(note.now)
        return json.dumps(seen)

    return handler


def make_message_search_handler():
    """Return a handler that searches notes by the *message* body (case-insensitive).

    Identical contract to ``make_tag_search_handler`` but targets
    ``SearchType.MESSAGE_I``.  The message body is typically the longest field
    in a note, so queries here cover the bulk of free-form note content.
    """

    def handler(query: str) -> str:
        seen = []
        for word in query.split():
            for note in Note.match(
                Note.NOTEFILE, [(SearchType.MESSAGE_I, word)], logic="or"
            ):
                if note.now not in seen:
                    seen.append(note.now)
        return json.dumps(seen)

    return handler


def make_directory_search_handler():
    """Return a handler that searches notes by the *directory* / pwd field.

    Identical contract to ``make_tag_search_handler`` but targets
    ``SearchType.DIRECTORY``.  Useful when the user query names a path
    fragment like ``/home/user/projects/catjot``.
    """

    def handler(query: str) -> str:
        seen = []
        for word in query.split():
            for note in Note.match(
                Note.NOTEFILE, [(SearchType.DIRECTORY, word)], logic="or"
            ):
                if note.now not in seen:
                    seen.append(note.now)
        return json.dumps(seen)

    return handler


# --- shared parameter schema for all four search tools ---

_SEARCH_PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Space-separated search terms to look up in this field.",
        }
    },
    "required": ["query"],
}


def register_search_tools():
    """Register the four field-search tools used by ``jot llm``.

    Called lazily from ``run_tool_loop`` so simply importing this module
    does not mutate the global tool registry.  Idempotent — calling it
    repeatedly only updates the entries already present.
    """
    register_tool(
        name="search_by_tag",
        description=(
            "Search catjot notes by the tag field. "
            "Returns a JSON list of note IDs (Note.now values)."
        ),
        parameters=_SEARCH_PARAM_SCHEMA,
        handler=make_tag_search_handler(),
    )
    register_tool(
        name="search_by_context",
        description=(
            "Search catjot notes by the context field (case-insensitive). "
            "Returns a JSON list of note IDs (Note.now values)."
        ),
        parameters=_SEARCH_PARAM_SCHEMA,
        handler=make_context_search_handler(),
    )
    register_tool(
        name="search_by_message",
        description=(
            "Search catjot notes by the message body (case-insensitive). "
            "Returns a JSON list of note IDs (Note.now values)."
        ),
        parameters=_SEARCH_PARAM_SCHEMA,
        handler=make_message_search_handler(),
    )
    register_tool(
        name="search_by_directory",
        description=(
            "Search catjot notes by the directory/pwd field. "
            "Returns a JSON list of note IDs (Note.now values)."
        ),
        parameters=_SEARCH_PARAM_SCHEMA,
        handler=make_directory_search_handler(),
    )


# --- aggregation helpers ---


def aggregate_note_ids(messages):
    """Walk the full message history and union every tool-result ID list.

    Each search-tool handler returns a JSON array of ``Note.now`` timestamps.
    After the LLM has called all four tools, this function scrapes those arrays
    out of the ``role=tool`` messages and unions them into one set — the
    complete candidate pool for the final answer pass.

    Non-JSON or non-list tool results are silently skipped (e.g., error strings
    from ``dispatch_tool_call``).

    Returns a set of integer note IDs.
    """
    all_ids = set()
    for msg in messages:
        if msg.get("role") == "tool":
            try:
                ids = json.loads(msg["content"])
                if isinstance(ids, list):
                    all_ids.update(ids)
            except (json.JSONDecodeError, TypeError):
                pass
    return all_ids


def fetch_notes_by_ids(note_ids):
    """Hydrate a set of note IDs into full note dicts for the final LLM pass.

    After ``aggregate_note_ids`` builds the candidate set, this function reads
    through ``Note.NOTEFILE`` once via ``NoteContext`` and plucks out every note
    whose ``note.now`` timestamp is in *note_ids*.

    Each result dict contains: ``now``, ``tag``, ``context``, ``directory``,
    and ``message`` — everything the LLM needs to write a grounded summary.

    Returns a list of dicts (order follows the on-disk note order).
    """
    results = []
    with NoteContext(Note.NOTEFILE, (SearchType.ALL, "")) as nc:
        for note in nc:
            if note.now in note_ids:
                results.append(
                    {
                        "now": note.now,
                        "tag": note.tag,
                        "context": note.context,
                        "directory": note.pwd,
                        "message": note.message,
                    }
                )
        return results


def run_tool_loop(user_query, max_iterations=10):
    """Drive the agentic search-and-answer loop for ``jot llm``.

    The loop enforces a protocol:

    1. The system prompt instructs the LLM to call *all four* search tools
       (tag, context, message, directory) before drawing conclusions.
    2. Each iteration calls ``call_llm`` with the current message history and
       the full ``TOOL_SCHEMAS`` list.
    3. If the response contains ``tool_calls``, each call is dispatched via
       ``dispatch_tool_call`` and the result is appended as a ``role=tool``
       message.
    4. After every iteration the set of tool names called so far is compared
       against ``REQUIRED_TOOLS``.  Once all four have fired, the loop exits the
       search phase: ``aggregate_note_ids`` + ``fetch_notes_by_ids`` hydrate the
       matched notes, a final user turn presents the full note data, and one last
       ``call_llm`` call (no tools) produces the human-readable summary.
    5. If the LLM stops calling tools before all four have run it has broken
       protocol — the loop returns whatever it said verbatim.
    6. If ``max_iterations`` is exhausted without completing, a polite failure
       string is returned instead of raising.

    The ``openai_api_sysrole`` environment variable is appended to the system
    prompt so operators can inject persona or constraint instructions.

    Returns a plain-text answer string suitable for ``print_ascii_cat_with_text``.
    """
    register_search_tools()
    CATGPT_ROLE = getenv("openai_api_sysrole", "")

    system_prompt = (
        "You are a helpful cat assistant with access to a notetaking system called catjot. "
        "To answer the user's query you MUST call ALL FOUR of the following search tools "
        "before drawing any conclusions:\n"
        "  1. search_by_tag       - searches the tag field\n"
        "  2. search_by_context   - searches the context field\n"
        "  3. search_by_message   - searches the message body\n"
        "  4. search_by_directory - searches the directory/pwd field\n"
        "Each tool returns a JSON list of note IDs. Call all four tools using the relevant "
        "search terms extracted from the user query. Do not skip any tool, even if you think "
        "it is unlikely to return results. "
        "Once all four tools have been called, stop making tool calls and wait. "
        "The system will aggregate the results and provide you with the full matching notes "
        "for your final summary.\n"
        f"{CATGPT_ROLE}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query},
    ]

    REQUIRED_TOOLS = {
        "search_by_tag",
        "search_by_context",
        "search_by_message",
        "search_by_directory",
    }

    for i in range(max_iterations):
        response_msg = call_llm(messages, tools=TOOL_SCHEMAS, tool_choice="auto")

        tool_calls = response_msg.get("tool_calls")
        if not tool_calls:
            # LLM stopped calling tools before all four were used - return whatever it said
            return response_msg.get("content", "")

        # Append the assistant message (with tool_calls) to history
        messages.append(response_msg)

        # Execute each tool call and append results
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            fn_args = tc["function"]["arguments"]
            tool_id = tc.get("id", fn_name)

            print(f"  [step {i+1}] calling tool: {fn_name}")

            result = dispatch_tool_call(fn_name, fn_args)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": str(result),
                }
            )

        # Determine which tool names have been called across the full history
        tool_names_called = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    tool_names_called.add(tc["function"]["name"])

        if REQUIRED_TOOLS.issubset(tool_names_called):
            # All four tools have reported - aggregate IDs and do final LLM pass
            note_ids = aggregate_note_ids(messages)
            notes = fetch_notes_by_ids(note_ids)

            messages.append(
                {
                    "role": "user",
                    "content": (
                        "All four searches are complete. "
                        "Here are the matching notes found across all search fields:\n"
                        + json.dumps(notes, indent=2)
                        + "\n\nPlease provide a final summary of these notes that is "
                        "relevant to the original query, noting which fields each match "
                        "came from where useful."
                    ),
                }
            )

            final_msg = call_llm(messages, tools=None)
            return final_msg.get("content", "")

    return "Max iterations reached without a final answer."


# END: LLM/MPC FUNCTIONS
# START: LAST REMAINING UNSORTED FUNCTIONS


def send_prompt_to_endpoint(messages, model_name, mode):
    """Send a chat-completion request to an OpenAI-compatible endpoint.

    Reads ``openai_api_key``, ``openai_api_url``, and ``openai_api_model`` from
    the environment.  If *model_name* is non-empty it overrides the env model
    (i.e. ``jot -m gpt-4o chat …`` wins).  The Authorization header is omitted
    when ``openai_api_key`` is falsy — handy for local models that need no key.

    *mode* controls the response strategy:

    ``"full"``
        Waits for the complete response and returns the raw JSON dict from the
        endpoint (the same ``response.json()`` you'd get from the API directly).
        Returns ``None`` and prints an error on network failure.

    ``"stream"``
        Sets ``stream: True`` in the request body and returns a **generator**
        that yields one character at a time as SSE ``data:`` lines arrive.
        Callers iterate the generator and print each character with ``flush=True``
        for a typewriter effect.  On network failure the generator yields
        ``"[Error]"`` rather than raising.
    """
    api_key = getenv("openai_api_key")
    api_url = getenv("openai_api_url")
    api_model = getenv("openai_api_model")
    if model_name:  # -m MODEL shall take precedence
        api_model = model_name
    headers = {"Content-Type": "application/json"}

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if mode == "full":
        data = {"model": api_model, "messages": messages}

        try:
            response = requests.post(api_url, headers=headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error sending request: {e}")
            return None
    elif mode == "stream":
        data = {
            "model": api_model,
            "messages": messages,
            "stream": True,  # Enable streaming
        }

        def stream_response():
            try:
                with requests.post(
                    api_url, headers=headers, json=data, stream=True
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if line:  # Filter out keep-alive new lines
                            decoded_line = line.decode("utf-8")
                            if decoded_line.startswith("data: "):
                                decoded_line = decoded_line[
                                    6:
                                ]  # Remove the "data: " prefix
                                if decoded_line != "[DONE]":
                                    try:
                                        content = json.loads(decoded_line)
                                        if "choices" in content:
                                            text = content["choices"][0]["delta"].get(
                                                "content", ""
                                            )
                                            for char in text:
                                                yield char
                                    except json.JSONDecodeError:
                                        print("Error decoding JSON:", decoded_line)
            except requests.exceptions.RequestException as e:
                print(f"Error sending request: {e}")
                yield "[Error]"  # Yield an error message in case of a failure

        return stream_response()  # Return the generator for streaming


def return_footer(gpt_reply):
    """Format a one-line footer string from a completed GPT response dict.

    Extracts ``finish_reason`` and ``model`` from the standard OpenAI response
    shape and returns them as a compact label — used as the *endtext* argument
    to ``print_ascii_cat_with_text`` so the user knows why generation stopped
    and which model was used.

    Example output: ``"stop. model=gpt-4o"``
    """
    finish_reason = gpt_reply["choices"][0].get("finish_reason", "stop")
    return f"{finish_reason}. model={gpt_reply['model']}"


def is_binary_string(data):
    """
    Determine if a string is binary or text by checking for non-text characters.

    Treats any printable Unicode character as text — only control characters
    (other than common whitespace) count as binary.  This keeps non-ASCII
    content like emoji and non-Latin scripts from being misclassified.

    :param data: A string to be checked
    :return: True if the string is binary, False if it is text
    """
    if not data:
        return False

    if "\x00" in data:
        return True

    allowed_whitespace = "\n\r\t\b"
    non_text_count = sum(
        1 for ch in data if ch not in allowed_whitespace and not ch.isprintable()
    )

    return non_text_count / len(data) > 0.3


def print_ascii_cat_with_text(
    intro, text, endtext="stop.", intro_color=OutputColors.CHAT_CAT
):
    """Render the signature ASCII cat alongside a prompt and response.

    Layout::

         /\\_/\\          <intro line 1>
        ( o.o )          <intro line 2 …>
         > ^ <

    *intro* is word-wrapped to 80 columns and printed line-by-line next to the
    cat art.  *text* follows on its own lines below (not wrapped — callers are
    responsible for pre-formatting).  *endtext* is printed last, coloured with
    ``OutputColors.CHAT_END`` so it visually marks the end of a response block.

    When ``Note.USE_COLORIZATION`` is False all ANSI colour codes are suppressed
    and the output remains clean for piping or dumb terminals.

    The *intro_color* parameter lets callers tint the intro text differently —
    ``OutputColors.CHAT_ME`` is used to distinguish the user's own words from the
    cat's words when the two colours differ.
    """
    import textwrap

    cat = r""" /\_/\
( o.o )
 > ^ <
"""
    wrapped_text = textwrap.wrap(intro, 80)

    # Print the ASCII cat and the wrapped text side by side
    cat_lines = cat.split("\n")
    for i in range(max(len(cat_lines), len(wrapped_text))):
        cat_line = cat_lines[i] if i < len(cat_lines) else " " * 8
        text_line = wrapped_text[i] if i < len(wrapped_text) else ""
        if Note.USE_COLORIZATION:
            print(
                f"{OutputColors.IMG_CAT.value}{cat_line:<8} {intro_color.value}{text_line}{AnsiColor.RESET.value}"
            )
        else:
            print(f"{cat_line:<8} {text_line}")

    print(text)
    print(f"{OutputColors.CHAT_END.value}{endtext}{AnsiColor.RESET.value}")


# END: LAST REMAINING UNSORTED FUNCTIONS
# START: COMMAND LINE RUNTIME CODE


def main():
    """CLI entry point — parse arguments and dispatch to the appropriate action.

    **Dispatch hierarchy**

    1. **Flag-only paths** (argparse short flags `-a`, `-c`, `-t`, `-p`) are
       resolved first.  Combining `-a` with at least one of ``-c/-t/-p`` means
       *amend* the most recent note's metadata.  Flags alone (without ``-a``)
       search notes by that field (context / tag / pwd).  Piping to any of
       these flags writes a new note instead of searching.

    2. **SHORTCUTS dict** maps canonical action names to their accepted keyword
       aliases.  All keyword matching is done against ``args.additional_args``
       (positional arguments after the flags).  The dict lives in the
       USER-EDITABLE AREA block so power users can remap words without touching
       logic.

    3. **Arity branching** — after IS_CHAT / IS_CONVO are resolved, the code
       branches on ``len(args.additional_args)``:

       - 0 args, interactive → show notes for current pwd + summary counts.
       - 0 args, piped → create a new note from stdin.
       - 1 arg → single-keyword shortcuts (head, last, pop, dump, home, zzz,
         scoop, stray, graphql, newsr, sr, llm, …).
       - 2 args → two-word shortcuts (match, search, ts, remove, tag, payload,
         sbs, head+N, last+N, …).

    **Key helper closures** (defined inside main):

    - ``flatten(arg_lst)``   – joins positional args with spaces (for queries).
    - ``flatten_pipe(arg_lst)`` – joins lines from stdin preserving newlines.
    - ``printout(note_obj, message_only, time_only)`` – formats a single note
      for display according to active flags.

    **Environment variable override**: if ``CATJOT_FILE`` is set and non-empty
    it replaces ``Note.NOTEFILE`` for this invocation (file is touch-created if
    it doesn't yet exist).

    **LLM subcommands** (``jot chat``, ``jot convo``, ``jot llm``):
    - ``chat``  – one-shot: build a single-turn messages list, call
      ``send_prompt_to_endpoint``, save the reply as a new note.
    - ``convo`` – multi-turn loop: accumulates user + assistant turns across
      Control-D-delimited blocks; supports ``continue`` (reload tag history),
      ``summarize`` (compress + carry over), and ``cat``/``catenate`` (merge
      multiple tag chains).
    - ``llm``   – agentic: invokes ``run_tool_loop`` which calls all four search
      tools before producing a grounded answer.

    Exits with a non-zero status code on user-facing errors (missing note,
    invalid input, insufficient terminal width, binary stdin, etc.).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="cat|jot notetaker",
        epilog="additional usage examples-\n"
        "the abbreviated and (parenthesized) forms are both acceptable:\n\n"
        "  jot c 16952...   (catgpt)/send note matching timestamp to openai endpoint.\n"
        "  jot cat a1 b2 .. (catenate) all notes matching each of the provided tags to a convo\n"
        "  jot d            (dump)/show all notes from all time, everywhere\n"
        "  jot h            show note (head)--show the last 1 note written, among all notes\n"
        "  jot h 3          show note (head)--show the last n notes written, among all notes\n"
        "  jot h ~3         show note (head)--show n-th from last note, among all notes\n"
        "  jot home         show (home)notes (shorthand to your home dir, like a catch-all)\n"
        "  jot l            show (last) written note from this directory only\n"
        "  jot l 3          show (last) n written notes from this directory only\n"
        "  jot l ~3         show n-th to (last) written note, from this directory only\n"
        "  jot m Milo       (match) case-sensitive <term> within message payload\n"
        "  jot p            (pop)/delete the last-written note in this pwd\n"
        "  jot pl           show last-written note, message (payload) only, omitting headers\n"
        "  jot pl 16952...  show note matching timestamp/s, concatenated, message (payload) only\n\n"
        "  jot r 16952...   (remove) note/s matching timestamp value\n"
        "  jot s tabby      (search) case-insensitive <term> within message payload\n"
        "  jot scoop        (scoop) list all notes in $EDITOR, allowing bulk deleting of records\n"
        "  jot stray        display all (strays) which are notes whose pwd no longer exist in this filesystem\n"
        "  jot ts 16952...  search all notes, filtering by (timestamp)\n\n"
        "  jot chat xxxx    (chat) with catgpt, sending a single line/jot/pipe to an openai api endpoint.\n"
        "                   you can also pipe to it: `cat myfile | jot chat summarize this for me`\n"
        "  jot convo        (convo) have an extended conversation, where each user prompt and gpt reply\n"
        "                   is accumulated and resubmitted as complete context.\n"
        "                   you can also continue a conversation using the tagging system:\n"
        "  jot -t convo-1695220591 continue\n"
        "                   'continue'/start a new convo with the all notes matching <tag> supplied as context.\n"
        "  jot -t convo-1695220591 continue 1695220600\n"
        "                   'continue'/start a new convo using the tag chain including up and until <timestamp>\n"
        "  jot zzz          take a nap with a kitten...\n"
        "  jot sbs 16952..  side-by-side transcription practice mode\n"
        "  jot t friendly   search all notes, filtering by (tag), case-sensitive\n"
        "  jot newsr        create a new note designed for spaced repetition practice\n"
        "  jot sr           iterate through all scheduled (sr) spaced repetition notes\n"
        "  jot llm          talk to a cat naturally to find information\n",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-a", action="store_true", help="amend last note instead of creating new note"
    )
    parser.add_argument(
        "-t", type=str, help="search notes by tag / set tag when amending"
    )
    parser.add_argument(
        "-p", type=str, help="search notes by pwd / set pwd when amending"
    )
    parser.add_argument("-m", type=str, default="", help="LLM model to engage")
    parser.add_argument(
        "-w", action="store_true", help="wall-of-text rather than stream"
    )
    parser.add_argument(
        "-c",
        action="store_const",
        const="context",
        help="search notes by context / read pipe into context as amendment",
    )
    parser.add_argument("additional_args", nargs="*", help="argument values")
    parser.add_argument(
        "-d", action="store_true", help="only return (date)/timestamps for match"
    )

    args = parser.parse_args()

    NOTEFILE = Note.NOTEFILE
    import sys

    if "CATJOT_FILE" in environ:
        # the environment variable will always supercede $HOME default when set
        if environ["CATJOT_FILE"]:  # truthy test for env that exists but unset
            NOTEFILE = environ["CATJOT_FILE"]
            with open(NOTEFILE, "a") as file:
                pass

    # helper variables for all CLI handling

    def flatten(arg_lst):
        return " ".join(arg_lst).rstrip()

    def flatten_pipe(arg_lst):
        return "".join(arg_lst).rstrip()

    def printout(note_obj, message_only=False, time_only=args.d):
        if message_only:
            print(note_obj.message, end="")
        elif time_only:
            print(note_obj.now)
        else:  # normal display
            print(Note.REC_TOP)
            print(note_obj, end="")
            print(Note.REC_BOT)

    params = {}
    if args.c:
        params["context"] = flatten(args.additional_args)
    if args.t:
        params["tag"] = args.t
    if args.p:
        params["pwd"] = args.p
    if args.a and (args.c or args.t or args.p):
        # amend engaged, and at least one amendable value provided
        # Accepted Usage:
        # jot -ac this is how i feel
        # jot -at personal_feelings
        # jot -ap /home/user
        # jot -ac "this is how i feel" -t "personal_feelings"
        # jot -ac "this is how i feel" -t "personal_feelings" -p /home/user

        if sys.stdin.isatty():  # interactive tty, not a pipe!
            # accept all attrs to change and complete amendment
            Note.amend(NOTEFILE, **params)
            Note.commit(NOTEFILE)
        else:  # pipe always interpreted as context, never pwd/tag
            # Accepted Usage:
            # | jot -act "celebration_notes"
            # | jot -acp "/home/user"
            # piped data will be a ''.joined string, maintaining newlines
            piped_data = flatten_pipe(sys.stdin.readlines())
            params["context"] = piped_data  # overwrite anything in args
            Note.amend(NOTEFILE, **params)
            Note.commit(NOTEFILE)
        exit(0)  # end logic for amending
    # context-related functionality
    elif args.c:
        if sys.stdin.isatty():  # interactive tty, no pipe!
            # jot -c observations
            # not intending to amend instead means match by context field
            with NoteContext(NOTEFILE, (SearchType.CONTEXT_I, params["context"])) as nc:
                for inst in nc:
                    printout(inst)
        else:  # yes pipe!
            # | jot -c musings
            # write new note with provided context from arg
            piped_data = flatten_pipe(sys.stdin.readlines())
            Note.append(NOTEFILE, Note.jot(piped_data, **params))
    # tagging-related functionality
    elif args.t and not set(args.additional_args) & set(
        ["continue", "sum", "summary", "summarize", "convo", "chat", "scoop"]
    ):
        # special case "summarize" implies convo
        # and should continue to the SHORTCUTS below
        # with no actions necessary here
        # also special case "continue" allows dropping past this specific elif
        # and allows evaluation below for IS_CONVO
        # continue only works with [tags]
        if sys.stdin.isatty():  # interactive tty, no pipe!
            # jot -t project2
            # not intending to amend instead means match by tag field
            with NoteContext(NOTEFILE, (SearchType.TAG, params["tag"])) as nc:
                for inst in nc:
                    printout(inst)
        else:  # yes pipe!
            # | jot -t project4
            # new note with tag set to [project4]
            piped_data = flatten_pipe(sys.stdin.readlines())
            Note.append(NOTEFILE, Note.jot(piped_data, **params))
    # pwd-related functionality
    elif args.p:
        if sys.stdin.isatty():  # interactive tty, no pipe!
            # jot -p /home/user
            # not intending to amend instead means match by pwd field
            with NoteContext(NOTEFILE, (SearchType.DIRECTORY, params["pwd"])) as nc:
                for inst in nc:
                    printout(inst)
        else:  # yes pipe!
            piped_data = flatten_pipe(sys.stdin.readlines())
            Note.append(NOTEFILE, Note.jot(piped_data, **params))
    else:
        # for all other cases where no argparse argument is provided
        # START: USER-EDITABLE AREA
        # this section can be freely customized for all strings
        # in the right-hand side lists.
        SHORTCUTS = {
            "MOST_RECENTLY_WRITTEN_ALLTIME": ["HEAD", "head", "h"],
            "MOST_RECENTLY_WRITTEN_HERE": ["last", "l"],
            "MATCH_NOTE_NAIVE": ["match", "m"],
            "MATCH_NOTE_NAIVE_I": ["search", "s", "mi"],
            "DELETE_MOST_RECENT_PWD": ["pop", "p"],
            "BULK_MANAGE_NOTES": ["scoop", "cherry-pick"],
            "NOTES_REFERENCING_ABSENT_DIRS": ["str", "stra", "stray", "strays"],
            "SHOW_ALL": ["dump", "display", "d"],
            "MATCH_TIMESTAMP": ["timestamp", "ts"],
            "REMOVE_BY_TIMESTAMP": ["remove", "r"],
            "HOMENOTES": ["home"],
            "SHOW_TAG": ["tagged", "tag", "t"],
            "AMEND": ["amend", "a"],
            "MESSAGE_ONLY": ["payload", "pl"],
            "SIDE_BY_SIDE": ["sidebyside", "sbs", "rewrite", "transcribe"],
            "SLEEPING_CAT": ["zzz"],
            "GRAPHQL": ["graph", "graphql", "ql"],
            "CREATE_SPACED_REPETITION": ["newsr"],
            "ITERATE_SPACED_REPETITIONS": ["sr"],
            "CHAT": ["chat", "catgpt", "c"],
            "LLM": ["llm"],
            "CONVO": [
                "cat",
                "catenate",
                "convo",
                "continue",
                "talk",
                "sum",
                "summary",
                "summarize",
            ],
        }

        # Default prompt to start jot chat & jot convo with
        from os import getenv

        CATGPT_ROLE = getenv(
            "openai_api_sysrole",
            """You're proudly a cat assistant here to help the user in any way you can.""",
        )
        # END: USER-EDITABLE AREA

        IS_CHAT = False
        IS_CONVO = False  # continuing conversation mode
        try:
            IS_CHAT = args.additional_args[0] in SHORTCUTS["CHAT"]
            IS_CONVO = args.additional_args[0] in SHORTCUTS["CONVO"]
        except IndexError:  # because no args were provided
            pass

        if IS_CHAT:
            # gpt-related functionality
            # INTRO AND TEXT are both are sent, in that order, to the GPT prompt.
            # ChatGPT recommends asking the query (intro) before providing the data (message)
            #
            # 1- jot chat
            #               intro=<usertyped>,
            #               text=,
            # 2- jot chat 1719967764
            #               intro=,
            #               text=note,
            # 3- jot chat 1719967764 what is this about?
            #               intro='what is this about?',
            #               text=note,
            # 4- jot chat when is national take your cat to work day?
            #               intro='when is...',
            #               text=,
            # 5- echo "tell me about national cat day" | jot chat
            #               intro='tell me about...',
            #               text=,
            # 6- echo "tell me about this file" | jot chat 1719967764
            #               intro='tell me about this file',
            #               text=note,
            # 7- cat requests | jot chat
            #               intro=requests
            #               text=,
            # 8- cat requests | jot chat 1719967764
            #               intro=requests,
            #               text=note,
            # 9- cat broken.jot | jot chat how many notes are there?
            #               intro='how many notes...',
            #               text=broken.jot,
            # 10- cat broken.jot | jot chat 1719967764 how many notes are there?
            #               intro='how many notes...',
            #               text=note+broken.jot,

            intro = ""
            txt = ""

            all_args = list(args.additional_args)
            all_args.pop(0)
            timestamp_tgt = all_args[0] if all_args else ""

            if timestamp_tgt.isdigit():
                all_args.pop(0)
                with NoteContext(
                    NOTEFILE, (SearchType.TIMESTAMP, int(timestamp_tgt))
                ) as nc:
                    txt = f"\n\n".join([str(inst) for inst in nc])
            intro = " ".join(all_args)  # join together everything after 'chat'

            if sys.stdin.isatty():  # interactive tty, no pipe!
                if (
                    timestamp_tgt.isdigit() and not txt
                ):  # requested note but wasnt found
                    print_ascii_cat_with_text(
                        "Uh oh, you requested a timestamp with no matching note. "
                        "Try another timestamp and try again.",
                        "",
                    )
                    sys.exit(1)
                elif not txt and not intro:
                    intro = flatten_pipe(sys.stdin.readlines())
            else:  # not interactive tty, all pipe!
                # routes 5,6,7,8,9,10: fill in the blank, pref intro
                if not txt and not intro:
                    intro = flatten_pipe(sys.stdin.readlines())
                elif txt and not intro:
                    intro = flatten_pipe(sys.stdin.readlines())
                elif not txt and intro:
                    txt = flatten_pipe(sys.stdin.readlines())
                elif txt and intro:
                    txt_append = flatten_pipe(sys.stdin.readlines())
                    txt = (
                        "### FILE 1 ###"
                        + "\n\n"
                        + txt
                        + "\n\n### FILE 2 ###\n\n"
                        + txt_append
                    )

            params["tag"] = params.get("tag", "catgpt")

            full_sendout = f"{intro}\n\n{txt}"

            if len(args.additional_args) and args.additional_args[0] in ["home"]:
                params["pwd"] = environ["HOME"]

            if is_binary_string(full_sendout):
                print_ascii_cat_with_text(
                    "Uh oh, the pipe I received seems to be binary data but -gpt accepts only text. "
                    "Try another file that is text-based, instead.",
                    "",
                )
                sys.exit(1)
            elif len(full_sendout.encode("utf-8")) > 512000:
                # 512000 bytes is an estimation of 128000  * 4 bytes
                # a real tokenizer might be better, but this approximation
                # should be sufficient and cost-proTECtive ($0.15/million tokens)
                print_ascii_cat_with_text(
                    "Uh oh, the pipe I received seems to have too much data. "
                    f"It has exceeded the 512000 character context limit (data size: {len(full_sendout)})",
                    "",
                )
                sys.exit(1)

            messages = [
                {
                    "role": "system",
                    "content": CATGPT_ROLE,
                },
                {
                    "role": "user",
                    "content": full_sendout,
                },
            ]

            response = ""
            if args.w:  # wall of text preferred
                response = send_prompt_to_endpoint(
                    messages, model_name=args.m, mode="full"
                )

                if response:
                    retval = response["choices"][0]["message"]["content"]
                    endline = return_footer(response)
                    print_ascii_cat_with_text(
                        intro, retval, endline, intro_color=OutputColors.CHAT_ME
                    )
                    Note.append(NOTEFILE, Note.jot(retval, **params))
                else:
                    print("Failed to get response from OpenAI API.")
            else:
                import time

                print_ascii_cat_with_text(
                    intro, "", "", intro_color=OutputColors.CHAT_ME
                )

                response_generator = send_prompt_to_endpoint(
                    messages, model_name=args.m, mode="stream"
                )

                for char in response_generator:
                    print(char, end="", flush=True)
                    response += char
                    time.sleep(0.01)
                print()

                if response:
                    Note.append(NOTEFILE, Note.jot(response, **params))

                    if Note.USE_COLORIZATION:
                        print(
                            f"{OutputColors.CHAT_END.value}stop.{AnsiColor.RESET.value}"
                        )
                    else:
                        print(f"stop.")
                else:
                    print("Failed to get response from OpenAI API.")
        elif IS_CONVO:
            # gpt-related functionality
            # Starts a conversation with a GPT;
            # Conversations include yours and the GPTs conversations for RESUBMISSION BACK,
            # which means the context grows on every single submission. This can get expensive.
            # Take note of the prompt/output token counts.
            # Starting a new conversation will, naturally, reset this count.
            from time import time

            now = int(time())

            SYS_ROLE_TRIGGER = "SYSTEM:"
            if not set(args.additional_args) & set(
                ["sum", "summary", "summarize", "continue"]
            ):
                print_ascii_cat_with_text(
                    "Hi, what can I help you with today? ",
                    "Enter your prompt and hit Control-D to submit. \n"
                    + f"If you have pre-prompt instructions, start the line with '{SYS_ROLE_TRIGGER}'",
                )

            note_count = 0
            notable_notes = []  # first and last note for reference
            messages = []
            user_input = ""

            if "continue" in args.additional_args:
                provided_args = list(args.additional_args)
                provided_args.remove("continue")
                timestamp = (
                    int(provided_args[0])
                    if len(provided_args) and provided_args[0].isdigit()
                    else 0
                )

                if timestamp and params.get("tag", ""):
                    # INPUT: timestamp and a tag
                    # OUTPUT: select by tag, include up to timestamp, truncate after
                    # jot -t convo-1234567 continue 2345678
                    with NoteContext(NOTEFILE, (SearchType.TAG, params["tag"])) as nc:
                        value_matched = False
                        notable_notes.append(nc[0])
                        for inst in nc:
                            if timestamp:
                                if inst.now == timestamp:
                                    value_matched = True
                                    notable_notes.append(inst)
                                elif value_matched:
                                    break  # hits only after timestamp is hit AND all matching timestamps
                            messages.append({"role": "user", "content": inst.context})
                            messages.append(
                                {"role": "assistant", "content": inst.message}
                            )
                            note_count += 1
                elif not timestamp and params.get("tag", ""):
                    # INPUT: accept a tag and NOT a timestamp
                    # OUTPUT: select by tag
                    # jot -t convo-1234567 continue
                    with NoteContext(NOTEFILE, (SearchType.TAG, params["tag"])) as nc:
                        notable_notes.append(nc[0])
                        notable_notes.append(nc[-1])
                        for inst in nc:
                            messages.append({"role": "user", "content": inst.context})
                            messages.append(
                                {"role": "assistant", "content": inst.message}
                            )
                            timestamp = inst.now
                            note_count += 1
                elif timestamp and not params.get("tag", ""):
                    # INPUT: accepting a timestamp and NOT a tag
                    # OUTPUT: select by tag, include up to timestamp, truncate after
                    # jot continue 2345678
                    # determine tag based on timestamp
                    with NoteContext(NOTEFILE, (SearchType.TIMESTAMP, timestamp)) as nc:
                        for inst in nc:
                            if inst.now == timestamp:
                                params["tag"] = inst.tag
                                break

                    # now that we have a tag to work with
                    if not params.get("tag", ""):
                        print("No valid tag or timestamp provided, aborting...")
                        exit(1)
                    else:
                        with NoteContext(
                            NOTEFILE, (SearchType.TAG, params["tag"])
                        ) as nc:
                            value_matched = False
                            notable_notes.append(nc[0])
                            for inst in nc:
                                if inst.now == timestamp:
                                    notable_notes.append(inst)
                                    value_matched = True
                                elif value_matched:
                                    break  # hits only after timestamp is hit AND all matching timestamps
                                messages.append(
                                    {"role": "user", "content": inst.context}
                                )
                                messages.append(
                                    {"role": "assistant", "content": inst.message}
                                )
                                note_count += 1
                else:
                    print("No valid tag or timestamp provided, aborting...")
                    exit(1)

                composite_string = (
                    "timestamp | context (30)                 | message (30)\n"
                    "----------|------------------------------|------------------------------\n"
                )
                for nn in notable_notes:
                    composite_string += f"{nn.now}|{nn.context[:30].strip():<30}|{nn.message[:30].strip()}\n"

                print_ascii_cat_with_text(
                    f"{note_count} notes included as context from conversation chain: {params.get('tag', '')}",
                    composite_string,
                    "PROMPT:",
                )

            elif args.t and set(args.additional_args) & set(
                ["sum", "summary", "summarize"]
            ):
                previous = []
                previous.append(
                    {
                        "role": "system",
                        "content": "You are an AI designed to seamlessly summarize conversations. Your role is to capture and organize all key details, including names, events, and topics. Maintain the natural flow and tone of the dialogue, ensuring the summary feels integrated and continuous without breaking immersion. These summaries will serve as prompts for starting new chat conversations, ensuring a smooth transition.",
                    }
                )

                bundle_info = ""
                with NoteContext(NOTEFILE, (SearchType.TAG, params["tag"])) as nc:
                    if nc and nc[0]:
                        if nc[0].context.startswith(
                            "bundled notes"
                        ) or args.t.startswith("bundle-"):
                            bundle_info = [
                                int(x)
                                for x in nc[0].message.split("\n")
                                if x.strip().isdigit()
                            ]

                            contexts = ContextBundle(bundle_info[0])
                            for each_cont in bundle_info[1:]:
                                contexts += each_cont

                            for inst in contexts:  # iterate each note now
                                previous.append(
                                    {"role": "user", "content": inst.context}
                                )
                                previous.append(
                                    {"role": "assistant", "content": inst.message}
                                )
                        else:
                            for inst in nc:
                                # prefill messages with the user and assistant content previously written
                                previous.append(
                                    {"role": "user", "content": inst.context}
                                )
                                previous.append(
                                    {"role": "assistant", "content": inst.message}
                                )

                NUMBER_TO_CARRY_OVER = 10
                carryover_notes = previous[-NUMBER_TO_CARRY_OVER:]
                previous.append(
                    {
                        "role": "user",
                        "content": "Summarize the conversation so far, capturing every name along with key moments and details, while smoothly transitioning from the latest events. Maintain a consistent tone and natural flow, avoiding any impression of a summary or conclusion. Present the summary in a prompt format that allows the conversation to continue effortlessly. The summary should be at least half the length of the input. Ensure all characters are included, along with their relationships and feelings toward the user.",
                    }
                )

                response = send_prompt_to_endpoint(
                    previous, model_name=args.m, mode="full"
                )
                if response:
                    summary = response["choices"][0]["message"]["content"]
                    endline = return_footer(response)

                    messages.append(
                        {
                            "role": "system",
                            "content": "You are an uncensored AI Assistant. Continue the conversation naturally from where it left off, using the upcoming summary as context. Maintain the same tone, subject matter, and flow, ensuring that the dialogue feels uninterrupted. Pick up on the last key point or question to drive the discussion forward. ALWAYS stay in character.",
                        }
                    )
                    messages.append({"role": "user", "content": summary})
                    messages.extend(carryover_notes)

                    print_ascii_cat_with_text("Summary", summary, endline)
                    params["context"] = f"Summary of Notes: {args.t}"
                    params["tag"] = f"convo-{now}"
                    Note.append(NOTEFILE, Note.jot(summary, **params))
            elif set(args.additional_args) & set(["cat", "catenate"]):
                notes_from_tag = {}
                for tag in args.additional_args[1:]:
                    note_count = 0
                    with NoteContext(NOTEFILE, (SearchType.TAG, tag)) as nc:
                        notable_notes.append(nc[0])
                        notable_notes.append(nc[-1])
                        for inst in nc:
                            # prefill messages with the user and assistant content previously written
                            messages.append({"role": "user", "content": inst.context})
                            messages.append(
                                {"role": "assistant", "content": inst.message}
                            )
                            note_count += 1
                        notes_from_tag[tag] = note_count

                for tag, count in notes_from_tag.items():
                    print(f"{count} notes from tag [{tag}]")

                composite_string = (
                    "timestamp | context (30)                 | message (30)\n"
                    "----------|------------------------------|------------------------------\n"
                )
                for nn in notable_notes:
                    composite_string += f"{nn.now}|{nn.context[:30].strip():<30}|{nn.message[:30].strip()}\n"

                print_ascii_cat_with_text(
                    f"{sum(notes_from_tag.values())} notes included as context from conversation chain: {' '.join([f'[{arg}]' for arg in args.additional_args[1:]])}",
                    composite_string,
                )

            # SUMMARY / CAT ends here, begins normal convo loop
            while True:
                try:
                    user_input = flatten_pipe(sys.stdin.readlines())
                    if not user_input:
                        return
                except KeyboardInterrupt:
                    return

                if user_input.startswith(SYS_ROLE_TRIGGER):
                    messages.append(
                        {
                            "role": "system",
                            "content": user_input[len(SYS_ROLE_TRIGGER) :],
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "user",
                            "content": user_input,
                        }
                    )

                response = ""
                params["context"] = user_input
                params["tag"] = params.get("tag", f"convo-{now}")

                if args.w:  # wall of text preferred
                    response = send_prompt_to_endpoint(
                        messages, model_name=args.m, mode="full"
                    )

                    if response:
                        retval = response["choices"][0]["message"]["content"]
                        messages.append(
                            {
                                "role": "assistant",
                                "content": retval,
                            }
                        )
                        endline = return_footer(response)
                        print_ascii_cat_with_text(
                            user_input,
                            retval,
                            endline,
                            intro_color=OutputColors.CHAT_ME,
                        )
                        Note.append(NOTEFILE, Note.jot(retval, **params))
                    else:
                        print("Failed to get response from OpenAI API.")
                else:
                    import time

                    print_ascii_cat_with_text(
                        user_input, "", "", intro_color=OutputColors.CHAT_ME
                    )

                    response_generator = send_prompt_to_endpoint(
                        messages, model_name=args.m, mode="stream"
                    )

                    for char in response_generator:
                        print(char, end="", flush=True)
                        response += char
                        time.sleep(0.01)
                    print()

                    if response:
                        messages.append(
                            {
                                "role": "assistant",
                                "content": response,
                            }
                        )
                        Note.append(NOTEFILE, Note.jot(response, **params))

                        if Note.USE_COLORIZATION:
                            print(
                                f"{OutputColors.CHAT_END.value}stop. {AnsiColor.RESET.value}"
                            )
                        else:
                            print(f"stop.")
                    else:
                        print("Failed to get response from OpenAI API.")
        # ZERO USER-PROVIDED PARAMETER SHORTCUTS
        elif len(args.additional_args) == 0:
            # show all notes originating from this PWD
            if sys.stdin.isatty():
                with NoteContext(NOTEFILE, (SearchType.ALL, "")) as nc:
                    match_count = 0
                    non_match_count = 0
                    total_count = 0
                    for inst in nc:
                        total_count += 1
                        if getcwd() == inst.pwd:
                            match_count += 1
                            printout(inst)
                        else:
                            non_match_count += 1

                if not args.d:
                    print(f"{Note.LABEL_SEP}")
                    print(f"{match_count} notes in current directory")
                    print(f"{non_match_count} notes in child directories")
                    print(f"{total_count} total notes overall")
            else:
                Note.append(
                    NOTEFILE, Note.jot(flatten_pipe(sys.stdin.readlines()), **params)
                )
        # SINGLE USER-PROVIDED PARAMETER SHORTCUTS
        elif len(args.additional_args) == 1:
            if args.additional_args[0] in SHORTCUTS["MOST_RECENTLY_WRITTEN_HERE"]:
                # only display the most recently created note in this PWD
                last_note = None
                with NoteContext(NOTEFILE, (SearchType.DIRECTORY, getcwd())) as nc:
                    for inst in nc:
                        last_note = inst
                if last_note is None:
                    print("No notes to show.")
                else:
                    printout(last_note)
            elif args.additional_args[0] in SHORTCUTS["MOST_RECENTLY_WRITTEN_ALLTIME"]:
                # only display the most recently created note in this PWD
                last_note = None
                with NoteContext(NOTEFILE, (SearchType.ALL, "")) as nc:
                    for inst in nc:
                        last_note = inst
                if last_note is None:
                    print("No notes to show.")
                else:
                    printout(last_note)
            elif args.additional_args[0] in SHORTCUTS["DELETE_MOST_RECENT_PWD"]:
                # always deletes the most recently created note in this PWD
                try:
                    Note.pop(NOTEFILE, getcwd())
                    Note.commit(NOTEFILE)
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
                except TypeError:
                    print(f"No note to pop for this path in {NOTEFILE}")
                    sys.exit(2)
            elif args.additional_args[0] in SHORTCUTS["HOMENOTES"]:
                # if simply typed, show home notes
                # if piped to, save as home note
                if sys.stdin.isatty():
                    with NoteContext(
                        NOTEFILE, (SearchType.DIRECTORY, environ["HOME"])
                    ) as nc:
                        for inst in nc:
                            printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes in current directory")
                else:
                    params["pwd"] = environ["HOME"]
                    Note.append(
                        NOTEFILE,
                        Note.jot(flatten_pipe(sys.stdin.readlines()), **params),
                    )
            elif args.additional_args[0] in SHORTCUTS["SHOW_ALL"]:
                # show all notes, from everywhere, everywhen
                with NoteContext(NOTEFILE, (SearchType.ALL, "")) as nc:
                    for inst in nc:
                        printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes in total")
            elif args.additional_args[0] in SHORTCUTS["MESSAGE_ONLY"]:
                # returns the last message, message only (no pwd, no timestamp, no context).
                last_note = None
                with NoteContext(NOTEFILE, (SearchType.ALL, "")) as nc:
                    for inst in nc:
                        last_note = inst
                if last_note is None:
                    print("No notes to show.")
                else:
                    printout(last_note, message_only=True)
            elif args.additional_args[0] in SHORTCUTS["SLEEPING_CAT"]:

                def alternate_last_n_lines(text, n):
                    import time

                    lines = text.strip().split("\n")
                    # Print all but the last 'n' lines
                    for line in lines[:-n]:
                        print(line)

                    while True:
                        for i in range(-n, 0):
                            # Print one of the last 'n' lines
                            print(lines[i], end="\r")
                            time.sleep(1)

                            # Clear the line
                            print(" " * len(lines[i]), end="\r")

                TWOCAT = r"""_____________________________________
\            |\      _,,,---,,_      \\
 \           /,`.-'`'    -.  ;-;;,_   \\
  \         |,4-  ) )-,_..;\ (  `'-'   \\
   \ ZzZ   '---''(_/--'  `-'\_)         \\
   \ zZ    '---''(_/--'  `-'\_)         \\
   \ Z     '---''(_/--'  `-'\_)         \\
   \   Z   '---''(_/--'  `-'\_)         \\
   \  Zz   '---''(_/--'  `-'\_)         \\
"""  # credits felix lee

                alternate_last_n_lines(TWOCAT, 5)
            elif args.additional_args[0] in SHORTCUTS["BULK_MANAGE_NOTES"]:
                import tempfile
                import subprocess
                import os

                records = []  # will consist of (timestamp, message[0])
                with NoteContext(NOTEFILE, (SearchType.ALL, "")) as nc:
                    for inst in nc:
                        records.append(
                            (
                                inst.now,
                                inst.pwd.ljust(25),
                                inst.message.split("\n")[0].strip(),
                            )
                        )

                with tempfile.NamedTemporaryFile(mode="w+t", delete=False) as f:
                    f.write(
                        f"# Prefix any timestamp with 'd' to delete all notes matching this timestamp\n"
                        f"# Prefix any timestamp with 'c' or 'p' to catenate / cherry-pick notes matching this timestamp\n"
                    )
                    for record in records:
                        f.write(f"{record[0]}\t{record[1]}\t{record[2]}\n")
                    temp_file_name = f.name

                preferred_editor = os.environ.get(
                    "EDITOR", "vim"
                )  # Default to vim if EDITOR is not set
                subprocess.run([preferred_editor, temp_file_name])

                to_delete = []
                to_cat = []
                with open(temp_file_name, "r") as f:
                    lines = f.readlines()
                    for line in lines:
                        try:
                            if line.startswith("d"):
                                to_delete.append(int(line[1:].split("\t")[0].strip()))
                            elif line.startswith("c") or line.startswith("p"):
                                to_cat.append(int(line[1:].split("\t")[0].strip()))
                        except ValueError:
                            pass  # if instruction line is delete, or too much of the line (not retaining timestamp)

                os.unlink(temp_file_name)

                ret_notes = []
                for record_ts in to_cat:
                    with NoteContext(NOTEFILE, (SearchType.TIMESTAMP, record_ts)) as nc:
                        ret_notes.extend(nc)

                for record_ts in to_delete:
                    with NoteContext(NOTEFILE, (SearchType.TIMESTAMP, record_ts)) as nc:
                        for inst in nc:
                            print(f"Removing records matching timestamp: {record_ts}")
                            Note.delete(NOTEFILE, record_ts)
                            Note.commit(NOTEFILE)

                if len(ret_notes):
                    # return at the end a space-separated list of the picked notes
                    from time import time

                    retval = "\n".join(str(n.now) for n in ret_notes)
                    params["now"] = int(time())
                    params["tag"] = params.get("tag", f"bundle-{params['now']}")
                    params["context"] = "bundled notes from jot scoop"
                    Note.append(NOTEFILE, Note.jot(retval, **params))

            elif args.additional_args[0] in SHORTCUTS["NOTES_REFERENCING_ABSENT_DIRS"]:
                import os

                with NoteContext(NOTEFILE, (SearchType.ALL, "")) as nc:
                    matches = 0
                    for inst in nc:
                        if not os.path.exists(inst.pwd):
                            matches += 1
                            printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{matches} stray notes among")
                        print(f"{len(nc)} notes in total")
            elif args.additional_args[0] in SHORTCUTS["GRAPHQL"]:
                # allow reading from "cat|jot ql" with k:v pairs space separated:
                # pwd /home/willy
                # -> {"pwd": "/home/willy"}
                # when invoked without a pipe, e.g., "jot ql", it gives all notes+child notes of the pwd

                # Trimmed projection: omit pwd/now fields the CLI doesn't display.
                CLI_QUERY = """
                query ($pwd: String, $now: Int, $tag: [String], $context: String, $message: String, $pwdtree: String, $logic: String) {
                  notes(pwd: $pwd, now: $now, tag: $tag, context: $context, message: $message, pwdtree: $pwdtree, logic: $logic) {
                    tag
                    context
                    message
                  }
                }
                """
                parsed_vars = {}
                if sys.stdin.isatty():  # jot ql
                    parsed_vars = {"pwdtree": getcwd()}
                else:  # cat | jot ql
                    for line in sys.stdin:
                        # Strip whitespace and split by a single space
                        line = line.strip()
                        if line:
                            # Split only on the first space to allow spaces in values
                            key, value = line.split(" ", 1)
                            parsed_vars[key] = value

                from pprint import pprint

                pprint(parsed_vars)
                pprint(
                    catjot_graphql().execute_query(parsed_vars, query=CLI_QUERY).data
                )
            elif args.additional_args[0] in SHORTCUTS["CREATE_SPACED_REPETITION"]:
                print("Enter note prompt/hint:")
                prompt = flatten_pipe(sys.stdin.readlines())  # this matches context
                print("Enter answer:")
                answer = flatten_pipe(sys.stdin.readlines())
                Note.append(
                    NOTEFILE, Note.jot(answer, context=prompt, pwd="/spaced_repetition")
                )
            elif args.additional_args[0] in SHORTCUTS["ITERATE_SPACED_REPETITIONS"]:
                from datetime import datetime, timedelta

                def next_interval(number, intervals=[1, 2, 4, 7, 12]):
                    """Finds the next interval based on the input number and a sorted intervals list."""
                    # Check if the number is greater than or equal to the largest interval
                    if number >= intervals[-1]:
                        return intervals[-1]

                    # Loop through intervals to find the next interval
                    for i in range(len(intervals)):
                        if intervals[i] == number:
                            # Return the next interval if it exists
                            return (
                                intervals[i + 1]
                                if i + 1 < len(intervals)
                                else intervals[-1]
                            )
                        elif intervals[i] > number:
                            # Return the first interval greater than the input number
                            return intervals[i]

                    # Fallback, though we expect to return within the loop
                    return intervals[0]

                def calculate_next_review(current_timestamp, days_until_next):
                    """Calculate the next review timestamp based on the current timestamp, review level, and interval list.

                    Args:
                    current_timestamp (int): The current timestamp in epoch format.
                    review_level (int): The level of days to adjust timestamp for

                    Returns:
                    int: The next scheduled review timestamp in epoch format.
                    """
                    # Calculate the next review date by adding the interval in days to the current date
                    next_review_date = datetime.fromtimestamp(
                        current_timestamp
                    ) + timedelta(days=days_until_next)

                    # Return the timestamp for the next review
                    return int(next_review_date.timestamp())

                import copy
                from random import randint
                from time import time
                from datetime import datetime, timedelta

                with NoteContext(
                    NOTEFILE, (SearchType.TREE, "/spaced_repetition")
                ) as nc:
                    for inst in nc:
                        if (
                            time() < inst.now
                        ):  # if current time has not yet reached note ts
                            continue

                        try:
                            current_interval = int(inst.pwd.split("/")[-1])
                        except ValueError:
                            current_interval = 1

                        try:
                            print_ascii_cat_with_text(inst.context, "", "type below:")
                            user_input = flatten_pipe(sys.stdin.readlines())
                        except KeyboardInterrupt:
                            break
                        except IndexError:
                            print("no notes remain for today")
                        else:
                            new_obj = copy.deepcopy(inst)

                            date_time = datetime.fromtimestamp(time())
                            start_of_day = datetime(
                                date_time.year, date_time.month, date_time.day
                            )
                            start_of_day_ts = int(start_of_day.timestamp()) + randint(
                                1, 3600
                            )
                            # add up to an hour to reduce collisions but
                            # still keep close to the new day.

                            if user_input.strip() == inst.message.strip():
                                next_int = next_interval(current_interval)
                                new_obj.now = calculate_next_review(
                                    start_of_day_ts, next_int
                                )
                                new_obj.pwd = f"/spaced_repetition/{next_int}"
                                print(
                                    "✓ Next note appearance:",
                                    datetime.fromtimestamp(new_obj.now),
                                )
                            else:
                                new_obj.now = calculate_next_review(
                                    start_of_day_ts, next_interval(0)
                                )
                                new_obj.pwd = f"/spaced_repetition/0"

                                print_ascii_cat_with_text(
                                    inst.message.strip(),
                                    "is the correct answer",
                                    f"✗ Next note appearance: {datetime.fromtimestamp(new_obj.now)}",
                                )
                            Note.append(NOTEFILE, new_obj)
                            Note.delete(NOTEFILE, int(inst.now))
                            Note.commit(NOTEFILE)
                    else:  # at end of iterating notes
                        print("Done for today")
            elif args.additional_args[0] in SHORTCUTS["LLM"]:
                if sys.stdin.isatty():  # jot llm
                    print_ascii_cat_with_text(
                        "Hi, what can I help you with today? ",
                        "Enter your prompt and hit Control-D to submit. \n",
                    )

                    while True:
                        try:
                            query = flatten_pipe(sys.stdin.readlines())
                            if not query:
                                return
                        except KeyboardInterrupt:
                            return
                        else:
                            answer = run_tool_loop(query)
                            print_ascii_cat_with_text(
                                query, answer, intro_color=OutputColors.CHAT_ME
                            )
                else:
                    query = flatten_pipe(sys.stdin.readlines())
                    answer = run_tool_loop(query)
                    print_ascii_cat_with_text(
                        query, answer, intro_color=OutputColors.CHAT_ME
                    )

        # TWO USER-PROVIDED PARAMETER SHORTCUTS
        elif len(args.additional_args) == 2:
            if args.additional_args[0] in SHORTCUTS["MATCH_NOTE_NAIVE"]:
                # match if "term [+term2] [..]" exists in any line of the note
                flattened = flatten(args.additional_args[1:])
                with NoteContext(NOTEFILE, (SearchType.MESSAGE, flattened)) as nc:
                    for inst in nc:
                        printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS["MATCH_NOTE_NAIVE_I"]:
                # match if "term [+term2] [..]" exists in any line of the note
                flattened = flatten(args.additional_args[1:])
                with NoteContext(NOTEFILE, (SearchType.MESSAGE_I, flattened)) as nc:
                    for inst in nc:
                        printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS["MATCH_TIMESTAMP"]:
                # match if timestamp matches!
                flattened = int(flatten(args.additional_args[1:]))
                with NoteContext(NOTEFILE, (SearchType.TIMESTAMP, flattened)) as nc:
                    for inst in nc:
                        printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS["REMOVE_BY_TIMESTAMP"]:
                # delete any notes matching the provided timestamp
                flattened = 0
                try:
                    flattened = int(flatten(args.additional_args[1:]))
                except ValueError:
                    print("Invalid input, like having an alpha in a numeric")
                    exit(2)

                with NoteContext(NOTEFILE, (SearchType.TIMESTAMP, flattened)) as nc:
                    for inst in nc:
                        Note.delete(NOTEFILE, int(args.additional_args[1]))
                        Note.commit(NOTEFILE)
            elif args.additional_args[0] in SHORTCUTS["SHOW_TAG"]:
                # show all notes with tag
                flattened = args.additional_args[1]
                with NoteContext(NOTEFILE, (SearchType.TAG, flattened)) as nc:
                    for inst in nc:
                        printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS["MESSAGE_ONLY"]:
                # returns the message only (no pwd, no timestamp, no context).
                # when provided a timestamp, any notes matching timestamp
                # will be sent to stdout, concatenated in order of appearance
                flattened = int(args.additional_args[1])
                if flattened:  # if truthy, e.g., timestamp, use it for search
                    with NoteContext(NOTEFILE, (SearchType.TIMESTAMP, flattened)) as nc:
                        for inst in nc:
                            printout(inst, message_only=True)
                else:  # if not truthy, display pwd matches without headers
                    # the fallback behavior of finding a non truthy value is not
                    # expected to happen normally. this would be providing
                    # a deliberately nonuseful value: |jot pl ""
                    # the actual implementation of payload without timestamp
                    # should be handled in # ONE USER-PROVIDED PARAMETER SHORTCUTS

                    # always displays the most recently created note in this PWD
                    last_note = None
                    with NoteContext(NOTEFILE, (SearchType.DIRECTORY, getcwd())) as nc:
                        for inst in nc:
                            last_note = inst
                    if last_note is None:
                        print("No notes to show.")
                    else:
                        printout(last_note, message_only=True)
            elif args.additional_args[0] in SHORTCUTS["SIDE_BY_SIDE"]:
                # prints a note and allows you to rewrite the line/accept line as-is
                # Acceptable Input:
                # <matched input> = matched input kept
                # <input comprised only of strip()'ed chars, including blank line> = keep original input
                # ' ' = delete line
                # <anything else> = keep new input
                import os
                from math import ceil

                last_note = "No notes to show.\n"
                last_mark = " "

                if args.additional_args[1] in SHORTCUTS["MOST_RECENTLY_WRITTEN_HERE"]:
                    # only display the most recently created note in this PWD
                    with NoteContext(
                        NOTEFILE, (SearchType.DIRECTORY, os.getcwd())
                    ) as nc:
                        for inst in nc:
                            last_note = inst
                elif (
                    args.additional_args[1]
                    in SHORTCUTS["MOST_RECENTLY_WRITTEN_ALLTIME"]
                ):
                    # last written note
                    with NoteContext(NOTEFILE, (SearchType.ALL, "")) as nc:
                        for inst in nc:
                            last_note = inst
                else:
                    user_timestamp = int(args.additional_args[1])
                    with NoteContext(NOTEFILE, (SearchType.ALL, "")) as nc:
                        for inst in nc:
                            last_note = inst
                            # falls back to very last note written if provided int
                            # doesnt match any existing note
                            if inst.now == user_timestamp:
                                break

                try:
                    if not last_note.message.strip():  # falsy message
                        print("No notes to side-by-side.")
                        exit(3)
                except AttributeError:  # missing attribute
                    print("No notes to side-by-side.")
                    exit(3)

                newnote_lines = []
                MARKS = {
                    "check": "✓",  # indicates for unchanged lines, typed
                    "circle": "⊕",  # indicates for unchanged lines, untyped (empty line)
                    "x": "✗",  # indicates changed line from original
                }

                longest_line_length = max(
                    len(line) for line in last_note.message.split("\n")
                )
                terminal_width = os.get_terminal_size().columns
                print(f"max line length: {longest_line_length}")
                print(f"terminal_width : {terminal_width}")

                min_left_side_width = max(
                    ceil(longest_line_length / 10) * 10, 35
                )  # Round up to the nearest 10, or 35 min
                if (
                    terminal_width >= (min_left_side_width * 2) + 3
                ):  # last mark, pipe sep, last char
                    for line in last_note.message.split("\n"):
                        print(
                            f"{line.rstrip().ljust(min_left_side_width)}{last_mark}|",
                            end="",
                        )
                        usr_in = input()
                        if line.rstrip() == usr_in.rstrip():  # line matches...
                            last_mark = MARKS["check"]
                            newnote_lines.append(line + "\n")  # ...preserve original
                        elif usr_in == " ":  # if the line is a single space...
                            last_mark = MARKS[
                                "x"
                            ]  # ... throw it away (by not appending it)
                        elif (
                            not usr_in.strip()
                        ):  # if the user provided line is effectively blank...
                            last_mark = MARKS["circle"]
                            newnote_lines.append(line + "\n")  # ...preserve original
                        else:
                            # value is changed from original, keep provided value
                            last_mark = MARKS["x"]
                            newnote_lines.append(usr_in.rstrip() + "\n")
                    addl_context = f"rewritten note from {last_note.now}"
                    new_note = {
                        "message": "".join(newnote_lines),
                        "pwd": last_note.pwd,
                        "now": None,
                        "tag": last_note.tag,
                        "context": (
                            addl_context
                            if not last_note.context
                            else f"{last_note.context};{addl_context}"
                        ),
                    }
                    Note.append(NOTEFILE, Note.jot(**new_note))
                else:
                    print(
                        f"The terminal is not sufficiently wide to match double the width of the longest line in the note. Aborting"
                    )
                    exit(2)
            elif args.additional_args[0] in SHORTCUTS["MOST_RECENTLY_WRITTEN_ALLTIME"]:
                # only display the most n recently, of all locations
                from collections import deque

                record_count_to_show = 1
                user_tilde_given = False
                try:
                    record_count_to_show = int(args.additional_args[1])
                except ValueError:
                    # if user includes ~ (tilde), show ONLY the one note, counting backwards
                    if args.additional_args[1].startswith("~"):
                        record_count_to_show = int(args.additional_args[1][1:])
                        user_tilde_given = True

                last_notes = deque(maxlen=record_count_to_show)
                with NoteContext(NOTEFILE, (SearchType.ALL, "")) as nc:
                    for inst in nc:
                        last_notes.append(inst)

                if not user_tilde_given:
                    for inst in last_notes:
                        printout(inst)
                else:
                    try:
                        printout(last_notes[-record_count_to_show])
                    except IndexError:
                        pass
            elif args.additional_args[0] in SHORTCUTS["MOST_RECENTLY_WRITTEN_HERE"]:
                # only display the most recently created n notes in this PWD
                from collections import deque

                record_count_to_show = 1
                user_tilde_given = False
                try:
                    record_count_to_show = int(args.additional_args[1])
                except ValueError:
                    # if user includes ~ (tilde), show ONLY the one note, counting backwards
                    if args.additional_args[1].startswith("~"):
                        record_count_to_show = int(args.additional_args[1][1:])
                        user_tilde_given = True

                last_notes = deque(maxlen=record_count_to_show)
                with NoteContext(NOTEFILE, (SearchType.DIRECTORY, getcwd())) as nc:
                    for inst in nc:
                        last_notes.append(inst)

                if not user_tilde_given:
                    for inst in last_notes:
                        printout(inst)
                else:
                    try:
                        printout(last_notes[-record_count_to_show])
                    except IndexError:
                        pass


if __name__ == "__main__":
    main()
