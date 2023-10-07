#!/usr/bin/env python3
__author__ = "William Dizon"
__license__ = "MIT"
__version__ = "0.0.1"
__maintainer__ = "William Dizon"
__email__ = "wdchromium@gmail.com"
__status__ = "Development"

import unittest
from catjot import Note, NoteContext, SearchType
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

    def test_init_note(self):
        data = {
            'pwd': '/home/user/git',
            'now': 1694747655,
            'tag': 'projectx',
            'context': 'whoami',
            'message': 'hello\nthere\n'
        }

        inst = Note(data)
        self.assertEqual(inst.pwd, "/home/user/git")
        self.assertEqual(inst.now, 1694747655)
        self.assertEqual(inst.tag, "projectx")
        self.assertEqual(inst.context, "whoami")
        self.assertEqual(inst.message, "hello\nthere\n")

    def test_create_note(self):
        data = {
            'pwd': '/home/user/git',
            'now': 1694747655,
            'tag': 'projectx',
            'context': 'whoami',
            'message': 'hello\nthere\n'
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
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.tag, "")
        self.assertEqual(inst.context, "")
        self.assertEqual(inst.message, "the smallest unit passable to make a note\n")

    def test_write_note(self):
        Note.append(TMP_CATNOTE, Note.jot("this is a note"))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "note")))
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

    def test_write_note_to_diff_pwd(self):
        Note.append(TMP_CATNOTE, Note.jot("this is a note", pwd="/home/user/git/git/git"))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "note")))
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, "/home/user/git/git/git")
        self.assertEqual(inst.message, "this is a note\n")

        iters = 0
        for inst in Note.match(TMP_CATNOTE, (SearchType.DIRECTORY, "/home/user/git/git/git")):
            self.assertEqual(inst.pwd, "/home/user/git/git/git")
            iters += 1
        self.assertEqual(iters, 1)

    def test_write_note_to_diff_timestamp(self):
        Note.append(TMP_CATNOTE, Note.jot("this is a note", pwd="/home/user/git/git/git", now=1694744444))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "note")))
        self.assertEqual(inst.now, 1694744444)
        self.assertEqual(inst.pwd, "/home/user/git/git/git")
        self.assertEqual(inst.message, "this is a note\n")

    def test_list_herenote(self):
        Note.append(TMP_CATNOTE, Note.jot("this is a note"))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.TREE, getcwd())))
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

        Note.append(TMP_CATNOTE, Note.jot("nnnnnote2"))
        multi = Note.match(TMP_CATNOTE, (SearchType.TREE, getcwd()))
        inst = next(multi)
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

        inst = next(multi)
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "nnnnnote2\n")

    def test_list_herenote_homenote(self):
        Note.append(TMP_CATNOTE, Note.jot("this is a note"))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.TREE, getcwd())))
        self.assertTrue(abs(time() - inst.now) <= 1) #is within one second
        self.assertEqual(inst.pwd, getcwd())
        self.assertEqual(inst.message, "this is a note\n")

        Note.append(TMP_CATNOTE, Note.jot("nnnnnote2"))
        multi = Note.match(TMP_CATNOTE, (SearchType.TREE, getcwd()))
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
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.TREE, getcwd())))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_multi_line_string(self):
        thenote = "notes can sometimes\ntake two lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.TREE, getcwd())))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_search_multi_line_string(self):
        thenote = "notes can sometimes\ntake two lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "take")))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_search_multi_line_string_with_empty_lines(self):
        thenote = "notes can sometimes\n\n\n\ntake many lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "many")))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_search_multi_line_string_insensitive(self):
        thenote = "notes can sometimes\nTAKE two lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "take")))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_search_multi_line_string_with_empty_lines_insensitive(self):
        thenote = "notes can sometimes\n\n\n\ntake mAny lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "MANY")))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_search_multi_line_string_with_split_words_insensitive(self):
        thenote = "notes can sometimes\n\n\n\ntake mAny lines"
        Note.append(TMP_CATNOTE, Note.jot(thenote))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "MANY")))
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n{thenote}\n")

    def test_creating_label(self):
        thenote = "notes take labels now"
        Note.append(TMP_CATNOTE, Note.jot(thenote, tag="secret"))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notes")))
        self.assertEqual(inst.tag, "secret")
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n[secret]\n{thenote}\n")

    def test_adding_context(self):
        thenote = ".bash_profile"
        Note.append(TMP_CATNOTE, Note.jot(thenote, context="ls /home/user"))
        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "profile")))
        self.assertEqual(inst.context, "ls /home/user")
        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n% ls /home/user\n{thenote}\n")

        multi = Note.iterate(FIXED_CATNOTE)
        inst = next(multi)
        self.assertEqual(inst.now, 1694747662)
        self.assertEqual(inst.context, "adoption")

    def test_adding_context_to_existing_jot(self):
        thenote = ".bashrc"
        Note.append(TMP_CATNOTE, Note.jot(thenote))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "bashrc")))
        self.assertEqual(inst.context, "") # no context yet
        inst.amend(TMP_CATNOTE, context="favorite_files") # context should exist in .new
        self.assertEqual(inst.context, "") # no but no read ever had it present

        inst = next(Note.match(TMP_CATNOTE + '.new', (SearchType.MESSAGE_I, "bashrc")))
        self.assertEqual(inst.context, "favorite_files") # new file should reflect this

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "bashrc")))
        self.assertEqual(inst.context, "") # but not on the original file

        Note.commit(TMP_CATNOTE) # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "bashrc")))
        self.assertEqual(inst.context, "favorite_files") # new file should reflect this

    def test_changing_pwd_to_existing_jot(self):
        pre_pwd = '/home/user/git'
        post_pwd = '/home/alice/in'
        Note.append(TMP_CATNOTE, Note.jot("notey", pwd=pre_pwd))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.pwd, pre_pwd)
        inst.amend(TMP_CATNOTE, pwd=post_pwd) # create .new file

        inst = next(Note.match(TMP_CATNOTE + '.new', (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.pwd, post_pwd) # new file should reflect this

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.pwd, pre_pwd)

        Note.commit(TMP_CATNOTE) # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.pwd, post_pwd)

    def test_deleting_tag_from_existing_note(self):
        pre_tag = 'stuff'
        post_tag = '~stuff'
        Note.append(TMP_CATNOTE, Note.jot("notey", tag=pre_tag))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.tag, pre_tag)
        inst.amend(TMP_CATNOTE, tag=post_tag) # create .new file

        inst = next(Note.match(TMP_CATNOTE + '.new', (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.tag, "") # new file should reflect this

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.tag, pre_tag)

        Note.commit(TMP_CATNOTE) # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.tag, "") # new file should reflect this

    def test_append_multiple_tags(self):
        pre_tag = 'blamo'
        post_tag = 'better_stuff'
        Note.append(TMP_CATNOTE, Note.jot("notey", tag=pre_tag))

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertIn(pre_tag, inst.tag)
        inst.amend(TMP_CATNOTE, tag=post_tag) # create .new file

        inst = next(Note.match(TMP_CATNOTE + '.new', (SearchType.MESSAGE_I, "notey")))
        self.assertIn(pre_tag, inst.tag)
        self.assertIn(post_tag, inst.tag)

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertEqual(inst.tag, pre_tag)
        self.assertNotIn(post_tag, inst.tag)

        Note.commit(TMP_CATNOTE) # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertIn(pre_tag, inst.tag)
        self.assertIn(post_tag, inst.tag)

        self.assertEqual(inst.tag, f"{pre_tag} {post_tag}")

        another_tag = 'thebest'
        inst.amend(TMP_CATNOTE, tag=another_tag) # create .new file
        Note.commit(TMP_CATNOTE) # commit the change

        inst = next(Note.match(TMP_CATNOTE, (SearchType.MESSAGE_I, "notey")))
        self.assertIn(pre_tag, inst.tag)
        self.assertIn(post_tag, inst.tag)
        self.assertIn(another_tag, inst.tag)
        self.assertEqual(inst.tag, f"{pre_tag} {post_tag} {another_tag}")

        last_tag = "~blamo"
        inst.amend(TMP_CATNOTE, tag=last_tag) # create .new file
        Note.commit(TMP_CATNOTE) # commit the change

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
        self.assertEqual(inst.now, 1394443232)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertTrue(inst.message.startswith('^-^\n^-^\n^-^\nDirectory:/home/user\nDate'))
        self.assertTrue(inst.message.endswith("important notice about your home warranty\n"))

        inst = next(multi)
        self.assertEqual(inst.now, 1694747613)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.message, "really important notice about your home warranty\n")

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

    def test_tag_header(self):
        inst = next(Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")))
        self.assertEqual(inst.tag, "project1")

        dt = datetime.fromtimestamp(inst.now)
        friendly_date = dt.strftime(Note.DATE_FORMAT)
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n[project1]\n% adoption\nhello\n")
        inst.tag = "blamo"
        self.assertEqual(str(inst), f"> cd {inst.pwd}\n# date {friendly_date}\n[blamo]\n% adoption\nhello\n")

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
        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user/child")):
            self.assertEqual(inst.pwd, "/home/user/child")
            iters += 1
        self.assertEqual(iters, 1)

    def test_readin_unicode(self):
        iters = 0
        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user/git/catjot")):
            self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")
            iters += 1
        self.assertEqual(iters, 1)

    def test_match_unicode(self):
        iters = 0
        for inst in Note.match(FIXED_CATNOTE, (SearchType.MESSAGE, "なんでこんなにふわふわなの?")):
            iters += 1
        for inst in Note.match(FIXED_CATNOTE, (SearchType.MESSAGE_I, "なんでこんなにふわふわなの?")):
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
        for inst in Note.match(FIXED_CATNOTE, (SearchType.CONTEXT_I, 'NEKO')):
            self.assertEqual(inst.message, "なんでこんなにふわふわなの?\n")
            iters += 1
        self.assertEqual(iters, 1)

    def test_delete_record(self):
        iters = 0
        # file, untouched
        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")):
            iters += 1
        self.assertEqual(iters, 4)

        Note.delete(FIXED_CATNOTE, 1694747797)

        iters = 0
        # new file, reduced by any timestamp matches
        for inst in Note.match(FIXED_CATNOTE + '.new', (SearchType.DIRECTORY, "/home/user")):
            iters += 1
            self.assertNotEqual(inst.now, 1694747797)
        self.assertEqual(iters, 3)

        Note.commit(FIXED_CATNOTE)

        iters = 0
        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")):
            iters += 1
        self.assertEqual(iters, 3)

        iters = 0
        for inst in Note.match(FIXED_CATNOTE + '.old', (SearchType.DIRECTORY, "/home/user")):
            iters += 1
        self.assertEqual(iters, 4)

    def test_pop_record(self):
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
        for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user/child")):
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
        NOTEFILE = "edgecase.jot"
        multi = Note.iterate(NOTEFILE)
        inst = next(multi)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.now, 1694747662)
        self.assertIn("catland", inst.tag)
        self.assertIn("project", inst.tag)
        self.assertEqual(inst.context, "adoption")
        self.assertEqual(inst.message, "the record separator looks like\n^-^\nand i want this to remain intact\n")

        inst = next(multi)
        self.assertEqual(inst.pwd, "/home/user")
        self.assertEqual(inst.now, 1694747841)
        self.assertEqual(inst.message, "testing that this shows up unimpacted\n")

    ### tests for context manager
    def test_note_context_creation(self):
        with NoteContext(FIXED_CATNOTE, (SearchType.DIRECTORY, '/home/user')) as nc:
            self.assertEqual(len(nc), 4)

        with NoteContext(FIXED_CATNOTE, (SearchType.DIRECTORY, '/home/user/git/catjot')) as nc:
            self.assertEqual(len(nc), 1)

        with NoteContext(FIXED_CATNOTE, []) as nc:
            self.assertEqual(len(nc), 0)

        with NoteContext(FIXED_CATNOTE, (SearchType.ALL, '')) as nc:
            self.assertEqual(len(nc), 7)

        with NoteContext(FIXED_CATNOTE, (SearchType.CONTEXT_I, 'adoption')) as nc:
            self.assertEqual(len(nc), 1)

    ### tests for enum-based record matching
    def test_match_and(self):
        matches = Note.match(FIXED_CATNOTE, [(SearchType.ALL, '')])
        self.assertEqual(len(list(matches)), 7)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.DIRECTORY, '/home/user')])
        self.assertEqual(len(list(matches)), 4)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, '/home/user')])
        self.assertEqual(len(list(matches)), 7)

        matches = Note.match(FIXED_CATNOTE, [])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, '/home/user'),
                                             (SearchType.TREE, '/home/user/catjot')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, '/home/user'),
                                             (SearchType.TREE, '/home/user/git/catjot')])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.DIRECTORY, '/home/user'),
                                             (SearchType.DIRECTORY, '/home/user/git/catjot')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 1694747797),
                                             (SearchType.DIRECTORY, '/home/user')])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 1111111111),
                                             (SearchType.DIRECTORY, '/home/user')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, 'work')])
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, 'work')])
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, 'WORK')])
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, 'WORK')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, 'WORK')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, 'ふわふわ')])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, 'ふわふわ')])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, '')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, '')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, '^-^')])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, '')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.DIRECTORY, '')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, 'neko')])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, 'adoption')])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, '')])
        self.assertEqual(len(list(matches)), 0) # no matches on falsy values

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, '')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, 'ふわふわ')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, 'ふわふわ')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 1695184544)])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, '0')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 0)])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'project1')])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'project2')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'neko')])
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'multiple')])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'unrelated')])
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'multiple'),
                                             (SearchType.TAG, 'unrelated')])
        self.assertEqual(len(list(matches)), 1)

    def test_match_or(self):
        matches = Note.match(FIXED_CATNOTE, [(SearchType.DIRECTORY, '/home/user')], 'or')
        self.assertEqual(len(list(matches)), 4)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, '/home/user')], 'or')
        self.assertEqual(len(list(matches)), 7)

        matches = Note.match(FIXED_CATNOTE, [], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, '/home/user'),
                                             (SearchType.TREE, '/home/user/catjot')], 'or')
        self.assertEqual(len(list(matches)), 7)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, '/home/user'),
                                             (SearchType.TREE, '/home/user/git/catjot')], 'or')
        self.assertEqual(len(list(matches)), 7)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.DIRECTORY, '/home/user'),
                                             (SearchType.DIRECTORY, '/home/user/git/catjot')], 'or')
        self.assertEqual(len(list(matches)), 5)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, 'adoption'),
                                             (SearchType.CONTEXT_I, 'neko')], 'or')
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 1694747797),
                                             (SearchType.DIRECTORY, '/home/user')], 'or')
        self.assertEqual(len(list(matches)), 4)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 1111111111),
                                             (SearchType.DIRECTORY, '/home/user')], 'or')
        self.assertEqual(len(list(matches)), 4)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 1694747797),
                                             (SearchType.TIMESTAMP, 1694748108)], 'or')
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, 'work')], 'or')
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, 'work')], 'or')
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, 'WORK')], 'or')
        self.assertEqual(len(list(matches)), 2)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, 'WORK')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, 'WORK')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, 'ふわふわ')], 'or')
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, 'ふわふわ')], 'or')
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE, '')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, '')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.MESSAGE_I, '^-^')], 'or')
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TREE, '')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.DIRECTORY, '')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, 'neko')], 'or')
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, 'adoption')], 'or')
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, '')], 'or')
        self.assertEqual(len(list(matches)), 0) # no matches on falsy values

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, '')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT_I, 'ふわふわ')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.CONTEXT, 'ふわふわ')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 1695184544)], 'or')
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, '0')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TIMESTAMP, 0)], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'project1')], 'or')
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'project2')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'neko')], 'or')
        self.assertEqual(len(list(matches)), 0)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'multiple')], 'or')
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'unrelated')], 'or')
        self.assertEqual(len(list(matches)), 1)

        matches = Note.match(FIXED_CATNOTE, [(SearchType.TAG, 'multiple'),
                                             (SearchType.TAG, 'unrelated')], 'or')
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
        b_note = Note.jot("helloz\nthere\n\n", pwd="/home", context="fun\n", tag="taggin")
        self.assertEqual(a_note, b_note)

        a_note = Note.jot("helloz\nthere", pwd="/home", context="fun", tag="taggin", now=1695184544)
        b_note = Note.jot("helloz\nthere\n\n", pwd="/home", context="fun\n", tag="taggin", now=1695184544)
        self.assertEqual(a_note, b_note)

        a_note = Note.jot("helloz\nthere", pwd="/home", context="fun", tag="taggin", now=1695184544)
        b_note = Note.jot("helloz\nthere\n\n", pwd="/home", context="fun\n", tag="taggin", now=1695184111)
        self.assertNotEqual(a_note, b_note)

    def test_equality_full_example(self):
        matches = list(inst for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user")))
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
        multi = Note.match(FIXED_CATNOTE + '.new', (SearchType.DIRECTORY, "/home/user"))
        inst = next(multi)
        self.assertEqual(inst.now, 1694747662)
        self.assertEqual(inst, matches.pop(0))
        matches.pop(0) # skip over dropped one (7797)
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
        matches = list(inst for inst in Note.match(FIXED_CATNOTE, (SearchType.DIRECTORY, "/home/user"), time_only=True))
        self.assertEqual(len(matches), 4)

        self.assertEqual(matches[0], 1694747662)
        self.assertEqual(matches[1], 1694747797)
        self.assertEqual(matches[2], 1694747841)
        self.assertEqual(matches[3], 1694748108)

        matches = list(inst for inst in Note.match(FIXED_CATNOTE, (SearchType.ALL, ""), time_only=True))
        self.assertEqual(matches[0], 1694747662)
        self.assertEqual(matches[1], 1694747797)
        self.assertEqual(matches[2], 1694747841)
        self.assertEqual(matches[3], 1694748108)
        self.assertEqual(matches[4], 1694941231)
        self.assertEqual(matches[5], 1695184544)
        self.assertEqual(matches[6], 1694955555)

if __name__ == '__main__':
    unittest.main()

