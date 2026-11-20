"""Download and verify NEXUS's local models (used by setup.bat).

    python -m scripts.prepare_assets            download/verify Needle, Whisper, Piper and the writing model
    python -m scripts.prepare_assets --verify   also check microphone, voice and Needle
    python -m scripts.prepare_assets --no-writer   skip the writing model (2.6 GB; fetched on first use instead)

Everything is cached; running it again does not download anything.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import paths  # noqa: E402
from app.ai.model_assets import AssetError, ModelAssets  # noqa: E402
from app.ai.needle_engine import configure_environment  # noqa: E402
from app.config.settings import Settings  # noqa: E402

_last = [0.0]


def progress(label: str, fraction: float | None) -> None:
    now = time.monotonic()
    if now - _last[0] > 0.5:
        _last[0] = now
        pct = f" {fraction * 100:3.0f}%" if fraction is not None else ""
        print(f"      {label}{pct}", flush=True)


def step(title: str, fn) -> bool:  # noqa: ANN001
    print(f"  - {title}", flush=True)
    try:
        detail = fn()
        print(f"    ok{': ' + str(detail) if detail else ''}", flush=True)
        return True
    except AssetError as exc:
        print(f"    FAILED: {exc}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"    FAILED: {type(exc).__name__}: {exc}", flush=True)
    return False


def main() -> int:
    settings = Settings.load()
    configure_environment(settings)
    assets = ModelAssets(paths.models_dir(), progress)
    ok = True
    print(f"Models folder: {paths.models_dir()}")
    ok &= step("Needle 3 engine", assets.needle_engine)
    ok &= step("Needle 3 weights (needle3.cact)", assets.needle_weights)
    ok &= step(f"Speech recognition model ({settings.stt_model_name()}, language {settings.lang()})",
               lambda: assets.whisper_model(settings.stt_model_name()))
    voice_ok = step(f"Voice ({settings.piper_voice_name()})", lambda: assets.piper_voice(settings.piper_voice_name()))
    if not voice_ok:
        print("    NEXUS will use the built-in Windows voices instead.")
    if "--no-writer" not in sys.argv:
        from app.ai.writer import writer_model

        model = writer_model(settings)
        step("Writing engine (llama.cpp)", assets.llama_server)
        writer_ok = step(f"Writing model ({model.label}, {model.size / 1e9:.1f} GB; resumes if interrupted)",
                         lambda: assets.writer_model(model))
        if not writer_ok:
            print("    NEXUS downloads it the first time you ask it to write something.")

    if "--verify" in sys.argv:
        print("\nVerifying:")

        def mic() -> str:
            import numpy as np
            from app.audio.microphone import Microphone, list_input_devices

            devices = list_input_devices()
            if not devices:
                raise RuntimeError("no microphone found")
            blocks: list = []
            m = Microphone(blocks.append, lambda _m: None)
            m.start(settings.audio.input_device, 16000)
            time.sleep(0.5)
            m.stop()
            x = np.concatenate(blocks)
            level = 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)
            return f"{m.device_name} ({level:.0f} dBFS); devices: {', '.join(d.name for d in devices)}"

        def tts() -> str:
            from app.audio.tts import PiperSynthesizer, SapiSynthesizer

            if voice_ok:
                synth = PiperSynthesizer(assets.piper_dir(), settings.piper_voice_name())
            else:
                synth = SapiSynthesizer()
            chunks = list(synth.synthesize("A NEXUS está pronta." if settings.lang() == "pt" else "NEXUS is ready.", 1.0))
            seconds = sum(c[0].size for c in chunks) / chunks[0][1]
            return f"{synth.name}, {seconds:.1f}s of speech synthesized"

        def needle() -> str:
            from app.ai.needle_engine import NeedleEngine
            from app.tools import load_all

            engine = NeedleEngine(settings, load_all())
            engine.submit(engine.load, assets).result()

            def ask() -> dict:
                engine.new_turn()
                from app.ai.language import normalize
                return engine.complete(normalize("que horas são" if settings.lang() == "pt" else "what time is it").text)

            r = engine.submit(ask).result()
            engine.shutdown()
            calls = [c["name"] for c in r.get("function_calls") or []]
            if calls != ["get_system_time"]:
                raise RuntimeError(f"unexpected tool call {calls}")
            return f"'what time is it' -> get_system_time (confidence {r.get('confidence')})"

        ok &= step("Microphone", mic)
        ok &= step("Text-to-speech", tts)
        ok &= step("Needle tool call", needle)

    print("\nAll set." if ok else "\nSome steps failed; see above. NEXUS logs are in the logs folder.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
