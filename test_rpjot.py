#!/usr/bin/env python3
__author__ = "William Dizon"
__license__ = "MIT"
__version__ = "0.0.1"
__maintainer__ = "William Dizon"
__email__ = "wdchromium@gmail.com"
__status__ = "Development"

import catjot
import unittest
from catjot import Note, NoteContext, SearchType, ContextBundle
from time import time
from datetime import datetime
from os import getcwd, remove, environ

TMP_CATNOTE = "tests/.catjot"
FIXED_CATNOTE = "tests/bellvue.jot"
catjot.NOTEFILE = FIXED_CATNOTE


class TestRpjot(unittest.TestCase):
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

        (
            shutil.move(f"{FIXED_CATNOTE}.old", FIXED_CATNOTE)
            if os.path.exists(f"{FIXED_CATNOTE}.old")
            else None
        )

    def test_holds_tags(self):
        ctx = ContextBundle("bartholomew")
        self.assertTrue("bartholomew" in ctx.tags)
        self.assertEqual(len(ctx.tags), 1)

        ctx = ContextBundle(["bartholomew"])
        self.assertTrue("bartholomew" in ctx.tags)
        self.assertEqual(len(ctx.tags), 1)

    def test_adds_tags(self):
        ctx = ContextBundle(["bartholomew"])
        ctx += "gameplay"

        self.assertTrue("bartholomew" in ctx.tags)
        self.assertTrue("gameplay" in ctx.tags)
        self.assertEqual(len(ctx.tags), 2)

    def test_remove_tags(self):
        ctx = ContextBundle(["bartholomew", "gameplay"])

        ctx -= "bartholomew"
        self.assertTrue("gameplay" in ctx.tags)
        self.assertEqual(len(ctx.tags), 1)

        ctx -= "bartholomew"
        self.assertTrue("gameplay" in ctx.tags)
        self.assertEqual(len(ctx.tags), 1)

        ctx -= "gameplay"
        self.assertEqual(len(ctx.tags), 0)

    def test_dont_add_dups(self):
        ctx = ContextBundle(["bartholomew"])
        ctx += "bartholomew"

        self.assertTrue("bartholomew" in ctx.tags)
        self.assertEqual(len(ctx.tags), 1)

    def test_len_returns_notecount(self):
        ctx = ContextBundle(["bartholomew"])
        self.assertEqual(len(ctx), 2)

        ctx = ContextBundle(["system_role"])
        self.assertEqual(len(ctx), 9)

        ctx = ContextBundle(["bartholomew", "system_role"])
        self.assertEqual(len(ctx), 11)

        ctx = ContextBundle(["bartholomew", "system_role", "story_premise"])
        self.assertEqual(len(ctx), 11)

    def test_remove_prunes_notes(self):
        ctx = ContextBundle(["bartholomew", "system_role", "story_premise"])
        self.assertEqual(len(ctx), 11)

        ctx -= "system_role"
        self.assertEqual(len(ctx), 2)

    def test_adds_notes_by_dir(self):
        ctx = ContextBundle("/system/rules/roleplaying")
        self.assertEqual(len(ctx.notes), 4)

        ctx += "/story/premises/main_plot"
        self.assertEqual(len(ctx.notes), 6)

    def test_subs_notes_by_dir(self):
        ctx = ContextBundle("/system/rules/roleplaying")
        ctx += "/story/premises/main_plot"
        self.assertEqual(len(ctx.notes), 6)

        ctx -= "/system/rules/roleplaying"
        self.assertEqual(len(ctx.notes), 2)

    def test_add_madeup_items(self):
        ctx = ContextBundle("/fake/things/roleplaying")
        ctx += "/story/that/isntreal"
        ctx += "/make/up/stuff"
        self.assertEqual(len(ctx.notes), 0)

    def test_sub_absent_items(self):
        ctx = ContextBundle("/system/rules/roleplaying")
        ctx -= "/story/premises/main_plot"
        ctx -= "/make/up/stuff"
        self.assertEqual(len(ctx.notes), 4)

    def test_adds_notes_by_ts(self):
        ctx = ContextBundle(1726009125)
        self.assertEqual(len(ctx.notes), 1)

        ctx += 1726009504
        self.assertEqual(len(ctx.notes), 2)

    def test_subs_notes_by_ts(self):
        ctx = ContextBundle(1726009125)
        ctx += 1726009504
        self.assertEqual(len(ctx.notes), 2)

        ctx -= 1726009125
        self.assertEqual(len(ctx.notes), 1)

    def test_add_madeup_ts(self):
        ctx = ContextBundle(12345)
        ctx += 123
        ctx += 1
        self.assertEqual(len(ctx.notes), 0)

    def test_sub_absent_ts(self):
        ctx = ContextBundle(1726009504)
        ctx -= 123
        ctx -= 1
        self.assertEqual(len(ctx.notes), 1)

    def test_iter_notes(self):
        ctx = ContextBundle("/system/rules/roleplaying")
        self.assertEqual(len(ctx.notes), 4)
        for i in ctx:
            self.assertIsInstance(i, Note, msg="every iterated object should be a note")

    def test_iter_tags(self):
        ctx = ContextBundle(1725989783)
        self.assertIn("uncensored_ai", ctx.active_tags)
        self.assertIn("system_role", ctx.active_tags)
        self.assertEqual(len(ctx.active_tags), 2)

        ctx += 1725989938

        self.assertIn("uncensored_ai", ctx.active_tags)
        self.assertIn("system_role", ctx.active_tags)
        self.assertIn("story_premise", ctx.active_tags)
        self.assertIn("bellvue_family", ctx.active_tags)
        self.assertIn("bartholomew", ctx.active_tags)
        self.assertEqual(len(ctx.active_tags), 5)

    def test_suppress_tag(self):
        ctx = ContextBundle("character-backstory")
        self.assertEqual(len(ctx), 6)

        ctx.suppress("bastard")  # get outta here, sam
        self.assertEqual(len(ctx), 5)

        counted = 0
        for n in ctx:
            counted += 1
            with self.subTest(n=n):
                self.assertTrue(set(n.tag.split()).isdisjoint(set("bastard")))
        else:
            self.assertEqual(counted, 5)

    def test_suppress_directory(self):
        ctx = ContextBundle("character-backstory")
        self.assertEqual(len(ctx), 6)

        ctx.suppress("/story/character")  # get outta here, sam
        self.assertEqual(len(ctx), 0)

    def test_suppress_timestamp(self):
        ctx = ContextBundle("character-backstory")
        self.assertEqual(len(ctx), 6)

        ctx.suppress(1725999543)  # rm aurora
        self.assertEqual(len(ctx), 5)

    def test_unsuppress_tag(self):
        ctx = ContextBundle("character-backstory")
        self.assertEqual(len(ctx), 6)

        ctx.suppress("bastard")  # get outta here, sam
        self.assertEqual(len(ctx), 5)

        ctx.unsuppress("bastard")  # get outta here, sam
        self.assertEqual(len(ctx), 6)

        ctx.unsuppress("bastard")  # handle gracefully and silently
        self.assertEqual(len(ctx), 6)

    def test_unsuppress_directory(self):
        ctx = ContextBundle("character-backstory")
        self.assertEqual(len(ctx), 6)

        ctx.suppress("/story/character")  # get outta here, sam
        self.assertEqual(len(ctx), 0)

        ctx.unsuppress("/story/character")
        self.assertEqual(len(ctx), 6)

        ctx.unsuppress("/story/character")  # handle gracefully and silently
        self.assertEqual(len(ctx), 6)

    def test_unsuppress_timestamp(self):
        ctx = ContextBundle("character-backstory")
        self.assertEqual(len(ctx), 6)

        ctx.suppress(1725999543)  # rm aurora
        self.assertEqual(len(ctx), 5)

        ctx.unsuppress(1725999543)  # rm aurora
        self.assertEqual(len(ctx), 6)

        ctx.unsuppress(1725999543)  # handle gracefully and silently
        self.assertEqual(len(ctx), 6)

    def test_iter_by_ts(self):
        ctx = ContextBundle(1726009125)
        self.assertEqual(len(ctx), 1)
        ctx += 1726009504
        self.assertEqual(len(ctx), 2)
        ctx -= 1726009125
        self.assertEqual(len(ctx), 1)

    def test_iter_by_dirs(self):
        ctx = ContextBundle("/story/character")
        self.assertEqual(len(ctx), 6)
        ctx += "/system/rules/gameplay"
        self.assertEqual(len(ctx), 9)
        ctx -= "/system/rules/gameplay"
        self.assertEqual(len(ctx), 6)

    def test_operations_return_newobj(self):
        ctx1 = ContextBundle("/story/character")
        ctx2 = ctx1 + 1726003872

        self.assertIsNot(ctx1, ctx2)

        ctx3 = ctx2 - 1725999875

        self.assertIsNot(ctx1, ctx3)
        self.assertIsNot(ctx2, ctx3)

        ctx4 = ctx3 - "/story/character"

        self.assertIsNot(ctx1, ctx2)
        self.assertIsNot(ctx1, ctx3)
        self.assertIsNot(ctx1, ctx4)
        self.assertIsNot(ctx2, ctx3)
        self.assertIsNot(ctx2, ctx4)
        self.assertIsNot(ctx3, ctx4)

    def test_consolidate_bundle(self):
        ctx1 = ContextBundle("/story/character")
        ctx2 = ContextBundle("/story/character/hardcoded_characteristics")
        ctx3 = ctx1 + ctx2

        self.assertIsNot(ctx3, ctx1)
        self.assertIsNot(ctx3, ctx2)

        self.assertEqual(len(ctx1.tags) + len(ctx2.tags), len(ctx3.tags))
        self.assertEqual(len(ctx1.dirs) + len(ctx2.dirs), len(ctx3.dirs))
        self.assertEqual(len(ctx1.ts) + len(ctx2.ts), len(ctx3.ts))

        self.assertSetEqual(ctx1.tags | ctx2.tags, ctx3.tags)
        self.assertSetEqual(ctx1.dirs | ctx2.dirs, ctx3.dirs)
        self.assertSetEqual(ctx1.ts | ctx2.ts, ctx3.ts)

        self.assertEqual(len(ctx1), 6)
        self.assertEqual(len(ctx2), 1)
        self.assertEqual(len(ctx3), 7)

    def test_nonoverlapping_bundle(self):
        ctx1 = ContextBundle("/story/character")
        ctx2 = ContextBundle("luna")

        self.assertIn("luna", ctx1.active_tags)
        self.assertIn("luna", ctx2.active_tags)
        self.assertIn("luna_bellvue", ctx1.active_tags)
        self.assertIn("luna_bellvue", ctx2.active_tags)

        ctx3 = ctx1 - ctx2

        self.assertIsNot(ctx3, ctx1)
        self.assertIsNot(ctx3, ctx2)

        self.assertEqual(len(ctx3), 5)  # 6 chars-1 subtracted
        self.assertIn("luna", ctx3.blocks["tag"])  # the return string

        ctx4 = ctx3 - ContextBundle(
            1725999624
        )  # the previous size 5 subtracting evie by timestamp
        self.assertIn(1725999624, ctx4.blocks["timestamp"])
        self.assertEqual(len(ctx4), 4)

        ctx5 = ctx4 - ContextBundle("/story/character")
        self.assertIn("/story/character", ctx5.blocks["directory"])
        self.assertEqual(len(ctx5), 0)

    def test_return_str(self):
        ctx = ContextBundle(1726009504)
        self.assertEqual(
            str(ctx),
            """Storytelling guidelines

- Allow the user role to guide most of the actions; do not imply them unless it is clear from context what the intent was. For example, if the user asks somebody to wait a moment, allow the user to choose the dialog to address the subject with.""",
        )

        ctx += 1726008604
        self.assertEqual(
            str(ctx),
            """Storytelling guidelines

- Allow the user role to guide most of the actions; do not imply them unless it is clear from context what the intent was. For example, if the user asks somebody to wait a moment, allow the user to choose the dialog to address the subject with.

Waitstaff, resident mechanics, and other servants, like butlers and drivers are all women.

All of them are women, every single one of them.""",
        )

        ctx -= 1726009504
        self.assertEqual(
            str(ctx),
            """Waitstaff, resident mechanics, and other servants, like butlers and drivers are all women.

All of them are women, every single one of them.""",
        )

        ctx.suppress(1726008604)
        self.assertEqual(
            str(ctx),
            """""",
        )

    def test_repr(self):
        # Create a ContextBundle instance with specific tags, dirs, and timestamps
        cb = ContextBundle(["/dir1", "tag1", 1234567890])

        # Manually construct the expected repr string
        expected_repr = (
            "ContextBundle(tags={'tag1'}, dirs={'/dir1'}, ts={1234567890}, "
            "notes=[], blocks={'directory': set(), 'tag': set(), 'timestamp': set()})"
        )

        # Assert that the repr of cb matches the expected string
        self.assertEqual(repr(cb), expected_repr)

    def test_repr_with_notes(self):
        # Create a ContextBundle instance with notes
        note1 = Note({"message": "Message1", "context": "Context1"})
        note2 = Note({"message": "Message2", "context": "Context2"})
        cb = ContextBundle(["/dir1", "tag1", 1234567890])
        cb.notes = [note1, note2]

        # Manually construct the expected repr string including notes
        expected_repr = (
            "ContextBundle(tags={'tag1'}, dirs={'/dir1'}, ts={1234567890}, "
            "notes=[Note(context='Context1', message='Message1'), Note(context='Context2', message='Message2')], "
            "blocks={'directory': set(), 'tag': set(), 'timestamp': set()})"
        )

        # Assert that the repr of cb matches the expected string
        self.assertEqual(repr(cb), expected_repr)


if __name__ == "__main__":
    unittest.main()
