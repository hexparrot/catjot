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
        ('dir', LABEL_PWD),
        ('now', LABEL_NOW),
        ('tag', LABEL_TAG),
        ('context', LABEL_CTX),
    ]

    # Filepath to save to, saves in $HOME
    NOTEFILE = f"{environ['HOME']}/.catjot"

    def __init__(self, values_dict={}):
        import os
        from time import time

        if 'message' not in values_dict:
            return

        now = int(time())
        self.pwd = values_dict.get('pwd', os.getcwd())
        assert self.pwd.startswith('/')
        self.now = values_dict.get('now', now)
        assert isinstance(self.now, int)
        self.tag = values_dict.get('tag', '')
        assert isinstance(self.tag, str)
        self.context = values_dict.get('context', '')
        self.message = values_dict.get('message', '')

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

    @classmethod
    def create(cls, basic_struct):
        retval = Note()

        retval.pwd = basic_struct['dir']
        assert retval.pwd.startswith("/")
        retval.now = int(basic_struct['now'])
        retval.tag = basic_struct.get('tag', "")
        retval.context = basic_struct.get('context', "")

        if isinstance(basic_struct['msg'], str):
            retval.message = basic_struct['msg'].rstrip() + '\n'
        elif isinstance(basic_struct['msg'], list):
            while len(basic_struct['msg']) > 1 and basic_struct['msg'][-1] == '\n':
                basic_struct['msg'].pop()
            retval.message = ''.join(basic_struct['msg'])

        if retval.message.startswith(Note.LABEL_ARG):
            retval.message = retval.message.lstrip(Note.LABEL_ARG)

        return retval

    @classmethod
    def append(cls, src, message, pwd=None, now=None, tag="", context=""):
        """ Accepts non-falsy text and writes it to the .catjot file. """
        if not message: return
        if not pwd: pwd = getcwd()
        if not now:
            from time import time
            now = int(time())

        with open(src, 'at') as file:
            file.write(f"{Note.LABEL_SEP}\n")
            file.write(f"{Note.LABEL_PWD}{pwd}\n")
            file.write(f"{Note.LABEL_NOW}{now}\n")
            file.write(f"{Note.LABEL_TAG}{tag}\n")
            file.write(f"{Note.LABEL_CTX}{context}\n")
            file.write(f"{Note.LABEL_ARG}{message}\n\n")

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
        for inst in cls.match_dir(src, path):
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
            current_read = {}
            # forces ordering of fields
            for field, label in cls.FIELDS_TO_PARSE:
                try:
                    current_read[field] = record.pop(0).split(label)[1].strip()
                except IndexError:
                    pass # label/order does not match expected headers
                    #print(f"Error reading line, expecting label \"{label}<value>\"")
            else:
                message = ''.join(record)
                current_read['msg'] = message
                return current_read

        current_record = []
        last_record = None
        last_line = ''
        with open(src, 'r') as file:
            for line in file:
                if last_line == '' and line.strip() == Note.LABEL_SEP:
                    if len(current_record):
                        import copy
                        last_record = copy.deepcopy(current_record)
                        yield Note.create(parse(current_record))
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
                if last_line == '' and len(current_record):
                    yield Note.create(parse(current_record))

    @classmethod
    def list(cls, src):
        """ Convenience function to iterate all notes corresponding to PWD
            *as well* as all notes held by sub-directories. """
        from os import getcwd

        pwd = getcwd()
        for inst in cls.iterate(src):
            if pwd in inst.pwd:
                yield inst

    @classmethod
    def match_dir(cls, src, path_match):
        """ Convenience function to iterate all notes corresponding to PWD ONLY """
        for inst in cls.iterate(src):
            if path_match == inst.pwd:
                yield inst

    @classmethod
    def match_time(cls, src, timestamp):
        """ Convenience function to iterate all notes corresponding to 'now' ONLY """
        for inst in cls.iterate(src):
            if int(timestamp) == inst.now:
                yield inst

    @classmethod
    def search_context_i(cls, src, context):
        """ Convenience function to iterate all notes matching context ONLY """
        for inst in cls.iterate(src):
            if context.lower() in inst.context.lower():
                yield inst

    @classmethod
    def search(cls, src, term):
        """ Match any notes that contain term in message, single-line comparison.
            case SENSITIVE """
        for inst in cls.iterate(src):
            if term in inst.message:
                yield inst

    @classmethod
    def search_i(cls, src, term):
        """ Match any notes that contain term in message, single-line comparison.
            case INSENSITIVE """
        for inst in cls.iterate(src):
            if term.lower() in inst.message.lower():
                yield inst

    @classmethod
    def tagged(cls, src, tag):
        """ Match any notes that contain match of tag
            case INSENSITIVE """
        for inst in cls.iterate(src):
            try:
                if tag.lower() in inst.tag.lower().split(' '):
                    yield inst
            except AttributeError: # it's Nonetype from having nothing set
                pass

class NoteContext:
    def __init__(self, notefile, method, params={}):
        self.notefile = notefile
        self.method = method
        self.params = params

    def __enter__(self):
        try:
            method = getattr(Note, self.method)
            return [i for i in method(self.notefile, **self.params)]
        except FileNotFoundError:
            print(f"No notefile found at {NOTEFILE}")
            sys.exit(1)

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

    args = parser.parse_args()

    NOTEFILE = Note.NOTEFILE
    import sys
    from os import environ
    if 'CATJOT_FILE' in environ:
        # the environment variable will always supercede $HOME default when set
        if environ['CATJOT_FILE']: # truthy test for env that exists but unset
            NOTEFILE = environ['CATJOT_FILE']

    # helper variables for all CLI handling

    def flatten(arg_lst):
        return ' '.join(arg_lst).rstrip()

    def flatten_pipe(arg_lst):
        return ''.join(arg_lst).rstrip()

    def printout(note_obj, message_only=False):
        if message_only:
            print(note_obj.message, end="")
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

    # context-related functionality
    if args.c:
        if sys.stdin.isatty(): # interactive tty, no pipe!
            # jot -c observations
            # not intending to amend instead means match by context field
            with NoteContext(NOTEFILE, "search_context_i", { 'context': params['context'] }) as nc:
                for inst in nc:
                    printout(inst)
        else: # yes pipe!
            # | jot -c musings
            # write new note with provided context from arg
            piped_data = flatten_pipe(sys.stdin.readlines())
            Note.append(NOTEFILE, piped_data, **params)
    # tagging-related functionality
    elif args.t:
        if sys.stdin.isatty(): # interactive tty, no pipe!
            # jot -t project2
            # not intending to amend instead means match by tag field
            with NoteContext(NOTEFILE, "tagged", { 'tag': params['tag'] }) as nc:
                for inst in nc:
                    printout(inst)
        else: # yes pipe!
            # | jot -t project4
            # new note with tag set to [project4]
            piped_data = flatten_pipe(sys.stdin.readlines())
            Note.append(NOTEFILE, piped_data, **params)
    # pwd-related functionality
    elif args.p:
        if sys.stdin.isatty(): # interactive tty, no pipe!
            # jot -p /home/user
            # not intending to amend instead means match by pwd field
            with NoteContext(NOTEFILE, "match_dir", { 'path_match': params['pwd'] }) as nc:
                for inst in nc:
                    printout(inst)
        else: # yes pipe!
            piped_data = flatten_pipe(sys.stdin.readlines())
            Note.append(NOTEFILE, piped_data, **params)
    else:
        # for all other cases where no argparse argument is provided
        SHORTCUTS = {
            'MOST_RECENT': ['last', 'l'],
            'MATCH_NOTE_NAIVE': ['match', 'm'],
            'MATCH_NOTE_NAIVE_I': ['search', 's', 'mi'],
            'DELETE_MOST_RECENT_PWD': ['pop', 'p'],
            'SHOW_ALL': ['dump', 'display', 'd'],
            'REMOVE_BY_TIMESTAMP': ['remove', 'r'],
            'HOMENOTES': ['home', 'h'],
            'SHOW_TAG': ['tagged', 'tag', 't'],
            'AMEND': ['amend', 'a'],
            'MESSAGE_ONLY': ['payload', 'pl'],
            'SIDE_BY_SIDE': ['sidebyside', 'sbs'],
        }
        # ZERO USER-PROVIDED PARAMETER SHORTCUTS
        if len(args.additional_args) == 0:
            # show all notes originating from this PWD
            from os import getcwd
            if sys.stdin.isatty():
                with NoteContext(NOTEFILE, "iterate", {}) as nc:
                    match_count = 0
                    non_match_count = 0
                    total_count = 0
                    for inst in nc:
                        total_count += 1
                        if getcwd() == inst.pwd:
                            match_count += 1
                            print(inst)
                        else:
                            non_match_count += 1

                print(f"{Note.LABEL_SEP}")
                print(f"{match_count} notes in current directory")
                print(f"{non_match_count} notes in child directories")
                print(f"{total_count} total notes overall")
            else:
                Note.append(NOTEFILE, flatten_pipe(sys.stdin.readlines()), **params)
        # SINGLE USER-PROVIDED PARAMETER SHORTCUTS
        elif len(args.additional_args) == 1:
            if args.additional_args[0] in SHORTCUTS['MOST_RECENT']:
                # only display the most recently created note in this PWD
                from os import getcwd
                last_note = "No notes to show.\n"
                with NoteContext(NOTEFILE, "match_dir", { 'path_match': getcwd() }) as nc:
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
                    with NoteContext(NOTEFILE, "match_dir", { 'path_match': environ['HOME'] }) as nc:
                        for inst in nc:
                            printout(inst)

                    print(f"{Note.LABEL_SEP}")
                    print(f"{len(nc)} notes in current directory")
                else:
                    params['pwd'] = environ['HOME']
                    Note.append(NOTEFILE, flatten_pipe(sys.stdin.readlines()), **params)
            elif args.additional_args[0] in SHORTCUTS['SHOW_ALL']:
                # show all notes, from everywhere, everywhen
                with NoteContext(NOTEFILE, "iterate", {}) as nc:
                    for inst in nc:
                        printout(inst)

                    print(f"{Note.LABEL_SEP}")
                    print(f"{len(nc)} notes in total")
            elif args.additional_args[0] in SHORTCUTS['MESSAGE_ONLY']:
                # returns the last message, message only (no pwd, no timestamp, no context).
                last_note = None
                try:
                    for inst in Note.list(NOTEFILE):
                        last_note = inst
                    else:
                        printout(last_note, message_only=True)
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
                except AttributeError:
                    print(f"No notes to show.")
                    sys.exit(2)
        # TWO USER-PROVIDED PARAMETER SHORTCUTS
        elif len(args.additional_args) == 2:
            if args.additional_args[0] in SHORTCUTS['MATCH_NOTE_NAIVE']:
                # match if "term [+term2] [..]" exists in any line of the note
                flattened = flatten(args.additional_args[1:])
                print(flattened)
                with NoteContext(NOTEFILE, "search", { 'term': flattened }) as nc:
                    for inst in nc:
                        printout(inst)

                    print(f"{Note.LABEL_SEP}")
                    print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS['MATCH_NOTE_NAIVE_I']:
                # match if "term [+term2] [..]" exists in any line of the note
                flattened = flatten(args.additional_args[1:])
                with NoteContext(NOTEFILE, "search_i", { 'term': flattened }) as nc:
                    for inst in nc:
                        printout(inst)

                    print(f"{Note.LABEL_SEP}")
                    print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS['REMOVE_BY_TIMESTAMP']:
                # delete any notes matching the provided timestamp
                try:
                    Note.delete(NOTEFILE, int(args.additional_args[1]))
                    Note.commit(NOTEFILE)
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
                except TypeError:
                    print(f"No note to pop for this path in {NOTEFILE}")
                    sys.exit(2)
                except ValueError:
                    print(f"Timestamp argument not an integer value.")
                    sys.exit(3)
            elif args.additional_args[0] in SHORTCUTS['SHOW_TAG']:
                # show all notes with tag
                flattened = args.additional_args[1]
                with NoteContext(NOTEFILE, "tagged", { 'tag': flattened }) as nc:
                    for inst in nc:
                        printout(inst)

                    print(f"{Note.LABEL_SEP}")
                    print(f"{len(nc)} notes matching '{flattened}'")
            elif args.additional_args[0] in SHORTCUTS['MESSAGE_ONLY']:
                # returns the message only (no pwd, no timestamp, no context).
                # when provided a timestamp, any notes matching timestamp
                # will be sent to stdout, concatenated in order of appearance
                flattened = args.additional_args[1]
                if flattened: # if truthy, e.g., timestamp, use it for search
                    try:
                        for inst in Note.match_time(NOTEFILE, flattened):
                            printout(inst, message_only=True)
                    except FileNotFoundError:
                        print(f"No notefile found at {NOTEFILE}")
                        sys.exit(1)
                    except ValueError:
                        print(f"Provided argument must be an int <timestamp>.")
                        sys.exit(2)
                else: # if not truthy, display pwd matches without headers
                    # the fallback behavior of finding a non truthy value is not
                    # expected to happen normally. this would be providing
                    # a deliberately nonuseful value: |jot pl ""
                    # the actual implementation of payload without timestamp
                    # should be handled in # ONE USER-PROVIDED PARAMETER SHORTCUTS

                    # always displays the most recently created note in this PWD
                    last_note = None
                    try:
                        for inst in Note.list(NOTEFILE):
                            last_note = inst
                        else:
                            printout(last_note, message_only=True)
                    except FileNotFoundError:
                        print(f"No notefile found at {NOTEFILE}")
                        sys.exit(1)
                    except AttributeError:
                        print(f"No notes to show.")
                        sys.exit(2)
            elif args.additional_args[0] in SHORTCUTS['SIDE_BY_SIDE']:
                # prints a note and allows you to rewrite the line/accept line as-is
                # Acceptable Input:
                # <matched input> = matched input kept
                # <input comprised only of strip()'ed chars, including blank line> = keep original input
                # ' ' = delete line
                # <anything else> = keep new input
                import os
                from math import ceil

                last_note = None
                last_mark = ' '
                user_timestamp = int(args.additional_args[1])
                
                with NoteContext(NOTEFILE, "iterate", {}) as nc:
                    for inst in nc:
                        if inst.now == user_timestamp:
                            last_note = inst
                            break
                    else: # if no match, and avoided "break"
                        print(f"{Note.LABEL_SEP}")
                        print(f"No note to display matching this timestamp ({user_timestamp}) in {NOTEFILE}")
                        #exit(2)

                    # this path accessible only if last_note was successfully populated else exited(2)
                    if not last_note:
                        last_note = inst

                newnote_lines = []
                MARKS = {
                    'check': '✓',
                    'circle': '⊕',
                    'x': '✗',
                }

                longest_line_length = max(len(line) for line in last_note.message.split('\n'))
                terminal_width = os.get_terminal_size().columns
                print(f"max line length: {longest_line_length}")
                print(f"terminal_width : {terminal_width}")

                min_left_side_width = max(ceil(longest_line_length / 10) * 10, 35) # Round up to the nearest 10, or 35 min
                if terminal_width >= (min_left_side_width * 2) + 3: # last mark, pipe sep, last char
                    for line in last_note.message.split():
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
                        Note.append(NOTEFILE, **new_note)
                else:
                    print(f"The terminal is not sufficiently wide to match double the width of the longest line in the note. Aborting")
                    exit(2)


if __name__ == "__main__":
    main()

