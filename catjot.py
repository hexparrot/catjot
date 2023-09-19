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
    REC_TOP = ""
    REC_BOT = "***************"

    # .catjot file formatting
    LABEL_PWD = "Directory:"
    LABEL_NOW = "Date:"
    LABEL_ARG = "" # additional prefixing label for first line of note data

    # Filepath to save to, saves in $HOME
    NOTEFILE = f"{environ['HOME']}/.catjot"

    def __init__(self):
        from time import time

        self.now = time()
        self.pwd = getcwd()

    def __str__(self):
        from datetime import datetime
        dt = datetime.fromtimestamp(self.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        return f"{Note.LABEL_DIR}{self.pwd}\n" + \
               f"{Note.LABEL_DATE}{friendly_date}\n" + \
               f"{Note.LABEL_DATA}{self.message}\n"

    @classmethod
    def append(cls, src, term):
        from time import time

        with open(src, 'at') as file:
            file.write(f"{Note.LABEL_PWD}{getcwd()}\n")
            file.write(f"{Note.LABEL_NOW}{int(time())}\n")
            file.write(f"{Note.LABEL_ARG}{term}\n\n")

    @classmethod
    def delete(cls, src, timestamp):
        import os

        newpath = src + ".new"
        with open(newpath, 'wt') as trunc_file:
            for inst in cls.iterate(src):
                if int(inst.now) != int(timestamp):
                    trunc_file.write(f"{Note.LABEL_PWD}{inst.pwd}\n")
                    trunc_file.write(f"{Note.LABEL_NOW}{inst.now}\n")
                    trunc_file.write(f"{Note.LABEL_ARG}{inst.message}\n\n")

    @classmethod
    def pop(cls, src, path):
        import os

        last_record = None
        for inst in cls.match_dir(src, path):
            last_record = inst.now
        else:
            cls.delete(src, last_record)

    @classmethod
    def commit(cls, src):
        import shutil

        shutil.move(src, src + ".old")
        shutil.move(src + ".new", src)

    @classmethod
    def iterate(cls, src):
        from collections import defaultdict

        def export_note(basic_struct):
            retval = Note()
            retval.now = basic_struct['now']
            retval.pwd = basic_struct['dir']
            retval.message = ''.join(basic_struct['msg'])
            return retval

        current_read = defaultdict(str)
        current_read['msg'] = []
        with open(src, 'r') as file:
            # open example note and find all matched lines
            for line in file:
                cleaned = line.strip()
                if cleaned.startswith(f"{Note.LABEL_PWD}/"):
                    # this forces the / as part of the directory/first line match
                    if len(current_read['msg']) > 0:
                        while len(current_read['msg']) > 1 and current_read['msg'][-1] == '\n':
                            current_read['msg'].pop()
                        yield export_note(current_read)

                    current_read = defaultdict(str)
                    current_read['msg'] = []
                    current_read['dir'] = cleaned.split(Note.LABEL_PWD)[1]
                elif cleaned.startswith(Note.LABEL_NOW):
                    current_read['now'] = int(cleaned.split(Note.LABEL_NOW)[1])
                else:
                    if Note.LABEL_ARG:
                        if cleaned.startswith(Note.LABEL_ARG):
                            current_read['msg'].append(line.split(Note.LABEL_ARG)[1])
                            continue

                    if len(cleaned) > 0:
                        current_read['msg'].append(line.rstrip() + '\n')
                    elif line == '\n':
                        current_read['msg'].append('\n')
                        
            else:
                if len(current_read['msg']) > 0:
                    while len(current_read['msg']) > 1 and current_read['msg'][-1] == '\n':
                        current_read['msg'].pop()
                    yield export_note(current_read)

    @classmethod
    def list(cls, src):
        # list all notes from NOTEFILE provided at src
        from os import getcwd

        pwd = getcwd()
        for inst in cls.iterate(src):
            if pwd in inst.pwd:
                yield inst

    @classmethod
    def match_dir(cls, src, path_match):
        # only list notes matching perfectly the path_match var
        for inst in cls.iterate(src):
            if path_match == inst.pwd:
                yield inst

    @classmethod
    def search(cls, src, term):
        # match any that contain term in message
        for inst in cls.iterate(src):
            if term in inst.message:
                yield inst

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Note Parser")
    parser.add_argument("-a", action="store_true", help="append message to .catjot")
    parser.add_argument("-s", action="store_true", help="search for term within .catjot")
    parser.add_argument("-l", action="store_true", help="show all notes matching pwd")
    parser.add_argument("-d", action="store_true", help="delete all notes matching timestamp")
    parser.add_argument("-p", action="store_true", help="delete most recent note in current dir")
    parser.add_argument("additional_args", nargs="*", help="argument values for search, delete, and append")

    args = parser.parse_args()

    import sys
    NOTEFILE = Note.NOTEFILE

    if not sys.stdin.isatty(): # is instead pipe input
        full_input = [line for line in sys.stdin]
        Note().append(NOTEFILE, ''.join(full_input))
    else:
        if args.a: # Append
            flattened = ' '.join(args.additional_args)
            Note().append(NOTEFILE, flattened)
        elif args.d: # Delete
            try:
                Note().delete(NOTEFILE, int(args.additional_args[0]))
                Note().commit(NOTEFILE)
            except FileNotFoundError:
                print(f"No notefile found at {NOTEFILE}")
            except TypeError:
                print(f"No note to pop for this path in {NOTEFILE}")
        elif args.p: # Pop
            from os import getcwd
            try:
                Note().pop(NOTEFILE, getcwd())
                Note().commit(NOTEFILE)
            except FileNotFoundError:
                print(f"No notefile found at {NOTEFILE}")
            except TypeError:
                print(f"No note to pop for this path in {NOTEFILE}")
        elif args.s: # Search
            try:
                for inst in Note().list(NOTEFILE):
                    flattened = ' '.join(args.additional_args)
                    for inst in Note().search(NOTEFILE, flattened):
                        print(Note.REC_TOP)
                        print(inst, end="")
                        print(Note.REC_BOT)
            except FileNotFoundError:
                print(f"No notefile found at {NOTEFILE}")
        elif args.l: # List
            try:
                for inst in Note().list(NOTEFILE):
                    print(Note.REC_TOP)
                    print(inst, end="")
                    print(Note.REC_BOT)
            except FileNotFoundError:
                print(f"No notefile found at {NOTEFILE}")
        else:
            # Available shortcuts listed below, choose as many words you like
            # for how to match, including abbreviations.
            # Be mindful to not have any duplicate keys!
            SHORTCUTS = {
                'MOST_RECENT': ['last', 'l'],
                'MATCH_NOTE_NAIVE': ['match', 'm', 'search', 's'],
            }
            if len(args.additional_args) == 0:
                # show pwd notes
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
            elif len(args.additional_args) == 1:
                if args.additional_args[0] in SHORTCUTS['MOST_RECENT']:
                    # always displays the most recently created note
                    last_note = "No notes to show.\n"
                    for note in Note().list(NOTEFILE):
                        last_note = note
                    else:
                        print(Note.REC_TOP)
                        print(last_note, end="")
                        print(Note.REC_BOT)
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

if __name__ == "__main__":
    main()
