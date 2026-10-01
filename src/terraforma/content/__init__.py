"""A game's content: abilities, items, jobs, personalities and monsters.

The game ships them as seed files (see schema.py for the formats, which are
a public interface); the engine checks them (schema.py), then loads them into
the database (loader.py) each time it starts. Each row has a ``key`` (a short
name the game chooses and never reuses) that other rows refer to and that
makes loading repeatable: loading the same seed twice changes nothing.
"""
