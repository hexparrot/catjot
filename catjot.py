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
    LABEL_ARG = "" # additional prefixing label for first line of note data

    # All required fields to exist before note data
    FIELDS_TO_PARSE = [
        ('dir', LABEL_PWD),
        ('now', LABEL_NOW),
        ('tag', LABEL_TAG),
        ('context', LABEL_CTX),
    ]

    # Filepath to save to, saves in $HOME
    NOTEFILE = f"{environ['HOME']}/.catjot"

    def __init__(self):
        pass

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
    def amend(cls, src, context):
        last_record = None
        for inst in cls.iterate(src):
            last_record = inst

        newpath = src + ".new"
        with open(newpath, 'wt') as trunc_file:
            for inst in cls.iterate(src):
                if int(inst.now) != int(last_record.now):
                    trunc_file.write(f"{Note.LABEL_SEP}\n")
                    trunc_file.write(f"{Note.LABEL_PWD}{inst.pwd}\n")
                    trunc_file.write(f"{Note.LABEL_NOW}{inst.now}\n")
                    trunc_file.write(f"{Note.LABEL_TAG}{inst.tag}\n")
                    trunc_file.write(f"{Note.LABEL_CTX}{inst.context}\n")
                    trunc_file.write(f"{Note.LABEL_ARG}{inst.message}\n\n")
                else:
                    trunc_file.write(f"{Note.LABEL_SEP}\n")
                    trunc_file.write(f"{Note.LABEL_PWD}{last_record.pwd}\n")
                    trunc_file.write(f"{Note.LABEL_NOW}{last_record.now}\n")
                    trunc_file.write(f"{Note.LABEL_TAG}{last_record.tag}\n")
                    trunc_file.write(f"{Note.LABEL_CTX}{context}\n")
                    trunc_file.write(f"{Note.LABEL_ARG}{last_record.message}\n\n")

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

        current_read = {'msg': []}
        with open(src, 'r') as file:
            # open the file, read-only
            line = file.readline()
            LINES_BEFORE_GIVEUP = 1000      # Number of lines to read without matching a record header
                                            # This is a very inexpensive operation, as it is simple
                                            # line reads, but should not be infinite.
            lines_skipped_counter = 0
            lastline_lost = False
            while line:
                cleaned = line.strip()
                # cleaned exists for line identification only.
                # once the purpose of a line is determined, the content is kept more or less
                # completely intact, with the exception of rstrip() and then manually
                # readding the newline \n
                if lines_skipped_counter > LINES_BEFORE_GIVEUP:
                    # lines_skipped_counter shows the number of times a readline has been
                    # executed for a LABEL_SEP match `^-^` was not followed by:
                    # LABEL_PWD ... LABEL_NOW ...
                    # This kind of corruption is unexpected to ever experience in the wild,
                    # but nonetheless not all edge cases assuredly have been accounted for.
                    # This number, simply put, represents the number of lines the application
                    # should continue to keep trying to find a valid record header
                    # if it processes a record separator in a jot: `^-^\n`
                    # Precisely the record separator, followed by a newline.
                    # and therefore mistakenly truncates the record right then and there,
                    # until the start of a valid record header n number down.
                    lastline_lost = False
                    line = file.readline()
                    continue

                if cleaned == Note.LABEL_SEP:
                    if 'dir' in current_read and 'now' in current_read \
                        and len(current_read['msg']) > 0:
                        yield cls.create(current_read)

                    try:
                        # In this current design, the ordering of fields is non-negotiable;
                        # Written in-file must match this exact ordering.
                        for field, label in cls.FIELDS_TO_PARSE:
                            current_read[field] = file.readline().split(label)[1].strip()
                    except IndexError:
                        lastline_lost = line
                        lines_skipped_counter += 1
                        current_read.pop('dir', None)
                        current_read.pop('now', None)
                        current_read['msg'] = []
                    else:
                        line = file.readline()
                        current_read['msg'] = [line.rstrip() + '\n']
                        lastline_lost = False
                        lines_skipped_counter = 0
                else:
                    if 'dir' in current_read and 'now' in current_read:
                        current_read['msg'].append(line.rstrip() + '\n')

                if not lastline_lost:
                    # in the event of a malformed record, esp multiple LABEL_NOW in a row
                    line = file.readline()

            if 'dir' in current_read and 'now' in current_read:
                yield cls.create(current_read)

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
                if tag.lower() == inst.tag.lower():
                    yield inst
            except AttributeError: # it's Nonetype from having nothing set
                pass

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Note Parser")
    parser.add_argument("-a", action="store_true", help="append single-line message")
    parser.add_argument("-s", action="store_true", help="case-insensitive search for term")
    parser.add_argument("-d", action="store_true", help="delete any notes matching timestamp")
    parser.add_argument("-t", action="store", help="tag it with a word")
    parser.add_argument("additional_args", nargs="*", help="argument values for search, delete, and append")

    args = parser.parse_args()

    NOTEFILE = Note.NOTEFILE
    import sys
    from os import environ
    if 'CATJOT_FILE' in environ:
        # the environment variable will always supercede $HOME default when set
        if environ['CATJOT_FILE']: # truthy test for env that exists but unset
            NOTEFILE = environ['CATJOT_FILE']

    if args.a:  # requesting appending
        # USAGE: jot -a "this is my note"
        # USAGE: <somepipe> | jot -a
        # `-a` limits input to a single line as demonstrated above.
        # in-line (not at end) escaped chars will be captured as-is, e.g., "\n"
        if sys.stdin.isatty(): # is interactive terminal
            try:
                flattened = ' '.join(args.additional_args).rstrip()
            except IndexError:
                sys.exit(3)
        else: # is piped input
            flattened = sys.stdin.readline().strip()

        Note().append(NOTEFILE, flattened)
    elif args.d: # requesting deletion
        # USAGE: jot -d 1234567890
        # USAGE: <somepipe> | jot -d
        # `-d` accepts only a single line, an integer representing a timestamp.
        # *all* notes with matching timestamp will be deleted, in all paths
        if sys.stdin.isatty(): # is interactive terminal
            try:
                flattened = args.additional_args[0]
            except IndexError:
                sys.exit(3)
        else: # is piped input
            flattened = sys.stdin.readline().strip()

        try:
            Note().delete(NOTEFILE, int(flattened))
            Note().commit(NOTEFILE)
        except FileNotFoundError:
            print(f"No notefile found at {NOTEFILE}")
            sys.exit(1)
        except TypeError:
            print(f"No note to pop for this path in {NOTEFILE}")
            sys.exit(2)
    elif args.s: # requesting search
        # USAGE: jot -s "search term"
        # USAGE: <somepipe> | jot -s
        # `-s` accepts only a single line, a string to match
        # *all* notes with matching string will be displayed, in all paths
        # search is case-insensitive and does not span multiple lines
        if sys.stdin.isatty(): # is interactive terminal
            try:
                flattened = args.additional_args[0]
            except IndexError:
                sys.exit(3)
        else: # is piped input
            flattened = sys.stdin.readline().strip()

        try:
            for inst in Note().search_i(NOTEFILE, flattened):
                print(Note.REC_TOP)
                print(inst, end="")
                print(Note.REC_BOT)
        except FileNotFoundError:
            print(f"No notefile found at {NOTEFILE}")
            sys.exit(1)
    else: # no hyphenated args provided
        # Available shortcuts listed below, choose as many words you like
        # for how to match, including abbreviations.
        # Be mindful to not have any duplicate keys!
        SHORTCUTS = {
            'MOST_RECENT': ['last', 'l'],
            'MATCH_NOTE_NAIVE': ['match', 'm'],
            'MATCH_NOTE_NAIVE_I': ['search', 's'],
            'DELETE_MOST_RECENT_PWD': ['pop', 'p'],
            'SHOW_ALL': ['dump', 'display', 'd'],
            'REMOVE_BY_TIMESTAMP': ['remove', 'r'],
            'HOMENOTES': ['home', 'h'],
            'SHOW_TAG': ['tagged', 'tag', 't'],
            'AMEND': ['amend', 'a'],
        }

        if not sys.stdin.isatty(): # is not a tty, but is a the pipe
            # default append, will accept lines with no limit
            full_input = [line for line in sys.stdin]
            pwd = None
            if 'additional_args' in args and \
                args.additional_args and \
                args.additional_args[0] in SHORTCUTS['HOMENOTES']:
                # if simply typed, show home notes
                # if piped to, save as home note
                from os import environ
                if args.t:
                    Note().append(NOTEFILE, ''.join(full_input), pwd=environ['HOME'], tag=args.t)
                else:
                    Note().append(NOTEFILE, ''.join(full_input), pwd=environ['HOME'])
            else:
                if args.t:
                    Note().append(NOTEFILE, ''.join(full_input), tag=args.t)
                else:
                    Note().append(NOTEFILE, ''.join(full_input))
        else: # is interactive tty
            # jot executed with no additional params, interactively
            import sys

            if len(args.additional_args) == 0:
                # show notes originating from this PWD
                from os import getcwd
                try:
                    count = 0
                    for inst in Note().match_dir(NOTEFILE, getcwd()):
                        count += 1
                        print(Note.REC_TOP)
                        print(inst, end="")
                        print(Note.REC_BOT)
                    else:
                        child_matches = len(list(Note().list(NOTEFILE)))
                        print(f"{child_matches-count} matches in child directories")
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
            elif len(args.additional_args) == 1:
                if args.additional_args[0] in SHORTCUTS['MOST_RECENT']:
                    # always displays the most recently created note in this PWD
                    last_note = "No notes to show.\n"
                    for note in Note().list(NOTEFILE):
                        last_note = note
                    else:
                        print(Note.REC_TOP)
                        print(last_note, end="")
                        print(Note.REC_BOT)
                elif args.additional_args[0] in SHORTCUTS['DELETE_MOST_RECENT_PWD']:
                    # always deletes the most recently created note in this PWD
                    from os import getcwd
                    try:
                        Note().pop(NOTEFILE, getcwd())
                        Note().commit(NOTEFILE)
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
                    try:
                        count = 0
                        for inst in Note().match_dir(NOTEFILE, environ['HOME']):
                            count += 1
                            print(Note.REC_TOP)
                            print(inst, end="")
                            print(Note.REC_BOT)
                        else:
                            child_matches = len(list(Note().list(NOTEFILE)))
                            print(f"{child_matches-count} matches in child directories")
                    except FileNotFoundError:
                        print(f"No notefile found at {NOTEFILE}")
                        sys.exit(1)
                elif args.additional_args[0] in SHORTCUTS['SHOW_ALL']:
                    # show all notes, from everywhere, everywhen
                    try:
                        for inst in Note().iterate(NOTEFILE):
                            print(Note.REC_TOP)
                            print(inst, end="")
                            print(Note.REC_BOT)
                    except FileNotFoundError:
                        print(f"No notefile found at {NOTEFILE}")
                        sys.exit(1)
            elif len(args.additional_args) == 2:
                if args.additional_args[0] in SHORTCUTS['MATCH_NOTE_NAIVE']:
                    # match if "term [+term2] [..]" exists in any line of the note
                    try:
                        flattened = ' '.join(args.additional_args[1:])
                        for inst in Note().search(NOTEFILE, flattened):
                            print(Note.REC_TOP)
                            print(inst, end="")
                            print(Note.REC_BOT)
                    except FileNotFoundError:
                        print(f"No notefile found at {NOTEFILE}")
                        sys.exit(1)
                elif args.additional_args[0] in SHORTCUTS['MATCH_NOTE_NAIVE_I']:
                    # match if "term [+term2] [..]" exists, case-insensitive!
                    try:
                        flattened = ' '.join(args.additional_args[1:])
                        for inst in Note().search(NOTEFILE, flattened):
                            print(Note.REC_TOP)
                            print(inst, end="")
                            print(Note.REC_BOT)
                    except FileNotFoundError:
                        print(f"No notefile found at {NOTEFILE}")
                        sys.exit(1)
                elif args.additional_args[0] in SHORTCUTS['REMOVE_BY_TIMESTAMP']:
                    # delete any notes matching a precise timestamp
                    try:
                        Note().delete(NOTEFILE, int(args.additional_args[1]))
                        Note().commit(NOTEFILE)
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
                    try:
                        flattened = args.additional_args[1]
                        for inst in Note().tagged(NOTEFILE, flattened):
                            print(Note.REC_TOP)
                            print(inst, end="")
                            print(Note.REC_BOT)
                    except FileNotFoundError:
                        print(f"No notefile found at {NOTEFILE}")
                        sys.exit(1)
                elif args.additional_args[0] in SHORTCUTS['AMEND']:
                    flattened = args.additional_args[1].strip()
                    Note.amend(NOTEFILE, flattened)
                    Note.commit(NOTEFILE)
            else:
                if args.additional_args[0] in SHORTCUTS['AMEND']:
                    flattened = ' '.join(args.additional_args[1:]).strip()
                    Note.amend(NOTEFILE, flattened)
                    Note.commit(NOTEFILE)

if __name__ == "__main__":
    main()

