#!/usr/bin/env python3
__author__ = "William Dizon"
__license__ = "MIT"
__version__ = "0.0.1"
__maintainer__ = "William Dizon"
__email__ = "wdchromium@gmail.com"
__status__ = "Development"

import unittest
import sys
import os
import shutil
import json
from time import time
from datetime import datetime
from os import getcwd, remove, environ
from unittest.mock import patch, MagicMock
from catjot import Note, NoteContext, SearchType
from conftest import jot_teardown

os.makedirs("local/scratch", exist_ok=True)
TMP_CATNOTE = "local/scratch/.catjot"  # writable scratch — tests/ is read-only
FIXED_CATNOTE = "tests/example.jot"  # read-only fixture
EXAMPLE_RW = "local/scratch/example.jot"  # writable copy for delete/pop/commit tests


def _writable_example():
    """Copy the read-only example fixture into scratch and return the copy's path.

    Write tests (delete/pop/commit) rebind FIXED_CATNOTE to this so tests/ is
    never mutated.
    """
    shutil.copyfile(FIXED_CATNOTE, EXAMPLE_RW)
    return EXAMPLE_RW


def strip_ansi_codes(text):
    import re

    ansi_escape = re.compile(r"\x1b\[([0-9]+)(;[0-9]+)*m")
    return ansi_escape.sub("", text)


class TestTaker(unittest.TestCase):
    def setup(self):
        pass

    def tearDown(self):
        jot_teardown(TMP_CATNOTE, FIXED_CATNOTE)

    def test_init_note(self):
        data = {
            "pwd": "/home/user/git",
            "now": 1694747655,
            "tag": "projectx",
            "context": "whoami",
            "message": "hello\nthere\n",
        }

        inst = Note(data)
        self.assertEqual(inst.pwd, "/home/user/git")
        self.assertEqual(inst.now, 1694747655)
        self.assertEqual(inst.tag, "projectx")
        self.assertEqual(inst.context, "whoami")
        self.assertEqual(inst.message, "hello\nthere\n")

    def test_find_path_match(self):
        # searches through an example file for a matching string argument

        inst = next(Note.match(FIXED_CATNOTE, (SearchType.MESSAGE_I, "hello")))
        self.assertEqual(inst.now, 1694747662)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "hello\n")

        inst = next(Note.match(FIXED_CATNOTE, (SearchType.MESSAGE_I, "really")))
        self.assertEqual(inst.now, 1694748108)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "This is really working!\n")

        multi = Note.match(FIXED_CATNOTE, (SearchType.MESSAGE_I, "what"))
        # expecting two hits
        inst = next(multi)
        self.assertEqual(inst.now, 1694747797)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "what\n")

        inst = next(multi)
        self.assertEqual(inst.now, 1694747841)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "that is what i call work\n")

        inst = next(Note.match(FIXED_CATNOTE, (SearchType.MESSAGE_I, "working")))
        self.assertEqual(inst.now, 1694748108)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "This is really working!\n")

        multi = Note.match(FIXED_CATNOTE, (SearchType.MESSAGE_I, "work"))
        # expecting two hits
        inst = next(multi)
        self.assertEqual(inst.now, 1694747841)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "that is what i call work\n")

        inst = next(multi)
        self.assertEqual(inst.now, 1694748108)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "This is really working!\n")

        with self.assertRaises(StopIteration):
            # but not three
            inst = next(multi)

    ### Start note creation tests

    def test_jot_note(self):
        inst = Note.jot("the smallest unit passable to make a note")

        self.assertEqual(inst.pwd, getcwd())
        self.assertTrue(abs(time() - inst.now) <= 1)  # is within one second
        self.assertEqual(inst.tag, "")
        self.assertEqual(inst.context, "")
        self.assertEqual(inst.message, "the smallest unit passable to make a note\n")

    def test_write_note(self):
        Note.append(TMP_CATNOTE, Note.jot("this is a note"))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "note")))
        self.assertTrue(abs(time() - inst.now) <= 1)  # is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

    def test_write_note_to_diff_pwd(self):
        Note.append(
            TMP_CATNOTE, Note.jot("this is a note", pwd="/home/user/git/git/git")
        )

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "note")))
        self.assertTrue(abs(time() - inst.now) <= 1)  # is within one second
        self.assertEqual(inst.pwd, "/home/user/git/git/git")
        self.assertEqual(inst.message, "this is a note\n")

        iters = 0
        for inst in Note.match(
            TMP_CATNOTE, (SearchType.DIRECTORY, "/home/user/git/git/git")
        ):
            self.assertEqual(inst.pwd, "/home/user/git/git/git")
            iters += 1
        self.assertEqual(iters, 1)

    def test_write_note_to_diff_timestamp(self):
        Note.append(
            TMP_CATNOTE,
            Note.jot("this is a note", pwd="/home/user/git/git/git", now=1694744444),
        )

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "note")))
        self.assertEqual(inst.now, 1694744444)
        self.assertEqual(inst.pwd, "/home/user/git/git/git")
        self.assertEqual(inst.message, "this is a note\n")

    def test_list_herenote(self):
        Note.append(TMP_CATNOTE, Note.jot("this is a note"))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.TREE, getcwd())))
        self.assertTrue(abs(time() - inst.now) <= 1)  # is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

        Note.append(TMP_CATNOTE, Note.jot("nnnnnote2"))
        multi = Note.match(TMP_CATNOTE, (SearchType.TREE, getcwd()))
        inst = next(multi)
        self.assertTrue(abs(time() - inst.now) <= 1)  # is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

        inst = next(multi)
        self.assertTrue(abs(time() - inst.now) <= 1)  # is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "nnnnnote2\n")

    def test_string_representation(self):
        thenote = "this is a note-o"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.TREE, getcwd())))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(
            strip_ansi_codes(str(inst)),
            f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n",
        )

    def test_multi_line_string(self):
        thenote = "notes can sometimes\ntake two lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.TREE, getcwd())))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(
            strip_ansi_codes(str(inst)),
            f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n",
        )

    def test_search_multi_line_string(self):
        thenote = "notes can sometimes\ntake two lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "take")))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(
            strip_ansi_codes(str(inst)),
            f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n",
        )

    def test_search_multi_line_string_with_empty_lines(self):
        thenote = "notes can sometimes\n\n\n\ntake many lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "many")))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(
            strip_ansi_codes(str(inst)),
            f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n",
        )

    def test_search_multi_line_string_insensitive(self):
        thenote = "notes can sometimes\nTAKE two lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "take")))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(
            strip_ansi_codes(str(inst)),
            f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n",
        )

    def test_search_multi_line_string_with_empty_lines_insensitive(self):
        thenote = "notes can sometimes\n\n\n\ntake mAny lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "MANY")))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(
            strip_ansi_codes(str(inst)),
            f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n",
        )

    def test_creating_label(self):
        thenote = "notes take labels now"
        Note.append(TMP_CATNOTE, Note.jot(thenote, tag="secret"))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notes")))
        self.assertEqual(inst.tag, "secret")
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(
            strip_ansi_codes(str(inst)),
            f"> cd {inst.pwd}\n# date {friendly_date}\n[secret]\n{thenote}\n",
        )

    def test_adding_context(self):
        thenote = ".bash_profile"
        Note.append(TMP_CATNOTE, Note.jot(thenote, context="ls /home/user"))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "profile")))
        self.assertEqual(inst.context, "ls /home/user")
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(
            strip_ansi_codes(str(inst)),
            f"> cd {inst.pwd}\n# date {friendly_date}\n% ls /home/user\n{thenote}\n",
        )

        multi = Note.iterate(FIXED_CATNOTE)
        inst = next(multi)
        self.assertEqual(inst.now, 1694747662)
        self.assertEqual(inst.context, "adoption")

    def test_adding_context_to_existing_jot(self):
        thenote = ".bashrc"
        Note.append(TMP_CATNOTE, Note.jot(thenote))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "bashrc")))
        self.assertEqual(inst.context, "")  # no context yet
        inst.amend(
            TMP_CATNOTE, context="favorite_files"
        )  # context should exist in .new
        self.assertEqual(inst.context, "")  # no but no read ever had it present

        inst = next(Note.match(TMP_CATNOTE + ".new", (SearchType.MESSAGE_I, "bashrc")))
        self.assertEqual(inst.context, "favorite_files")  # new file should reflect this

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "bashrc")))
        self.assertEqual(inst.context, "")  # but not on the original file

        Note.commit(TMP_CATNOTE)  # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "bashrc")))
        self.assertEqual(inst.context, "favorite_files")  # new file should reflect this

    def test_changing_pwd_to_existing_jot(self):
        pre_pwd = "/home/user/git"
        post_pwd = "/home/alice/in"
        Note.append(TMP_CATNOTE, Note.jot("notey", pwd=pre_pwd))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.pwd, pre_pwd)
        inst.amend(TMP_CATNOTE, pwd=post_pwd)  # create .new file

        inst = next(Note.match(TMP_CATNOTE + ".new", (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.pwd, post_pwd)  # new file should reflect this

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.pwd, pre_pwd)

        Note.commit(TMP_CATNOTE)  # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.pwd, post_pwd)

    def test_deleting_tag_from_existing_note(self):
        pre_tag = "stuff"
        post_tag = "~stuff"
        Note.append(TMP_CATNOTE, Note.jot("notey", tag=pre_tag))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.tag, pre_tag)
        inst.amend(TMP_CATNOTE, tag=post_tag)  # create .new file

        inst = next(Note.match(TMP_CATNOTE + ".new", (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.tag, "")  # new file should reflect this

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.tag, pre_tag)

        Note.commit(TMP_CATNOTE)  # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.tag, "")  # new file should reflect this

    def test_append_multiple_tags(self):
        pre_tag = "blamo"
        post_tag = "better_stuff"
        Note.append(TMP_CATNOTE, Note.jot("notey", tag=pre_tag))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertIn(pre_tag, inst.tag)
        inst.amend(TMP_CATNOTE, tag=post_tag)  # create .new file

        inst = next(Note.match(TMP_CATNOTE + ".new", (SearchType.MESSAGE_I, "notey")))
        self.assertIn(pre_tag, inst.tag)
        self.assertIn(post_tag, inst.tag)

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.tag, pre_tag)
        self.assertNotIn(post_tag, inst.tag)

        Note.commit(TMP_CATNOTE)  # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertIn(pre_tag, inst.tag)
        self.assertIn(post_tag, inst.tag)

        self.assertEqual(inst.tag, f"{pre_tag} {post_tag}")

        another_tag = "thebest"
        inst.amend(TMP_CATNOTE, tag=another_tag)  # create .new file
        Note.commit(TMP_CATNOTE)  # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertIn(pre_tag, inst.tag)
        self.assertIn(post_tag, inst.tag)
        self.assertIn(another_tag, inst.tag)
        self.assertEqual(inst.tag, f"{pre_tag} {post_tag} {another_tag}")

        last_tag = "~blamo"
        inst.amend(TMP_CATNOTE, tag=last_tag)  # create .new file
        Note.commit(TMP_CATNOTE)  # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertNotIn(pre_tag, inst.tag)
        self.assertIn(post_tag, inst.tag)
        self.assertIn(another_tag, inst.tag)
        self.assertNotIn(last_tag, inst.tag)

        self.assertEqual(inst.tag, f"{post_tag} {another_tag}")

    #### end note creation

    def test_iterate_all_notes(self):
        multi = Note.iterate(FIXED_CATNOTE)
        inst = next(multi)
        self.assertEqual(inst.now, 1694747662)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "hello\n")

        for _ in range(0, 4):
            inst = next(multi)

        self.assertEqual(inst.now, 1694941231)
        self.assertEqual(inst.pwd, "/home/user/child")
        self.assertEqual(inst.message, "hierarchical\n")

        inst = next(multi)
        self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")

        inst = next(multi)
        self.assertEqual(inst.message, "^-^\n")

        with self.assertRaises(StopIteration):
            inst = next(multi)

    def test_handle_separator_in_data(self):
        multi = Note.iterate(FIXED_CATNOTE)
        for _ in range(0, 6):
            inst = next(multi)

        self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")

        inst = next(multi)
        self.assertEqual(inst.now, 1694955555)
        self.assertEqual(inst.pwd, "/home/user/alice")
        self.assertEqual(inst.message, "^-^\n")

    def test_handle_record_separator_note_invalidation(self):
        multi = Note.iterate("tests/broken.jot")
        inst = next(multi)  # ^-^ data case
        self.assertEqual(inst.now, 1394443232)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertTrue(
            inst.message.startswith("^-^\n^-^\n^-^\nDirectory:/home/user\nDate")
        )
        self.assertTrue(
            inst.message.endswith("important notice about your home warranty\n")
        )

        inst = next(multi)
        self.assertEqual(inst.now, 1694747613)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(
            inst.message, "really important notice about your home warranty\n"
        )

        inst = next(multi)
        self.assertEqual(inst.now, 1694747614)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "really really important notice\n")

        inst = next(multi)
        self.assertEqual(inst.now, 1694747616)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "and back to working\n")

        inst = next(multi)
        self.assertEqual(inst.now, 1694747619)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "^-^\nand back to working\n")

        with self.assertRaises(StopIteration):
            inst = next(multi)

    def test_handle_jotting_into_self(self):
        multi = Note.iterate("tests/broken2.jot")
        inst = next(multi)  # ^-^ data case
        self.assertEqual(inst.now, 1725412377)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertTrue(inst.message.endswith("What would you like to do?\n"))

        # here is a second note, that should NOT be parsed

        inst = next(multi)
        self.assertEqual(inst.now, 1725412481)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "This should be captured\n")

        with self.assertRaises(StopIteration):
            inst = next(multi)

    def test_tag_header(self):
        inst = next(Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")))
        self.assertEqual(inst.tag, "project1")

        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(
            strip_ansi_codes(str(inst)),
            f"> cd {inst.pwd}\n# date {friendly_date}\n[project1]\n% adoption\nhello\n",
        )
        inst.tag = "blamo"
        self.assertEqual(
            strip_ansi_codes(str(inst)),
            f"> cd {inst.pwd}\n# date {friendly_date}\n[blamo]\n% adoption\nhello\n",
        )

    def test_show_only_tagged_by(self):
        iters = 0
        for inst in Note.match(FIXED_CATNOTE, (SearchType.TAG, "project1")):
            self.assertEqual(inst.now, 1694747662)
            self.assertEqual(inst.tag, "project1")
            iters += 1
        self.assertEqual(iters, 1)

    def test_only_perfect_path_match(self):
        iters = 0
        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")):
            self.assertEqual(inst.pwd, "/home/user")
            iters += 1
        self.assertEqual(iters, 4)

        iters = 0
        for inst in Note.match(
            FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user/child")
        ):
            self.assertEqual(inst.pwd, "/home/user/child")
            iters += 1
        self.assertEqual(iters, 1)

    def test_readin_unicode(self):
        iters = 0
        for inst in Note.match(
            FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user/git/catjot")
        ):
            self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")
            iters += 1
        self.assertEqual(iters, 1)

    def test_match_unicode(self):
        iters = 0
        for inst in Note.match(
            FIXED_CATNOTE, (SearchType.MESSAGE, "なんでこんなにふわふわなの?")
        ):
            iters += 1
        for inst in Note.match(
            FIXED_CATNOTE, (SearchType.MESSAGE_I, "なんでこんなにふわふわなの?")
        ):
            iters += 1
        self.assertEqual(iters, 2)

        self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")

    def test_search_by_timestamp(self):
        iters = 0
        for inst in Note.match(FIXED_CATNOTE, (SearchType.TIMESTAMP, 1695184544)):
            self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")
            iters += 1
        self.assertEqual(iters, 1)

        iters = 0
        for inst in Note.match(FIXED_CATNOTE, (SearchType.TIMESTAMP, 1694747662)):
            self.assertEqual(inst.context, "adoption")
            iters += 1
        self.assertEqual(iters, 1)

    def test_search_by_context(self):
        iters = 0
        for inst in Note.match(FIXED_CATNOTE, (SearchType.CONTEXT_I, "adoption")):
            self.assertEqual(inst.now, 1694747662)
            iters += 1
        self.assertEqual(iters, 1)

        iters = 0
        for inst in Note.match(FIXED_CATNOTE, (SearchType.CONTEXT_I, "NEKO")):
            self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")
            iters += 1
        self.assertEqual(iters, 1)

    def test_delete_record(self):
        FIXED_CATNOTE = _writable_example()  # operate on a scratch copy (tests/ read-only)
        iters = 0
        # file, untouched
        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")):
            iters += 1
        self.assertEqual(iters, 4)

        Note.delete(FIXED_CATNOTE, 1694747797)

        iters = 0
        # new file, reduced by any timestamp matches
        for inst in Note.match(
            FIXED_CATNOTE + ".new", (SearchType.DIRECTORY, "/home/user")
        ):
            iters += 1
            self.assertNotEqual(inst.now, 1694747797)
        self.assertEqual(iters, 3)

        Note.commit(FIXED_CATNOTE)

        iters = 0
        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")):
            iters += 1
        self.assertEqual(iters, 3)

        iters = 0
        for inst in Note.match(
            FIXED_CATNOTE + ".old", (SearchType.DIRECTORY, "/home/user")
        ):
            iters += 1
        self.assertEqual(iters, 4)

    def test_pop_record(self):
        FIXED_CATNOTE = _writable_example()  # operate on a scratch copy (tests/ read-only)
        iters = 0
        # file, untouched
        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")):
            iters += 1
        self.assertEqual(iters, 4)

        Note.pop(FIXED_CATNOTE, "/home/user")
        Note.commit(FIXED_CATNOTE)

        for inst in Note.match(FIXED_CATNOTE, (SearchType.TREE, "/home/user")):
            # check the exact match (last timestamp is removed)
            self.assertNotEqual(inst.now, 1694748108)

        iters = 0
        # should show one fewer entry
        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")):
            iters += 1
        self.assertEqual(iters, 3)

        iters = 0
        # other directories should be untouched
        for inst in Note.match(
            FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user/child")
        ):
            iters += 1
        self.assertEqual(iters, 1)

    def test_empty_append_is_aborted(self):
        Note.append(TMP_CATNOTE, Note.jot("this is the first note"))

        iters = 0
        for inst in Note.iterate(TMP_CATNOTE):
            iters += 1
        self.assertEqual(iters, 1)

        with self.assertRaises(ValueError):
            # empty message not allowed
            Note.append(TMP_CATNOTE, Note.jot(""))

        iters = 0
        for inst in Note.iterate(TMP_CATNOTE):
            iters += 1
        self.assertEqual(iters, 1)

    def test_separator_in_data_detectable(self):
        NOTEFILE = "tests/edgecase.jot"
        multi = Note.iterate(NOTEFILE)
        inst = next(multi)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.now, 1694747662)
        self.assertIn("catland", inst.tag)
        self.assertIn("project", inst.tag)
        self.assertEqual(inst.context, "adoption")
        self.assertEqual(
            inst.message,
            "the record separator looks like\n^-^\nand i want this to remain intact\n",
        )

        inst = next(multi)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.now, 1694747841)
        self.assertEqual(inst.message, "testing that this shows up unimpacted\n")

    ### tests for context manager
    def test_note_context_creation(self):
        with NoteContext(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")) as nc:
            self.assertEqual(len(nc), 4)

        with NoteContext(
            FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user/git/catjot")
        ) as nc:
            self.assertEqual(len(nc), 1)

        with NoteContext(FIXED_CATNOTE, []) as nc:
            self.assertEqual(len(nc), 0)

        with NoteContext(FIXED_CATNOTE, (SearchType.ALL, "")) as nc:
            self.assertEqual(len(nc), 7)

        with NoteContext(FIXED_CATNOTE, (SearchType.CONTEXT_I, "adoption")) as nc:
            self.assertEqual(len(nc), 1)

    ### tests for enum-based record matching
    def test_match_and(self):
        matches = Note.match(FIXED_CATNOTE, [(SearchType.ALL, "")])
        self.assertEqual(len(list(matches)), 7)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.DIRECTORY, "/home/user")])
        self.assertEqual(len(list(matches)), 4)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, "/home/user")])
        self.assertEqual(len(list(matches)), 7)

        matches = Note.match(FIXED_CATNOTE, [])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(
            FIXED_CATNOTE,
            [(SearchType.TREE, "/home/user"), (SearchType.TREE, "/home/user/catjot")],
        )
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(
            FIXED_CATNOTE,
            [
                (SearchType.TREE, "/home/user"),
                (SearchType.TREE, "/home/user/git/catjot"),
            ],
        )
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(
            FIXED_CATNOTE,
            [
                (SearchType.DIRECTORY, "/home/user"),
                (SearchType.DIRECTORY, "/home/user/git/catjot"),
            ],
        )
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(
            FIXED_CATNOTE,
            [(SearchType.TIMESTAMP, 1694747797), (SearchType.DIRECTORY, "/home/user")],
        )
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(
            FIXED_CATNOTE,
            [(SearchType.TIMESTAMP, 1111111111), (SearchType.DIRECTORY, "/home/user")],
        )
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, "work")])
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, "work")])
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, "WORK")])
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, "WORK")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, "WORK")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, "ふわふわ")])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, "ふわふわ")])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, "")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, "")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, "^-^")])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, "")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.DIRECTORY, "")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, "neko")])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, "adoption")])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, "")])
        self.assertEqual(len(list(matches)), 0)  # no matches on falsy values

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, "")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, "ふわふわ")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, "ふわふわ")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 1695184544)])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, "0")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 0)])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, "project1")])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, "project2")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, "neko")])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, "multiple")])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, "unrelated")])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(
            FIXED_CATNOTE, [(SearchType.TAG, "multiple"), (SearchType.TAG, "unrelated")]
        )
        self.assertEqual(len(list(matches)), 1)

    def test_match_or(self):
        matches = Note.match(
            FIXED_CATNOTE, [(SearchType.DIRECTORY, "/home/user")], "or"
        )
        self.assertEqual(len(list(matches)), 4)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, "/home/user")], "or")
        self.assertEqual(len(list(matches)), 7)

        matches = Note.match(FIXED_CATNOTE, [], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(
            FIXED_CATNOTE,
            [(SearchType.TREE, "/home/user"), (SearchType.TREE, "/home/user/catjot")],
            "or",
        )
        self.assertEqual(len(list(matches)), 7)

        matches = Note.match(
            FIXED_CATNOTE,
            [
                (SearchType.TREE, "/home/user"),
                (SearchType.TREE, "/home/user/git/catjot"),
            ],
            "or",
        )
        self.assertEqual(len(list(matches)), 7)

        matches = Note.match(
            FIXED_CATNOTE,
            [
                (SearchType.DIRECTORY, "/home/user"),
                (SearchType.DIRECTORY, "/home/user/git/catjot"),
            ],
            "or",
        )
        self.assertEqual(len(list(matches)), 5)

        matches = Note.match(
            FIXED_CATNOTE,
            [(SearchType.CONTEXT_I, "adoption"), (SearchType.CONTEXT_I, "neko")],
            "or",
        )
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(
            FIXED_CATNOTE,
            [(SearchType.TIMESTAMP, 1694747797), (SearchType.DIRECTORY, "/home/user")],
            "or",
        )
        self.assertEqual(len(list(matches)), 4)

        matches = Note.match(
            FIXED_CATNOTE,
            [(SearchType.TIMESTAMP, 1111111111), (SearchType.DIRECTORY, "/home/user")],
            "or",
        )
        self.assertEqual(len(list(matches)), 4)

        matches = Note.match(
            FIXED_CATNOTE,
            [(SearchType.TIMESTAMP, 1694747797), (SearchType.TIMESTAMP, 1694748108)],
            "or",
        )
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, "work")], "or")
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, "work")], "or")
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, "WORK")], "or")
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, "WORK")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, "WORK")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, "ふわふわ")], "or")
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, "ふわふわ")], "or")
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, "")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, "")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, "^-^")], "or")
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, "")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.DIRECTORY, "")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, "neko")], "or")
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, "adoption")], "or")
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, "")], "or")
        self.assertEqual(len(list(matches)), 0)  # no matches on falsy values

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, "")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, "ふわふわ")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, "ふわふわ")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 1695184544)], "or")
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, "0")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 0)], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, "project1")], "or")
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, "project2")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, "neko")], "or")
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, "multiple")], "or")
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, "unrelated")], "or")
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(
            FIXED_CATNOTE,
            [(SearchType.TAG, "multiple"), (SearchType.TAG, "unrelated")],
            "or",
        )
        self.assertEqual(len(list(matches)), 1)

    def test_equality(self):
        a_note = Note.jot("hello")
        b_note = Note.jot("hello")
        self.assertEqual(a_note, b_note)

        a_note = Note.jot("hello")
        b_note = Note.jot("helloz")
        self.assertNotEqual(a_note, b_note)

        a_note = Note.jot("helloz")
        b_note = Note.jot("helloz", pwd="/home")
        self.assertNotEqual(a_note, b_note)

        a_note = Note.jot("helloz", pwd="/home")
        b_note = Note.jot("helloz", pwd="/home", context="fun")
        self.assertNotEqual(a_note, b_note)

        a_note = Note.jot("helloz", pwd="/home", context="fun")
        b_note = Note.jot("helloz", pwd="/home", context="fun", tag="taggin")
        self.assertNotEqual(a_note, b_note)

        a_note = Note.jot("helloz\nthere\n", pwd="/home", context="fun")
        b_note = Note.jot("helloz\nthere", pwd="/home", context="fun", tag="taggin")
        self.assertNotEqual(a_note, b_note)

        a_note = Note.jot("helloz\nthere\n", pwd="/home", context="fun")
        b_note = Note.jot("helloz\nthere", pwd="/home", context="fun\n", tag="taggin")
        self.assertNotEqual(a_note, b_note)

        a_note = Note.jot("helloz\nthere", pwd="/home", context="fun", tag="taggin")
        b_note = Note.jot(
            "helloz\nthere\n\n", pwd="/home", context="fun\n", tag="taggin"
        )
        self.assertEqual(a_note, b_note)

        a_note = Note.jot(
            "helloz\nthere", pwd="/home", context="fun", tag="taggin", now=1695184544
        )
        b_note = Note.jot(
            "helloz\nthere\n\n",
            pwd="/home",
            context="fun\n",
            tag="taggin",
            now=1695184544,
        )
        self.assertEqual(a_note, b_note)

        a_note = Note.jot(
            "helloz\nthere", pwd="/home", context="fun", tag="taggin", now=1695184544
        )
        b_note = Note.jot(
            "helloz\nthere\n\n",
            pwd="/home",
            context="fun\n",
            tag="taggin",
            now=1695184111,
        )
        self.assertNotEqual(a_note, b_note)

    def test_equality_full_example(self):
        FIXED_CATNOTE = _writable_example()  # operate on a scratch copy (tests/ read-only)
        matches = list(
            inst
            for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user"))
        )
        self.assertEqual(len(matches), 4)

        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")):
            self.assertEqual(inst, matches.pop(0))

        # showing subsequent iterations still yields same objects
        matches = list(inst for inst in Note.match(FIXED_CATNOTE, (SearchType.ALL, "")))
        self.assertEqual(len(matches), 7)

        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")):
            self.assertEqual(inst, matches.pop(0))

        matches = list(inst for inst in Note.match(FIXED_CATNOTE, (SearchType.ALL, "")))
        # capture before delete
        Note.delete(FIXED_CATNOTE, 1694747797)

        # new file, reduced by one timestamp match and removal
        multi = Note.match(FIXED_CATNOTE + ".new", (SearchType.DIRECTORY, "/home/user"))
        inst = next(multi)
        self.assertEqual(inst.now, 1694747662)
        self.assertEqual(inst, matches.pop(0))
        matches.pop(0)  # skip over dropped one (7797)
        inst = next(multi)
        self.assertEqual(inst.now, 1694747841)
        self.assertEqual(inst, matches.pop(0))
        inst = next(multi)
        self.assertEqual(inst.now, 1694748108)
        self.assertEqual(inst, matches.pop(0))

        Note.commit(FIXED_CATNOTE)
        matches = list(inst for inst in Note.match(FIXED_CATNOTE, (SearchType.ALL, "")))

        multi = Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user"))
        inst = next(multi)
        self.assertEqual(inst.now, 1694747662)
        self.assertEqual(inst, matches.pop(0))
        inst = next(multi)
        self.assertEqual(inst.now, 1694747841)
        self.assertEqual(inst, matches.pop(0))
        inst = next(multi)
        self.assertEqual(inst.now, 1694748108)
        self.assertEqual(inst, matches.pop(0))

    def test_return_only_timestamps(self):
        matches = list(
            inst
            for inst in Note.match(
                FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user"), time_only=True
            )
        )
        self.assertEqual(len(matches), 4)

        self.assertEqual(matches[0], 1694747662)
        self.assertEqual(matches[1], 1694747797)
        self.assertEqual(matches[2], 1694747841)
        self.assertEqual(matches[3], 1694748108)

        matches = list(
            inst
            for inst in Note.match(FIXED_CATNOTE, (SearchType.ALL, ""), time_only=True)
        )
        self.assertEqual(matches[0], 1694747662)
        self.assertEqual(matches[1], 1694747797)
        self.assertEqual(matches[2], 1694747841)
        self.assertEqual(matches[3], 1694748108)
        self.assertEqual(matches[4], 1694941231)
        self.assertEqual(matches[5], 1695184544)
        self.assertEqual(matches[6], 1694955555)

    def test_note_repr(self):
        # Create a Note object with specific context and message
        note = Note({"context": "Context1", "message": "Message1"})

        # Define the expected repr string
        expected_repr = "Note(context='Context1', message='Message1')"

        # Assert that the repr of the note matches the expected string
        self.assertEqual(repr(note), expected_repr)

    def test_note_repr_with_special_characters(self):
        # Create a Note object with context and message containing special characters
        note = Note(
            {
                "context": "Special & Context",
                "message": "Message with 'quotes' and \"double quotes\"",
            }
        )

        # Define the expected repr string
        expected_repr = "Note(context='Special & Context', message='Message with 'quotes' and \"double quotes\"')"

        # Assert that the repr of the note matches the expected string
        self.assertEqual(repr(note), expected_repr)

    def test_match_all_in_or_mode(self):
        # SearchType.ALL must work in OR mode, mirroring AND mode behavior
        matches = Note.match(FIXED_CATNOTE, [(SearchType.ALL, "")], "or")
        self.assertEqual(len(list(matches)), 7)

        # OR with ALL plus another criterion should still return all 7 (union)
        matches = Note.match(
            FIXED_CATNOTE,
            [(SearchType.ALL, ""), (SearchType.DIRECTORY, "/home/user")],
            "or",
        )
        self.assertEqual(len(list(matches)), 7)

    def test_empty_criteria_yields_nothing_in_or_mode(self):
        matches = Note.match(FIXED_CATNOTE, [], "or")
        self.assertEqual(len(list(matches)), 0)

    def test_empty_append_raises_valueerror(self):
        Note.append(TMP_CATNOTE, Note.jot("first note"))

        with self.assertRaises(ValueError):
            Note.append(TMP_CATNOTE, Note.jot(""))

        # file should still have exactly one note
        iters = sum(1 for _ in Note.iterate(TMP_CATNOTE))
        self.assertEqual(iters, 1)

    def test_is_binary_string_empty(self):
        from catjot import is_binary_string

        self.assertFalse(is_binary_string(""))
        self.assertFalse(is_binary_string("plain text"))
        self.assertTrue(is_binary_string("\x00binary"))

    def test_note_default_init(self):
        # Calling Note() with no args must not share state across instances
        n1 = Note()
        n2 = Note()
        n1.tag = "modified"
        self.assertEqual(n2.tag, "")


class TestToolRegistration(unittest.TestCase):
    """#16 regression (R4a): repeated register_tool must dedupe by name.

    TOOL_SCHEMAS / TOOL_HANDLERS are process-level module globals, so each test
    snapshots and restores them to stay independent.
    """

    def setUp(self):
        import catjot

        self.catjot = catjot
        self._saved_schemas = list(catjot.TOOL_SCHEMAS)
        self._saved_handlers = dict(catjot.TOOL_HANDLERS)

    def tearDown(self):
        self.catjot.TOOL_SCHEMAS[:] = self._saved_schemas
        self.catjot.TOOL_HANDLERS.clear()
        self.catjot.TOOL_HANDLERS.update(self._saved_handlers)

    def _count(self, name):
        return len(
            [s for s in self.catjot.TOOL_SCHEMAS if s["function"]["name"] == name]
        )

    def _empty_params(self):
        return {"type": "object", "properties": {}}

    def test_five_registrations_yield_one_schema(self):
        for _ in range(5):
            self.catjot.register_tool(
                "t_dup", "desc", self._empty_params(), lambda **k: "ok"
            )
        self.assertEqual(self._count("t_dup"), 1)

    def test_changed_description_updates_in_place(self):
        self.catjot.register_tool(
            "t_desc", "first description", self._empty_params(), lambda **k: "a"
        )
        self.catjot.register_tool(
            "t_desc", "second description", self._empty_params(), lambda **k: "b"
        )
        self.assertEqual(self._count("t_desc"), 1)
        schema = next(
            s
            for s in self.catjot.TOOL_SCHEMAS
            if s["function"]["name"] == "t_desc"
        )
        self.assertEqual(schema["function"]["description"], "second description")

    def test_latest_handler_wins(self):
        self.catjot.register_tool(
            "t_handler", "d", self._empty_params(), lambda **k: "first"
        )
        self.catjot.register_tool(
            "t_handler", "d", self._empty_params(), lambda **k: "second"
        )
        self.assertEqual(self.catjot.TOOL_HANDLERS["t_handler"](), "second")

    def test_register_search_tools_is_idempotent(self):
        for _ in range(3):
            self.catjot.register_search_tools()
        self.assertEqual(
            self._count("search_notes"), 1, "search_notes was duplicated"
        )

    def test_search_notes_schema_exposes_field_enum(self):
        self.catjot.register_search_tools()
        schema = next(
            s
            for s in self.catjot.TOOL_SCHEMAS
            if s["function"]["name"] == "search_notes"
        )
        enum = schema["function"]["parameters"]["properties"]["field"]["enum"]
        self.assertEqual(set(enum), {"tag", "context", "message", "directory"})


class _RecordingLLM:
    """Stand-in for call_llm: returns scripted responses and snapshots each
    call's message history so tests can inspect what the loop appended."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []  # snapshot of `messages` at each invocation

    def __call__(self, messages, **kwargs):
        self.calls.append([dict(m) for m in messages])
        return self.responses.pop(0)


def _tool_call(field, query, call_id=None):
    """Build an assistant message carrying one search_notes tool call."""
    tc = {
        "function": {
            "name": "search_notes",
            "arguments": json.dumps({"field": field, "query": query}),
        }
    }
    if call_id is not None:
        tc["id"] = call_id
    return tc


def _four_field_response():
    return {
        "role": "assistant",
        "tool_calls": [
            _tool_call("tag", "project1"),
            _tool_call("context", "adoption"),
            _tool_call("message", "hello"),
            _tool_call("directory", "/home/user"),
        ],
    }


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            err = requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def json(self):
        return self._payload


_GOOD_LLM_PAYLOAD = {
    "choices": [{"message": {"role": "assistant", "content": "hi"}}]
}


class TestDispatchGuard(unittest.TestCase):
    """dispatch_tool_call must convert every failure to error-JSON, never raise."""

    def setUp(self):
        import catjot

        self.catjot = catjot
        self._saved_schemas = list(catjot.TOOL_SCHEMAS)
        self._saved_handlers = dict(catjot.TOOL_HANDLERS)
        catjot.register_search_tools()

    def tearDown(self):
        self.catjot.TOOL_SCHEMAS[:] = self._saved_schemas
        self.catjot.TOOL_HANDLERS.clear()
        self.catjot.TOOL_HANDLERS.update(self._saved_handlers)

    def test_unknown_tool_returns_error_json(self):
        parsed = json.loads(self.catjot.dispatch_tool_call("no_such_tool", "{}"))
        self.assertIn("unknown tool", parsed["error"])

    def test_malformed_json_returns_error_json(self):
        parsed = json.loads(
            self.catjot.dispatch_tool_call("search_notes", "{not json")
        )
        self.assertIn("failed", parsed["error"])
        self.assertIn("hint", parsed)

    def test_non_object_arguments_returns_error_json(self):
        parsed = json.loads(
            self.catjot.dispatch_tool_call("search_notes", "[1, 2, 3]")
        )
        self.assertIn("must be a JSON object", parsed["error"])

    def test_missing_required_argument_returns_error_json(self):
        parsed = json.loads(
            self.catjot.dispatch_tool_call(
                "search_notes", json.dumps({"field": "tag"})
            )
        )
        self.assertIn("missing required argument", parsed["error"])
        self.assertIn("query", parsed["error"])

    def test_handler_exception_returns_error_json(self):
        def boom(**kwargs):
            raise RuntimeError("kaboom")

        self.catjot.register_tool(
            name="boom_tool",
            description="explodes",
            parameters={"type": "object", "properties": {}},
            handler=boom,
        )
        parsed = json.loads(self.catjot.dispatch_tool_call("boom_tool", "{}"))
        self.assertIn("RuntimeError", parsed["error"])
        self.assertIn("kaboom", parsed["error"])


class TestFieldSearchHandlers(unittest.TestCase):
    """One factory covers all four fields; results match the fixture."""

    def setUp(self):
        self._orig_notefile = Note.NOTEFILE
        Note.NOTEFILE = FIXED_CATNOTE

    def tearDown(self):
        Note.NOTEFILE = self._orig_notefile

    def _search(self, field, query):
        from catjot import make_field_search_handler, _FIELD_SEARCH_TYPES

        handler = make_field_search_handler(_FIELD_SEARCH_TYPES[field])
        return json.loads(handler(query))

    def test_tag_field(self):
        self.assertEqual(self._search("tag", "project1"), [1694747662])

    def test_context_field(self):
        self.assertEqual(self._search("context", "adoption"), [1694747662])

    def test_message_field(self):
        self.assertEqual(self._search("message", "hello"), [1694747662])

    def test_directory_field(self):
        self.assertEqual(
            self._search("directory", "/home/user"),
            [1694747662, 1694747797, 1694747841, 1694748108],
        )

    def test_search_notes_handler_routes_by_field(self):
        from catjot import make_search_notes_handler

        handler = make_search_notes_handler()
        self.assertEqual(json.loads(handler("tag", "project1")), [1694747662])

    def test_search_notes_handler_unknown_field(self):
        from catjot import make_search_notes_handler

        handler = make_search_notes_handler()
        parsed = json.loads(handler("bogus", "x"))
        self.assertIn("unknown field", parsed["error"])


class TestRunToolLoop(unittest.TestCase):
    def setUp(self):
        import catjot

        self.catjot = catjot
        self._orig_notefile = Note.NOTEFILE
        Note.NOTEFILE = FIXED_CATNOTE

    def tearDown(self):
        Note.NOTEFILE = self._orig_notefile

    def test_all_four_fields_then_summary(self):
        summary = {"role": "assistant", "content": "FINAL SUMMARY"}
        llm = _RecordingLLM([_four_field_response(), summary])
        with patch.object(self.catjot, "call_llm", llm):
            out = self.catjot.run_tool_loop("find project1")
        self.assertEqual(out, "FINAL SUMMARY")
        self.assertEqual(len(llm.calls), 2)
        final_history = llm.calls[1]
        self.assertTrue(
            any(
                "field searches are complete" in m.get("content", "")
                for m in final_history
            )
        )

    def test_tool_call_ids_are_unique_without_provider_ids(self):
        llm = _RecordingLLM(
            [_four_field_response(), {"role": "assistant", "content": "ok"}]
        )
        with patch.object(self.catjot, "call_llm", llm):
            self.catjot.run_tool_loop("q")
        final_history = llm.calls[1]
        tool_ids = [
            m["tool_call_id"] for m in final_history if m.get("role") == "tool"
        ]
        self.assertEqual(len(tool_ids), 4)
        self.assertEqual(len(set(tool_ids)), 4)

    def test_early_stop_nudges_missing_fields_then_completes(self):
        stop_early = {"role": "assistant", "content": "I'm done early"}
        summary = {"role": "assistant", "content": "SUMMARY"}
        llm = _RecordingLLM([stop_early, _four_field_response(), summary])
        with patch.object(self.catjot, "call_llm", llm):
            out = self.catjot.run_tool_loop("q")
        self.assertEqual(out, "SUMMARY")
        nudge = llm.calls[1][-1]["content"]
        self.assertIn("not yet searched", nudge)
        for field in ("tag", "context", "message", "directory"):
            self.assertIn(field, nudge)

    def test_second_early_stop_returns_verbatim(self):
        stop1 = {"role": "assistant", "content": "first stop"}
        stop2 = {"role": "assistant", "content": "VERBATIM ANSWER"}
        llm = _RecordingLLM([stop1, stop2])
        with patch.object(self.catjot, "call_llm", llm):
            out = self.catjot.run_tool_loop("q")
        self.assertEqual(out, "VERBATIM ANSWER")

    def test_max_iterations_exhausted(self):
        def only_tag(messages, **kwargs):
            return {
                "role": "assistant",
                "tool_calls": [_tool_call("tag", "project1")],
            }

        with patch.object(self.catjot, "call_llm", side_effect=only_tag):
            out = self.catjot.run_tool_loop("q", max_iterations=3)
        self.assertEqual(out, "Max iterations reached without a final answer.")


class TestCallLLMTransport(unittest.TestCase):
    def setUp(self):
        import catjot

        self.catjot = catjot

    def _env(self, with_key):
        env = {
            "openai_api_url": "http://localhost:9/v1/chat",
            "openai_api_model": "test-model",
        }
        if with_key:
            env["openai_api_key"] = "secret"
        return env

    def test_no_auth_header_when_key_unset(self):
        with patch.dict(os.environ, self._env(with_key=False), clear=False):
            os.environ.pop("openai_api_key", None)
            with patch.object(
                self.catjot.requests,
                "post",
                return_value=_FakeResp(_GOOD_LLM_PAYLOAD),
            ) as post:
                self.catjot.call_llm([{"role": "user", "content": "hi"}])
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])

    def test_auth_header_present_when_key_set(self):
        with patch.dict(os.environ, self._env(with_key=True), clear=False):
            with patch.object(
                self.catjot.requests,
                "post",
                return_value=_FakeResp(_GOOD_LLM_PAYLOAD),
            ) as post:
                self.catjot.call_llm([{"role": "user", "content": "hi"}])
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"], "Bearer secret"
        )

    def test_timeout_passed_to_request(self):
        with patch.dict(os.environ, self._env(with_key=True), clear=False):
            with patch.object(
                self.catjot.requests,
                "post",
                return_value=_FakeResp(_GOOD_LLM_PAYLOAD),
            ) as post:
                self.catjot.call_llm([{"role": "user", "content": "hi"}])
        self.assertIn("timeout", post.call_args.kwargs)

    def test_retry_once_on_transient_then_succeeds(self):
        import requests

        orig_backoff = self.catjot.LLM_RETRY_BACKOFF
        self.catjot.LLM_RETRY_BACKOFF = 0
        try:
            with patch.dict(os.environ, self._env(with_key=True), clear=False):
                post = MagicMock(
                    side_effect=[
                        requests.exceptions.ConnectionError("reset"),
                        _FakeResp(_GOOD_LLM_PAYLOAD),
                    ]
                )
                with patch.object(self.catjot.requests, "post", post):
                    msg = self.catjot.call_llm(
                        [{"role": "user", "content": "hi"}], retries=1
                    )
            self.assertEqual(post.call_count, 2)
            self.assertEqual(msg["content"], "hi")
        finally:
            self.catjot.LLM_RETRY_BACKOFF = orig_backoff

    def test_no_retry_by_default(self):
        import requests

        with patch.dict(os.environ, self._env(with_key=True), clear=False):
            post = MagicMock(
                side_effect=requests.exceptions.ConnectionError("reset")
            )
            with patch.object(self.catjot.requests, "post", post):
                with self.assertRaises(requests.exceptions.ConnectionError):
                    self.catjot.call_llm([{"role": "user", "content": "hi"}])
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
