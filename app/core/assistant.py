"""Assistant orchestrator: UNDERSTAND -> ACT -> REPORT.

Lives on the UI thread and coordinates everything else:

* start-up (audio, Needle, tools, models, TTS) on background threads;
* text commands and voice transcripts, which converge on the same
  :class:`CommandProcessor` running on the Needle thread;
* conversation (voice) mode: listen -> answer -> listen again, until the
  user says "parar"/"stop" or clicks the microphone;
* confirmation, clarification and cancellation;
* speech output and state changes for the NEXUS Core;
* history and debug metadata.

The UI only calls the public slots here and listens to the signals.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app import paths
from app.ai.language import fold
from app.ai.memory import LocalMemory
from app.ai.model_assets import AssetError, ModelAssets
from app.ai.needle_engine import NeedleEngine
from app.ai.writer import LocalWriter, reap_orphans
from app.audio.audio_manager import AudioManager
from app.audio.stt import create_recognizer
from app.config.settings import ACTIVATION_CONVERSATION, Settings
from app.core.events import ConfirmationRequest, HistoryEntry, OutcomeKind, TurnOutcome
from app.core.history import CommandHistory
from app.core.jobs import BackgroundJobs, JobResult
from app.core.pipeline import CommandProcessor, parse_yes_no
from app.core.state import NexusState, StateModel
from app.i18n import set_language, tr
from app.tools import load_all, windows
from app.tools.app_resolver import AppResolver
from app.tools.context import services
from app.tools.file_index import FileIndex

log = logging.getLogger("nexus.assistant")

# Whole utterances that end conversation mode.
_EXIT = re.compile(r"^\s*(?:ok[,.!]?\s+|nexus[,.!]?\s+|obrigad[oa][,.!]?\s+)?(?:pode\s+)?"
                   r"(?:parar?|pare|para de ouvir|pare de ouvir|sair|sai|encerrar|encerra|desativar(?: o modo de voz)?|"
                   r"desliga o microfone|chega|tchau|at[eé] mais|at[eé] logo|[eé] s[oó] isso|s[oó] isso|"
                   r"stop(?: listening)?|exit|goodbye|bye|that'?s all)"
                   r"(?:[,.!]?\s*(?:nexus|obrigad[oa]|thanks?))?\s*[.!]*\s*$", re.IGNORECASE)


def is_exit_phrase(text: str) -> bool:
    return bool(_EXIT.match(fold(text)) or _EXIT.match(text))


# Stops a writing job ("cancela", "para de escrever"); a plain "parar" still ends voice mode.
_STOP_JOB = re.compile(r"^\s*(?:nexus[,.!]?\s+)?(?:(?:cancela|cancele|cancelar|interrompe|interrompa)"
                       r"(?:\s+(?:isso|a escrita|o codigo|o texto|o programa|tudo))?|"
                       r"(?:para|pare|parar|chega)\s+de\s+escrever|stop writing|cancel(?: (?:it|that|writing))?)"
                       r"\s*[.!]*\s*$", re.IGNORECASE)


def is_stop_job_phrase(text: str) -> bool:
    return bool(_STOP_JOB.match(fold(text)))


class Assistant(QObject):
    startup_progress = Signal(str, float)          # label, fraction (<0 = indeterminate)
    startup_finished = Signal(bool, str)           # ok, message
    transcript = Signal(str, str)                  # text, source ("text" | "voice")
    reply = Signal(str, bool, list)                # message, success, extra lines
    notice = Signal(str)                           # short transient hint
    confirmation_requested = Signal(object)        # ConfirmationRequest
    confirmation_closed = Signal()
    pulse = Signal(str)                            # "success" | "error"
    debug_event = Signal(dict)
    history_added = Signal(object)                 # HistoryEntry
    voice_available = Signal(bool)
    voice_mode_changed = Signal(bool)
    job_progress = Signal(str)                     # background writing job; "" when it ends

    _outcome_ready = Signal(object, str, str)
    _job_done = Signal(object)
    _progress = Signal(str, float)
    _startup_done = Signal(bool, str)
    _voice_done = Signal(bool)

    def __init__(self, settings: Settings, state: StateModel, audio: AudioManager) -> None:
        super().__init__()
        self.settings = settings
        set_language(settings.lang())
        self.state = state
        self.audio = audio
        self.memory = LocalMemory()
        self.history = CommandHistory()
        self.registry = load_all()
        self.engine = NeedleEngine(settings, self.registry)
        self.processor = CommandProcessor(self.engine, self.registry, settings, self.memory)
        self.ready = False
        self.voice_ready = False
        self.voice_mode = False
        self._voice_failed = False
        self._busy = False
        self._last_text = ""
        self._last_source = "text"
        self._pending_confirmation: ConfirmationRequest | None = None

        services.settings = settings
        services.memory = self.memory
        services.apps = AppResolver(user_alias=self.memory.alias_for)
        services.files = FileIndex(self._search_roots, max_depth=settings.files.index_max_depth)
        # Writing model + background jobs (write_code / write_text run for minutes)
        services.writer = LocalWriter(settings)
        threading.Thread(target=reap_orphans, name="reap-llama", daemon=True).start()
        self.jobs = services.jobs = BackgroundJobs()
        self.jobs.on_progress = self.job_progress.emit        # Qt queues these onto the UI thread
        self.jobs.on_finished = self._job_done.emit
        windows.tracker.start()   # remembers your last app window (type_text / press_keys target)

        self._outcome_ready.connect(self._on_outcome)
        self._job_done.connect(self._on_job_done)
        self._progress.connect(self.startup_progress)
        self._startup_done.connect(self._on_startup_done)
        self._voice_done.connect(self._on_voice_done)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5 * 60 * 1000)
        self._refresh_timer.timeout.connect(self._refresh_facts_when_idle)
        self._relisten_timer = QTimer(self)
        self._relisten_timer.setSingleShot(True)
        self._relisten_timer.timeout.connect(self._listen_again)
        audio.listening_changed.connect(self._on_listening_changed)
        audio.transcribing.connect(self._on_transcribing)
        audio.transcript_ready.connect(lambda text: self.submit(text, "voice"))
        audio.no_speech.connect(self._on_no_speech)
        audio.mic_error.connect(self._on_mic_error)
        audio.speaking_changed.connect(self._on_speaking_changed)
        audio.tts_error.connect(lambda msg: log.warning("TTS error: %s", msg))
        audio.device_switched.connect(lambda name: self.notice.emit(tr("Using microphone: {name}", name=name)))

    # ============================================================ startup
    def start(self) -> None:
        threading.Thread(target=self._startup, name="startup", daemon=True).start()

    def _search_roots(self) -> list:
        roots = [paths.user_folder(n) for n in ("desktop", "documents", "downloads", "pictures", "music", "videos")]
        onedrive = os.environ.get("OneDrive")
        if onedrive:
            roots.append(Path(onedrive))
        roots += [Path(p) for p in self.memory.locations().values()]
        roots += [Path(p) for p in self.settings.files.extra_search_roots]
        return [r for r in roots if r is not None]

    def _report(self, label: str, fraction: float | None) -> None:
        self._progress.emit(label, -1.0 if fraction is None else float(fraction))

    def _startup(self) -> None:
        started = time.perf_counter()
        assets = ModelAssets(paths.models_dir(), self._report)
        try:
            self._report(tr("Loading tools"), None)
            services.apps.refresh_async()  # type: ignore[union-attr]
            services.files.build_async()   # type: ignore[union-attr]
            # Speech models load in parallel with Needle; text works as soon as Needle is ready.
            threading.Thread(target=self._startup_voice, args=(assets,), name="voice-init", daemon=True).start()

            self._report(tr("Preparing local AI core"), None)
            self.engine.submit(self.engine.load, assets).result()
            self.memory.attach_embedder(self._embed_on_needle_thread)
            self.engine.submit(self.memory.prime)  # background vocabulary for semantic lookup
        except AssetError as exc:
            log.error("AI core unavailable: %s", exc)
            self._startup_done.emit(False, str(exc))
            return
        except Exception as exc:
            log.exception("Needle failed to start")
            self._startup_done.emit(False, tr("The AI core failed to start ({name}).", name=type(exc).__name__))
            return
        log.info("AI core ready in %.1fs", time.perf_counter() - started)
        self._startup_done.emit(True, "")

    def _load_voice(self, assets: ModelAssets) -> bool:
        voice_ok = True
        try:
            model_dir = assets.whisper_model(self.settings.stt_model_name())
            recognizer = create_recognizer(model_dir, self.settings.stt_language())
            recognizer.load()
            self.audio.set_recognizer(recognizer)
        except Exception:
            log.exception("speech recognition unavailable")
            voice_ok = False
        try:
            if self.settings.voice.engine == "piper":
                try:
                    assets.piper_voice(self.settings.piper_voice_name())
                except AssetError as exc:
                    log.warning("Piper voice unavailable: %s", exc)
            engine = self.audio.build_tts(assets.piper_dir())
            log.info("TTS engine: %s (%s)", engine, self.settings.piper_voice_name())
        except Exception:
            log.exception("speech output unavailable; continuing silently")
        return voice_ok

    def _startup_voice(self, assets: ModelAssets) -> None:
        started = time.perf_counter()
        voice_ok = self._load_voice(assets)
        log.info("voice ready in %.1fs (input %s)", time.perf_counter() - started, "ok" if voice_ok else "unavailable")
        self._voice_done.emit(voice_ok)

    def _embed_on_needle_thread(self, text: str) -> list[float] | None:
        # Memory lookups run inside tools, i.e. on the Needle thread already.
        if not self.engine._on_needle_thread():
            return None
        return self.engine.embed(text)

    @Slot(bool, str)
    def _on_startup_done(self, ok: bool, message: str) -> None:
        self.ready = ok
        if ok:
            self.state.set(NexusState.IDLE)
            self._refresh_timer.start()
        else:
            self.state.set(NexusState.ERROR)
        self.startup_finished.emit(ok, message)

    @Slot(bool)
    def _on_voice_done(self, ok: bool) -> None:
        self.voice_ready = ok
        self._voice_failed = not ok
        self.voice_available.emit(ok)

    def _refresh_facts_when_idle(self) -> None:
        if self.ready and not self._busy and self.state.state is NexusState.IDLE and not self.audio.listening:
            self.engine.submit(self.engine.refresh_if_stale)

    # ========================================================= commands
    @Slot(str)
    def submit_text(self, text: str) -> None:
        self.submit(text, "text")

    def submit(self, text: str, source: str) -> None:
        text = text.strip()
        if not text:
            if self.state.state is NexusState.PROCESSING:
                self.state.set(NexusState.IDLE)
            self._schedule_relisten()
            return
        if not self.ready:
            self.notice.emit(tr("Still preparing the AI core…"))
            return
        if self._busy:
            self.notice.emit(tr("One moment…"))
            return
        if self.audio.speaking:
            self.audio.stop_speaking()
        if self.voice_mode and source == "voice" and is_exit_phrase(text):
            self.transcript.emit(text, source)
            self.set_voice_mode(False, farewell=True)
            return
        if self._pending_confirmation is not None:
            decision = parse_yes_no(text)
            if decision is not None:
                self.transcript.emit(text, source)
                self.confirm(decision)
                return
            self._close_confirmation()
        if self.jobs.running and is_stop_job_phrase(text):
            self.transcript.emit(text, source)
            self.jobs.cancel()        # the job reports "Parei de escrever" when it has stopped
            return
        self._busy = True
        self._last_text, self._last_source = text, source
        self.state.set(NexusState.PROCESSING)
        self.transcript.emit(text, source)
        future = self.engine.submit(self.processor.handle, text)
        future.add_done_callback(lambda f: self._outcome_ready.emit(f, text, source))

    @Slot(bool)
    def confirm(self, approved: bool) -> None:
        if self._pending_confirmation is None or self._busy:
            return
        self._close_confirmation()
        self._busy = True
        self.state.set(NexusState.PROCESSING)
        text, source = self._last_text, self._last_source
        future = self.engine.submit(self.processor.resolve_pending, approved)
        future.add_done_callback(lambda f: self._outcome_ready.emit(f, text, source))

    def _close_confirmation(self) -> None:
        if self._pending_confirmation is not None:
            self._pending_confirmation = None
            self.confirmation_closed.emit()

    @Slot(object, str, str)
    def _on_outcome(self, future: concurrent.futures.Future, text: str, source: str) -> None:
        self._busy = False
        try:
            outcome: TurnOutcome = future.result()
        except Exception:
            log.exception("command pipeline crashed")
            outcome = TurnOutcome(OutcomeKind.ERROR, tr("Something went wrong."))
        self._emit_debug(text, source, outcome)
        kind = outcome.kind
        if kind is OutcomeKind.CONFIRM and outcome.confirmation is not None:
            self._pending_confirmation = outcome.confirmation
            self.state.set(NexusState.CONFIRMATION)
            self.confirmation_requested.emit(outcome.confirmation)
            self.reply.emit(outcome.message, True, [])
            self.audio.speak(outcome.confirmation.spoken)
            self._schedule_relisten()   # in voice mode, listen for "sim" / "não"
            return

        failed = kind is OutcomeKind.ERROR or (kind is OutcomeKind.DONE and not outcome.success)
        if kind is OutcomeKind.DONE and outcome.success:
            self.state.set(NexusState.SUCCESS)
            self.pulse.emit("success")
        elif failed:
            self.state.set(NexusState.ERROR)
            self.pulse.emit("error")
        else:
            self.state.set(NexusState.IDLE)
        self.reply.emit(outcome.message, not failed, outcome.extra_lines)
        self.audio.speak(outcome.message)
        executed = [r.name for r in outcome.records if r.result is not None and r.route.startswith(("execute", "confirmed"))]
        entry = HistoryEntry(user=text, reply=outcome.message, source=source, success=not failed, tools=executed)
        self.history.add(entry)
        self.history_added.emit(entry)
        self._schedule_relisten()

    @Slot(object)
    def _on_job_done(self, result: JobResult) -> None:
        """A writing job finished: say so, even in the middle of voice mode."""
        if self.audio.listening:
            # Never let the microphone hear NEXUS's own announcement; voice mode
            # listens again once it has been said.
            self.audio.cancel_listening()
            if self.state.state is NexusState.LISTENING:
                self.state.set(NexusState.IDLE)
        self.pulse.emit("success" if result.success else "error")
        self.reply.emit(result.message, result.success, result.extra)
        self.audio.speak(result.message)
        entry = HistoryEntry(user=f"✎ {result.label}", reply=result.message, source="job", success=result.success,
                             tools=[])
        self.history.add(entry)
        self.history_added.emit(entry)
        if not self.audio.speech_pending:
            self._schedule_relisten()

    def _emit_debug(self, text: str, source: str, outcome: TurnOutcome) -> None:
        calls: list[dict[str, Any]] = []
        for r in outcome.records:
            calls.append({"tool": r.name, "arguments": r.arguments, "confidence": r.confidence,
                          "suppressed": r.suppressed, "route": r.route, "result": r.result,
                          "ms": round(r.duration_ms) if r.duration_ms is not None else None})
        self.debug_event.emit({"transcript": text, "source": source, "outcome": outcome.kind.value,
                               "message": outcome.message, "calls": calls, "timings": outcome.timings,
                               "needle_input": outcome.needle_input})

    # ============================================================ voice
    def _require_voice(self) -> bool:
        if not self.ready:
            self.notice.emit(tr("Still preparing the AI core…"))
            return False
        if not self.voice_ready:
            self.notice.emit(tr("Voice input isn't available. Check the log for details.") if self._voice_failed
                             else tr("Voice input is still loading…"))
            return False
        return True

    @Slot()
    def toggle_listening(self) -> None:
        """Mic button / Ctrl+Space."""
        if self.settings.audio.activation_mode == ACTIVATION_CONVERSATION:
            if not self.voice_mode:
                self.set_voice_mode(True)
            elif self.audio.speech_pending:
                # Talking over NEXUS: stop it and listen right away.
                self.audio.stop_speaking()
                self._relisten_timer.start(150)
            else:
                self.set_voice_mode(False)
            return
        if self.audio.listening:
            self.audio.stop_listening(submit=True)
        else:
            self.start_listening()

    def set_voice_mode(self, on: bool, farewell: bool = False) -> None:
        if on == self.voice_mode:
            return
        if on:
            if not self._require_voice():
                return
            self.voice_mode = True
            self.voice_mode_changed.emit(True)
            log.info("conversation mode on")
            self._listen_again()
        else:
            self.voice_mode = False
            self._relisten_timer.stop()
            self.voice_mode_changed.emit(False)
            if self.audio.listening:
                self.audio.cancel_listening()
            if self.state.state in (NexusState.LISTENING, NexusState.SPEAKING):
                self.state.set(NexusState.IDLE)
            log.info("conversation mode off")
            if farewell:
                self.reply.emit(tr("See you later."), True, [])
                self.audio.speak(tr("See you later."))
            else:
                self.notice.emit(tr("Voice mode off."))

    def _schedule_relisten(self, delay_ms: int = 350) -> None:
        if self.voice_mode:
            self._relisten_timer.start(delay_ms)

    def _listen_again(self) -> None:
        """Conversation mode: open the mic once NEXUS has finished talking."""
        if not self.voice_mode or self._busy or self.audio.listening:
            return
        if self.audio.speech_pending:
            self._relisten_timer.start(200)   # still talking (or about to): try again shortly
            return
        if self.audio.start_listening(continuous=True, chime=self.settings.audio.chime):
            self.state.set(NexusState.LISTENING)

    @Slot()
    def start_listening(self) -> None:
        if not self._require_voice() or self._busy:
            return
        if self.audio.start_listening(chime=self.settings.audio.chime):
            self.state.set(NexusState.LISTENING)

    @Slot()
    def stop_listening(self) -> None:
        self.audio.stop_listening(submit=True)

    @Slot()
    def cancel(self) -> None:
        """Esc: stop whatever NEXUS is doing that the user can stop."""
        if self.voice_mode:
            self.audio.stop_speaking()
            self.set_voice_mode(False)
        elif self.audio.listening:
            self.audio.cancel_listening()
            self.state.set(NexusState.IDLE)
        elif self._pending_confirmation is not None:
            self.confirm(False)
        elif self.audio.speaking:
            self.audio.stop_speaking()
        elif self.jobs.running:
            self.jobs.cancel()

    def _on_listening_changed(self, listening: bool) -> None:
        if listening:
            self.state.set(NexusState.LISTENING)
        elif self.state.state is NexusState.LISTENING:
            self.state.set(NexusState.IDLE)

    def _on_transcribing(self) -> None:
        self.state.set(NexusState.PROCESSING)
        self.transcript.emit("…", "voice")

    def _on_no_speech(self) -> None:
        if self.state.state in (NexusState.PROCESSING, NexusState.LISTENING):
            self.state.set(NexusState.IDLE)
        if self.voice_mode:
            self._schedule_relisten(100)   # a cough or a click: just keep listening
        else:
            self.notice.emit(tr("I didn't hear anything."))

    def _on_mic_error(self, message: str) -> None:
        if self.voice_mode:
            self.set_voice_mode(False)
        self.state.set(NexusState.ERROR)
        self.pulse.emit("error")
        self.reply.emit(message, False, [])

    def _on_speaking_changed(self, speaking: bool) -> None:
        current = self.state.state
        if speaking and current in (NexusState.IDLE, NexusState.SUCCESS, NexusState.ERROR):
            self.state.set(NexusState.SPEAKING)
        elif not speaking:
            if current is NexusState.SPEAKING:
                self.state.set(NexusState.IDLE)
            self._schedule_relisten(250)

    # ========================================================= settings
    def apply_settings(self, voice_changed: bool) -> None:
        set_language(self.settings.lang())
        self.audio.apply_settings()
        services.files.max_depth = self.settings.files.index_max_depth  # type: ignore[union-attr]
        if voice_changed:
            def rebuild() -> None:
                try:
                    ok = self._load_voice(ModelAssets(paths.models_dir()))
                    self._voice_done.emit(ok)
                except Exception:
                    log.exception("could not switch voice")
            threading.Thread(target=rebuild, name="voice-rebuild", daemon=True).start()

    def test_voice(self) -> None:
        self.audio.speak(tr("This is how I sound."))

    def shutdown(self) -> None:
        self.set_voice_mode(False)
        self.jobs.on_progress = lambda _text: None          # the window is going away
        self.jobs.on_finished = lambda _result: None
        self.jobs.cancel()
        if services.writer is not None:
            services.writer.shutdown()
        windows.tracker.stop()
        self.audio.shutdown()
        self.engine.shutdown()
