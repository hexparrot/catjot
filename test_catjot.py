#!/usr/bin/env python3
__author__ = "William Dizon"
__license__ = "MIT"
__version__ = "0.0.1"
__maintainer__ = "William Dizon"
__email__ = "wdchromium@gmail.com"
__status__ = "Development"

import unittest
from catjot import Note
from time import time
from datetime import datetime
from os import getcwd, remove, environ

TMP_CATNOTE=".catjot"
FIXED_CATNOTE="example.jot"

class TestTaker(unittest.TestCase):
    def setup(self):
        pass

    def tearDown(self):
        try:
            remove(TMP_CATNOTE)
        except FileNotFoundError:
            pass

        try:
            remove(f"{TMP_CATNOTE}.new")
        except FileNotFoundError:
            pass

        import shutil, os
        shutil.move(f"{FIXED_CATNOTE}.old", FIXED_CATNOTE) if os.path.exists(f"{FIXED_CATNOTE}.old") else None

    def test_find_path_match(self):
        # searches through an example file for a matching string argument
        # using the default parameter "search"

        inst = next(Note.search(FIXED_CATNOTE, "hello"))
        self.assertEqual(inst.now, 1694747662)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "hello\n")

        inst = next(Note.search(FIXED_CATNOTE, "really"))
        self.assertEqual(inst.now, 1694748108)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "This is really working!\n")

        multi = Note.search(FIXED_CATNOTE, "what")
        # expecting two hits
        inst = next(multi)
        self.assertEqual(inst.now, 1694747797)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "what\n")

        inst = next(multi)
        self.assertEqual(inst.now, 1694747841)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "that is what i call work\n")

        inst = next(Note.search(FIXED_CATNOTE, "working"))
        self.assertEqual(inst.now, 1694748108)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "This is really working!\n")

        multi = Note.search(FIXED_CATNOTE, "work")
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

    def test_write_note(self):
        Note.append(TMP_CATNOTE, "this is a note")

        inst = next(Note.search(TMP_CATNOTE, "note"))
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

    def test_write_note_to_diff_pwd(self):
        Note.append(TMP_CATNOTE, "this is a note", pwd="/home/user/git/git/git")

        inst = next(Note.search(TMP_CATNOTE, "note"))
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, "/home/user/git/git/git")
        self.assertEqual(inst.message, "this is a note\n")

        iters = 0
        for inst in Note.match_dir(TMP_CATNOTE, "/home/user/git/git/git"):
            self.assertEqual(inst.pwd, "/home/user/git/git/git")
            iters += 1
        self.assertEqual(iters, 1)

    def test_write_note_to_diff_timestamp(self):
        Note.append(TMP_CATNOTE, "this is a note", pwd="/home/user/git/git/git", now=1694744444)

        inst = next(Note.search(TMP_CATNOTE, "note"))
        self.assertEqual(inst.now, 1694744444)
        self.assertEqual(inst.pwd, "/home/user/git/git/git")
        self.assertEqual(inst.message, "this is a note\n")

    def test_list_herenote(self):
        Note.append(TMP_CATNOTE, "this is a note")
        inst = next(Note.list(TMP_CATNOTE))
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

        Note.append(TMP_CATNOTE, "nnnnnote2")
        multi = Note.list(TMP_CATNOTE)
        inst = next(multi)
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

        inst = next(multi)
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "nnnnnote2\n")

    def test_list_herenote_homenote(self):
        Note.append(TMP_CATNOTE, "this is a note")
        inst = next(Note.list(TMP_CATNOTE))
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

        Note.append(TMP_CATNOTE, "nnnnnote2")
        multi = Note.list(TMP_CATNOTE)
        inst = next(multi)
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

        inst = next(multi)
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "nnnnnote2\n")

    def test_string_representation(self):
        thenote = "this is a note-o"
        Note.append(TMP_CATNOTE, thenote)
        inst = next(Note().list(TMP_CATNOTE))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_multi_line_string(self):
        thenote = "notes can sometimes\ntake two lines"
        Note.append(TMP_CATNOTE, thenote)
        inst = next(Note().list(TMP_CATNOTE))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_search_multi_line_string(self):
        thenote = "notes can sometimes\ntake two lines"
        Note.append(TMP_CATNOTE, thenote)
        inst = next(Note().search(TMP_CATNOTE, "take"))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_search_multi_line_string_with_empty_lines(self):
        thenote = "notes can sometimes\n\n\n\ntake many lines"
        Note.append(TMP_CATNOTE, thenote)
        inst = next(Note().search(TMP_CATNOTE, "many"))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_search_multi_line_string_insensitive(self):
        thenote = "notes can sometimes\nTAKE two lines"
        Note.append(TMP_CATNOTE, thenote)
        inst = next(Note().search_i(TMP_CATNOTE, "take"))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_search_multi_line_string_with_empty_lines_insensitive(self):
        thenote = "notes can sometimes\n\n\n\ntake mAny lines"
        Note.append(TMP_CATNOTE, thenote)
        inst = next(Note().search_i(TMP_CATNOTE, "MANY"))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_search_multi_line_string_with_split_words_insensitive(self):
        thenote = "notes can sometimes\n\n\n\ntake mAny lines"
        Note.append(TMP_CATNOTE, thenote)
        inst = next(Note().search_i(TMP_CATNOTE, "MANY"))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_creating_label(self):
        thenote = "notes take labels now"
        Note.append(TMP_CATNOTE, thenote, tag="secret")
        inst = next(Note().search_i(TMP_CATNOTE, "notes"))
        self.assertEqual(inst.tag, "secret")
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n[secret]\n{thenote}\n")

    #### end note creation

    def test_iterate_all_notes(self):
        multi = Note.iterate(FIXED_CATNOTE)
        inst = next(multi)
        self.assertEqual(inst.now, 1694747662)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "hello\n")

        for _ in range(0,4):
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
        for _ in range(0,6):
            inst = next(multi)

        self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")

        inst = next(multi)
        self.assertEqual(inst.now, 1694955555)
        self.assertEqual(inst.pwd, "/home/user/alice")
        self.assertEqual(inst.message, "^-^\n")

    def test_handle_record_separator_note_invalidation(self):
        multi = Note.iterate("broken.jot")
        inst = next(multi) # ^-^ data case
        inst = next(multi) # ^-^ extras "home warranty"
        self.assertEqual(inst.now, 1694747612)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "important notice about your home warranty\n")

        inst = next(multi) # ^-^ extras "home warranty"
        self.assertEqual(inst.now, 1694747613)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "really important notice about your home warranty\n")

        inst = next(multi) # ^-^ extras "home warranty"
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

    def test_label_header(self):
        inst = next(Note.match_dir(FIXED_CATNOTE, "/home/user"))
        self.assertEqual(inst.tag, "project1")

        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n[project1]\nhello\n")
        inst.tag = "blamo"
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n[blamo]\nhello\n")

    def test_only_perfect_path_match(self):
        iters = 0
        for inst in Note.match_dir(FIXED_CATNOTE, "/home/user"):
            self.assertEqual(inst.pwd, "/home/user")
            iters += 1
        self.assertEqual(iters, 4)

        iters = 0
        for inst in Note.match_dir(FIXED_CATNOTE, "/home/user/child"):
            self.assertEqual(inst.pwd, "/home/user/child")
            iters += 1
        self.assertEqual(iters, 1)

    def test_readin_unicode(self):
        iters = 0
        for inst in Note.match_dir(FIXED_CATNOTE, "/home/user/git/catjot"):
            self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")
            iters += 1
        self.assertEqual(iters, 1)

    def test_match_unicode(self):
        iters = 0
        for inst in Note().search(FIXED_CATNOTE, "なんでこんなにふわふわなの?"):
            iters += 1
        for inst in Note().search_i(FIXED_CATNOTE, "なんでこんなにふわふわなの?"):
            iters += 1
        self.assertEqual(iters, 2)

        self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")

    def test_delete_record(self):
        iters = 0
        # file, untouched
        for inst in Note.match_dir(FIXED_CATNOTE, "/home/user"):
            iters += 1
        self.assertEqual(iters, 4)

        Note.delete(FIXED_CATNOTE, 1694747797)

        iters = 0
        # new file, reduced by any timestamp matches
        for inst in Note.match_dir(f"{FIXED_CATNOTE}.new", "/home/user"):
            iters += 1
            self.assertNotEqual(inst.now, 1694747797)
        self.assertEqual(iters, 3)

        Note.commit(FIXED_CATNOTE)

        iters = 0
        for inst in Note.match_dir(FIXED_CATNOTE, "/home/user"):
            iters += 1
        self.assertEqual(iters, 3)

        iters = 0
        for inst in Note.match_dir(f"{FIXED_CATNOTE}.old", "/home/user"):
            iters += 1
        self.assertEqual(iters, 4)

    def test_pop_record(self):
        iters = 0
        # file, untouched
        for inst in Note.match_dir(FIXED_CATNOTE, "/home/user"):
            iters += 1
        self.assertEqual(iters, 4)

        Note.pop(FIXED_CATNOTE, "/home/user")
        Note.commit(FIXED_CATNOTE)

        for inst in Note.list(FIXED_CATNOTE):
            # check the exact match (last timestamp is removed)
            self.assertNotEqual(inst.now, 1694748108)

        iters = 0
        # should show one fewer entry
        for inst in Note.match_dir(FIXED_CATNOTE, "/home/user"):
            iters += 1
        self.assertEqual(iters, 3)

        iters = 0
        # other directories should be untouched
        for inst in Note.match_dir(FIXED_CATNOTE, "/home/user/child"):
            iters += 1
        self.assertEqual(iters, 1)

    def test_empty_append_is_aborted(self):
        Note.append(TMP_CATNOTE, "this is the first note")

        iters = 0
        for inst in Note.iterate(TMP_CATNOTE):
            iters += 1
        self.assertEqual(iters, 1)

        Note.append(TMP_CATNOTE, "")

        iters = 0
        for inst in Note.iterate(TMP_CATNOTE):
            iters += 1
        self.assertEqual(iters, 1)

if __name__ == '__main__':
    unittest.main()

