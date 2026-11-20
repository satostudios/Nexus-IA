"""Memory tools: remember a place, remember an app nickname, forget a place.

Needle 3 sometimes copies the path into both arguments of ``remember_location``
("My coding project is D:\\Projects\\Nexus" -> name=path) or the application
into the alias. When the arguments are clearly wrong, the tool repairs them from
the user's own words for this command instead of saving nonsense.
"""

from __future__ import annotations

import re

from app.tools import targets
from app.tools.applications import _apps
from app.tools.context import require_memory, services
from app.i18n import join_options, tr
from app.tools.registry import Safety, ambiguous, fail, nexus_tool, ok

_PLACE_IS = re.compile(r"^(?:please\s+|por favor\s+)?(?:(?:remember|lembre(?:-se)?|guarde|memorize)\s+(?:that\s+|que\s+)?)?"
                       r"(?:my\s+|the\s+|our\s+|meu\s+|minha\s+|o\s+|a\s+)?(?P<name>.+?)\s+"
                       r"(?:is|are|lives|é|e|fica(?:\s+em)?|est[áa](?:\s+em)?)\s+(?:at\s+|in\s+|em\s+|no\s+)?"
                       r"\"?(?P<path>[a-z]:[\\/][^\"]*?)\"?\s*\.?$", re.IGNORECASE)
_PLACE_AS = re.compile(r"(?:remember|lembre|guarde|memorize)\s+\"?(?P<path>[a-z]:[\\/].*?)\"?\s+(?:as|como)\s+"
                       r"(?:my\s+|the\s+|meu\s+|minha\s+)?(?P<name>.+?)\s*\.?$", re.IGNORECASE)
_ALIAS = re.compile(r"^(?:please\s+|por favor\s+)?(?:(?:remember|lembre(?:-se)?|guarde)\s+(?:that\s+|que\s+)?)?"
                    r"(?P<alias>[\w' -]+?)\s+(?:means|stands for|is short for|significa|quer dizer)"
                    r"\s+(?:o\s+|a\s+)?(?P<app>.+?)\s*\.?$", re.IGNORECASE)


def _repair_place(name: str, path: str) -> tuple[str, str]:
    """Take name and path from the sentence when Needle's pair is clearly wrong."""
    request = services.session.request
    m = _PLACE_IS.match(request) or _PLACE_AS.search(request)
    if not m:
        return name, path
    spoken_name, spoken_path = m.group("name").strip(), m.group("path").strip()
    wrong = (not name.strip() or not path.strip() or targets.explicit_path(name) is not None
             or name.strip().lower() == path.strip().lower()
             or targets.explicit_path(path) is None
             or path.strip().lower() != spoken_path.lower())
    return (spoken_name, spoken_path) if wrong else (name, path)


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Remember {name} as {path}?",
    triggers=[r"\b(is|are|lives?)\s+(at\s+|in\s+)?\"?[a-z]:[\\/]",
              r"\bremember\b(?![\s\S]*\bmeans\b)(?![\s\S]*\bcalled\b)"],
)
def remember_location(name: str, path: str):
    """Remember a named place on disk, such as a project folder, for later.

    Args:
        name: the name to remember it by, the words before 'is', e.g. coding project
        path: the folder or file path after 'is', e.g. D:\\Projects\\Nexus
    """
    name, path = _repair_place(name, path)
    if targets.explicit_path(name) is not None:
        return fail("INVALID_ARGUMENTS", tr("What should I call that place?"))
    target = targets.explicit_path(path)
    if target is None:
        found = targets.resolve_path(path, strict=True)
        target = found.path
    if target is None or not target.exists():
        return fail("FILE_NOT_FOUND", tr("I can't find {name}.", name=path.strip()))
    key = require_memory().remember_location(name, str(target))
    return ok(tr("Got it. {name} is {path}.", name=key.capitalize(), path=target), name=key, path=str(target))


@remember_location.repair
def _place_repair(args: dict, _request) -> dict:  # noqa: ANN001
    name, path = _repair_place(str(args.get("name") or ""), str(args.get("path") or ""))
    return dict(args, name=name, path=path)


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Remember {alias} as {application}?",
    triggers=[r"\b(means|stands for|nickname|alias)\b", r"\bcall\b[\s\S]*\b(app|application|program)\b"],
)
def remember_app_alias(alias: str, application: str):
    """Remember a nickname for an application.

    Args:
        alias: the new nickname, the word before 'means', e.g. editor
        application: the application it stands for, after 'means', e.g. Visual Studio Code
    """
    if alias.strip().lower() == application.strip().lower():
        m = _ALIAS.match(services.session.request)
        if m:
            alias, application = m.group("alias").strip(), m.group("app").strip()
    res = _apps().resolve(application)
    if res.entry is None:
        return fail("APPLICATION_NOT_FOUND", tr("I couldn't find an app called {name}.", name=application.strip()))
    if alias.strip().lower() in (application.strip().lower(), res.entry.name.lower()):
        return fail("INVALID_ARGUMENTS", tr("What nickname should I use?"))
    require_memory().remember_alias(alias, res.entry.name)
    return ok(tr("Got it. {alias} means {app}.", alias=alias.strip(), app=res.entry.name), alias=alias.strip(), application=res.entry.name)


@remember_app_alias.repair
def _alias_repair(args: dict, _request) -> dict:  # noqa: ANN001
    m = _ALIAS.match(services.session.request)
    if m:
        return dict(args, alias=m.group("alias").strip(), application=m.group("app").strip())
    return args


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Forget {name}?",
    triggers=[r"\bforget\b"],
)
def forget_location(name: str):
    """Forget a remembered place.

    Args:
        name: the remembered name to forget
    """
    removed = require_memory().forget_location(name)
    if not removed:
        return fail("NOT_REMEMBERED", tr("I don't have anything saved as {name}.", name=name.strip()))
    if len(removed) > 1:
        return ambiguous(tr("Which one: {options}?", options=join_options(removed)), removed)
    return ok(tr("Forgot {name}.", name=removed[0]), name=removed[0])
