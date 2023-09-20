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
    # REC_TOP    |
    # LABEL_DIR  | > cd /home/user
    # LABEL_DATE | # date 2023-09-17 19:01:10 (1695002470)
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
    LABEL_ARG = "" # additional prefixing label for first line of note data

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
        return f"{Note.LABEL_DIR}{self.pwd}\n" + \
               f"{Note.LABEL_DATE}{friendly_date}\n" + \
               f"{Note.LABEL_DATA}{self.message}"

    @classmethod
    def append(cls, src, message, pwd=None, now=None):
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

        def export_note(basic_struct):
            """ Constructs a note based on collected information from the iteration below """
            retval = Note()
            retval.pwd = basic_struct['dir']
            assert retval.pwd.startswith("/")
            retval.now = int(basic_struct['now'])
            retval.message = ''.join(basic_struct['msg'])
            return retval

        def clean_extra_newlines(msg_lst):
            """ Accepts a list and removes any newline-only lines """
            while len(msg_lst) > 1 and msg_lst[-1] == '\n':
                msg_lst.pop()

        current_read = {'msg': []}
        with open(src, 'r') as file:
            # open the file, read-only
            line = file.readline()
            while line:
                # cleaned exists for line identification only.
                # once the purpose of a line is determined, the content is sanitized more
                # surgically starting from the original line contents.
                cleaned = line.strip()
                if cleaned == Note.LABEL_SEP:
                    if len(current_read['msg']) > 0:
                        clean_extra_newlines(current_read['msg'])
                        yield export_note(current_read)

                    current_read['dir'] = file.readline().rstrip().split(Note.LABEL_PWD)[1]
                    current_read['now'] = file.readline().rstrip().split(Note.LABEL_NOW)[1]
                    current_read['msg'] = []
                else:
                    if 'dir' in current_read and 'now' in current_read:
                        current_read['msg'].append(line.rstrip() + '\n')

                line = file.readline()

            if 'dir' in current_read and 'now' in current_read:
                clean_extra_newlines(current_read['msg'])
                yield export_note(current_read)

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

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Note Parser")
    parser.add_argument("-a", action="store_true", help="append single-line message")
    parser.add_argument("-s", action="store_true", help="case-insensitive search for term")
    parser.add_argument("-d", action="store_true", help="delete any notes matching timestamp")
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
        }

        if not sys.stdin.isatty(): # is not a tty, but is a the pipe
            # default append, will accept lines with no limit
            full_input = [line for line in sys.stdin]
            pwd = None
            if args.additional_args[0] in SHORTCUTS['HOMENOTES']:
                # if simply typed, show home notes
                # if piped to, save as home note
                from os import environ
                Note().append(NOTEFILE, ''.join(full_input), pwd=environ['HOME'])
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

if __name__ == "__main__":
    main()

