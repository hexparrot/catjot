#!/usr/bin/python
import sys
import time
from catjot import Note, SearchType, NoteContext
from os import environ
from pprint import pprint

NOTEFILE = "bellvue.jot"


class ContextBundle(object):
    def __init__(self, tags_dirs_ts):
        """Holds a set of correlated notes, distinguished by:
        Tags, dirs, and timestamps. The NOTEFILE is iterated and any matching
        notes are copied into the `.notes` list.

        This object also can suppress tags/dirs/ts, which leaves the notes
        intact in the context, but inaccessible on iteration of the context.
        This enables wholesale blocking out of memories."""
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
        """Returns a string of `context' and `message' separated by two newlines.
        Iterates through all available visible notes--notes not suppressed."""
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
        """Iterate through visible notes--ones not suppressed"""
        for n in self._visible_notes():
            yield n

    def __add__(self, item):
        """Combines the matching terms of two contexts, effectively combining them.
        Suppressions are not copied from either ContextBundle."""
        import copy

        # Create a deep copy of the current instance
        new_obj = copy.deepcopy(self)

        if isinstance(item, ContextBundle):
            # returned object has values from both a + b
            new_obj.tags.update(item.tags)
            new_obj.dirs.update(item.dirs)
            new_obj.ts.update(item.ts)
        else:
            new_obj += item

        # Reread file on disk and repopulate self.notes list
        new_obj._regen_notes()

        return new_obj

    def __iadd__(self, item):
        """Add 'matching' term to the object via +="""
        # adds notes if not existing
        if isinstance(item, int):
            self.ts.add(item)
        elif item.startswith("/"):
            self.dirs.add(item)
        else:
            self.tags.add(item)

        # Reread file on disk and repopulate self.notes list
        self._regen_notes()

        return self

    def __isub__(self, item):
        """Remove 'matching' term to the object via -="""
        # identifies and removes matching notes
        if isinstance(item, int):
            if item in self.ts:
                self.ts.remove(item)
        elif item.startswith("/"):
            if item in self.dirs:
                self.dirs.remove(item)
        elif item in self.tags:
            self.tags.remove(item)

        # Reread file on disk and repopulate self.notes list
        self._regen_notes()

        return self

    def __sub__(self, item):
        """Either remove a 'matching' term, or if subtracting a ContextBundle:
        Suppress all b's 'matching' terms from obj a."""
        import copy

        # Create a deep copy of the current instance
        new_obj = copy.deepcopy(self)

        if isinstance(item, ContextBundle):
            [new_obj.suppress(t) for t in item.tags]
            [new_obj.suppress(t) for t in item.ts]
            [new_obj.suppress(t) for t in item.dirs]
        else:
            # Identifies and removes matching notes
            new_obj -= item

            # Reread file on disk and repopulate self.notes list
            new_obj._regen_notes()

        return new_obj

    def __len__(self):
        """Return the amount of notes not suppressed."""
        return len(list(self._visible_notes()))

    def _visible_notes(self):
        """Helper function to return the list of notes, impacted by suppression"""
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
        """Reads disk and iterates all notes, adding notes that match on 'matching' terms"""
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
        """Returns a list of all tags present in all available notes, not impacted by suppression"""
        all_tags = set()
        for n in self.notes:
            all_tags.update(n.tag.split())
        return all_tags

    def suppress(self, item):
        """Accepts a 'matching' term and adds it to the blocklist, so it will be hidden from iteration"""
        if isinstance(item, int):
            self.blocks["timestamp"].add(item)
        elif item.startswith("/"):
            self.blocks["directory"].add(item)
        else:
            self.blocks["tag"].add(item)

    def unsuppress(self, item):
        """Reverses suppression--ensures notes matching the terms still are iterable"""
        try:
            if isinstance(item, int):
                self.blocks["timestamp"].remove(item)
            elif item.startswith("/"):
                self.blocks["directory"].remove(item)
            else:
                self.blocks["tag"].remove(item)
        except KeyError:
            pass
