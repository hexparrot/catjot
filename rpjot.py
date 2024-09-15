#!/usr/bin/python
import sys
import time
from catjot import Note, SearchType, NoteContext
from os import environ
from pprint import pprint

NOTEFILE = "bellvue.jot"


class ContextBundle(object):
    def __init__(self, tags_dirs_ts):
        self.tags = set()
        self.dirs = set()
        self.ts = set()
        self.notes = []
        self.blocks = {"directory": set(), "tag": set(), "timestamp": set()}

        if isinstance(tags_dirs_ts, list):
            for t in tags_dirs_ts:
                self += t
        else:
            self += tags_dirs_ts

    def __str__(self):
        combined_str = ""
        for note in self._visible_notes():
            combined_str += (
                note.context.strip() + "\n\n" + note.message.strip() + "\n\n"
            )
        return (
            combined_str.strip()
        )  # Remove the trailing newline from the final concatenation

    def __repr__(self):
        return (
            f"ContextBundle(tags={self.tags}, dirs={self.dirs}, ts={self.ts}, "
            f"notes={self.notes}, blocks={self.blocks})"
        )

    def __iter__(self):
        for n in self._visible_notes():
            yield n

    def __add__(self, item):
        # Create a deep copy of the current instance
        import copy

        new_obj = copy.deepcopy(self)

        # Adds notes if not existing
        if isinstance(item, int):
            if item not in new_obj.ts:
                new_obj.ts.add(item)
        elif isinstance(item, str) and item.startswith("/"):
            if item not in new_obj.dirs:
                new_obj.dirs.add(item)
        elif isinstance(item, str) and item not in new_obj.tags:
            new_obj.tags.add(item)
        elif isinstance(item, ContextBundle):
            new_obj.tags.update(item.tags)
            new_obj.dirs.update(item.dirs)
            new_obj.ts.update(item.ts)

        # Regenerate notes for the new object
        new_obj._regen_notes()

        return new_obj

    def __iadd__(self, item):
        # adds notes if not existing
        if isinstance(item, int):
            if item not in self.ts:
                self.ts.add(item)
        elif item.startswith("/"):
            if item not in self.dirs:
                self.dirs.add(item)
        elif item not in self.tags:
            self.tags.add(item)

        self._regen_notes()

        return self

    def __isub__(self, item):
        # identifies and removes matching notes
        if isinstance(item, int):
            if item in self.ts:
                self.ts.remove(item)
        elif item.startswith("/"):
            if item in self.dirs:
                self.dirs.remove(item)
        elif item in self.tags:
            self.tags.remove(item)

        self._regen_notes()

        return self

    def __sub__(self, item):
        # Create a deep copy of the current instance
        import copy

        new_obj = copy.deepcopy(self)

        # Identifies and removes matching notes
        new_obj -= item

        # Regenerate notes for the new object
        new_obj._regen_notes()

        return new_obj

    def __len__(self):
        return len(list(self._visible_notes()))

    def _visible_notes(self):
        seen = []

        def iterate_notes(search_type, values):
            for value in values:
                for n in self.notes:
                    # Check if the note is not blocked by tags, directory, or timestamp
                    if (
                        set(n.tag.split()).isdisjoint(self.blocks["tag"])
                        and n.pwd not in self.blocks["directory"]
                        and n.now not in self.blocks["timestamp"]
                        and n not in seen
                    ):
                        seen.append(n)
                        yield n

        # Iterate over notes for tags, timestamps, and directories
        yield from iterate_notes(SearchType.TAG, self.tags)
        yield from iterate_notes(SearchType.TIMESTAMP, self.ts)
        yield from iterate_notes(SearchType.DIRECTORY, self.dirs)

    def _regen_notes(self):
        self.notes = []

        def add_notes(search_type, values):
            for value in values:
                with NoteContext(NOTEFILE, (search_type, value)) as notes:
                    for n in notes:
                        if n not in self.notes:
                            self.notes.append(n)

        # Regenerate notes based on tags, directories, and timestamps
        add_notes(SearchType.TAG, self.tags)
        add_notes(SearchType.DIRECTORY, self.dirs)
        add_notes(SearchType.TIMESTAMP, self.ts)

    @property
    def active_tags(self):
        all_tags = set()
        for n in self.notes:
            all_tags.update(n.tag.split())
        return all_tags

    def suppress(self, item):
        if isinstance(item, int):
            self.blocks["timestamp"].add(item)
        elif item.startswith("/"):
            self.blocks["directory"].add(item)
        else:
            self.blocks["tag"].add(item)

    def unsuppress(self, item):
        try:
            if isinstance(item, int):
                self.blocks["timestamp"].remove(item)
            elif item.startswith("/"):
                self.blocks["directory"].remove(item)
            else:
                self.blocks["tag"].remove(item)
        except KeyError:
            pass
