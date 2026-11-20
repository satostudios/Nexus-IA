"""File tools: open, search, create, rename, copy, move and delete.

Destructive tools have a *preflight* that resolves the spoken target to one
concrete path before confirmation, so the confirmation shows exactly what will
be touched. Deletion sends items to the Recycle Bin. Protected locations
(drive roots, Windows, Program Files, the user profile root, the standard user
folders themselves) are BLOCKED regardless of confirmation.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from app.tools import targets
from app.tools.applications import _apps, open_app_entry, open_path, open_web
from app.tools.context import services
from app.i18n import tr
from app.tools.file_index import KNOWN_EXTENSIONS
from app.tools.registry import Preflight, Safety, ToolError, fail, nexus_tool, ok

log = logging.getLogger("nexus.tools.files")

_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NEG = r"\b(don'?t|do not|never)\s+"


def _no(*verbs: str) -> str:
    """Negative lookahead for "don't <verb>" right before the verb."""
    return r"^(?![\s\S]*" + _NEG + "(" + "|".join(verbs) + r"))"


def _resolve_or_raise(text: str, kind: str, want_dir: bool | None = None, strict: bool = True) -> Path:
    res = targets.resolve_path(text, want_dir=want_dir, strict=strict)
    if res.path is not None:
        return res.path
    if res.error == "AMBIGUOUS" and res.candidates:
        question, labels = targets.which_question(kind, res.candidates)
        raise ToolError("AMBIGUOUS", question, options=labels, candidates=[str(c) for c in res.candidates[:4]])
    raise ToolError("FILE_NOT_FOUND", tr("I couldn't find {name}.", name=text.strip()))


def _clean_name(name: str) -> str:
    cleaned = name.strip().strip("\"'").strip()
    if not cleaned or _INVALID_NAME.search(cleaned) or cleaned in (".", ".."):
        raise ToolError("INVALID_NAME", tr("{name} isn't a valid name.", name=name.strip() or tr("That")))
    return cleaned


def _kind(path: Path) -> str:
    return "folder" if path.is_dir() else "file"


# --------------------------------------------------------------- opening
@nexus_tool(
    safety=Safety.SAFE,
    confirm="Open {name}?",
    triggers=[r"^(?![\s\S]*\b(delete|remove|erase|rename|copy|move|create|make|search|find|close|quit)\b)"
              r"[\s\S]*\b(open|show|view|edit|play|read)\b[\s\S]*"
              r"(\.(?!(com|org|net|io|dev|app|ai|co|br|tv|me|gg)\b)[a-z0-9]{1,5}\b|\bfile\b(?!\s+explorer)|\bdocument\b|\breadme\b)"],
)
def open_file(file: str):
    """Open a file with its default program.

    Args:
        file: the file name or full path, e.g. README.md or report.pdf
    """
    has_ext = bool(re.search(r"\.[a-z0-9]{1,5}$", file.strip(), re.IGNORECASE))
    if not has_ext:
        app = _apps().resolve(file)
        if app.entry is not None and app.score >= 1.0:
            return open_app_entry(file, app.entry)
    res = targets.resolve_path(file)
    if res.path is not None:
        return open_path(res.path)
    if res.error == "AMBIGUOUS" and res.candidates:
        return targets.ambiguous_paths("file", res.candidates)
    url = targets.as_url(file)
    if url:
        return open_web(url)
    app = _apps().resolve(file)
    if app.entry is not None and app.score >= 0.9:
        return open_app_entry(file, app.entry)
    return fail("FILE_NOT_FOUND", tr("I couldn't find {name}.", name=file.strip()))


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Open {name}?",
    triggers=[r"^(?![\s\S]*\b(delete|remove|erase|rename|copy|move|create|make|search|find|remember|forget|close)\b)"
              r"[\s\S]*\b(open|show|go to|browse|take me to)\b[\s\S]*"
              r"\b(folders?|director(y|ies)|projects?|downloads|documents|desktop|pictures|music|videos|screenshots)\b",
              r"^(?![\s\S]*\b(delete|remove|erase|rename|copy|move|remember)\b)[\s\S]*\b(open|show|go to|browse)\b"
              r"\s+\"?[a-z]:[\\/](?![\s\S]*\.[a-z0-9]{1,5}\"?$)"],
)
def open_folder(folder: str):
    """Open a folder, directory or saved project in File Explorer.

    Args:
        folder: the folder name, saved place or path, e.g. Downloads, my coding project, D:\\Projects
    """
    res = targets.resolve_path(folder, want_dir=True)
    if res.path is None and res.error != "AMBIGUOUS":
        res = targets.resolve_path(folder)  # a saved "project" may be a file
    if res.path is not None:
        return open_path(res.path)
    if res.error == "AMBIGUOUS" and res.candidates:
        return targets.ambiguous_paths("folder", res.candidates)
    return fail("FOLDER_NOT_FOUND", tr("I couldn't find a folder called {name}.", name=folder.strip()))


# --------------------------------------------------------------- search
@nexus_tool(
    safety=Safety.SAFE,
    confirm="Search for {name}?",
    triggers=[r"\b(search|find|look for|locate)\b[\s\S]*\b(files?|documents?|folders?|pdfs?|photos?|pictures?|"
              r"called|named)\b",
              r"\bwhere (is|are)\b|\bwhere's\b"],
)
def search_files(query: str, extension: Optional[str] = None, folder: Optional[str] = None):
    """Search for or find files on this computer by name.

    Args:
        query: part of the file name to look for
        extension: file type to limit results to, e.g. pdf
        folder: folder to search in, e.g. Documents
    """
    index = services.files
    if index is None:
        return fail("SEARCH_UNAVAILABLE", tr("File search isn't ready yet."))
    q = re.sub(r"\b(files?|named|called|documents?)\b", " ", query, flags=re.IGNORECASE).strip()
    ext = extension
    if not ext and q.lower().lstrip(".") in KNOWN_EXTENSIONS:
        ext, q = q, ""
    root: Path | None = None
    if folder:
        root = _resolve_or_raise(folder, "folder", want_dir=True, strict=False)
    if not q and not ext:
        return fail("INVALID_ARGUMENTS", tr("What should I search for?"))
    matches = index.search(q, ext, root, None, limit=10)
    services.session.set_results([m.path for m in matches])
    if not matches:
        kind = tr("{ext} files", ext=ext.lstrip(".")) if ext else tr("files")
        what = f"{kind} {q}" if q else kind
        return fail("NO_RESULTS", tr("I couldn't find any {name}.", name=what))
    top = matches[0]
    results = [{"name": m.path.name, "folder": str(m.path.parent), "path": str(m.path)} for m in matches]
    if len(matches) == 1:
        message = tr("I found {name}.", name=targets.describe(top.path))
    else:
        message = tr("I found {count} matches. The top one is {name}.",
                     count=f"{len(matches)}{'+' if len(matches) == 10 else ''}", name=targets.describe(top.path))
    return ok(message, results=results)


# -------------------------------------------------------------- creating
_ABOUT = re.compile(r"^(?:sobre|about|falando|explicando|contando|com (?:um|uma) (?:texto|resumo))\b|\s(?:sobre|about)\s",
                    re.IGNORECASE)


def _name_repair(args: dict, _request) -> dict:  # noqa: ANN001
    """"Crie uma nota sobre demogorgons" asks NEXUS to *write* something; a file
    name is a short name, not a topic. Composition is unsupported, so drop it."""
    name = str(args.get("name") or "").strip()
    if _ABOUT.search(name) or len(name.split()) > 6:
        return dict(args, name="")
    return args


def _target_folder(folder: str | None) -> Path:
    if not folder:
        return targets.default_create_folder()
    return _resolve_or_raise(folder, "folder", want_dir=True, strict=False)


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Create {name}?",
    triggers=[r"^(?![\s\S]*\b(folder|directory)\b)[\s\S]*\b(create|make|new)\b[\s\S]*"
              r"(\bfile\b|\bdocument\b|\.[a-z0-9]{1,5}\b)"],
)
def create_file(name: str, folder: Optional[str] = None):
    """Create a new empty file.

    Args:
        name: the new file name with extension, e.g. notes.txt
        folder: where to create it, e.g. Desktop
    """
    clean = _clean_name(name)
    if "." not in clean:
        clean += ".txt"
    parent = _target_folder(folder)
    path = parent / clean
    if path.exists():
        return fail("FILE_EXISTS", tr("{name} already exists in {folder}.", name=clean, folder=parent.name or parent))
    path.touch(exist_ok=False)
    services.session.touch(path)
    return ok(tr("{name} created in {folder}.", name=clean, folder=parent.name or parent), path=str(path))


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Create folder {name}?",
    triggers=[r"\b(create|make|new)\b[\s\S]*\b(folder|directory)\b"],
)
def create_folder(name: str, folder: Optional[str] = None):
    """Create a new folder (directory).

    Args:
        name: the new folder name
        folder: where to create it, e.g. Documents
    """
    clean = _clean_name(name)
    parent = _target_folder(folder)
    path = parent / clean
    if path.exists():
        return fail("FOLDER_EXISTS", tr("{name} already exists in {folder}.", name=clean, folder=parent.name or parent))
    path.mkdir(parents=False)
    services.session.touch(path)
    return ok(tr("Folder {name} created in {folder}.", name=clean, folder=parent.name or parent), path=str(path))


create_file.repair(_name_repair)
create_folder.repair(_name_repair)


# --------------------------------------------------------- modifying (confirm)
def _guard(path: Path, verb: str) -> Preflight | None:
    if targets.protected(path):
        return Preflight(arguments={}, error=fail("BLOCKED", tr("I won't {verb} {name}. It's a protected location.", verb=tr(verb), name=path.name or path)),
                         safety=Safety.BLOCKED)
    return None


@nexus_tool(
    safety=Safety.CONFIRMATION_REQUIRED,
    confirm="Rename {name} to {new}?",
    triggers=[_no("rename") + r"[\s\S]*\brename\b"],
)
def rename_file(file: str, new_name: str):
    """Rename a file or folder.

    Args:
        file: the current file or folder name or path
        new_name: the new name
    """
    source = _resolve_or_raise(file, "file")
    target = _rename_target(source, new_name)
    source.rename(target)
    services.session.touch(target)
    return ok(tr("Renamed to {name}.", name=target.name), path=str(target))


def _rename_target(source: Path, new_name: str) -> Path:
    clean = _clean_name(new_name)
    if source.is_file() and source.suffix and not Path(clean).suffix:
        clean += source.suffix
    target = source.with_name(clean)
    if target.exists() and target.resolve() != source.resolve():
        raise ToolError("FILE_EXISTS", tr("{name} already exists there.", name=clean))
    return target


@rename_file.preflight
def _rename_preflight(args: dict) -> Preflight:
    source = _resolve_or_raise(args.get("file", ""), "file")
    blocked = _guard(source, "rename")
    if blocked:
        return blocked
    target = _rename_target(source, args.get("new_name", ""))
    return Preflight(arguments={"file": str(source), "new_name": target.name},
                     title=tr(f"Rename {_kind(source)}?"), detail=f"{source.name}  →  {target.name}\n{source.parent}",
                     spoken=tr("Rename {name} to {new}?", name=source.name, new=target.name))


def _copy_move_prepare(args: dict, verb: str) -> tuple[Path, Path]:
    source = _resolve_or_raise(args.get("file", ""), "file")
    dest_dir = _resolve_or_raise(args.get("destination", ""), "folder", want_dir=True, strict=False)
    if not dest_dir.is_dir():
        raise ToolError("FOLDER_NOT_FOUND", tr("The destination isn't a folder."))
    target = dest_dir / source.name
    if target.exists():
        raise ToolError("FILE_EXISTS", tr("{name} already exists in {folder}.", name=source.name, folder=dest_dir.name))
    if source.is_dir() and (dest_dir == source or dest_dir.is_relative_to(source)):
        raise ToolError("INVALID_DESTINATION", tr("I can't {verb} a folder into itself.", verb=tr(verb)))
    return source, target


@nexus_tool(
    safety=Safety.CONFIRMATION_REQUIRED,
    confirm="Copy {name} to {folder}?",
    triggers=[_no("copy", "duplicate") + r"[\s\S]*\b(copy|duplicate)\b[\s\S]*\b(to|into)\b"],
)
def copy_file(file: str, destination: str):
    """Copy a file or folder to another folder.

    Args:
        file: the file or folder to copy
        destination: the folder to copy it into
    """
    source, target = _copy_move_prepare({"file": file, "destination": destination}, "copy")
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
    services.session.touch(target)
    return ok(tr("Copied to {name}.", name=target.parent.name), path=str(target))


@copy_file.preflight
def _copy_preflight(args: dict) -> Preflight:
    source, target = _copy_move_prepare(args, "copy")
    return Preflight(arguments={"file": str(source), "destination": str(target.parent)},
                     title=tr(f"Copy {_kind(source)}?"), detail=f"{source.name}  →  {target.parent}",
                     spoken=tr("Copy {name} to {folder}?", name=source.name, folder=target.parent.name))


@nexus_tool(
    safety=Safety.CONFIRMATION_REQUIRED,
    confirm="Move {name} to {folder}?",
    triggers=[_no("move") + r"[\s\S]*\bmove\b"],
)
def move_file(file: str, destination: str):
    """Move a file or folder to another folder.

    Args:
        file: the file or folder to move
        destination: the folder to move it into
    """
    source, target = _copy_move_prepare({"file": file, "destination": destination}, "move")
    shutil.move(str(source), str(target))
    services.session.touch(target)
    return ok(tr("Moved to {name}.", name=target.parent.name), path=str(target))


@move_file.preflight
def _move_preflight(args: dict) -> Preflight:
    source, target = _copy_move_prepare(args, "move")
    blocked = _guard(source, "move")
    if blocked:
        return blocked
    return Preflight(arguments={"file": str(source), "destination": str(target.parent)},
                     title=tr(f"Move {_kind(source)}?"), detail=f"{source.name}  →  {target.parent}",
                     spoken=tr("Move {name} to {folder}?", name=source.name, folder=target.parent.name))


@nexus_tool(
    safety=Safety.CONFIRMATION_REQUIRED,
    destructive=True,
    confirm="Delete {name}?",
    triggers=[_no("delete", "remove", "erase") + r"[\s\S]*\b(delete|remove|erase|trash|get rid of)\b"],
)
def delete_file(file: str):
    """Delete a file or folder (it goes to the Recycle Bin).

    Args:
        file: the file or folder name or path to delete
    """
    from send2trash import send2trash

    path = _resolve_or_raise(file, "file")
    if targets.protected(path):
        return fail("BLOCKED", tr("I won't {verb} {name}. It's a protected location.", verb=tr("delete"), name=path.name))
    kind = _kind(path)
    send2trash(str(path))
    if services.session.last_path == path:
        services.session.last_path = None
    return ok(tr(f"{kind.capitalize()} moved to the Recycle Bin."), path=str(path))


@delete_file.preflight
def _delete_preflight(args: dict) -> Preflight:
    path = _resolve_or_raise(args.get("file", ""), "file")
    blocked = _guard(path, "delete")
    if blocked:
        return blocked
    kind = _kind(path)
    return Preflight(arguments={"file": str(path)}, title=tr(f"Delete {kind}?"),
                     detail=f"{path.name}\n{path.parent}",
                     spoken=tr(f"Delete the {kind} {{name}}? Please confirm.", name=path.name))
