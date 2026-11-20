"""The command pipeline: text -> Needle -> gated tool calls -> result.

Runs entirely on the Needle worker thread. Text and voice both end up here, so
there is exactly one path from a request to an action.

For every call Needle proposes the pipeline

1. validates the arguments against the tool schema,
2. drops calls outside the request's declared command surface (tool triggers)
   and calls the engine flagged as ungrounded or negated,
3. resolves the target (preflight) so confirmations show what will happen,
4. asks :class:`ConfidencePolicy` whether to execute, confirm, refuse or block,
5. executes, then feeds the structured results back to Needle so multi-step
   requests ("open my project and then open README.md") continue in Needle's
   own tool loop.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from app.ai.confidence import ConfidencePolicy, Route
from app.ai.language import Normalized, normalize
from app.ai.memory import LocalMemory
from app.config.settings import Settings
from app.core.events import ConfirmationRequest, OutcomeKind, ToolCallRecord, TurnOutcome
from app.tools.context import services
from app.i18n import tr
from app.tools.registry import Preflight, Safety, ToolRegistry, ToolSpec

log = logging.getLogger("nexus.pipeline")

UNSUPPORTED_MESSAGE = "I can't do that yet."
LOW_CONFIDENCE_MESSAGE = "I'm not sure what you mean."

_YES = re.compile(r"^\s*(?:(?:yes|yeah|yep|yup|sure|confirm(?:ed)?|do it|go ahead|ok(?:ay)?|proceed|affirmative|"
                  r"correct|please do|yes please|sim|s[ií]m? senhor|pode|pode sim|pode ir|confirma(?:do|r)?|confirmo|"
                  r"claro|isso|isso mesmo|manda|manda ver|vai|beleza|positivo|certo|com certeza|fa[çc]a|faz|"
                  r"fechado|t[áa] bom|ta|ok pode)[\s,.!]*)+$", re.IGNORECASE)
_NO = re.compile(r"^\s*(?:(?:no|nope|cancel|stop|don'?t|do not|never ?mind|abort|negative|no thanks|n[ãa]o|"
                 r"cancela(?:r)?|deixa(?: pra l[áa])?|esquece|negativo|para|pare|nem|n[ãa]o precisa|melhor n[ãa]o)"
                 r"[\s,.!]*)+$", re.IGNORECASE)
_VERB_ONLY = {"open", "launch", "start", "run", "close", "quit", "delete", "remove", "find", "search", "move",
              "copy", "rename", "create", "make", "abra", "abre", "abrir", "feche", "fecha", "fechar", "apague",
              "apagar", "exclua", "procure", "pesquise", "mova", "copie", "crie", "digite", "escreva"}
_ORDINAL = {"first": 0, "1": 0, "one": 0, "1st": 0, "second": 1, "2": 1, "two": 1, "2nd": 1, "third": 2,
            "3": 2, "three": 2, "3rd": 2, "fourth": 3, "4": 3, "four": 3, "4th": 3, "last": -1,
            "primeiro": 0, "primeira": 0, "um": 0, "segundo": 1, "segunda": 1, "dois": 1, "terceiro": 2,
            "terceira": 2, "tres": 2, "quarto": 3, "quarta": 3, "ultimo": -1, "ultima": -1}
_PATH = re.compile(r"[A-Za-z]:\\[^\s\"'<>|]*")
_FILLER_WORDS = {"the", "one", "in", "on", "a", "my", "that", "folder", "file", "o", "a", "da", "do", "de", "na",
                 "no", "em", "que", "um", "uma", "pasta", "arquivo", "esse", "essa", "este", "esta"}


def parse_yes_no(text: str) -> bool | None:
    if _YES.match(text):
        return True
    if _NO.match(text):
        return False
    return None


class Engine(Protocol):
    def new_turn(self) -> None: ...
    def complete(self, text: str, max_new_tokens: int = 512) -> dict[str, Any]: ...


@dataclass
class _Turn:
    text: str
    covered: set[str]
    norm: Normalized | None = None
    followup: bool = False
    step: int = 0
    records: list[ToolCallRecord] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    successes: int = 0
    failures: int = 0
    executed: set[str] = field(default_factory=set)
    seen: set[str] = field(default_factory=set)
    queue: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    confidence: float | None = None
    suppressed: bool = False
    ungrounded: set[str] = field(default_factory=set)
    clarify: str | None = None
    started: float = field(default_factory=time.perf_counter)
    needle_ms: float = 0.0
    tools_ms: float = 0.0


@dataclass
class _Pending:
    turn: _Turn
    spec: ToolSpec
    call: dict[str, Any]
    preflight: Preflight
    record: ToolCallRecord


@dataclass
class _Clarification:
    turn: _Turn
    call: dict[str, Any]
    options: list[str]
    candidates: list[Any]


class CommandProcessor:
    def __init__(self, engine: Engine, registry: ToolRegistry, settings: Settings,
                 memory: LocalMemory | None = None,
                 policy: Callable[[], ConfidencePolicy] | None = None, dry_run: bool = False) -> None:
        self.engine = engine
        self.registry = registry
        self.settings = settings
        self.memory = memory
        self.dry_run = dry_run   # route and repair only; never preflight or execute (scripts/probe_routing.py)
        self._policy = policy or (lambda: ConfidencePolicy(settings.ai.execute_threshold,
                                                           settings.ai.refuse_threshold))
        self.pending: _Pending | None = None
        self.clarification: _Clarification | None = None

    # ================================================================ API
    @property
    def has_pending(self) -> bool:
        return self.pending is not None

    def handle(self, text: str) -> TurnOutcome:
        """Process one user request. Must run on the Needle thread."""
        text = text.strip()
        if self.pending is not None:
            decision = parse_yes_no(text)
            if decision is not None:
                return self.resolve_pending(decision)
            self.pending = None  # a new request replaces the unanswered confirmation
        if self.clarification is not None:
            clar, self.clarification = self.clarification, None
            chosen = self._match_clarification(text, clar)
            if chosen is not None:
                return self._retry_with(clar, chosen)
            if parse_yes_no(text) is False:
                return TurnOutcome(OutcomeKind.CANCELLED, tr("Okay."), success=False)

        # Portuguese: Needle reads an English command skeleton; arguments are
        # mapped back onto the user's own words (see app/ai/language.py).
        norm = normalize(text)
        turn = _Turn(text=text, covered=self.registry.matching(norm.text), norm=norm)
        services.session.request = text
        services.session.normalized = norm
        try:
            self.engine.new_turn()
            response = self._complete(turn, norm.text)
        except Exception:
            log.exception("Needle failed on %r", text)
            return TurnOutcome(OutcomeKind.ERROR, tr("My AI core had a problem. Please try again."))
        if not self._load(turn, response):
            return self._finish(turn)
        return self._loop(turn)

    def resolve_pending(self, approved: bool) -> TurnOutcome:
        pending, self.pending = self.pending, None
        if pending is None:
            return TurnOutcome(OutcomeKind.CANCELLED, tr("There's nothing to confirm."))
        turn = pending.turn
        if not approved:
            pending.record.route = "cancelled"
            turn.records.append(pending.record)
            return self._finish(turn, override=(OutcomeKind.CANCELLED, tr("Cancelled.")))
        pending.record.route = "confirmed"
        self._execute(turn, pending.spec, pending.call, pending.preflight, pending.record)
        return self._loop(turn)

    def cancel_pending(self) -> None:
        self.pending = None
        self.clarification = None

    # ============================================================ engine
    def _complete(self, turn: _Turn, text: str, max_new_tokens: int = 512) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return self.engine.complete(text, max_new_tokens=max_new_tokens)
        finally:
            turn.needle_ms += (time.perf_counter() - started) * 1000

    def _load(self, turn: _Turn, response: dict[str, Any]) -> bool:
        """Queue the calls from a Needle response. False when there is nothing to do."""
        if response.get("error"):
            log.warning("engine reported error: %s (%s)", response.get("error"), response.get("error_code"))
        calls = [c for c in (response.get("function_calls") or []) if isinstance(c, dict)]
        held = [c for c in (response.get("suppressed_calls") or []) if isinstance(c, dict)]
        if response.get("type") != "call" or (not calls and not held):
            return False
        # The engine flags requests that negate the tool's verb; never act on
        # those without asking, whatever the confidence.
        turn.suppressed = not calls or bool((response.get("validation") or {}).get("negation"))
        conf = response.get("confidence")
        turn.confidence = float(conf) if isinstance(conf, (int, float)) else None
        turn.ungrounded = {str(n).split(".", 1)[0] for n in ((response.get("validation") or {}).get("ungrounded") or [])}
        turn.queue = self._settle_creation(turn, self._settle_dictation(turn, calls or held))
        turn.results = []
        return True

    def _settle_dictation(self, turn: _Turn, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """ "open notepad and type hello world": with 30 tools, Needle sometimes
        answers open_application("hello world"). An app name that is exactly the
        dictated text (the words after "type"/"digite") is dictation, not an app."""
        if "type_text" not in turn.covered or turn.followup:
            return calls
        from app.ai.language import fold
        from app.tools.input_control import dictated_text

        dictated = fold(dictated_text(turn.text)).strip(" .!?")
        if not dictated:
            return calls
        out = []
        for call in calls:
            app = (call.get("arguments") or {}).get("application") if call.get("name") == "open_application" else None
            if isinstance(app, str) and app.strip() and fold(app).strip(" .!?") == dictated:
                log.info("open_application(%r) is the dictated text: type_text", app)
                call = {"name": "type_text", "arguments": {"text": app}}
            out.append(call)
        return out

    def _settle_creation(self, turn: _Turn, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Creation requests ("escreva um poema", "crie um app"), before anything runs:

        * when the request matches exactly one self-contained creation tool,
          that tool runs even if Needle proposed another one: Needle 3 was
          trained on commands, not on telling dictation from composition, and
          answers "write a poem" with type_text, "open vs code and write a
          snake game" with open_application("Vocal"), "...renames my photos by
          date" with get_system_time. The triggers (app/tools/intents.py) are
          what tell them apart, and the creation tools rebuild every argument
          from the user's words, so nothing of the wrong guess is kept;
        * "abra o antigravity e escreva um código" asks for one thing: the
          open_application call is folded into write_code(editor=...) instead
          of opening an empty extra window;
        * other tools the free text happens to mention ("...renames my photos
          by date" -> get_system_time) are dropped: it is one request.
        """
        if not calls or turn.followup or not self.settings.ai.require_trigger_coverage:
            return calls
        creators = [n for n in turn.covered if (s := self.registry.get(n)) is not None and s.self_contained]
        out: list[dict[str, Any]] = []
        if len(creators) == 1 and not any(c.get("name") == creators[0] for c in calls):
            # The request matches exactly one creation tool but Needle proposed
            # something else: open_application("Vocal") for "open vs code and
            # write a snake game", get_system_time for "...renames my photos by
            # date". The creation tool is what was asked for; the rest was
            # triggered by words inside the description.
            log.info("rerouted %s -> %s (request matches only %s)",
                     [c.get("name") for c in calls], creators[0], creators[0])
            out.append({"name": creators[0], "arguments": {}})
        for call in calls:
            name = str(call.get("name") or "")
            if name and name not in turn.covered and len(creators) == 1 and \
                    not any(c.get("name") == creators[0] for c in out + calls):
                log.info("rerouted %s -> %s (request matches only %s)", name, creators[0], creators[0])
                call = {"name": creators[0], "arguments": {}}
            out.append(call)
        creating = [c for c in out if (s := self.registry.get(str(c.get("name")))) is not None and s.self_contained]
        if not creating:
            return out
        absorber = next((c for c in creating
                         if (s := self.registry.get(str(c.get("name")))) is not None and s.absorbs_open), None)
        settled: list[dict[str, Any]] = []
        for call in out:
            name = str(call.get("name") or "")
            spec = self.registry.get(name)
            args = dict(call.get("arguments") or {})
            key = name + json.dumps(args, sort_keys=True, default=str)
            if spec is None or spec.self_contained or (name == "open_application" and absorber is None):
                settled.append(call)
                continue
            if name == "open_application":
                field_name = self.registry.get(str(absorber.get("name"))).absorbs_open  # type: ignore[union-attr]
                target_args = absorber.setdefault("arguments", {})
                if isinstance(target_args, dict) and args.get("application") and not target_args.get(field_name):
                    target_args[field_name] = args["application"]
                route = f"absorbed into {absorber.get('name')}"
            else:
                # "renames my photos by date" is part of what to write, not a
                # request for the time: a creation request is one action.
                route = f"dropped: part of {creating[0].get('name')}"
            # counts as done, so Needle's loop doesn't bring it back afterwards
            turn.executed.add(key)
            turn.records.append(ToolCallRecord(name=name, arguments=args, confidence=None, route=route,
                                               suppressed=False))
        return settled

    # ============================================================== loop
    def _loop(self, turn: _Turn) -> TurnOutcome:
        max_steps = self.settings.ai.max_steps
        while True:
            paused = self._run_queue(turn)
            if paused is not None:
                return paused
            if turn.clarify:
                self._feed_back(turn)
                return self._finish(turn, override=(OutcomeKind.CLARIFY, turn.clarify))
            if not turn.results:
                return self._finish(turn)
            if not self._expects_more(turn):
                # Every tool the request mentions has run: Needle's next turn
                # would only be "respond", so skip that engine round trip.
                self._feed_back(turn, discard=True)
                return self._finish(turn)
            turn.step += 1
            if turn.step >= max_steps:
                log.info("max steps reached")
                return self._finish(turn)
            response = self._feed_back(turn)
            if response is None or not self._load(turn, response):
                return self._finish(turn)

    def _feed_back(self, turn: _Turn, discard: bool = False) -> dict[str, Any] | None:
        """Send the batch's results to Needle (its tool loop). ``discard`` skips the
        round trip when the conversation will not continue."""
        if not turn.results:
            return None
        payload = json.dumps([_compact(r) for r in turn.results], default=str)
        turn.results = []
        if discard:
            return None
        try:
            # A continuation is a short call or "respond": cap decoding (measured
            # 3-4.6 s for an uncapped feed-back turn in real use).
            return self._complete(turn, payload, max_new_tokens=160)
        except Exception:
            log.exception("feeding results back to Needle failed")
            return None

    def _expects_more(self, turn: _Turn) -> bool:
        """Could Needle still call another tool the request covers?"""
        if turn.followup or not self.settings.ai.require_trigger_coverage:
            return True
        executed = {key.split("{", 1)[0] for key in turn.executed}
        if any((s := self.registry.get(n)) is not None and s.self_contained for n in executed):
            return False   # a creation request is complete once its tool has run
        return bool(turn.covered - executed) and turn.failures == 0

    def _run_queue(self, turn: _Turn) -> TurnOutcome | None:
        policy = self._policy()
        strict_surface = self.settings.ai.require_trigger_coverage and not turn.followup
        while turn.queue:
            call = turn.queue.pop(0)
            name = str(call.get("name") or "")
            args = call.get("arguments") or {}
            record = ToolCallRecord(name=name, arguments=dict(args) if isinstance(args, dict) else {},
                                    confidence=turn.confidence, route="", suppressed=turn.suppressed)
            spec = self.registry.get(name)
            if spec is None:
                record.route = "unknown-tool"
                turn.records.append(record)
                continue
            raw_args = args
            args = self._repair(spec, args, turn)
            repaired = args != raw_args
            record.arguments = dict(args)
            # Needle sometimes emits the same action several times (e.g. dictation
            # split into fragments that repair turns into one sentence): run it once.
            key = name + json.dumps(args, sort_keys=True, default=str)
            if key in turn.executed or key in turn.seen:
                continue
            turn.seen.add(key)
            call = {"name": name, "arguments": args}
            problem = self.registry.validate_arguments(name, args) or _junk_arguments(args)
            if problem:
                record.route = f"invalid: {problem}"
                turn.records.append(record)
                continue
            if strict_surface and name not in turn.covered:
                # Needle proposed a tool whose declared phrasings do not match the
                # request. Measured on this toolset, such calls are almost always
                # off-topic requests forced onto the nearest tool.
                record.route = "unsupported: outside command surface"
                turn.records.append(record)
                continue
            if name in turn.ungrounded and not repaired:
                # (a repaired call was rebuilt from the user's own words, so the
                # engine's grounding flag on its original arguments no longer applies)
                record.route = "ungrounded"
                turn.records.append(record)
                continue
            if self.dry_run:
                record.route = "dry-run: " + policy.decide(spec.safety, turn.confidence, turn.suppressed).route.value
                turn.records.append(record)
                turn.messages.append(name)
                continue
            pf = self.registry.run_preflight(name, args)
            if pf.error is not None:
                record.route = "resolved-with-error"
                record.result = pf.error
                turn.records.append(record)
                self._absorb(turn, pf.error, call)
                continue
            decision = policy.decide(spec.safety, turn.confidence, turn.suppressed, pf.safety)
            record.route = f"{decision.route.value}: {decision.reason}"
            if decision.route is Route.BLOCK:
                result = pf.error or {"success": False, "error": "BLOCKED", "message": tr("That action isn't allowed.")}
                record.result = result
                turn.records.append(record)
                self._absorb(turn, result, call)
                continue
            if decision.route is Route.REFUSE:
                turn.records.append(record)
                if not turn.successes:
                    return self._finish(turn, override=(OutcomeKind.REFUSED, tr(LOW_CONFIDENCE_MESSAGE)))
                continue
            if decision.route is Route.CONFIRM:
                self.pending = _Pending(turn=turn, spec=spec, call=call, preflight=pf, record=record)
                destructive = spec.destructive or spec.safety is Safety.CONFIRMATION_REQUIRED
                title = pf.title if destructive or not turn.suppressed else \
                    tr("Did you mean: {name}", name=pf.title[0].lower() + pf.title[1:])
                request = ConfirmationRequest(title=title, detail=pf.detail, destructive=destructive,
                                              spoken=pf.spoken if destructive else title, tool=name)
                return TurnOutcome(OutcomeKind.CONFIRM, request.spoken, records=turn.records + [record],
                                   confirmation=request, timings=self._timings(turn),
                                   needle_input=turn.norm.text if turn.norm is not None else turn.text)
            self._execute(turn, spec, call, pf, record)
        return None

    def _execute(self, turn: _Turn, spec: ToolSpec, call: dict[str, Any], pf: Preflight,
                 record: ToolCallRecord) -> None:
        started = time.perf_counter()
        result = self.registry.execute(spec.name, pf.arguments)
        elapsed = (time.perf_counter() - started) * 1000
        turn.tools_ms += elapsed
        record.result = result
        record.duration_ms = elapsed
        turn.records.append(record)
        turn.executed.add(spec.name + json.dumps(call.get("arguments") or {}, sort_keys=True, default=str))
        if result.get("success") and self.memory is not None:
            try:
                self.memory.record_command(turn.text, spec.name, pf.arguments)
            except OSError:
                log.warning("could not record command", exc_info=True)
        self._absorb(turn, result, call)

    def _absorb(self, turn: _Turn, result: dict[str, Any], call: dict[str, Any]) -> None:
        turn.results.append(result)
        message = str(result.get("message") or "").strip()
        if result.get("success"):
            turn.successes += 1
            data = result.get("data") or {}
            for item in (data.get("results") or [])[:5] if isinstance(data, dict) else []:
                if isinstance(item, dict) and item.get("path"):
                    turn.extra.append(item["path"])
        else:
            turn.failures += 1
        if result.get("error") == "AMBIGUOUS":
            data = result.get("data") or {}
            options = [str(o) for o in (data.get("options") or [])]
            candidates = [str(c) for c in (data.get("candidates") or options)]
            if len(candidates) != len(options):
                candidates = options
            self.clarification = _Clarification(turn=turn, call=call, options=options, candidates=candidates)
            turn.clarify = message
            return
        if message and message not in turn.messages:
            turn.messages.append(message)

    # ===================================================== clarification
    def _match_clarification(self, reply: str, clar: _Clarification) -> Any | None:
        from app.ai.language import fold

        text = fold(reply).strip().strip(".!?")
        words = re.findall(r"[a-z0-9]+", text)
        for w in words:
            if w in _ORDINAL and len(words) <= 5:
                idx = _ORDINAL[w]
                try:
                    return clar.candidates[idx]
                except IndexError:
                    return None
        best, best_score, second = None, 0.0, 0.0
        for option, cand in zip(clar.options, clar.candidates):
            hay = fold(f"{option} {cand}")
            tokens = set(re.findall(r"[a-z0-9]+", hay))
            reply_tokens = {w for w in words if w not in _FILLER_WORDS}
            if not reply_tokens:
                continue
            overlap = len(reply_tokens & tokens) / len(reply_tokens)
            ratio = difflib.SequenceMatcher(None, text, fold(option)).ratio()
            score = max(overlap, ratio)
            if score > best_score:
                best, best_score, second = cand, score, best_score
            elif score > second:
                second = score
        if best is not None and best_score >= 0.6 and best_score - second >= 0.2:
            return best
        return None

    def _retry_with(self, clar: _Clarification, chosen: Any) -> TurnOutcome:
        turn = clar.turn
        turn.clarify = None
        turn.followup = True
        args = dict(clar.call.get("arguments") or {})
        value = str(chosen)
        if args:
            label = Path(value).name.lower() if Path(value).is_absolute() else value.lower()
            key = max(args, key=lambda k: difflib.SequenceMatcher(None, str(args[k]).lower(), label).ratio())
            args[key] = value
        turn.queue = [{"name": clar.call.get("name"), "arguments": args}]
        return self._loop(turn)

    # ============================================================ finish
    def _finish(self, turn: _Turn, override: tuple[OutcomeKind, str] | None = None) -> TurnOutcome:
        timings = self._timings(turn)
        seen = turn.norm.text if turn.norm is not None else turn.text
        if override is not None:
            kind, message = override
            return TurnOutcome(kind, message, success=turn.successes > 0 and kind is not OutcomeKind.CANCELLED,
                               records=turn.records, extra_lines=turn.extra,
                               awaiting_followup=kind is OutcomeKind.CLARIFY, timings=timings, needle_input=seen)
        if not turn.messages:
            return TurnOutcome(OutcomeKind.UNSUPPORTED, tr(UNSUPPORTED_MESSAGE), records=turn.records, timings=timings,
                               needle_input=seen)
        message = " ".join(turn.messages)
        return TurnOutcome(OutcomeKind.DONE, message, success=turn.successes > 0 and turn.failures == 0,
                           records=turn.records, extra_lines=turn.extra, timings=timings, needle_input=seen)

    @staticmethod
    def _repair(spec: ToolSpec, args: dict[str, Any], turn: _Turn) -> dict[str, Any]:
        """Map Needle's arguments back onto the user's words, then apply the tool's repair."""
        norm = turn.norm
        fixed = dict(args) if isinstance(args, dict) else {}
        if norm is not None and norm.portuguese:
            fixed = {k: norm.to_original(v) if isinstance(v, str) else v for k, v in fixed.items()}
        # Needle sometimes drops the last component of a long path; if what it
        # returned is the start of a path the user typed, use the whole path.
        spoken_paths = _PATH.findall(turn.text)
        for k, v in list(fixed.items()):
            if isinstance(v, str) and _PATH.fullmatch(v.strip()):
                longer = [p.rstrip(".,;") for p in spoken_paths
                          if p.lower().startswith(v.strip().lower()) and len(p) > len(v.strip())]
                if longer:
                    fixed[k] = max(longer, key=len)
        if spec.repair is not None and norm is not None:
            try:
                fixed = spec.repair(fixed, norm)
            except Exception:
                log.exception("argument repair for %s failed", spec.name)
        if fixed != args:
            log.info("arguments %s -> %s", args, fixed)
        return fixed

    @staticmethod
    def _timings(turn: _Turn) -> dict[str, float]:
        return {"needle_ms": round(turn.needle_ms), "tools_ms": round(turn.tools_ms),
                "total_ms": round((time.perf_counter() - turn.started) * 1000), "steps": turn.step + 1}


def _junk_arguments(args: dict[str, Any]) -> str | None:
    for key, value in args.items():
        if isinstance(value, str) and value.strip().lower().strip(".") in _VERB_ONLY:
            return f"{key} is just a verb"
    return None


def _compact(result: dict[str, Any]) -> dict[str, Any]:
    """Short version of a tool result for Needle's context window."""
    out = {k: result[k] for k in ("success", "message", "error") if k in result}
    data = result.get("data")
    if isinstance(data, dict) and data:
        small: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(v, list):
                small[k] = v[:3]
            elif isinstance(v, str):
                small[k] = v[:160]
            else:
                small[k] = v
        out["data"] = small
    return out
