"""Headless self-test of every subsystem: ``NEXUS.exe --selftest``.

Runs real components only (Needle, Whisper, Piper/SAPI, microphone, tools),
never performs a destructive action (the shutdown request is only checked for
reaching CONFIRMATION, and the power command is intercepted), and writes
``logs/selftest.json``. Exit code 0 when every critical check passes.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import numpy as np


def run_selftest() -> int:
    from app import __version__, paths
    from app.config.settings import Settings
    from app.logging_setup import setup_logging

    settings = Settings.load()
    setup_logging(True, "INFO")
    from app.i18n import set_language

    lang = settings.lang()
    set_language(lang)
    pt = lang == "pt"
    report: dict[str, Any] = {"version": __version__, "frozen": paths.is_frozen(), "language": lang, "checks": {}}
    ok_all = True

    def check(name: str, critical: bool, fn: Callable[[], Any]) -> Any:
        nonlocal ok_all
        started = time.perf_counter()
        try:
            detail = fn()
            report["checks"][name] = {"ok": True, "ms": round((time.perf_counter() - started) * 1000),
                                      "detail": detail}
            return detail
        except Exception as exc:
            report["checks"][name] = {"ok": False, "critical": critical, "error": f"{type(exc).__name__}: {exc}",
                                      "trace": traceback.format_exc()[-1500:]}
            if critical:
                ok_all = False
            return None

    from app.ai.memory import LocalMemory
    from app.ai.model_assets import ModelAssets
    from app.ai.needle_engine import NeedleEngine, configure_environment
    from app.core.events import OutcomeKind
    from app.core.pipeline import CommandProcessor
    from app.tools import load_all
    from app.tools import system as system_tools
    from app.tools.app_resolver import AppResolver
    from app.tools.context import services
    from app.tools.file_index import FileIndex

    configure_environment(settings)
    sandbox = Path(tempfile.mkdtemp(prefix="nexus_selftest_"))
    memory = LocalMemory(sandbox / "memory.json")
    services.settings, services.memory = settings, memory
    services.apps = AppResolver(cache_file=sandbox / "apps.json", user_alias=memory.alias_for)
    services.files = FileIndex(lambda: [sandbox])
    check("tools.registry", True, lambda: len(load_all().names()))
    assets = ModelAssets(paths.models_dir())
    engine = NeedleEngine(settings, load_all())
    check("needle.load", True, lambda: engine.submit(engine.load, assets).result())
    processor = CommandProcessor(engine, load_all(), settings, memory)
    intercepted: list[list[str]] = []
    system_tools._run_power_command = lambda args: intercepted.append(args)  # never power off

    def text_command(text: str, expect: OutcomeKind, tool: str | None) -> dict[str, Any]:
        out = engine.submit(processor.handle, text).result()
        calls = [r.name for r in out.records]
        if out.kind is not expect or (tool and tool not in calls):
            raise AssertionError(f"{text!r} -> {out.kind.value} {calls} {out.message!r}")
        return {"message": out.message, "calls": calls, "timings": out.timings}

    if engine.ready.is_set():
        check("text.time", True, lambda: text_command("que horas são" if pt else "what time is it",
                                                       OutcomeKind.DONE, "get_system_time"))
        check("text.unsupported", True, lambda: text_command("me conta uma piada" if pt else "tell me a joke",
                                                              OutcomeKind.UNSUPPORTED, None))

        def write_routes() -> dict[str, Any]:
            # routed only (dry run): the self-test never opens an editor or types
            dry = CommandProcessor(engine, load_all(), settings, memory, dry_run=True)
            got = {}
            for text, tool in ((("escreva um poema sobre o mar", "write_text"),
                                ("abra o antigravity e escreva um código em .py pra criar um app de economia",
                                 "write_code")) if pt else
                               (("write a poem about the sea", "write_text"),
                                ("create a python script that renames my photos", "write_code"))):
                out = engine.submit(dry.handle, text).result()
                acted = [r.name for r in out.records if r.route.startswith("dry-run")]
                if acted != [tool]:
                    raise AssertionError(f"{text!r} -> {acted}")
                got[text] = acted[0]
            return got

        check("text.write_routes", True, write_routes)

        def shutdown_guard() -> dict[str, Any]:
            detail = text_command("desligue o computador" if pt else "Shutdown the computer.", OutcomeKind.CONFIRM,
                                  "shutdown_windows")
            engine.submit(processor.resolve_pending, False).result()
            if intercepted:
                raise AssertionError("power command ran without confirmation")
            return detail

        check("text.shutdown_requires_confirmation", True, shutdown_guard)

    def piper() -> tuple[np.ndarray, int]:
        from app.audio.tts import PiperSynthesizer

        voice_dir = assets.piper_dir()
        assets.piper_voice(settings.piper_voice_name())
        synth = PiperSynthesizer(voice_dir, settings.piper_voice_name())
        chunks = list(synth.synthesize("Tire uma captura de tela." if pt else "Take a screenshot.", 1.0))
        audio = np.concatenate([c[0] for c in chunks])
        return audio, chunks[0][1]

    speech = check("tts.piper", False, lambda: _describe(piper()))
    check("tts.sapi", False, lambda: _describe(_sapi()))

    def stt() -> str:
        from app.audio.stt import create_recognizer

        audio, rate = piper() if speech else _sapi()
        x = np.interp(np.arange(int(audio.size * 16000 / rate)) * rate / 16000, np.arange(audio.size), audio)
        x = np.concatenate([np.zeros(4000), x, np.zeros(4000)]).astype(np.float32)
        rec = create_recognizer(assets.whisper_model(settings.stt_model_name()), settings.stt_language())
        rec.load()
        text = rec.transcribe(x)
        heard = text.lower() if pt else text.lower().replace(" ", "")
        if ("captura" if pt else "screenshot") not in heard:
            raise AssertionError(f"transcript {text!r}")
        return text

    check("stt.whisper", True, stt)

    def microphone() -> dict[str, Any]:
        from app.audio.microphone import Microphone, list_input_devices

        devices = [d.name for d in list_input_devices()]
        blocks: list[np.ndarray] = []
        mic = Microphone(blocks.append, lambda m: None)
        mic.start(settings.audio.input_device, 16000)
        time.sleep(0.6)
        mic.stop()
        if not blocks:
            raise AssertionError("no audio blocks")
        x = np.concatenate(blocks)
        return {"devices": devices, "device": mic.device_name, "seconds": round(x.size / 16000, 2),
                "rms_dbfs": round(20 * float(np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)), 1)}

    check("audio.microphone", False, microphone)

    def screenshot() -> dict[str, Any]:
        from app.tools.screenshots import capture

        path = capture(sandbox)
        return {"bytes": path.stat().st_size}

    check("tools.screenshot", True, screenshot)
    check("tools.app_index", False, lambda: services.apps.refresh())  # type: ignore[union-attr]

    def writer() -> Any:
        from app.ai.writer import LocalWriter, clean_stats

        w = LocalWriter(settings)
        if w.missing_bytes() > 0:
            return "not downloaded yet (downloads on first use)"
        stats: dict = {}
        try:
            prompt = "Responda só com a palavra: pronto" if pt else "Reply with just the word: ready"
            text = "".join(w.stream([{"role": "user", "content": prompt}], "text", 16, stats=stats))
        finally:
            w.shutdown()
        if not text.strip():
            raise AssertionError("empty reply")
        return {"reply": text.strip(), "speed": clean_stats(stats), "model": settings.ai.writer_model}

    check("writer.generate", False, writer)

    def opengl() -> bool:
        from PySide6.QtGui import QGuiApplication

        from app.ui.nexus_core import configure_default_format, opengl_available

        configure_default_format()
        app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
        if app is None or not opengl_available():
            raise AssertionError("OpenGL 3.3 unavailable (software core would be used)")
        for name in ("fullscreen.vert", "core_scene.frag", "core_post.frag"):
            if not paths.asset("shaders", name).is_file():
                raise AssertionError(f"missing shader {name}")
        return True

    check("ui.opengl", False, opengl)
    engine.shutdown()
    report["ok"] = ok_all
    out = paths.logs_dir() / "selftest.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    try:
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)
    except Exception:
        pass
    summary = " ".join(f"{k}={'ok' if v['ok'] else 'FAIL'}" for k, v in report["checks"].items())
    print(f"NEXUS selftest {'PASSED' if ok_all else 'FAILED'}: {summary}\nreport: {out}")
    return 0 if ok_all else 1


def _sapi() -> tuple[np.ndarray, int]:
    from app.audio.tts import SapiSynthesizer

    synth = SapiSynthesizer()
    voices = synth.voices()
    from app.i18n import is_pt

    wanted = "portuguese" if is_pt() else "english"
    synth.voice_name = next((v for v in voices if wanted in v.lower()), "")
    chunks = list(synth.synthesize("Tire uma captura de tela." if is_pt() else "Take a screenshot.", 1.0))
    return np.concatenate([c[0] for c in chunks]), chunks[0][1]


def _describe(result: tuple[np.ndarray, int]) -> dict[str, Any]:
    audio, rate = result
    if audio.size == 0:
        raise AssertionError("no audio synthesized")
    return {"seconds": round(audio.size / rate, 2), "rate": rate}
