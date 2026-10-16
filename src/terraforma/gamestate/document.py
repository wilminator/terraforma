"""The settings document: what is canonical, how it is hashed, and the sections that fill it.

A document is ``{"format": 1, "sections": {name: {...}}}``. Each *section* belongs to one part of the game (the add-ons' component
states, say) and is registered by the game (``Game.state_sections``) or an engine module: it says what an empty section looks
like and what is wrong with a filled one. The engine owns the machinery (draft, check, commit); sections own the meaning.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..db.base import canonical_json

FORMAT = 1
#: The canonical text of a document may not be longer than this (an edit that makes it so is refused).
MAX_BYTES = 1_000_000


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a document, naming the section it is in (None: the document itself)."""

    section: str | None
    message: str

    def view(self) -> dict:
        return {"section": self.section, "message": self.message}


class Section:
    """A part of the settings document. Override ``check`` (and ``default`` when the empty section is not ``{}``).

    A public interface: the license exception covers it, so change it deliberately."""

    name: str = ""

    def default(self) -> dict:
        """The section when nothing has been decided: what the first, live document holds."""
        return {}

    async def apply(self, db, value: dict) -> None:
        """Makes the database match $value, the live value of this section. Called in the transaction of a commit (so a failure takes
        the commit with it) and when the server starts (``service.apply_live``), so it has to give the same result however often it
        runs. Nothing by default: a section the game reads straight from the live document needs no work here."""

    def check(self, value: dict, document: dict) -> list[str]:
        """What is wrong with $value, as sentences an admin can read (none: it is fine). $document is the whole document, for a
        section that depends on another."""
        return []


def sections_by_name(sections: tuple[Section, ...] | list[Section]) -> dict[str, Section]:
    found: dict[str, Section] = {}
    for section in sections:
        if not section.name or section.name in found:
            raise ValueError(f"state sections need distinct, non-empty names (got {section.name!r})")
        found[section.name] = section
    return found


def new_document(sections: dict[str, Section]) -> dict:
    """The document with every registered section at its default."""
    return {"format": FORMAT, "sections": {name: section.default() for name, section in sorted(sections.items())}}


def text_of(document: Any) -> str:
    """The canonical text of $document (the same on every database and every run). Raises ValueError for what JSON can't carry
    exactly (NaN, infinity) or an object whose keys are not text."""
    try:
        json.dumps(document, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"the document is not plain JSON: {error}") from error
    return canonical_json(document)


def hash_of(document: Any) -> str:
    return hashlib.sha256(text_of(document).encode()).hexdigest()


def problems_in(document: Any, sections: dict[str, Section]) -> list[Problem]:
    """Everything wrong with $document, nothing raised: the document's own shape first, then each section's own checks."""
    if not isinstance(document, dict) or set(document) != {"format", "sections"}:
        return [Problem(None, "the document must hold exactly 'format' and 'sections'")]
    problems: list[Problem] = []
    if document["format"] != FORMAT:
        problems.append(Problem(None, f"the document's format is {document['format']!r}; this server reads format {FORMAT}"))
    held = document["sections"]
    if not isinstance(held, dict):
        return problems + [Problem(None, "'sections' must be an object")]
    try:
        size = len(text_of(document).encode())
    except ValueError as error:
        return problems + [Problem(None, str(error))]
    if size > MAX_BYTES:
        problems.append(Problem(None, f"the document is {size} bytes; the most allowed is {MAX_BYTES}"))
    for name in sorted(set(held) - set(sections)):
        problems.append(Problem(name, f"there is no section called {name!r} in this game"))
    for name, section in sorted(sections.items()):
        if name not in held:
            problems.append(Problem(name, "the section is missing"))
        elif not isinstance(held[name], dict):
            problems.append(Problem(name, "a section must be an object"))
        else:
            problems.extend(Problem(name, message) for message in section.check(held[name], document))
    return problems
