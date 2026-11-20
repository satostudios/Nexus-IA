"""Central tool registry.

A NEXUS tool is an ordinary Python function made into a Needle tool with
``@needle.tool`` (signature -> argument schema, docstring -> description) and
registered here with the application-level metadata Needle must not decide:

* ``safety``   SAFE, CONFIRMATION_REQUIRED or BLOCKED;
* ``confirm``  how to phrase a confirmation for the call;
* ``preflight`` optional resolver that turns what the user said ("this folder",
  "report") into concrete arguments *before* confirmation, so the dialog shows
  exactly what will happen.

Adding a tool::

    from app.tools.registry import nexus_tool, ok

    @nexus_tool(triggers=[r"\\bcalculator\\b"])
    def open_calculator():
        \"\"\"Open Windows Calculator.\"\"\"
        ...
        return ok("Calculator opened.")

then import its module in ``app/tools/__init__.py``. Nothing else changes.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import needle

from app.i18n import tr

log = logging.getLogger("nexus.tools")


class Safety(Enum):
    SAFE = "safe"
    CONFIRMATION_REQUIRED = "confirmation_required"
    BLOCKED = "blocked"


# ---------------------------------------------------------------- results
def ok(message: str, **data: Any) -> dict[str, Any]:
    """Successful tool result (the contract every tool returns)."""
    return {"success": True, "message": message, "data": data}


def fail(error: str, message: str, **data: Any) -> dict[str, Any]:
    """Failed tool result. ``error`` is a stable UPPER_SNAKE code."""
    result: dict[str, Any] = {"success": False, "message": message, "error": error}
    if data:
        result["data"] = data
    return result


def ambiguous(question: str, options: list[str], candidates: list[str] | None = None) -> dict[str, Any]:
    """The tool needs the user to choose; ``question`` is asked verbatim.

    ``options`` are the spoken labels, ``candidates`` the concrete values
    (paths or application names) that replace the argument once chosen.
    """
    return fail("AMBIGUOUS", question, options=options[:5], candidates=(candidates or options)[:5])


class ToolError(Exception):
    """Raise inside a tool to return a clean failure result."""

    def __init__(self, error: str, message: str, **data: Any) -> None:
        super().__init__(message)
        self.result = fail(error, message, **data)


# ------------------------------------------------------------- preflight
@dataclass
class Preflight:
    """Outcome of resolving a call's arguments before gating."""
    arguments: dict[str, Any]
    title: str = ""
    detail: str = ""
    spoken: str = ""
    error: dict[str, Any] | None = None     # report this instead of running
    safety: Safety | None = None             # argument-level escalation (e.g. BLOCKED)


# ------------------------------------------------------------------ spec
@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., dict[str, Any]]
    schema: dict[str, Any]
    safety: Safety
    triggers: list[str]
    confirm: str | None
    destructive: bool
    preflight: Callable[[dict[str, Any]], Preflight] | None = None
    # Rebuilds arguments from the user's own words (free text such as a search
    # query or dictation) where Needle's extraction is known to be unreliable.
    repair: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None
    # Creation tools open their own app: "abra o antigravity e escreva um código"
    # becomes one write_code(editor="antigravity") instead of an extra empty window.
    absorbs_open: str | None = None
    # Every argument is rebuilt from the user's words by ``repair``, so the call
    # stays correct even when Needle picked a sibling tool (see pipeline).
    self_contained: bool = False
    _compiled: list[re.Pattern[str]] = field(default_factory=list, repr=False)

    def matches(self, text: str) -> bool:
        return any(p.search(text) for p in self._compiled)

    def describe(self, arguments: dict[str, Any]) -> str:
        """Human confirmation question for this call, e.g. ``Open Chrome?``."""
        from app.i18n import tr

        if self.confirm:
            values = {k: v for k, v in arguments.items() if v is not None}
            first = next(iter(values.values()), "")
            try:
                return tr(self.confirm, **{"name": first, **values})
            except (KeyError, IndexError, ValueError):
                pass
        return tr("Run {name}?", name=self.name.replace("_", " "))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    # ------------------------------------------------------ registration
    def register(self, fn: Callable[..., dict[str, Any]], *, safety: Safety = Safety.SAFE,
                 triggers: list[str] | None = None, confirm: str | None = None,
                 destructive: bool = False) -> ToolSpec:
        schema = getattr(fn, "_needle_tool", None)
        if schema is None:
            fn = needle.tool(fn, triggers=triggers)
            schema = fn._needle_tool  # type: ignore[attr-defined]
        name = schema["name"]
        if name in self._tools:
            raise ValueError(f"tool {name!r} registered twice")
        trig = list(schema.get("triggers") or triggers or [])
        spec = ToolSpec(name=name, fn=fn, schema=schema, safety=safety, triggers=trig,
                        confirm=confirm, destructive=destructive,
                        _compiled=[re.compile(t, re.IGNORECASE) for t in trig])
        self._tools[name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def functions(self) -> list[Callable[..., Any]]:
        """Decorated functions, in registration order, for ``needle.Needle(tools=...)``."""
        return [spec.fn for spec in self._tools.values()]

    def matching(self, text: str) -> set[str]:
        """Tools whose declared trigger phrasings match ``text``."""
        return {spec.name for spec in self._tools.values() if spec.matches(text)}

    # -------------------------------------------------------- validation
    def validate_arguments(self, name: str, arguments: Any) -> str | None:
        """Return a reason string when arguments do not fit the schema."""
        spec = self._tools.get(name)
        if spec is None:
            return f"unknown tool {name}"
        if not isinstance(arguments, dict):
            return "arguments are not an object"
        params = spec.schema.get("parameters", {})
        props: dict[str, Any] = params.get("properties", {})
        for key in params.get("required", []):
            value = arguments.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                return f"missing {key}"
        for key, value in arguments.items():
            prop = props.get(key)
            if prop is None:
                return f"unexpected argument {key}"
            if value is None:
                continue
            kind = prop.get("type")
            if kind == "string" and not isinstance(value, str):
                return f"{key} must be text"
            if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                return f"{key} must be a whole number"
            if kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                return f"{key} must be a number"
            if kind == "boolean" and not isinstance(value, bool):
                return f"{key} must be true or false"
            if "enum" in prop and value not in prop["enum"]:
                return f"{key} must be one of {prop['enum']}"
        return None

    # ---------------------------------------------------------- execution
    def run_preflight(self, name: str, arguments: dict[str, Any]) -> Preflight:
        spec = self._tools[name]
        if spec.preflight is None:
            text = spec.describe(arguments)
            return Preflight(arguments=dict(arguments), title=text, spoken=text)
        try:
            pf = spec.preflight(dict(arguments))
        except ToolError as exc:
            return Preflight(arguments=dict(arguments), error=exc.result)
        except Exception:
            log.exception("preflight for %s failed", name)
            return Preflight(arguments=dict(arguments),
                             error=fail("TOOL_FAILURE", tr("Something went wrong preparing that.")))
        if not pf.title:
            pf.title = spec.describe(pf.arguments)
        if not pf.spoken:
            pf.spoken = pf.title
        return pf

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self._tools.get(name)
        if spec is None:
            return fail("UNKNOWN_TOOL", tr("I can't do that yet."))
        started = time.perf_counter()
        try:
            result = spec.fn(**arguments)
        except ToolError as exc:
            result = exc.result
        except PermissionError:
            log.warning("permission denied in %s", name, exc_info=True)
            result = fail("PERMISSION_DENIED", tr("Windows denied permission for that."))
        except FileNotFoundError:
            log.warning("file not found in %s", name, exc_info=True)
            result = fail("FILE_NOT_FOUND", tr("I couldn't find that."))
        except Exception:
            log.exception("tool %s crashed", name)
            result = fail("TOOL_FAILURE", tr("Something went wrong doing that."))
        result = _normalise(result)
        log.info("tool %s(%s) -> %s %s (%.0f ms)", name, arguments, result.get("success"),
                 result.get("error", ""), (time.perf_counter() - started) * 1000)
        return result


def _normalise(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return ok(str(result) if result is not None else tr("Done."))
    if "success" not in result:
        result = {"success": True, "message": result.get("message", tr("Done.")), "data": result}
    result.setdefault("message", tr("Done.") if result["success"] else tr("That didn't work."))
    return result


REGISTRY = ToolRegistry()


def nexus_tool(*, safety: Safety = Safety.SAFE, triggers: list[str] | None = None,
               confirm: str | None = None, destructive: bool = False, absorbs_open: str | None = None,
               self_contained: bool = False,
               registry: ToolRegistry | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: ``@needle.tool`` + registration with NEXUS safety metadata.

    The returned function gains a ``.preflight`` decorator for an optional
    argument resolver.
    """

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        target = registry or REGISTRY
        decorated = needle.tool(fn, triggers=triggers)
        spec = target.register(decorated, safety=safety, triggers=triggers,
                               confirm=confirm, destructive=destructive)
        spec.absorbs_open = absorbs_open
        spec.self_contained = self_contained

        def set_preflight(pre: Callable[[dict[str, Any]], Preflight]) -> Callable[[dict[str, Any]], Preflight]:
            spec.preflight = pre
            return pre

        def set_repair(rep: Callable[[dict[str, Any], Any], dict[str, Any]]) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
            spec.repair = rep
            return rep

        decorated.preflight = set_preflight  # type: ignore[attr-defined]
        decorated.repair = set_repair  # type: ignore[attr-defined]
        decorated.spec = spec  # type: ignore[attr-defined]
        return decorated

    return wrap
