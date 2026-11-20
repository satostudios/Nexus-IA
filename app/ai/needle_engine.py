"""Needle 3 integration: the AI / tool-calling core of NEXUS.

Uses the public ``cactus-needle`` API only:

* ``needle.Needle(tools=..., system=..., weights=..., tool_index_path=...)``
* ``agent.complete(text)`` for each turn (NEXUS drives the tool loop itself so
  it can gate every call before execution instead of letting ``run()`` execute
  it immediately; this is the documented manual loop),
* ``agent.reset()`` between independent commands,
* ``agent.embed(text)`` for semantic memory lookup.

The engine is a process-wide native library, so every call is confined to one
dedicated worker thread; the UI never waits on it.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import locale
import logging
import os
import platform
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Any, Callable, TypeVar

from app.ai.model_assets import AssetError, ModelAssets
from app.config.settings import Settings
from app.i18n import tr
from app.tools.registry import ToolRegistry

log = logging.getLogger("nexus.needle")
T = TypeVar("T")

_THREAD_NAME = "needle"


def configure_environment(settings: Settings) -> None:
    """Privacy and headless-friendly environment for Needle / Hugging Face."""
    if not settings.system.telemetry:
        os.environ["NEEDLE_TELEMETRY"] = "0"
        os.environ["DO_NOT_TRACK"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _locale_name() -> str:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):  # type: ignore[attr-defined]
            return buf.value
    except Exception:
        pass
    return (locale.getlocale()[0] or "en-US").replace("_", "-")


def _os_name() -> str:
    release = platform.release()
    try:
        if release == "10" and sys.getwindowsversion().build >= 22000:  # type: ignore[attr-defined]
            release = "11"
    except AttributeError:
        pass
    return f"Windows {release}"


def system_facts(settings: Settings, now: datetime.datetime | None = None) -> str:
    """Environment facts for Needle's system turn. Facts only, no instructions.

    The date is given to the day, not the minute: ``needle_init`` re-processes
    the whole static prefix (system facts + 21 tool schemas, about 12 s on a
    laptop CPU) whenever it changes, so the facts must stay stable. The current
    time comes from the get_system_time tool instead.
    """
    now = now or datetime.datetime.now()
    facts = [
        f"date: {now:%Y-%m-%d %a}",
        f"locale: {_locale_name()}",
        f"device: {_os_name()} desktop computer",
    ]
    if settings.system.user_name.strip():
        facts.append(f"user: {settings.system.user_name.strip()}")
    facts.append("assistant: NEXUS")
    return "; ".join(facts)


class EngineNotReady(RuntimeError):
    pass


class NeedleEngine:
    def __init__(self, settings: Settings, registry: ToolRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=_THREAD_NAME)
        self._agent: Any = None
        self._facts = ""
        self._weights: str | None = None
        self.info: dict[str, Any] = {}
        self.ready = threading.Event()

    # ---------------------------------------------------------- threading
    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> "concurrent.futures.Future[T]":
        return self._executor.submit(fn, *args, **kwargs)

    @staticmethod
    def _on_needle_thread() -> bool:
        return threading.current_thread().name.startswith(_THREAD_NAME)

    def _check_thread(self) -> None:
        if not self._on_needle_thread():
            raise RuntimeError("Needle calls must run on the Needle worker thread (use submit())")

    # -------------------------------------------------------------- setup
    def load(self, assets: ModelAssets) -> dict[str, Any]:
        """Verify/fetch assets and create the agent. Runs on the Needle thread."""
        self._check_thread()
        configure_environment(self.settings)
        logging.captureWarnings(True)
        warnings.filterwarnings("default", module="needle")
        started = time.perf_counter()
        engine = assets.needle_engine()
        custom = self.settings.ai.model_path.strip()
        if custom:
            path = Path(custom)
            if not path.is_file() or path.suffix.lower() != ".cact":
                raise AssetError(f"The Needle model {custom} was not found (a .cact file is required).")
            self._weights = str(path)
            weights = path
        else:
            self._weights = None
            weights = assets.needle_weights()
        assets.progress(tr("Starting Needle 3"), None)
        import needle

        self._create_agent()
        self.info = {
            "package": getattr(needle, "__version__", "?"),
            "engine": str(engine),
            "engine_override": os.environ.get("NEEDLE3_LIB_PATH", ""),
            "weights": str(weights),
            "tools": len(self.registry.names()),
            "tool_index": str(self.settings.tool_index_file()),
            "load_seconds": round(time.perf_counter() - started, 2),
        }
        log.info("Needle ready: %s", self.info)
        self.ready.set()
        return self.info

    def _create_agent(self) -> None:
        import needle

        index = self.settings.tool_index_file()
        index.parent.mkdir(parents=True, exist_ok=True)
        facts = system_facts(self.settings)
        if self._agent is not None:
            try:
                self._agent.close()
            except Exception:
                pass
        started = time.perf_counter()
        # tool_index_path is passed so the engine can persist tool embeddings.
        # Verified: the published 3.0.1 Windows engine does not write the file
        # yet, so every launch re-embeds the tools (~12 s, in the background).
        self._agent = needle.Needle(tools=self.registry.functions(), system=facts, weights=self._weights,
                                    tool_index_path=str(index), auto_date=False)
        self._facts = facts
        log.info("Needle agent created in %.1fs (facts: %s)", time.perf_counter() - started, facts)

    def refresh_if_stale(self) -> bool:
        """Rebuild the agent when the facts changed (new day, new user name).
        Call only while idle: it can take ~12 s."""
        self._check_thread()
        if self._agent is not None and system_facts(self.settings) != self._facts:
            self._create_agent()
            return True
        return False

    # -------------------------------------------------------------- turns
    def new_turn(self) -> None:
        """Start an independent command with a fresh conversation."""
        self._check_thread()
        if self._agent is None:
            raise EngineNotReady("Needle is not loaded")
        self._agent.reset()

    def complete(self, text: str, max_new_tokens: int = 512) -> dict[str, Any]:
        self._check_thread()
        if self._agent is None:
            raise EngineNotReady("Needle is not loaded")
        started = time.perf_counter()
        response = self._agent.complete(text, max_new_tokens=max_new_tokens)
        log.info("needle %.0f ms: %r -> type=%s calls=%s held=%s conf=%s", (time.perf_counter() - started) * 1000,
                 text[:120], response.get("type"), response.get("function_calls"),
                 response.get("suppressed_calls"), response.get("confidence"))
        return response

    def embed(self, text: str) -> list[float]:
        self._check_thread()
        if self._agent is None:
            raise EngineNotReady("Needle is not loaded")
        return list(self._agent.embed(text))

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
