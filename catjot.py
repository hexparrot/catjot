#!/usr/bin/env python3
__author__ = "William Dizon"
__license__ = "MIT"
__version__ = "0.0.1"
__maintainer__ = "William Dizon"
__email__ = "wdchromium@gmail.com"
__status__ = "Development"

from os import environ, getcwd
from enum import Enum, auto


def supports_color():
    import os
    import sys

    supported_platform = (
        os.name != "nt" or "ANSICON" in os.environ or "WT_SESSION" in os.environ
    )
    is_a_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    return supported_platform and is_a_tty


class AnsiColor(Enum):
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


class Note(object):
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
    # REC_BOT    | ***************
    #            | 0 matches in child directories

    # Label Name | File internally looks like:
    # -----------|----------------------------------
    # LABEL_SEP  | ^-^
    # LABEL_PWD  | Directory:/home/user
    # LABEL_NOW  | Date:1695002470
    # LABEL_TAG  | Tag:project1
    # LABEL_CTX  | Context:ls /home/user
    # LABEL_ARG  | note_goes_here
    #            | note_continued
    #            |

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

    def __init__(self, values_dict={}):
        from time import time

        now = int(time())
        self.pwd = values_dict.get("pwd", getcwd())
        assert self.pwd.startswith("/")
        self.now = int(values_dict.get("now", now))
        assert isinstance(self.now, int)
        self.tag = values_dict.get("tag", "")
        assert isinstance(self.tag, str)
        self.context = values_dict.get("context", "")
        self.message = values_dict.get("message", "")
        if self.message.startswith(Note.LABEL_ARG):
            self.message = self.message[len(Note.LABEL_ARG) :]

    def __str__(self):
        """Returns the string representation of a note.
        This representation does not need to reflect the format
        on the underlying .catjot file."""
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
        return f"Note(context='{self.context}', message='{self.message}')"

    def __eq__(self, other):
        """Equality test for notes should:
        Return true if strip()'ed and flattened values match."""

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
        """Convenience function for low-effort creation of notes"""
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
        """Accepts non-falsy text and writes it to the .catjot file."""
        if not note.message:
            return
        if not note.pwd:
            note.pwd = getcwd()
        if not note.now:
            from time import time

            note.now = int(time())

        with open(src, "at") as file:
            file.write(f"{Note.LABEL_SEP}\n")
            file.write(f"{Note.LABEL_PWD}{note.pwd}\n")
            file.write(f"{Note.LABEL_NOW}{note.now}\n")
            file.write(f"{Note.LABEL_TAG}{note.tag}\n")
            file.write(f"{Note.LABEL_CTX}{note.context}\n")
            file.write(f"{Note.LABEL_ARG}{note.message}\n\n")

    @classmethod
    def delete(cls, src, timestamp):
        """Deletes a single note from the .catjot file.
        It first creates .catjot.new which should have the full contents
        of the original minus any timestamps (likely one) that is omitted"""
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
        """Deletes the most recent note from the PWD"""
        last_record = None
        for inst in Note.match(src, [(SearchType.DIRECTORY, path)]):
            last_record = inst.now
        else:
            cls.delete(src, last_record)

    @classmethod
    def commit(cls, src):
        """Finally commits to the filesystem changes implemented by delete().
        It is a separate function, but should be expected to be paired,
        100% of the time, alongside pop/deletes"""
        import shutil

        shutil.move(src, src + ".old")
        shutil.move(src + ".new", src)

    @classmethod
    def iterate(cls, src):
        """Iterate all notes, across all paths.
        Other functions should expect to start with this, pruning down unwanted
        notes via a matching mechanism such as search()"""

        def parse(record):
            """Receives a list of lines which represent one single record.
            It forces the arrangement of the notes to be specifically matching
            that of FIELDS_TO_PARSE, but it is not enforced here.
            Leaving here is simply a dictionary matching all the fields
            from __init__"""
            current_read = {}
            for field, label in cls.FIELDS_TO_PARSE:  # forces ordering of fields
                try:
                    current_read[field] = record.pop(0).split(label, 1)[1].strip()
                except IndexError:
                    break  # label/order does not match expected headers
                    # print(f"Error reading line, expecting label \"{label}<value>\"")
            else:
                message = "".join(record).rstrip() + "\n"
                current_read["message"] = message
                return current_read

        current_record = []
        last_record = None
        last_line = ""
        # Loops through all lines in the file looking for anywhere where the last line
        # was empty, followed by the LABEL_SEP (^-^). Lines are added to the
        # current_record list and once the next LABEL_SEP combo is met, the previous
        # record parsed and casted into a Note
        with open(src, "r") as file:
            for line in file:
                if last_line == "" and line.strip() == Note.LABEL_SEP:
                    if len(current_record):
                        yield Note(parse(current_record))
                    current_record = []
                else:
                    if current_record and Note.LABEL_PWD not in current_record[0]:
                        # if its reading a line, but the first of this record
                        try:
                            current_record = last_record[0:3]  # pwd, now, tag
                        except TypeError:
                            # faulty jot in file, such as record separator
                            # being piped in from jot into jot
                            current_record = []
                            last_line = ""
                            continue
                        else:
                            current_record.append(
                                f"{Note.LABEL_CTX}Unexpected new-record line found in data.\n"
                                + f"Salvaging remaining note into this new note.\n"
                                + f"Ignore this line up to and including Date above, to restore original form."
                            )
                        # Adding context to this new note about why it now exists

                    current_record.append(line)
                    last_line = line.strip()
            else:
                # There is no LABEL_SEP at the end of a file, so this would be reached
                # at the end of loop, and occurs once only per file
                if last_line == "" and len(current_record):
                    yield Note(parse(current_record))

    @classmethod
    def match(cls, src, criteria, logic="and", time_only=False):
        if isinstance(criteria, tuple):
            criteria = [criteria]  # force all criteria passed in as tuples into a list
            # and save all the boilerplate of [] everywhere else

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
                    if not s_text:
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
    def __init__(self, tags_dirs_ts):
        """Holds a set of correlated notes, distinguished by:
        Tags, dirs, and timestamps. The NOTEFILE is iterated and any matching
        notes are copied into the `.notes` list.

        This object also can suppress tags/dirs/ts, which leaves the notes
        intact in the context, but inaccessible on iteration of the context.
        This enables wholesale blocking out of memories."""
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
        """Returns a string of `context' and `message' separated by two newlines.
        Iterates through all available visible notes--notes not suppressed."""
        combined_str = ""
        for note in self._visible_notes():
            combined_str += (
                note.context.strip() + "\n\n" + note.message.strip() + "\n\n"
            )
        return (
            combined_str.strip()
        )  # Remove the trailing newline from the final concatenation

    def __repr__(self):
        return (
            f"ContextBundle(tags={self.tags}, dirs={self.dirs}, ts={self.ts}, "
            f"notes={self.notes}, blocks={self.blocks})"
        )

    def __iter__(self):
        """Iterate through visible notes--ones not suppressed"""
        for n in self._visible_notes():
            yield n

    def __add__(self, item):
        """Combines the matching terms of two contexts, effectively combining them.
        Suppressions are not copied from either ContextBundle."""
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
        """Add 'matching' term to the object via +="""
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
        """Remove 'matching' term to the object via -="""
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
        """Either remove a 'matching' term, or if subtracting a ContextBundle:
        Suppress all b's 'matching' terms from obj a."""
        import copy

        # Create a deep copy of the current instance
        new_obj = copy.deepcopy(self)

        if isinstance(item, ContextBundle):
            [new_obj.suppress(t) for t in item.tags]
            [new_obj.suppress(t) for t in item.ts]
            [new_obj.suppress(t) for t in item.dirs]
        else:
            # Identifies and removes matching notes
            new_obj -= item

            # Reread file on disk and repopulate self.notes list
            new_obj._regen_notes()

        return new_obj

    def __len__(self):
        """Return the amount of notes not suppressed."""
        return len(list(self._visible_notes()))

    def _visible_notes(self):
        """Helper function to return the list of notes, impacted by suppression"""
        seen = []

        def iterate_notes(search_type, values):
            for value in values:
                for n in self.notes:
                    # Check if the note is not blocked by tags, directory, or timestamp
                    if (
                        set(n.tag.split()).isdisjoint(self.blocks["tag"])
                        and n.pwd not in self.blocks["directory"]
                        and n.now not in self.blocks["timestamp"]
                        and n not in seen
                    ):
                        seen.append(n)
                        yield n

        # Iterate over notes for tags, timestamps, and directories
        yield from iterate_notes(SearchType.TAG, self.tags)
        yield from iterate_notes(SearchType.TIMESTAMP, self.ts)
        yield from iterate_notes(SearchType.DIRECTORY, self.dirs)

    def _regen_notes(self):
        """Reads disk and iterates all notes, adding notes that match on 'matching' terms"""
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
        """Returns a list of all tags present in all available notes, not impacted by suppression"""
        all_tags = set()
        for n in self.notes:
            all_tags.update(n.tag.split())
        return all_tags

    def suppress(self, item):
        """Accepts a 'matching' term and adds it to the blocklist, so it will be hidden from iteration"""
        if isinstance(item, int):
            self.blocks["timestamp"].add(item)
        elif item.startswith("/"):
            self.blocks["directory"].add(item)
        else:
            self.blocks["tag"].add(item)

    def unsuppress(self, item):
        """Reverses suppression--ensures notes matching the terms still are iterable"""
        try:
            if isinstance(item, int):
                self.blocks["timestamp"].remove(item)
            elif item.startswith("/"):
                self.blocks["directory"].remove(item)
            else:
                self.blocks["tag"].remove(item)
        except KeyError:
            pass


NEWCAT = r"""-------------------------------------
     ("`-/")_.-'"``-._
      . . `; -._    )-;-,_`)
     (v_,)'  _  )`-.\  ``-'
    _.- _..-_/ / ((.'
  ((,.-'   ((,/
   ((,-'    ((,|
"""  # credits felix lee

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

CATGPT_ROLE = (
    """You're proudly a cat assistant here to help the user in any way you can."""
)


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


def send_prompt_to_endpoint(messages, model_name, mode):
    """
    Sends a prompt to a streaming-supported OpenAI GPT completion API and handles the response.

    Parameters:
    - messages: List of messages to send to the API.
    - model_name: The name of the model to use.
    - mode: "stream" for character-by-character streaming, "full" for collecting the full response.

    Returns:
    - In "stream" mode: A generator that yields characters as they are streamed.
    - In "full" mode: A tuple (full_response, last_token_obj), where:
      - full_response: The full response string.
      - last_token_obj: The last JSON object received, containing metadata like token count.
    """
    import requests
    import json
    from os import getenv

    api_key = getenv("openai_api_key")
    api_url = getenv("openai_api_url", "https://api.openai.com/v1/chat/completions")
    api_model = getenv("openai_api_model", model_name)
    # set this key in your shell, e.g., this line in your ~/.bash_profile:
    # export openai_api_key="sk-proj...8EEF"
    headers = {"Content-Type": "application/json"}

    if api_url.startswith("https://api.openai.com"):
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

        token_count = 0
        last_token_obj = {}

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
    # receives the jSON object from a successful gpt call
    # returns technical details of token usage/model
    if "usage" in gpt_reply:
        prompt_tokens = gpt_reply["usage"]["prompt_tokens"]
        output_tokens = gpt_reply["usage"]["completion_tokens"]
        model_name = gpt_reply["model"]
        return f"stop. (prompt tokens={prompt_tokens}, output_tokens={output_tokens}, model={model_name})"
    else:
        print(gpt_reply)
        finish_reason = gpt_reply["choices"][0].get("finish_reason", "stop")
        return f"{finish_reason}. (token_count={gpt_reply['token_count']}, model={gpt_reply['model']})"


def is_binary_string(data):
    """
    Determine if a string is binary or text by checking for non-text characters.

    :param data: A string to be checked
    :return: True if the string is binary, False if it is text
    """
    if "\x00" in data:
        return True

    # Heuristic: if more than 30% of the characters are non-text characters, it's binary
    text_characters = "".join(map(chr, range(32, 127))) + "\n\r\t\b"
    non_text_chars = "".join([char for char in data if char not in text_characters])

    return len(non_text_chars) / len(data) > 0.3


def print_ascii_cat_with_text(intro, text, endtext="stop."):
    import textwrap

    cat = r""" /\_/\
( o.o )
 > ^ <
"""
    wrapped_text = textwrap.wrap(intro, 80)

    # Determine the height of the ASCII cat
    cat_height = cat.count("\n")

    # Print the ASCII cat and the wrapped text side by side
    cat_lines = cat.split("\n")
    for i in range(max(len(cat_lines), len(wrapped_text))):
        cat_line = cat_lines[i] if i < len(cat_lines) else " " * 8
        text_line = wrapped_text[i] if i < len(wrapped_text) else ""
        if Note.USE_COLORIZATION:
            print(
                f"{AnsiColor.YELLOW.value}{cat_line:<8} {AnsiColor.GREEN.value}{text_line}{AnsiColor.RESET.value}"
            )
        else:
            print(f"{cat_line:<8} {text_line}")

    print(text)
    print(f"{AnsiColor.MAGENTA.value}{endtext}{AnsiColor.RESET.value}")


class SearchType(Enum):
    ALL = auto()
    TAG = auto()
    MESSAGE = auto()
    MESSAGE_I = auto()
    CONTEXT = auto()
    CONTEXT_I = auto()
    TIMESTAMP = auto()
    DIRECTORY = auto()
    TREE = auto()


class NoteContext:
    def __init__(self, notefile, search_criteria):
        self.notefile = notefile
        self.criteria = search_criteria

    def __enter__(self):
        import sys

        try:
            return list(Note.match(self.notefile, self.criteria))
        except FileNotFoundError:
            print(f"Waking up the cat at {self.notefile}. Now, try again.")
            for line in NEWCAT.split("\n")[0:-2]:
                print(line)
            open(self.notefile, "a").close()
            sys.exit(1)
        except ValueError:
            print(
                f"Value provided does not match expected type, like having a character in an int."
            )
            sys.exit(3)

    def __exit__(self, exc_type, exc_value, traceback):
        pass


def main():
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
        "  jot t friendly   search all notes, filtering by (tag), case-sensitive\n",
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
    parser.add_argument(
        "-m", type=str, default="gpt-4o-mini", help="LLM model to engage"
    )
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
    from os import environ

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
            "CHAT": ["chat", "catgpt", "c"],
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
            elif not sys.stdin.isatty():  # not interactive tty, all pipe!
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
                from os import environ

                params["pwd"] = environ["HOME"]

            if is_binary_string(full_sendout):
                print_ascii_cat_with_text(
                    "Uh oh, the pipe I received seems to be binary data but -gpt accepts only text. "
                    "Try another file that is text-based, instead.",
                    "",
                )
                sys.exit(1)
            elif len(full_sendout.encode("utf-8")) > 512000:
                # 512000 bytes is an estimation of 128000 tokens * 4 bytes
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
                    print_ascii_cat_with_text(intro, retval, endline)
                    Note.append(NOTEFILE, Note.jot(retval, **params))
                else:
                    print("Failed to get response from OpenAI API.")
            else:
                import time

                print_ascii_cat_with_text(intro, "", "")

                response_generator = send_prompt_to_endpoint(
                    messages, model_name=args.m, mode="stream"
                )

                for char in response_generator:
                    print(char, end="", flush=True)
                    response += char
                    time.sleep(0.01)
                else:
                    print()

                if response:
                    Note.append(NOTEFILE, Note.jot(response, **params))

                    if Note.USE_COLORIZATION:
                        print(f"{AnsiColor.MAGENTA.value}stop.{AnsiColor.RESET.value}")
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
                    messages.append({"role": "user", "content": user_input})

                response = ""
                params["context"] = user_input
                params["tag"] = params.get("tag", f"convo-{now}")

                if args.w:  # wall of text preferred
                    response = send_prompt_to_endpoint(
                        messages, model_name=args.m, mode="full"
                    )

                    if response:
                        retval = response["choices"][0]["message"]["content"]
                        messages.append({"role": "assistant", "content": retval})
                        endline = return_footer(response)
                        print_ascii_cat_with_text(user_input, retval, endline)
                        Note.append(NOTEFILE, Note.jot(retval, **params))
                    else:
                        print("Failed to get response from OpenAI API.")
                else:
                    import time

                    print_ascii_cat_with_text(user_input, "", "")

                    response_generator = send_prompt_to_endpoint(
                        messages, model_name=args.m, mode="stream"
                    )

                    for char in response_generator:
                        print(char, end="", flush=True)
                        response += char
                        time.sleep(0.01)
                    else:
                        print()

                    if response:
                        messages.append({"role": "assistant", "content": response})
                        Note.append(NOTEFILE, Note.jot(response, **params))

                        if Note.USE_COLORIZATION:
                            print(
                                f"{AnsiColor.MAGENTA.value}stop.{AnsiColor.RESET.value}"
                            )
                        else:
                            print(f"stop.")
                    else:
                        print("Failed to get response from OpenAI API.")
        # ZERO USER-PROVIDED PARAMETER SHORTCUTS
        elif len(args.additional_args) == 0:
            # show all notes originating from this PWD
            from os import getcwd

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
                from os import getcwd

                last_note = "No notes to show.\n"
                with NoteContext(NOTEFILE, (SearchType.DIRECTORY, getcwd())) as nc:
                    for inst in nc:
                        last_note = inst
                    else:
                        printout(last_note)
            elif args.additional_args[0] in SHORTCUTS["MOST_RECENTLY_WRITTEN_ALLTIME"]:
                # only display the most recently created note in this PWD
                from os import getcwd

                last_note = "No notes to show.\n"
                with NoteContext(NOTEFILE, (SearchType.ALL, "")) as nc:
                    for inst in nc:
                        last_note = inst
                    else:
                        printout(last_note)
            elif args.additional_args[0] in SHORTCUTS["DELETE_MOST_RECENT_PWD"]:
                # always deletes the most recently created note in this PWD
                from os import getcwd

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
                from os import environ

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
                    else:
                        printout(last_note, message_only=True)
            elif args.additional_args[0] in SHORTCUTS["SLEEPING_CAT"]:
                alternate_last_n_lines(TWOCAT, 5)
            elif args.additional_args[0] in SHORTCUTS["BULK_MANAGE_NOTES"]:
                import tempfile
                import subprocess
                import os

                last_note = None
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
                    "EDITOR", "vi"
                )  # Default to nano if EDITOR is not set
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
                from os import getcwd

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
                    else:  # after all the iterating
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
                from os import getcwd

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
