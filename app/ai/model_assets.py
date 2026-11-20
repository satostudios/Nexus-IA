"""Local model/runtime assets: Needle engine + weights, Whisper STT model, Piper
voice, and the writing model (llama.cpp runtime + Qwen3.5 GGUF).

Everything is fetched once from its official source and cached; later launches
work offline and never touch the network:

* Needle engine and ``needle3.cact`` come through ``needle.agent.fetch`` (the
  package's own mechanism) into ``~/.cache/cactus-needle``;
* Whisper (CTranslate2) comes from the Systran repositories via faster-whisper
  into ``%LOCALAPPDATA%\\NEXUS\\models\\whisper``;
* Piper voices come from ``rhasspy/piper-voices`` into ``...\\models\\piper``;
* the writing model comes from the pinned official llama.cpp release (GitHub)
  and a pinned Qwen3.5 GGUF revision (Hugging Face), both SHA-256 checked, into
  ``...\\models\\llama`` and ``...\\models\\writer`` (only when NEXUS is first
  asked to write something).

Engine note (verified September 2026): ``cactus-needle`` 3.0.5 pins engine
3.0.2, but Hugging Face only publishes Python engine wheels up to 3.0.1, so the
package's automatic download returns 404 on a fresh machine. When that happens
NEXUS fetches the newest *published* engine for this platform with the same
``fetch_library`` function and points ``NEEDLE3_LIB_PATH`` (the documented
override) at it. As soon as the pinned version is published it is used instead.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from app.i18n import tr

if TYPE_CHECKING:
    from app.ai.writer import WriterModel

log = logging.getLogger("nexus.assets")

Progress = Callable[[str, "float | None"], None]


class AssetError(RuntimeError):
    """A required asset is missing and cannot be obtained. ``str()`` is user-facing."""


def _noop(_label: str, _fraction: float | None) -> None:
    pass


def _mb(n: float) -> str:
    return f"{n / 1_048_576:.1f} MB"


class _IncompleteWatcher:
    """Reports progress of a Hugging Face download by watching ``*.incomplete`` files."""

    def __init__(self, root: Path, label: str, total: int | None, progress: Progress) -> None:
        self.root, self.label, self.total, self.progress = root, label, total, progress
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="download-watch", daemon=True)

    def __enter__(self) -> "_IncompleteWatcher":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def _run(self) -> None:
        while not self._stop.wait(0.3):
            size = 0
            try:
                for p in self.root.rglob("*.incomplete"):
                    try:
                        size += p.stat().st_size
                    except OSError:
                        pass
            except OSError:
                continue
            if size:
                if self.total:
                    self.progress(f"{self.label} {_mb(size)} / {_mb(self.total)}", min(size / self.total, 1.0))
                else:
                    self.progress(f"{self.label} {_mb(size)}", None)


def _is_not_found(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}"
    return "EntryNotFound" in text or "404" in text


def _is_network(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(k in text for k in ("connect", "offline", "timed out", "timeout", "name resolution",
                                   "getaddrinfo", "network", "localentrynotfound"))


class ModelAssets:
    def __init__(self, models_dir: Path, progress: Progress | None = None) -> None:
        self.models_dir = models_dir
        self.progress = progress or _noop

    # ============================================================ Needle
    def needle_engine(self) -> Path:
        """Path to a loadable Needle 3 engine library (downloads it if needed)."""
        from needle.agent import fetch

        override = os.environ.get("NEEDLE3_LIB_PATH")
        if override and Path(override).is_file():
            return Path(override)
        lib_name = "libneedle.dll" if os.name == "nt" else fetch._lib_name()
        pinned = fetch.engine_version(3)
        package_cache = Path(fetch.cache_dir(3)) / lib_name
        if package_cache.is_file():
            return package_cache

        manifest = self.models_dir / "needle" / "engine.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                path = Path(data["path"])
                if path.is_file() and data.get("pinned") == pinned:
                    os.environ["NEEDLE3_LIB_PATH"] = str(path)
                    log.info("Needle engine %s (fallback for pinned %s): %s", data.get("version"), pinned, path)
                    return path
            except (OSError, ValueError, KeyError):
                pass

        self.progress(tr("Downloading Needle engine"), None)
        try:
            out = Path(fetch.fetch_library(pinned, str(package_cache.parent), generation=3))
            log.info("Needle engine %s downloaded to %s", pinned, out)
            return out
        except Exception as exc:
            if _is_network(exc) and not _is_not_found(exc):
                raise AssetError("The Needle engine isn't downloaded yet and there is no internet connection.") from exc
            if not _is_not_found(exc):
                raise AssetError(f"Could not download the Needle engine ({exc}).") from exc
            log.warning("Needle engine %s is not published for this platform (%s); looking for the newest "
                        "published engine", pinned, exc)
        for version in self._published_engine_versions():
            dest = self.models_dir / "needle" / "engine" / version
            try:
                out = Path(fetch.fetch_library(version, str(dest), generation=3))
            except Exception as exc:
                log.warning("engine %s failed: %s", version, exc)
                continue
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({"version": version, "pinned": pinned, "path": str(out)}), encoding="utf-8")
            os.environ["NEEDLE3_LIB_PATH"] = str(out)
            log.info("Using published Needle engine %s at %s (package pins %s)", version, out, pinned)
            return out
        raise AssetError("No Needle engine is published for this Windows version.")

    @staticmethod
    def _published_engine_versions() -> list[str]:
        from huggingface_hub import HfApi
        from needle.agent import fetch

        tag = fetch._platform_tag()
        files = HfApi().list_repo_files(fetch.engine_repo(3))
        pattern = re.compile(rf"^python/cactus_needle-(\d+(?:\.\d+)*)-py3-none-{re.escape(tag)}\.whl$")
        versions = [m.group(1) for f in files if (m := pattern.match(f))]
        versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)
        return versions

    def needle_weights(self) -> Path:
        """Path to the base ``needle3.cact`` (downloads 35 MB once)."""
        from needle.agent import fetch

        local = Path(fetch.cache_dir(3)) / fetch.base_weights(3)
        if local.is_file() and local.stat().st_size > 1_000_000:
            return local
        total = None
        try:
            from huggingface_hub import get_hf_file_metadata, hf_hub_url

            total = get_hf_file_metadata(hf_hub_url(fetch.engine_repo(3), fetch.base_weights(3))).size
        except Exception:
            pass
        from huggingface_hub import constants

        repo_cache = Path(constants.HF_HUB_CACHE) / ("models--" + fetch.engine_repo(3).replace("/", "--"))
        self.progress(tr("Downloading Needle 3 weights"), 0.0 if total else None)
        try:
            with _IncompleteWatcher(repo_cache, tr("Downloading Needle 3 weights"), total, self.progress):
                out = Path(fetch.fetch_weights(3))
        except Exception as exc:
            if _is_network(exc):
                raise AssetError("Needle 3 weights aren't downloaded yet and there is no internet connection.") from exc
            raise AssetError(f"Could not download Needle 3 weights ({exc}).") from exc
        return out

    def needle_ready_offline(self) -> bool:
        from needle.agent import fetch

        weights = Path(fetch.cache_dir(3)) / fetch.base_weights(3)
        engine_ok = bool(os.environ.get("NEEDLE3_LIB_PATH")) or \
            (Path(fetch.cache_dir(3)) / "libneedle.dll").is_file() or \
            (self.models_dir / "needle" / "engine.json").is_file()
        return weights.is_file() and engine_ok

    # =========================================================== Whisper
    def whisper_dir(self, name: str) -> Path:
        return self.models_dir / "whisper" / name

    def whisper_model(self, name: str) -> Path:
        path = self.whisper_dir(name)
        if (path / "model.bin").is_file() and (path / "config.json").is_file():
            return path
        from faster_whisper.utils import download_model

        path.mkdir(parents=True, exist_ok=True)
        label = tr("Downloading speech model ({name})", name=name)
        self.progress(label, None)
        try:
            with _IncompleteWatcher(path, label, None, self.progress):
                download_model(name, output_dir=str(path))
        except Exception as exc:
            if _is_network(exc):
                raise AssetError("The speech recognition model isn't downloaded yet and there is no internet "
                                 "connection.") from exc
            raise AssetError(f"Could not download the speech model ({exc}).") from exc
        return path

    # ============================================================= Piper
    def piper_dir(self) -> Path:
        path = self.models_dir / "piper"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def installed_piper_voices(self) -> list[str]:
        return sorted(p.name[:-5] for p in self.piper_dir().glob("*.onnx")
                      if (p.parent / (p.name + ".json")).is_file())

    def piper_voice(self, voice: str) -> Path:
        model = self.piper_dir() / f"{voice}.onnx"
        config = self.piper_dir() / f"{voice}.onnx.json"
        if model.is_file() and config.is_file() and model.stat().st_size > 1_000_000:
            return model
        from piper.download_voices import URL_FORMAT, VOICE_PATTERN

        match = VOICE_PATTERN.match(voice)
        if not match:
            raise AssetError(f"{voice} is not a valid Piper voice name.")
        lang_family = match.group("lang_family")
        args = {"lang_family": lang_family, "lang_code": f"{lang_family}_{match.group('lang_region')}",
                "voice_name": match.group("voice_name"), "voice_quality": match.group("voice_quality")}
        try:
            self._download(URL_FORMAT.format(extension=".onnx.json", **args), config, tr("Downloading voice config"))
            self._download(URL_FORMAT.format(extension=".onnx", **args), model, tr("Downloading voice {name}", name=voice))
        except Exception as exc:
            for p in (model, config):
                if p.exists() and p.stat().st_size == 0:
                    p.unlink()
            if _is_network(exc):
                raise AssetError("The voice isn't downloaded yet and there is no internet connection.") from exc
            raise AssetError(f"Could not download the voice ({exc}).") from exc
        return model

    # ============================================================ writer
    def llama_dir(self) -> Path:
        from app.ai.writer import LLAMA_BUILD

        return self.models_dir / "llama" / LLAMA_BUILD

    def writer_path(self, model: "WriterModel") -> Path:
        return self.models_dir / "writer" / model.file

    def writer_missing_bytes(self, model: "WriterModel") -> int:
        from app.ai.writer import LLAMA_SIZE

        missing = 0 if (self.llama_dir() / "llama-server.exe").is_file() else LLAMA_SIZE
        path = self.writer_path(model)
        if not (path.is_file() and path.stat().st_size == model.size):
            part = path.with_suffix(path.suffix + ".part")
            missing += model.size - (part.stat().st_size if part.is_file() else 0)
        return missing

    def llama_server(self, cancel: threading.Event | None = None) -> Path:
        """``llama-server.exe`` from the pinned official llama.cpp CPU build (18 MB, once)."""
        from app.ai.writer import LLAMA_SHA256, LLAMA_URL, LLAMA_ZIP, Cancelled

        root = self.llama_dir()
        exe = root / "llama-server.exe"
        if exe.is_file():
            return exe
        archive = root.parent / LLAMA_ZIP
        root.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._download(LLAMA_URL, archive, tr("Downloading the writing engine"), sha256=LLAMA_SHA256,
                           cancel=cancel)
            staging = root.with_name(root.name + ".tmp")
            shutil.rmtree(staging, ignore_errors=True)
            import zipfile

            with zipfile.ZipFile(archive) as z:
                z.extractall(staging)
            shutil.rmtree(root, ignore_errors=True)
            staging.rename(root)
            archive.unlink(missing_ok=True)
        except (AssetError, Cancelled):
            raise
        except Exception as exc:
            if _is_network(exc):
                raise AssetError(tr("The writing model isn't downloaded yet and there is no internet connection.")) \
                    from exc
            raise AssetError(tr("Could not download the writing engine ({name}).", name=exc)) from exc
        log.info("llama.cpp runtime ready at %s", root)
        return exe

    def writer_model(self, model: "WriterModel", cancel: threading.Event | None = None) -> Path:
        """The writing model's GGUF file (downloads it once, resumable, hash-checked)."""
        from app.ai.writer import Cancelled

        path = self.writer_path(model)
        if path.is_file() and path.stat().st_size == model.size:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._download(model.url, path, tr("Downloading the writing model"), sha256=model.sha256,
                           cancel=cancel, resume=True)
        except (AssetError, Cancelled):
            raise
        except Exception as exc:
            if _is_network(exc):
                raise AssetError(tr("The writing model isn't downloaded yet and there is no internet connection.")) \
                    from exc
            raise AssetError(tr("Could not download the writing model ({name}).", name=exc)) from exc
        return path

    # ========================================================== download
    def _download(self, url: str, dest: Path, label: str, sha256: str | None = None,
                  cancel: threading.Event | None = None, resume: bool = False) -> None:
        """Download ``url`` to ``dest`` atomically. ``resume`` continues a ``.part``
        file left by an interrupted download; ``sha256`` is checked before the
        file is put in place."""
        import hashlib

        tmp = dest.with_suffix(dest.suffix + ".part")
        digest = hashlib.sha256() if sha256 else None
        start = tmp.stat().st_size if resume and tmp.is_file() else 0
        headers = {"User-Agent": "NEXUS/1.0"}
        if start:
            headers["Range"] = f"bytes={start}-"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            if start and response.status != 206:
                start = 0   # the server ignored the range: start over
            if digest is not None and start:
                with open(tmp, "rb") as existing:
                    for block in iter(lambda: existing.read(1 << 20), b""):
                        digest.update(block)
            length = int(response.headers.get("Content-Length") or 0)
            total = (start + length) if length else None
            done = start
            last = 0.0
            with open(tmp, "ab" if start else "wb") as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        from app.ai.writer import Cancelled

                        raise Cancelled()
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    if digest is not None:
                        digest.update(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last > 0.25:
                        last = now
                        if total:
                            self.progress(f"{label} {_mb(done)} / {_mb(total)}", done / total)
                        else:
                            self.progress(f"{label} {_mb(done)}", None)
        if total and done != total:
            if not resume:
                tmp.unlink(missing_ok=True)
            raise AssetError(f"Download of {dest.name} was incomplete.")
        if digest is not None and digest.hexdigest() != sha256:
            tmp.unlink(missing_ok=True)
            raise AssetError(f"{dest.name} failed its integrity check; it will be downloaded again.")
        shutil.move(str(tmp), str(dest))
