"""Moving the planned state between servers: export it to a file, import a file into the draft.

An export is ``{"export_format": 1, "kind": "planned-state", "source": "planned"|"live", "hash": ..., "modules": [...], "document": {...}}``.
``modules`` lists the add-ons the exporting server had installed, in layer order; ``hash`` is the hash of ``document``. An import
checks the file before anything is touched, and only replaces the *draft* (frozen: it is exactly what was imported); live changes
only when that draft is committed. Player saves are never part of it.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from . import document as documents
from . import service
from .document import Problem, Section

EXPORT_FORMAT = 1
KIND = "planned-state"
SOURCES = ("planned", "live")
KEYS = {"export_format", "kind", "source", "hash", "modules", "document"}


async def export(db: AsyncSession, sections: dict[str, Section], modules: list[str], source: str = "planned") -> dict:
    """The file for $source (the draft, or the live document), with the server's installed $modules."""
    if source not in SOURCES:
        raise service.StateError(f"the source must be one of {', '.join(SOURCES)}")
    current = await service.state(db, sections)
    document = current.planned.document if source == "planned" else current.live.document
    return {"export_format": EXPORT_FORMAT, "kind": KIND, "source": source, "hash": documents.hash_of(document), "modules": list(modules),
            "document": document}


def problems_in_file(data: object, modules: list[str]) -> list[Problem]:
    """What stops $data from being imported by a server with $modules installed (before the document itself is checked)."""
    if not isinstance(data, dict) or set(data) != KEYS:
        return [Problem(None, f"an export file holds exactly: {', '.join(sorted(KEYS))}")]
    problems: list[Problem] = []
    if data["export_format"] != EXPORT_FORMAT or data["kind"] != KIND:
        problems.append(Problem(None, f"this is not a format {EXPORT_FORMAT} {KIND} export"))
    if data["source"] not in SOURCES:
        problems.append(Problem(None, f"the source must be one of {', '.join(SOURCES)}"))
    if not isinstance(data["modules"], list) or not all(isinstance(each, str) for each in data["modules"]):
        problems.append(Problem(None, "'modules' must be a list of add-on keys"))
    elif data["modules"] != modules:
        problems.append(Problem(None, f"the export was made with the add-ons [{', '.join(data['modules'])}] and this server has "
                                      f"[{', '.join(modules)}]; install the same add-ons, in the same order, first"))
    try:
        if data["hash"] != documents.hash_of(data["document"]):
            problems.append(Problem(None, "the document does not match the file's hash (the file was changed or damaged)"))
    except ValueError as error:
        problems.append(Problem(None, str(error)))
    return problems


async def import_file(db: AsyncSession, sections: dict[str, Section], modules: list[str], data: object, account_id: int | None) -> service.State:
    """Replaces the draft with the file's document, frozen. Refuses (Refused, naming every problem, changing nothing) a file that is
    not a good export, was made with other add-ons, or whose document does not check out here. Live is never touched."""
    found = problems_in_file(data, modules)
    if found:
        raise service.Refused(f"that file can't be imported ({len(found)} problem{'s' if len(found) != 1 else ''}); nothing was changed", found)
    return await service.freeze(db, sections, data["document"], account_id)
