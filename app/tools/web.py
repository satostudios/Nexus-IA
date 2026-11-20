"""search_web: search Google / YouTube / Wikipedia / Maps in the default browser."""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import Literal, Optional

from app.i18n import is_pt, tr
from app.tools.context import require_settings, services
from app.tools.registry import Safety, fail, nexus_tool, ok

_LEAD = re.compile(
    r"^\s*(?:por favor\s+|please\s+|nexus[,\s]+)?(?:(?:pesquis\w*|busc\w*|procur\w*|googl\w*|search(?:\s+for)?|"
    r"look\s+up|google)\s+)(?:(?:por|sobre|pelo|pela|pra|para|for|about|it:?|isso:?)\s+)?"
    r"(?:(?:no|na|on|in)\s+(?:o\s+|a\s+|the\s+)?(?:google|youtube|internet|web|wikip[eé]dia|maps?|mapa)\s+)?"
    r"(?:(?:por|sobre|pelo|pela|for|about)\s+)?", re.IGNORECASE)
_TAIL = re.compile(r"\s+(?:no|na|on|in|pelo|pela|using|usando)\s+(?:o\s+|a\s+|the\s+)?(?:google|youtube|internet|web|"
                   r"wikip[eé]dia|navegador|browser|google maps|maps?|mapa)\s*[.!?]*$", re.IGNORECASE)
_WEATHER = re.compile(r"\b(tempo|clima|previs\w+|chover|chuva|temperatura|weather|forecast|rain)\b", re.IGNORECASE)

_ENGINES = {
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
}
_SITES = {
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "images": "https://www.google.com/search?tbm=isch&q={q}",
}


def search_url(query: str, site: str | None) -> str:
    q = urllib.parse.quote_plus(query)
    if site == "wikipedia":
        lang = "pt" if is_pt() else "en"
        return f"https://{lang}.wikipedia.org/w/index.php?search={q}"
    if site in _SITES:
        return _SITES[site].format(q=q)
    return _ENGINES.get(require_settings().ai.search_engine, _ENGINES["google"]).format(q=q)


def site_from(text: str) -> str | None:
    low = text.lower()
    if "youtube" in low:
        return "youtube"
    if re.search(r"wikip[eé]dia", low):
        return "wikipedia"
    if re.search(r"\b(maps?|mapa|como chegar|directions to|rota para)\b", low):
        return "maps"
    if re.search(r"\b(imagens|images|fotos de|pictures of)\b", low):
        return "images"
    return None


_VERB = re.compile(r"\b(?:pesquis\w*|busqu\w*|busc\w*|procur\w*|search|look\s+up)\b", re.IGNORECASE)
_GOOGLE_VERB = re.compile(r"\bgoogl\w*\b(?!\s+chrome)", re.IGNORECASE)   # "google it", not "Google Chrome"
_NEXT_CLAUSE = re.compile(r"\s+(?:e depois|e em seguida|e ent[ãa]o|and then|depois)\s+.*$", re.IGNORECASE)


def query_from(original: str) -> str:
    """The words the user wants searched, without the command around them.

    The search verb may come anywhere: "abra o chrome e pesquise o canal Você
    sabia no youtube" -> 'canal Você sabia'.
    """
    text = original.strip()
    m = _VERB.search(text) or _GOOGLE_VERB.search(text)
    if m:
        text = text[m.start():]
    q = _TAIL.sub("", _LEAD.sub("", text, count=1)).strip()
    q = _NEXT_CLAUSE.sub("", q)
    q = _TAIL.sub("", q)
    q = re.sub(r"\s+(?:pra mim|para mim|por favor|for me|please)\s*[.!?]*$", "", q, flags=re.IGNORECASE)
    q = re.sub(r"^(?:o|a|os|as|the)\s+", "", q, flags=re.IGNORECASE)
    return q.replace('"', "").replace("“", "").replace("”", "").strip(" .!,")


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Search for {name}?",
    # "google" alone is also part of "Google Chrome": only "google it / on google" count.
    triggers=[r"\b(search|look up|google (it|for|this|that))\b(?![\s\S]*\b(files?|folders?|documents?|pdfs?|"
              r"on (my|this) computer)\b)",
              r"\bon (google|youtube|the web|wikipedia)\b(?![\s\S]*\b(files?|folders?)\b)",
              r"\b(weather|forecast)\b"],
)
def search_web(query: str, site: Optional[Literal["google", "youtube", "wikipedia", "maps", "images"]] = None):
    """Search the web for something in the browser.

    Args:
        query: what to search for
        site: where to search, only if the user named it
    """
    query = query.strip()
    if not query:
        return fail("INVALID_ARGUMENTS", tr("What should I search for?"))
    url = search_url(query, None if site == "google" else site)
    os.startfile(url)  # type: ignore[attr-defined]
    message = tr("Searching YouTube for {name}.", name=query) if site == "youtube" else \
        tr("Searching for {name}.", name=query)
    return ok(message, url=url, query=query)


@search_web.repair
def _repair_search(args: dict, request) -> dict:  # noqa: ANN001
    original = getattr(request, "original", "") or services.session.request
    spoken = query_from(original)
    site = site_from(original)
    if _WEATHER.search(original) and not re.search(r"\b(pesquis\w*|busc\w*|search|look up)\b", original, re.I):
        spoken = "previsão do tempo" if is_pt() else "weather forecast"
    fixed = dict(args)
    # The user's own words, minus the command around them, are the query; Needle's
    # span is only a fallback (it tends to keep "no youtube" or drop words).
    if spoken:
        fixed["query"] = spoken
    fixed["site"] = site
    return fixed
