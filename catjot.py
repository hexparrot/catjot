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
                    if tag.startswith('-'):
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

def main():
    import argparse
    parser = argparse.ArgumentParser(description="cat|jot notetaker")
    parser.add_argument("-c", action="store_const", const="context", help="set context or amend context of last message")
    parser.add_argument("-a", action="store_true", help="amend last jot instead")
    parser.add_argument("-t", action="store_const", const="tag", help="set tag or amend tag of last message")
    parser.add_argument("-p", action="store_const", const="pwd", help="set pwd or amend pwd of last message")
    parser.add_argument("additional_args", nargs="*", help="argument values")

    args = parser.parse_args()

    NOTEFILE = Note.NOTEFILE
    import sys
    from os import environ
    if 'CATJOT_FILE' in environ:
        # the environment variable will always supercede $HOME default when set
        if environ['CATJOT_FILE']: # truthy test for env that exists but unset
            NOTEFILE = environ['CATJOT_FILE']

    def flatten(arg_lst):
        return ' '.join(arg_lst).rstrip()

    def flatten_pipe(arg_lst):
        return ''.join(arg_lst).rstrip()

    def printout(note_obj, message_only=False):
        if message_only:
            print(note_obj.message)
        else: # normal display
            print(Note.REC_TOP)
            print(note_obj, end="")
            print(Note.REC_BOT)

    mode = args.c or args.t or args.p
    params = { mode: flatten(args.additional_args) }
    try:
        params.pop(None)
    except KeyError:
        pass

    # context-related functionality
    if args.c:
        if sys.stdin.isatty(): # interactive tty, no pipe!
            if args.a:
                # amend means editing last written note
                context = flatten(args.additional_args)
                if context: # non-null contents of addl_args
                    # jot -ac personal_feelings
                    params = { mode: context }
                    Note.amend(NOTEFILE, **params)
                    Note.commit(NOTEFILE)
                else: # falsy evaluation means absent
                    # jot -ac
                    print("Missing piped <context> or provide to -c")
                    exit(4)
            else:
                # jot -c observations
                # not intending to amend means match by context field
                context = flatten(args.additional_args)
                params = { mode: context }
                # TODO: implement match by context
                raise NotImplementedError
        else: # yes pipe!
            if args.a:
                # amend means editing last written note
                context = flatten(args.additional_args)
                if context: # non-null contents of addl_args
                    # whoami | jot -ac ponderings
                    print("Ambiguous input--amending last note with pipe or -c args?")
                    exit(4)
                else: # falsy evaluation means absent args
                    # whoami | jot -ac
                    # update last notes' context with piped data
                    # EXAMPLE USAGE: Saving last executed command as context of last note
                    # $ ls /usr |jot
                    # $ echo "!!" |jot -ac
                    # ensures that the context of /usr is included for a note
                    # that otherwise will be displaying pwd of the dir it was executed
                    piped_data = flatten_pipe(sys.stdin.readlines())
                    if piped_data:
                        params = { mode: piped_data }
                        Note.amend(NOTEFILE, **params)
                        Note.commit(NOTEFILE)
                    else:
                        # should definitely be getting piped info down this path
                        print("Received no piped input, bailing")
                        exit(4)
            else:
                context = flatten(args.additional_args)
                if context:
                    # | jot -c musings
                    # write new note with provided context from arg
                    piped_data = flatten_pipe(sys.stdin.readlines())
                    params = { mode: context }
                    Note().append(NOTEFILE, piped_data, **params)
                else:
                    # | jot -c
                    print("Lacking required argument <context>")
                    exit(4)
    # tagging-related functionality
    elif args.t:
        if sys.stdin.isatty(): # interactive tty, no pipe!
            if args.a:
                # amend means editing last written note
                tag = flatten(args.additional_args)
                if tag: # non-null contents of addl_args
                    # jot -at project1
                    params = { mode: tag }
                    Note.amend(NOTEFILE, **params)
                    Note.commit(NOTEFILE)
                else: # falsy evaluation means absent
                    # jot -at
                    print("Missing piped <tag> or provide to -t")
                    exit(4)
            else:
                # jot -t project2
                # not intending to amend means match by tag field
                tag = flatten(args.additional_args)
                try:
                    for inst in Note().tagged(NOTEFILE, tag):
                        printout(inst)
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
        else: # yes pipe!
            if args.a:
                # amend means editing last written note
                tag = flatten(args.additional_args)
                if tag: # non-null contents of addl_args
                    # whoami | jot -at project3
                    print("Ambiguous input--amending last tag with pipe or -t args?")
                    exit(4)
                else: # falsy evaluation means absent args
                    # whoami | jot -at
                    # update last notes' tag with piped data
                    # tags only take the first line, first word! tags are like that.
                    piped_data = sys.stdin.readline().split(" ")[0].strip()
                    if piped_data:
                        params = { mode: piped_data }
                        Note.amend(NOTEFILE, **params)
                        Note.commit(NOTEFILE)
                    else:
                        # should definitely be getting piped info down this path
                        print("Received no piped input, bailing")
                        exit(4)
            else:
                tag = flatten(args.additional_args)
                if tag:
                    # | jot -t project4
                    # new note with tag set to [project4]
                    piped_data = flatten_pipe(sys.stdin.readlines())
                    params = { mode: tag }
                    Note().append(NOTEFILE, piped_data, **params)
                else:
                    # | jot -t
                    print("Pipe lacking required argument <tag>")
                    exit(4)
    # pwd-related functionality
    elif args.p:
        if sys.stdin.isatty(): # interactive tty, no pipe!
            if args.a:
                # amend means editing last written note
                pwd = flatten(args.additional_args)
                if pwd: # non-null contents of addl_args
                    # jot -ap /home/user
                    params = { mode: pwd }
                    Note.amend(NOTEFILE, **params)
                    Note.commit(NOTEFILE)
                else: # falsy evaluation means absent
                    # jot -ap
                    print("Missing piped <pwd> or provide to -p")
                    exit(4)
            else:
                # jot -p /home/user
                # not intending to amend means match by pwd field
                pwd = flatten(args.additional_args)
                try:
                    for inst in Note().match_dir(NOTEFILE, pwd):
                        printout(inst)
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
        else: # yes pipe!
            if args.a:
                # amend means editing last written note
                pwd = flatten(args.additional_args)
                if pwd: # non-null contents of addl_args
                    # echo $PWD | jot -ap /home/user
                    print("Ambiguous input--amending last pwd with pipe or -p args?")
                    exit(4)
                else: # falsy evaluation means absent args
                    # echo $PWD | jot -ap
                    # update last notes' pwd with piped data
                    # tags only take the first line, first word! pwds are like that.
                    piped_data = sys.stdin.readline().split(" ")[0].strip()
                    if piped_data:
                        assert mode == "pwd"
                        params = { mode: piped_data }
                        Note.amend(NOTEFILE, **params)
                        Note.commit(NOTEFILE)
                    else:
                        # should definitely be getting piped info down this path
                        print("Received no piped input, bailing")
                        exit(4)
            else:
                pwd = flatten(args.additional_args)
                if pwd:
                    # | jot -p /home/usr
                    # new note with pwd set to /home/usr
                    piped_data = flatten_pipe(sys.stdin.readlines())
                    params = { mode: pwd }
                    Note().append(NOTEFILE, piped_data, **params)
                else:
                    # | jot -p
                    print("Pipe lacking required argument <pwd>")
                    exit(4)
    else:
        # for all other cases where no argparse argument is provided
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
            'MESSAGE_ONLY': ['payload', 'pl'],
        }
        # ZERO USER-PROVIDED PARAMETER SHORTCUTS
        if len(args.additional_args) == 0:
            # show notes originating from this PWD
            from os import getcwd
            if sys.stdin.isatty():
                try:
                    count = 0
                    for inst in Note().match_dir(NOTEFILE, getcwd()):
                        count += 1
                        printout(inst)
                    else:
                        child_matches = len(list(Note().list(NOTEFILE)))
                        print(f"{child_matches-count} matches in child directories")
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
            else:
                Note().append(NOTEFILE, flatten_pipe(sys.stdin.readlines()), **params)
        # SINGLE USER-PROVIDED PARAMETER SHORTCUTS
        elif len(args.additional_args) == 1:
            if args.additional_args[0] in SHORTCUTS['MOST_RECENT']:
                # always displays the most recently created note in this PWD
                last_note = "No notes to show.\n"
                for inst in Note().list(NOTEFILE):
                    last_note = inst
                else:
                    printout(last_note)
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

                if sys.stdin.isatty():
                    try:
                        for inst in Note().match_dir(NOTEFILE, environ['HOME']):
                            printout(inst)
                    except FileNotFoundError:
                        print(f"No notefile found at {NOTEFILE}")
                        sys.exit(1)
                else:
                    params['pwd'] = environ['HOME']
                    Note().append(NOTEFILE, flatten_pipe(sys.stdin.readlines()), **params)
            elif args.additional_args[0] in SHORTCUTS['SHOW_ALL']:
                # show all notes, from everywhere, everywhen
                try:
                    for inst in Note().iterate(NOTEFILE):
                        printout(inst)
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
            elif args.additional_args[0] in SHORTCUTS['MESSAGE_ONLY']:
                # returns the last message, message only (no pwd, no timestamp, no context).
                last_note = "No notes to show.\n"
                try:
                    for inst in Note().list(NOTEFILE):
                        last_note = inst
                    else:
                        printout(last_note, message_only=True)
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
        # TWO USER-PROVIDED PARAMETER SHORTCUTS
        elif len(args.additional_args) == 2:
            if args.additional_args[0] in SHORTCUTS['MATCH_NOTE_NAIVE']:
                # match if "term [+term2] [..]" exists in any line of the note
                flattened = flatten(args.additional_args[1:])
                try:
                    for inst in Note().search(NOTEFILE, flattened):
                        printout(inst)
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
            elif args.additional_args[0] in SHORTCUTS['MATCH_NOTE_NAIVE_I']:
                # match if "term [+term2] [..]" exists in any line of the note
                flattened = flatten(args.additional_args[1:])
                try:
                    for inst in Note().search_i(NOTEFILE, flattened):
                        printout(inst)
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
                flattened = args.additional_args[1]
                try:
                    for inst in Note().tagged(NOTEFILE, flattened):
                        printout(inst)
                except FileNotFoundError:
                    print(f"No notefile found at {NOTEFILE}")
                    sys.exit(1)
            elif args.additional_args[0] in SHORTCUTS['MESSAGE_ONLY']:
                # returns the message only (no pwd, no timestamp, no context).
                # when provided a timestamp, any notes matching timestamp
                # will be sent to stdout, concatenated in order of appearance
                flattened = args.additional_args[1]
                if flattened: # if truthy, somehow, use it for search
                    try:
                        for inst in Note().match_time(NOTEFILE, flattened):
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
                    last_note = "No notes to show.\n"
                    try:
                        for inst in Note().list(NOTEFILE):
                            last_note = inst
                        else:
                            printout(last_note, message_only=True)
                    except FileNotFoundError:
                        print(f"No notefile found at {NOTEFILE}")
                        sys.exit(1)

if __name__ == "__main__":
    main()

