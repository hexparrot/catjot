#!/usr/bin/env python3
__author__ = "William Dizon"
__license__ = "MIT"
__version__ = "0.0.1"
__maintainer__ = "William Dizon"
__email__ = "wdchromium@gmail.com"
__status__ = "Development"

from os import environ, getcwd

class Note(object):
    # Label Name | Default pattern shown at runtime:
    #------------|----------------------------------
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
    #------------|----------------------------------
    # LABEL_SEP  | ^-^
    # LABEL_PWD  | Directory:/home/user
    # LABEL_NOW  | Date:1695002470
    # LABEL_TAG  | Tag:project1
    # LABEL_CTX  | Context:ls /home/user
    # LABEL_ARG  | note_goes_here
    #            | note_continued
    #            |

    # Runtime display presentation
    LABEL_DIR  = "> cd "
    LABEL_DATE = "# date "
    DATE_FORMAT = '%Y-%m-%d %H:%M:%S (%s)'
    LABEL_DATA = "" # line has no add'l prefix as default

    # Top and bottom record separators
    REC_TOP = "^-^"
    REC_BOT = ""

    # .catjot file formatting
    LABEL_SEP = "^-^" # record separator
    LABEL_PWD = "Directory:"
    LABEL_NOW = "Date:"
    LABEL_TAG = "Tag:"
    LABEL_CTX = "Context:"
    LABEL_ARG = "Message:"

    # All required fields to exist before note data
    FIELDS_TO_PARSE = [
        ('pwd', LABEL_PWD),
        ('now', LABEL_NOW),
        ('tag', LABEL_TAG),
        ('context', LABEL_CTX),
    ]

    # Filepath to save to, saves in $HOME
    NOTEFILE = f"{environ['HOME']}/.catjot"

    def __init__(self, values_dict={}):
        from time import time

        now = int(time())
        self.pwd = values_dict.get('pwd', getcwd())
        assert self.pwd.startswith('/')
        self.now = int(values_dict.get('now', now))
        assert isinstance(self.now, int)
        self.tag = values_dict.get('tag', '')
        assert isinstance(self.tag, str)
        self.context = values_dict.get('context', '')
        self.message = values_dict.get('message', '')
        if self.message.startswith(Note.LABEL_ARG):
            self.message = self.message[len(Note.LABEL_ARG):]

    def __str__(self):
        """ Returns the string representation of a note.
            This representation does not need to reflect the format
            on the underlying .catjot file."""
        from datetime import datetime
        dt = datetime.fromtimestamp(self.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)

        tagline = ''
        if self.tag:
            tagline = f"[{self.tag}]\n"

        context = ""
        if self.context:
            context = f"% {self.context}\n"

        return f"{Note.LABEL_DIR}{self.pwd}\n" + \
               f"{Note.LABEL_DATE}{friendly_date}\n" + \
               tagline + \
               context + \
               f"{Note.LABEL_DATA}{self.message}"

    def __eq__(self, other):
        """ Equality test for notes should:
            Return true if strip()'ed and flattened values match. """

        if isinstance(other, Note):
            return self.message.strip() == other.message.strip() and \
                   self.pwd == other.pwd and \
                   self.now == other.now and \
                   self.context.strip() == other.context.strip() and \
                   self.tag == other.tag
        else:
            return False

    @classmethod
    def jot(cls, message, tag="", context="", pwd=None, now=None):
        """ Convenience function for low-effort creation of notes """
        from time import time
        if not message: raise ValueError

        return Note({
            'pwd': pwd or getcwd(),
            'now': now or int(time()),
            'tag': tag,
            'context': context,
            'message': message.strip() + '\n',
        })

    @classmethod
    def append(cls, src, note):
        """ Accepts non-falsy text and writes it to the .catjot file. """
        if not note.message: return
        if not note.pwd: note.pwd = getcwd()
        if not note.now:
            from time import time
            note.now = int(time())

        with open(src, 'at') as file:
            file.write(f"{Note.LABEL_SEP}\n")
            file.write(f"{Note.LABEL_PWD}{note.pwd}\n")
            file.write(f"{Note.LABEL_NOW}{note.now}\n")
            file.write(f"{Note.LABEL_TAG}{note.tag}\n")
            file.write(f"{Note.LABEL_CTX}{note.context}\n")
            file.write(f"{Note.LABEL_ARG}{note.message}\n\n")

    @classmethod
    def delete(cls, src, timestamp):
        """ Deletes a single note from the .catjot file.
            It first creates .catjot.new which should have the full contents
            of the original minus any timestamps (likely one) that is omitted """
        newpath = src + ".new"
        with open(newpath, 'wt') as trunc_file:
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
        with open(newpath, 'wt') as trunc_file:
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
                    if tag.startswith('~'):
                        try:
                            all_tags.remove(tag[1:])
                        except ValueError:
                            pass #don't care if its not in there
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
        """ Deletes the most recent note from the PWD """
        last_record = None
        for inst in Note.match(src, [(SearchType.DIRECTORY, path)]):
            last_record = inst.now
        else:
            cls.delete(src, last_record)

    @classmethod
    def commit(cls, src):
        """ Finally commits to the filesystem changes implemented by delete().
            It is a separate function, but should be expected to be paired,
            100% of the time, alongside pop/deletes """
        import shutil

        shutil.move(src, src + ".old")
        shutil.move(src + ".new", src)

    @classmethod
    def iterate(cls, src):
        """ Iterate all notes, across all paths.
            Other functions should expect to start with this, pruning down unwanted
            notes via a matching mechanism such as search() """

        def parse(record):
            """ Receives a list of lines which represent one single record.
                It forces the arrangement of the notes to be specifically matching
                that of FIELDS_TO_PARSE, but it is not enforced here.
                Leaving here is simply a dictionary matching all the fields
                from __init__ """
            current_read = {}
            for field, label in cls.FIELDS_TO_PARSE: # forces ordering of fields
                try:
                    current_read[field] = record.pop(0).split(label, 1)[1].strip()
                except IndexError:
                    break # label/order does not match expected headers
                    #print(f"Error reading line, expecting label \"{label}<value>\"")
            else:
                message = ''.join(record).rstrip() + '\n'
                current_read['message'] = message
                return current_read

        current_record = []
        last_record = None
        last_line = ''
        # Loops through all lines in the file looking for anywhere where the last line
        # was empty, followed by the LABEL_SEP (^-^). Lines are added to the
        # current_record list and once the next LABEL_SEP combo is met, the previous
        # record parsed and casted into a Note
        with open(src, 'r') as file:
            for line in file:
                if last_line == '' and line.strip() == Note.LABEL_SEP:
                    if len(current_record):
                        yield Note(parse(current_record))
                    current_record = []
                else:
                    if current_record and Note.LABEL_PWD not in current_record[0]:
                        # if its reading a line, but the first of this record
                        current_record = last_record[0:3] # pwd, now, tag
                        current_record.append(f"{Note.LABEL_CTX}Unexpected new-record line found in data.\n" + \
                                              f"Salvaging remaining note into this new note.\n" + \
                                              f"Ignore this line up to and including Date above, to restore original form.")
                        # Adding context to this new note about why it now exists

                    current_record.append(line)
                    last_line = line.strip()
            else:
                # There is no LABEL_SEP at the end of a file, so this would be reached
                # at the end of loop, and occurs once only per file
                if last_line == '' and len(current_record):
                    yield Note(parse(current_record))

    @classmethod
    def match(cls, src, criteria, logic='and', time_only=False):
        if isinstance(criteria, tuple):
            criteria = [criteria] # force all criteria passed in as tuples into a list
                                  # and save all the boilerplate of [] everywhere else

        if logic == 'and':
            for inst in cls.iterate(src):
                CRITERIA_MET = 0
                for s_type, s_text in criteria:
                    if s_type is SearchType.ALL:
                        CRITERIA_MET += 1 # ALL, match all
                    elif not s_text:
                        pass #no matching, no incrementing
                    elif s_type is SearchType.DIRECTORY:
                        CRITERIA_MET += 1 if inst.pwd == s_text else 0
                    elif s_type is SearchType.TREE:
                        CRITERIA_MET += 1 if inst.pwd.startswith(s_text) else 0
                    elif s_type is SearchType.MESSAGE:
                        CRITERIA_MET += 1 if s_text in inst.message else 0
                    elif s_type is SearchType.MESSAGE_I:
                        CRITERIA_MET += 1 if s_text.lower() in inst.message.lower() else 0
                    elif s_type is SearchType.CONTEXT:
                        CRITERIA_MET += 1 if s_text in inst.context else 0
                    elif s_type is SearchType.CONTEXT_I:
                        CRITERIA_MET += 1 if s_text.lower() in inst.context.lower() else 0
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
                        pass #no matching, no incrementing
                    elif s_type is SearchType.DIRECTORY:
                        CRITERIA_MET += 1 if inst.pwd == s_text else 0
                    elif s_type is SearchType.TREE:
                        CRITERIA_MET += 1 if inst.pwd.startswith(s_text) else 0
                    elif s_type is SearchType.MESSAGE:
                        CRITERIA_MET += 1 if s_text in inst.message else 0
                    elif s_type is SearchType.MESSAGE_I:
                        CRITERIA_MET += 1 if s_text.lower() in inst.message.lower() else 0
                    elif s_type is SearchType.CONTEXT:
                        CRITERIA_MET += 1 if s_text in inst.context else 0
                    elif s_type is SearchType.CONTEXT_I:
                        CRITERIA_MET += 1 if s_text.lower() in inst.context.lower() else 0
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

NEWCAT = r'''-------------------------------------
     ("`-/")_.-'"``-._
      . . `; -._    )-;-,_`)
     (v_,)'  _  )`-.\  ``-'
    _.- _..-_/ / ((.'
  ((,.-'   ((,/
   ((,-'    ((,|
'''  # credits felix lee

TWOCAT = r'''_____________________________________
\            |\      _,,,---,,_      \\
 \           /,`.-'`'    -.  ;-;;,_   \\
  \         |,4-  ) )-,_..;\ (  `'-'   \\
   \ ZzZ   '---''(_/--'  `-'\_)         \\
   \ zZ    '---''(_/--'  `-'\_)         \\
   \ Z     '---''(_/--'  `-'\_)         \\
   \   Z   '---''(_/--'  `-'\_)         \\
   \  Zz   '---''(_/--'  `-'\_)         \\
'''  # credits felix lee

CATGPT_ROLE = """You're proudly a cat assistant trained to review shorthand notes
written using a command-line, filepath-based, note-taking system called "catjot"."""

def alternate_last_n_lines(text, n):
    import time
    lines = text.strip().split('\n')
    # Print all but the last 'n' lines
    for line in lines[:-n]:
        print(line)

    while True:
        for i in range(-n, 0):
            # Print one of the last 'n' lines
            print(lines[i], end='\r')
            time.sleep(1)

            # Clear the line
            print(' ' * len(lines[i]), end='\r')

def send_prompt_to_openai(messages, model_name="gpt-3.5-turbo"):
    # sends a prompt and receives the response in a json object
    # from the openai gpt completion api
    import requests
    import json
    from os import getenv

    api_key = getenv('openai_api_key')
    # set this key in your shell, e.g., this line in your ~/.bash_profile:
    #export openai_api_key="sk-proj...8EEF"
    url = 'https://api.openai.com/v1/chat/completions'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    data = {
        "model": model_name,
        "messages": messages
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error sending request: {e}")
        return None

def is_binary_string(data):
    """
    Determine if a string is binary or text by checking for non-text characters.

    :param data: A string to be checked
    :return: True if the string is binary, False if it is text
    """
    if '\x00' in data:
        return True

    # Heuristic: if more than 30% of the characters are non-text characters, it's binary
    text_characters = ''.join(map(chr, range(32, 127))) + '\n\r\t\b'
    non_text_chars = ''.join([char for char in data if char not in text_characters])

    return len(non_text_chars) / len(data) > 0.3

def print_ascii_cat_with_text(context, text):
    import textwrap
    cat = r""" /\_/\
( o.o )
 > ^ <
"""
    wrapped_text = textwrap.wrap(context, 80)

    # Determine the height of the ASCII cat
    cat_height = cat.count('\n')

    # Print the ASCII cat and the wrapped text side by side
    cat_lines = cat.split('\n')
    for i in range(max(len(cat_lines), len(wrapped_text))):
        cat_line = cat_lines[i] if i < len(cat_lines) else " " * 8
        text_line = wrapped_text[i] if i < len(wrapped_text) else ""
        print(f"{cat_line:<8} {text_line}")

    print(text)

from enum import Enum, auto

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
            for line in NEWCAT.split('\n')[0:-2]:
                print(line)
            open(self.notefile, 'a').close()
            sys.exit(1)
        except ValueError:
            print(f"Value provided does not match expected type, like having a character in an int.")
            sys.exit(3)

    def __exit__(self, exc_type, exc_value, traceback):
        pass

def main():
    import argparse
    parser = argparse.ArgumentParser(description="cat|jot notetaker")
    parser.add_argument("-a", action="store_true", help="amend last note instead of creating new note")
    parser.add_argument("-t", type=str, help="search notes by tag / set tag when amending")
    parser.add_argument("-p", type=str, help="search notes by pwd / set pwd when amending")
    parser.add_argument("-c", action="store_const", const="context", help="search notes by context / read pipe into context as amendment")
    parser.add_argument("additional_args", nargs="*", help="argument values")
    parser.add_argument("-d", action="store_true", help="only return (date)/timestamps for match")
    parser.add_argument("-gpt", action="store_true", help="create new note from gpt reply to pipe")

    args = parser.parse_args()

    NOTEFILE = Note.NOTEFILE
    import sys
    from os import environ
    if 'CATJOT_FILE' in environ:
        # the environment variable will always supercede $HOME default when set
        if environ['CATJOT_FILE']: # truthy test for env that exists but unset
            NOTEFILE = environ['CATJOT_FILE']
            with open(NOTEFILE, 'a') as file:
                pass

    # helper variables for all CLI handling

    def flatten(arg_lst):
        return ' '.join(arg_lst).rstrip()

    def flatten_pipe(arg_lst):
        return ''.join(arg_lst).rstrip()

    def printout(note_obj, message_only=False, time_only=args.d):
        if message_only:
            print(note_obj.message, end="")
        elif time_only:
            print(note_obj.now)
        else: # normal display
            print(Note.REC_TOP)
            print(note_obj, end="")
            print(Note.REC_BOT)

    params = {}
    if args.c: params['context'] = flatten(args.additional_args)
    if args.t: params['tag'] = args.t
    if args.p: params['pwd'] = args.p
    if args.a and (args.c or args.t or args.p):
        # amend engaged, and at least one amendable value provided
        # Accepted Usage:
        # jot -ac this is how i feel
        # jot -at personal_feelings
        # jot -ap /home/user
        # jot -ac "this is how i feel" -t "personal_feelings"
        # jot -ac "this is how i feel" -t "personal_feelings" -p /home/user

        if sys.stdin.isatty(): # interactive tty, not a pipe!
            # accept all attrs to change and complete amendment
            Note.amend(NOTEFILE, **params)
            Note.commit(NOTEFILE)
        else: # pipe always interpreted as context, never pwd/tag
            # Accepted Usage:
            # | jot -act "celebration_notes"
            # | jot -acp "/home/user"
            # piped data will be a ''.joined string, maintaining newlines
            piped_data = flatten_pipe(sys.stdin.readlines())
            params['context'] = piped_data # overwrite anything in args
            Note.amend(NOTEFILE, **params)
            Note.commit(NOTEFILE)
        exit(0) # end logic for amending

    # gpt-related functionality
    if args.gpt:
        # this happens for interactive or not
        params['tag'] = "catgpt"
        piped_data = flatten_pipe(sys.stdin.readlines())

        full_sendout = ""
        if args.c:
            params['context'] = args.additional_args[0]
            full_sendout = params['context'] + '\n' + str(piped_data)
        else:
            if len(piped_data) < 80:
                params['context'] = piped_data
            else:
                params['context'] = f"piped data was {len(piped_data)} chars long"
            full_sendout = piped_data

        if len(args.additional_args) and args.additional_args[0] in ['home']:
            from os import environ
            params['pwd'] = environ['HOME']

        if sys.stdin.isatty(): # interactive tty, no pipe!
            # jot -gpt <enter>
            # type freely, end with CTRL-D

            print("Sending prompt:")
            print()
            print(full_sendout)
            print(Note.LABEL_SEP)

            try:
                throwaway = input("any key to submit above note (control-c to cancel)...")
            except KeyboardInterrupt:
                exit(0)
            else:
                print()

            messages = [
                {
                    "role": "system",
                    "content": CATGPT_ROLE,
                },
                {
                    "role": "user",
                    "content": full_sendout,
                }
            ]

            response = send_prompt_to_openai(messages)

            if response:
                retval = response['choices'][0]['message']['content']
                print_ascii_cat_with_text(params['context'], retval)
                Note.append(NOTEFILE, Note.jot(retval, **params))
            else:
                print("Failed to get response from OpenAI API.")
        else: # yes pipe!
            # when piping a file, not asking for confirmation
            # as the file might be huge and we dont need to replicate
            # it in the note itself.
            # better know what you're sending with this one!

            if is_binary_string(full_sendout):
                print_ascii_cat_with_text("Uh oh, the pipe I received seems to be binary data but -gpt accepts only text. "
                                          "Try another file that is text-based, instead.", "")
                exit(1)
            elif len(full_sendout.encode('utf-8')) > 16384:
                print_ascii_cat_with_text("Uh oh, the pipe I received seems to have too much data. "
                                          f"It has exceeded the 16384 character context limit (data size: {len(piped_data)})", "")
                exit(1)

            messages = [
                {
                    "role": "system",
                    "content": CATGPT_ROLE,
                },
                {
                    "role": "user",
                    "content": full_sendout,
                }
            ]

            response = send_prompt_to_openai(messages)
            if response:
                retval = response['choices'][0]['message']['content']
                print_ascii_cat_with_text(params['context'], retval)
                Note.append(NOTEFILE, Note.jot(retval, **params))
            else:
                print("Failed to get response from OpenAI API.")
    # context-related functionality
    elif args.c:
        if sys.stdin.isatty(): # interactive tty, no pipe!
            # jot -c observations
            # not intending to amend instead means match by context field
            with NoteContext(NOTEFILE, (SearchType.CONTEXT_I, params['context'])) as nc:
                for inst in nc:
                    printout(inst)
        else: # yes pipe!
            # | jot -c musings
            # write new note with provided context from arg
            piped_data = flatten_pipe(sys.stdin.readlines())
            Note.append(NOTEFILE, Note.jot(piped_data, **params))
    # tagging-related functionality
    elif args.t:
        if sys.stdin.isatty(): # interactive tty, no pipe!
            # jot -t project2
            # not intending to amend instead means match by tag field
            with NoteContext(NOTEFILE, (SearchType.TAG, params['tag'])) as nc:
                for inst in nc:
                    printout(inst)
        else: # yes pipe!
            # | jot -t project4
            # new note with tag set to [project4]
            piped_data = flatten_pipe(sys.stdin.readlines())
            Note.append(NOTEFILE, Note.jot(piped_data, **params))
    # pwd-related functionality
    elif args.p:
        if sys.stdin.isatty(): # interactive tty, no pipe!
            # jot -p /home/user
            # not intending to amend instead means match by pwd field
            with NoteContext(NOTEFILE, (SearchType.DIRECTORY, params['pwd'])) as nc:
                for inst in nc:
                    printout(inst)
        else: # yes pipe!
            piped_data = flatten_pipe(sys.stdin.readlines())
            Note.append(NOTEFILE, Note.jot(piped_data, **params))
    else:
        # for all other cases where no argparse argument is provided
        SHORTCUTS = {
            'MOST_RECENTLY_WRITTEN_ALLTIME': ['HEAD', 'head', 'h'],
            'MOST_RECENTLY_WRITTEN_HERE': ['last', 'l'],
            'MATCH_NOTE_NAIVE': ['match', 'm'],
            'MATCH_NOTE_NAIVE_I': ['search', 's', 'mi'],
            'DELETE_MOST_RECENT_PWD': ['pop', 'p'],
            'BULK_DELETE_NOTES': ['scoop'],
            'NOTES_REFERENCING_ABSENT_DIRS': ['str', 'stra', 'stray', 'strays'],
            'SHOW_ALL': ['dump', 'display', 'd'],
            'MATCH_TIMESTAMP': ['timestamp', 'ts'],
            'REMOVE_BY_TIMESTAMP': ['remove', 'r'],
            'HOMENOTES': ['home'],
            'SHOW_TAG': ['tagged', 'tag', 't'],
            'AMEND': ['amend', 'a'],
            'MESSAGE_ONLY': ['payload', 'pl'],
            'SIDE_BY_SIDE': ['sidebyside', 'sbs', 'rewrite', 'transcribe'],
            'SLEEPING_CAT': ['zzz'],
            'CHAT': ['chat', 'catgpt', 'c'],
        }
        # ZERO USER-PROVIDED PARAMETER SHORTCUTS
        if len(args.additional_args) == 0:
            # show all notes originating from this PWD
            from os import getcwd
            if sys.stdin.isatty():
                with NoteContext(NOTEFILE, (SearchType.ALL, '')) as nc:
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
                Note.append(NOTEFILE, Note.jot(flatten_pipe(sys.stdin.readlines()), **params))
        # SINGLE USER-PROVIDED PARAMETER SHORTCUTS
        elif len(args.additional_args) == 1:
            if args.additional_args[0] in SHORTCUTS['MOST_RECENTLY_WRITTEN_HERE']:
                # only display the most recently created note in this PWD
                from os import getcwd
                last_note = "No notes to show.\n"
                with NoteContext(NOTEFILE, (SearchType.DIRECTORY, getcwd())) as nc:
                    for inst in nc:
                        last_note = inst
                    else:
                        printout(last_note)
            elif args.additional_args[0] in SHORTCUTS['MOST_RECENTLY_WRITTEN_ALLTIME']:
                # only display the most recently created note in this PWD
                from os import getcwd
                last_note = "No notes to show.\n"
                with NoteContext(NOTEFILE, (SearchType.ALL, '')) as nc:
                    for inst in nc:
                        last_note = inst
                    else:
                        printout(last_note)
            elif args.additional_args[0] in SHORTCUTS['DELETE_MOST_RECENT_PWD']:
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
            elif args.additional_args[0] in SHORTCUTS['HOMENOTES']:
                # if simply typed, show home notes
                # if piped to, save as home note
                from os import environ

                if sys.stdin.isatty():
                    with NoteContext(NOTEFILE, (SearchType.DIRECTORY, environ['HOME'])) as nc:
                        for inst in nc:
                            printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes in current directory")
                else:
                    params['pwd'] = environ['HOME']
                    Note.append(NOTEFILE, Note.jot(flatten_pipe(sys.stdin.readlines()), **params))
            elif args.additional_args[0] in SHORTCUTS['SHOW_ALL']:
                # show all notes, from everywhere, everywhen
                with NoteContext(NOTEFILE, (SearchType.ALL, '')) as nc:
                    for inst in nc:
                        printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes in total")
            elif args.additional_args[0] in SHORTCUTS['MESSAGE_ONLY']:
                # returns the last message, message only (no pwd, no timestamp, no context).
                last_note = None
                with NoteContext(NOTEFILE, (SearchType.ALL, '')) as nc:
                    for inst in nc:
                        last_note = inst
                    else:
                        printout(last_note, message_only=True)
            elif args.additional_args[0] in SHORTCUTS['SLEEPING_CAT']:
                alternate_last_n_lines(TWOCAT, 5)
            elif args.additional_args[0] in SHORTCUTS['BULK_DELETE_NOTES']:
                import tempfile
                import subprocess
                import os

                last_note = None
                records = [] # will consist of (timestamp, message[0])
                with NoteContext(NOTEFILE, (SearchType.ALL, '')) as nc:
                    for inst in nc:
                        records.append((inst.now,
                                        inst.pwd.ljust(25),
                                        inst.message.split('\n')[0].strip()))

                with tempfile.NamedTemporaryFile(mode='w+t', delete=False) as f:
                    f.write(f"# Prefix any timestamp with 'd' or 's' to delete all notes matching this timestamp\n")
                    for record in records:
                        f.write(f"{record[0]}\t{record[1]}\t{record[2]}\n")
                    temp_file_name = f.name

                preferred_editor = os.environ.get('EDITOR', 'vi')  # Default to nano if EDITOR is not set
                subprocess.run([preferred_editor, temp_file_name])

                to_delete = []
                with open(temp_file_name, 'r') as f:
                    lines = f.readlines()
                    for line in lines:
                        if line.startswith('d') or line.startswith('s'):
                            try:
                                to_delete.append(int(line[1:].split('\t')[0].strip()))
                            except ValueError:
                                pass # if instruction line is delete, or too much of the line (not retaining timestamp)

                os.unlink(temp_file_name)

                for record_ts in to_delete:
                    with NoteContext(NOTEFILE, (SearchType.TIMESTAMP, record_ts)) as nc:
                        for inst in nc:
                            print(f"Removing records matching timestamp: {record_ts}")
                            Note.delete(NOTEFILE, record_ts)
                            Note.commit(NOTEFILE)
            elif args.additional_args[0] in SHORTCUTS['NOTES_REFERENCING_ABSENT_DIRS']:
                import os

                with NoteContext(NOTEFILE, (SearchType.ALL, '')) as nc:
                    matches = 0
                    for inst in nc:
                        if not os.path.exists(inst.pwd):
                            matches += 1
                            printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{matches} stray notes among")
                        print(f"{len(nc)} notes in total")
            elif args.additional_args[0] in SHORTCUTS['CHAT']:
                # submits the last note (in this dir) to an LLM-endpoint
                from os import getcwd

                last_note = "No notes to show.\n"
                with NoteContext(NOTEFILE, (SearchType.DIRECTORY, getcwd())) as nc:
                    for inst in nc:
                        last_note = inst
                    else:
                        print("Sending prompt:")
                        print()
                        print(str(last_note))
                        print(Note.LABEL_SEP)

                try:
                    throwaway = input("any key to submit above note (control-c to cancel)...")
                except KeyboardInterrupt:
                    exit(0)
                else:
                    print()

                messages = [
                    {
                        "role": "system",
                        "content": CATGPT_ROLE,
                    },
                    {
                        "role": "user",
                        "content": str(last_note),
                    }
                ]

                response = send_prompt_to_openai(messages)
                if response:
                    print_ascii_cat_with_text(getcwd(), response['choices'][0]['message']['content'])
                else:
                    print("Failed to get response from OpenAI API.")
        # TWO USER-PROVIDED PARAMETER SHORTCUTS
        elif len(args.additional_args) == 2:
            if args.additional_args[0] in SHORTCUTS['MATCH_NOTE_NAIVE']:
                # match if "term [+term2] [..]" exists in any line of the note
                flattened = flatten(args.additional_args[1:])
                with NoteContext(NOTEFILE, (SearchType.MESSAGE, flattened)) as nc:
                    for inst in nc:
                        printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS['MATCH_NOTE_NAIVE_I']:
                # match if "term [+term2] [..]" exists in any line of the note
                flattened = flatten(args.additional_args[1:])
                with NoteContext(NOTEFILE, (SearchType.MESSAGE_I, flattened)) as nc:
                    for inst in nc:
                        printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS['MATCH_TIMESTAMP']:
                # match if timestamp matches!
                flattened = int(flatten(args.additional_args[1:]))
                with NoteContext(NOTEFILE, (SearchType.TIMESTAMP, flattened)) as nc:
                    for inst in nc:
                        printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS['REMOVE_BY_TIMESTAMP']:
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
            elif args.additional_args[0] in SHORTCUTS['SHOW_TAG']:
                # show all notes with tag
                flattened = args.additional_args[1]
                with NoteContext(NOTEFILE, (SearchType.TAG, flattened)) as nc:
                    for inst in nc:
                        printout(inst)

                    if not args.d:
                        print(f"{Note.LABEL_SEP}")
                        print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS['MESSAGE_ONLY']:
                from os import getcwd
                # returns the message only (no pwd, no timestamp, no context).
                # when provided a timestamp, any notes matching timestamp
                # will be sent to stdout, concatenated in order of appearance
                flattened = int(args.additional_args[1])
                if flattened: # if truthy, e.g., timestamp, use it for search
                    with NoteContext(NOTEFILE, (SearchType.TIMESTAMP, flattened)) as nc:
                        for inst in nc:
                            printout(inst, message_only=True)
                else: # if not truthy, display pwd matches without headers
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
            elif args.additional_args[0] in SHORTCUTS['SIDE_BY_SIDE']:
                # prints a note and allows you to rewrite the line/accept line as-is
                # Acceptable Input:
                # <matched input> = matched input kept
                # <input comprised only of strip()'ed chars, including blank line> = keep original input
                # ' ' = delete line
                # <anything else> = keep new input
                import os
                from math import ceil
                last_note = "No notes to show.\n"
                last_mark = ' '

                if args.additional_args[1] in SHORTCUTS['MOST_RECENTLY_WRITTEN_HERE']:
                    # only display the most recently created note in this PWD
                    with NoteContext(NOTEFILE, (SearchType.DIRECTORY, os.getcwd())) as nc:
                        for inst in nc:
                            last_note = inst
                elif args.additional_args[1] in SHORTCUTS['MOST_RECENTLY_WRITTEN_ALLTIME']:
                    # last written note
                    with NoteContext(NOTEFILE, (SearchType.ALL, '')) as nc:
                        for inst in nc:
                            last_note = inst
                else:
                    user_timestamp = int(args.additional_args[1])
                    with NoteContext(NOTEFILE, (SearchType.ALL, '')) as nc:
                        for inst in nc:
                            last_note = inst
                            # falls back to very last note written if provided int
                            # doesnt match any existing note
                            if inst.now == user_timestamp:
                                break

                try:
                    if not last_note.message.strip(): #falsy message
                        print("No notes to side-by-side.")
                        exit(3)
                except AttributeError: #missing attribute
                    print("No notes to side-by-side.")
                    exit(3)

                newnote_lines = []
                MARKS = {
                    'check': '✓', # indicates for unchanged lines, typed
                    'circle': '⊕', # indicates for unchanged lines, untyped (empty line)
                    'x': '✗', # indicates changed line from original
                }

                longest_line_length = max(len(line) for line in last_note.message.split('\n'))
                terminal_width = os.get_terminal_size().columns
                print(f"max line length: {longest_line_length}")
                print(f"terminal_width : {terminal_width}")

                min_left_side_width = max(ceil(longest_line_length / 10) * 10, 35) # Round up to the nearest 10, or 35 min
                if terminal_width >= (min_left_side_width * 2) + 3: # last mark, pipe sep, last char
                    for line in last_note.message.split('\n'):
                        print(f"{line.rstrip().ljust(min_left_side_width)}{last_mark}|", end="")
                        usr_in = input()
                        if line.rstrip() == usr_in.rstrip(): # line matches...
                            last_mark = MARKS['check']
                            newnote_lines.append(line + '\n') # ...preserve original
                        elif usr_in == ' ': # if the line is a single space...
                            last_mark = MARKS['x'] # ... throw it away (by not appending it)
                        elif not usr_in.strip(): # if the user provided line is effectively blank...
                            last_mark = MARKS['circle']
                            newnote_lines.append(line + '\n') # ...preserve original
                        else:
                            # value is changed from original, keep provided value
                            last_mark = MARKS['x']
                            newnote_lines.append(usr_in.rstrip() + '\n')
                    else: # after all the iterating
                        addl_context = f"rewritten note from {last_note.now}"
                        new_note = {
                            'message': ''.join(newnote_lines),
                            'pwd': last_note.pwd,
                            'now': None,
                            'tag': last_note.tag,
                            'context': addl_context if not last_note.context else f"{last_note.context};{addl_context}"
                        }
                        Note.append(NOTEFILE, Note.jot(**new_note))
                else:
                    print(f"The terminal is not sufficiently wide to match double the width of the longest line in the note. Aborting")
                    exit(2)
            elif args.additional_args[0] in SHORTCUTS['MOST_RECENTLY_WRITTEN_ALLTIME']:
                # only display the most n recently, of all locations
                from collections import deque

                record_count_to_show = 1
                user_tilde_given = False
                try:
                    record_count_to_show = int(args.additional_args[1])
                except ValueError:
                    # if user includes ~ (tilde), show ONLY the one note, counting backwards
                    if args.additional_args[1].startswith('~'):
                        record_count_to_show = int(args.additional_args[1][1:])
                        user_tilde_given = True

                last_notes = deque(maxlen=record_count_to_show)
                with NoteContext(NOTEFILE, (SearchType.ALL, '')) as nc:
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
            elif args.additional_args[0] in SHORTCUTS['MOST_RECENTLY_WRITTEN_HERE']:
                # only display the most recently created n notes in this PWD
                from collections import deque
                from os import getcwd

                record_count_to_show = 1
                user_tilde_given = False
                try:
                    record_count_to_show = int(args.additional_args[1])
                except ValueError:
                    # if user includes ~ (tilde), show ONLY the one note, counting backwards
                    if args.additional_args[1].startswith('~'):
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
            elif args.additional_args[0] in SHORTCUTS['CHAT']:
                # submits the last note (in this dir) to an LLM-endpoint
                # or alternatively, send it a prompt enclosed in quotes
                full_msg = ""
                flattened = ""
                try:
                    flattened = int(args.additional_args[1])
                    if flattened: # if truthy, e.g., timestamp, use it for search
                        with NoteContext(NOTEFILE, (SearchType.TIMESTAMP, flattened)) as nc:
                            full_msg = f"\n\n".join([str(inst) for inst in nc])
                            print(full_msg)
                except ValueError:
                    # if not a timestamp, just send it directly as is
                    full_msg = args.additional_args[1]
                    print("Sending prompt:")
                    print()
                    print(full_msg)
                    print(Note.LABEL_SEP)

                try:
                    throwaway = input("any key to submit above note (control-c to cancel)...")
                except KeyboardInterrupt:
                    exit(0)
                else:
                    print()

                messages = [
                    {
                        "role": "system",
                        "content": CATGPT_ROLE,
                    },
                    {
                        "role": "user",
                        "content": full_msg,
                    }
                ]

                response = send_prompt_to_openai(messages)
                if response:
                    print_ascii_cat_with_text(full_msg, response['choices'][0]['message']['content'])
                else:
                    print("Failed to get response from OpenAI API.")

if __name__ == "__main__":
    main()

