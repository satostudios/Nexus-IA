"""Local writing model: composes the texts and programs NEXUS is asked to create.

Needle 3 decides *what* to do ("write_code: an expense tracker, open it in
Antigravity"). It is a small function-calling model and does not write prose or
programs. Those are written by a second local model, Qwen3.5 (Apache-2.0),
served by the official llama.cpp ``llama-server`` on 127.0.0.1. Nothing leaves
the machine.

Measured on the development laptop (i5-1235U, 16 GB RAM, CPU only, September
2026, llama.cpp b11185):

==================  ======  ==========  =====================================
model (Q4_K_M)      size    tokens/s    notes
==================  ======  ==========  =====================================
Qwen3.5-4B          2.6 GB  5.0 - 5.7   default: best Portuguese and code
Qwen3.5-2B          1.3 GB  8.9 - 9.4   faster; programs often have logic bugs
==================  ======  ==========  =====================================

The Iris Xe GPU (Vulkan build) was slower (5.0 tok/s), and speculative decoding
(Qwen3.5-0.8B draft: 2.0 tok/s; n-gram: 3.3 tok/s) is slower on this CPU, so
NEXUS uses the plain CPU build.

The server starts on first use (about 5 s) and stops after a few idle minutes
to give its ~3 GB of RAM back. It runs inside a Windows job object, so it can
never outlive NEXUS.
"""

from __future__ import annotations

import atexit
import json
import logging
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

from app import paths
from app.i18n import tr

if TYPE_CHECKING:
    from app.config.settings import Settings

log = logging.getLogger("nexus.writer")

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


@dataclass(frozen=True)
class WriterModel:
    key: str
    label: str
    repo: str
    revision: str
    file: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.file}"


WRITER_MODELS: dict[str, WriterModel] = {
    "qwen3.5-4b": WriterModel("qwen3.5-4b", "Qwen3.5 4B", "unsloth/Qwen3.5-4B-GGUF",
                              "e87f176479d0855a907a41277aca2f8ee7a09523", "Qwen3.5-4B-Q4_K_M.gguf",
                              "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4", 2740937888),
    "qwen3.5-2b": WriterModel("qwen3.5-2b", "Qwen3.5 2B", "unsloth/Qwen3.5-2B-GGUF",
                              "f6d5376be1edb4d416d56da11e5397a961aca8ae", "Qwen3.5-2B-Q4_K_M.gguf",
                              "aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223", 1280835840),
}
DEFAULT_WRITER = "qwen3.5-4b"

# Official llama.cpp Windows CPU build, pinned (hash from the GitHub release).
LLAMA_BUILD = "b11185"
LLAMA_ZIP = f"llama-{LLAMA_BUILD}-bin-win-cpu-x64.zip"
LLAMA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}/{LLAMA_ZIP}"
LLAMA_SHA256 = "ff83bbf26f06792f6a54e7335e65a76fd31ac5008fc43afac241d16877480ba5"
LLAMA_SIZE = 18_560_000   # approximate, for "how much is left to download"

# Recommended non-thinking sampling for Qwen3.5 (model card); code gets a lower
# temperature and no presence penalty (code repeats identifiers on purpose).
SAMPLING = {
    "code": {"temperature": 0.6, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0},
    "text": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.0},
}


class WriterError(RuntimeError):
    """The writing model could not run. ``str()`` is user-facing."""


class Cancelled(Exception):
    pass


def writer_model(settings: "Settings") -> WriterModel:
    return WRITER_MODELS.get(settings.ai.writer_model, WRITER_MODELS[DEFAULT_WRITER])


# ================================================================ server
def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _kill_with_us(proc: subprocess.Popen) -> object | None:
    """Put ``proc`` in a job object that is closed (killing it) when NEXUS exits.

    Windows refuses this when NEXUS itself runs inside a restricted job (e.g.
    the Microsoft Store Python used in development); then ``atexit`` and
    :func:`reap_orphans` at the next start are what clean up.
    """
    try:
        import win32api
        import win32con
        import win32job

        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid)
        win32job.AssignProcessToJobObject(job, handle)
        return job
    except Exception as exc:  # noqa: BLE001
        log.info("llama-server not in a kill-on-close job (%s); relying on atexit/orphan cleanup", exc)
        return None


def reap_orphans() -> int:
    """Stop llama-server processes that a crashed NEXUS left behind: started from
    a NEXUS runtime folder (``...\\NEXUS\\models\\llama``) and whose parent is
    gone. A server whose NEXUS is still running, and any other llama.cpp
    installation, is never touched."""
    import psutil

    killed = 0
    for proc in psutil.process_iter(["name", "exe", "ppid", "create_time"]):
        try:
            exe = str(proc.info["exe"] or "").lower()
            if (proc.info["name"] or "").lower() != "llama-server.exe" or "\\nexus\\models\\llama\\" not in exe:
                continue
            try:
                parent = psutil.Process(proc.info["ppid"])
                orphan = parent.create_time() > proc.info["create_time"]   # the pid was reused
            except psutil.NoSuchProcess:
                orphan = True
            if orphan:
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if killed:
        log.info("stopped %d orphaned llama-server process(es)", killed)
    return killed


class WriterServer:
    """One ``llama-server`` process serving one model on 127.0.0.1."""

    def __init__(self, exe: Path, model: Path, threads: int = 0, context: int = 16384) -> None:
        self.exe, self.model, self.threads, self.context = exe, model, threads, context
        self.port = 0
        self._proc: subprocess.Popen | None = None
        self._job: object | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout: float = 180.0, cancel: threading.Event | None = None) -> None:
        self.port = _free_port()
        exe, model = paths.launchable(self.exe), paths.launchable(self.model)
        cmd = [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.port),
               "-c", str(self.context), "-np", "1", "--no-webui", "--reasoning", "off"]
        if self.threads > 0:
            cmd += ["-t", str(self.threads)]
        log_path = paths.logs_dir() / "writer-server.log"
        started = time.perf_counter()
        with open(log_path, "w", encoding="utf-8", errors="replace") as out:
            # Below-normal priority: the model takes every core, and the UI, the
            # microphone and Needle must stay responsive while it writes.
            self._proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                          cwd=str(exe.parent),
                                          creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS)
        self._job = _kill_with_us(self._proc)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                self.stop()
                raise Cancelled()
            if self._proc.poll() is not None:
                log.error("llama-server exited with 0x%08X", self._proc.returncode & 0xFFFFFFFF)
                raise WriterError(tr("The writing model failed to start. See logs/writer-server.log."))
            try:
                with urllib.request.urlopen(self.base + "/health", timeout=2) as r:
                    if r.status == 200:
                        log.info("llama-server ready on %d in %.1fs (%s)", self.port,
                                 time.perf_counter() - started, self.model.name)
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.3)
        self.stop()
        raise WriterError(tr("The writing model took too long to start."))

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._job = None

    def chat(self, messages: list[dict[str, str]], *, max_tokens: int, sampling: dict[str, float],
             cancel: threading.Event | None = None, stats: dict | None = None) -> Iterator[str]:
        """Stream the assistant's reply. Stops (and the server stops generating)
        as soon as ``cancel`` is set."""
        body = {"messages": messages, "stream": True, "max_tokens": max_tokens, **sampling,
                "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(self.base + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        think = _ThinkFilter()
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw in resp:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()    # closing the connection stops generation server-side
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if stats is not None and chunk.get("timings"):
                    stats["timings"] = chunk["timings"]
                for choice in chunk.get("choices") or []:
                    if stats is not None and choice.get("finish_reason"):
                        stats["finish_reason"] = choice["finish_reason"]
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        visible = think.feed(delta)
                        if visible:
                            yield visible


class _ThinkFilter:
    """Drops a ``<think>...</think>`` block if a template still emits one."""

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False
        self._started = False

    def feed(self, text: str) -> str:
        if self._started and not self._inside:
            return text
        self._buf += text
        if not self._started:
            stripped = self._buf.lstrip()
            if not stripped:
                return ""
            if "<think>".startswith(stripped[:7]) and len(stripped) < 7:
                return ""
            self._started = True
            if stripped.startswith("<think>"):
                self._inside = True
            else:
                out, self._buf = self._buf, ""
                return out
        end = self._buf.find("</think>")
        if end < 0:
            return ""
        self._inside = False
        out, self._buf = self._buf[end + len("</think>"):].lstrip("\n"), ""
        return out


# ================================================================ facade
class LocalWriter:
    """Downloads (once), starts and stops the writing model; streams completions."""

    def __init__(self, settings: "Settings", models_dir: Path | None = None) -> None:
        self.settings = settings
        self.models_dir = models_dir or paths.models_dir()
        self._server: WriterServer | None = None
        self._lock = threading.Lock()
        self._idle: threading.Timer | None = None
        self._uses = 0
        atexit.register(self.shutdown)

    # ---------------------------------------------------------- assets
    def _assets(self, progress: Callable[[str, float | None], None] | None = None):  # noqa: ANN202
        from app.ai.model_assets import ModelAssets

        return ModelAssets(self.models_dir, progress)

    def missing_bytes(self) -> int:
        """How much still has to be downloaded before the first use (0 = ready)."""
        return self._assets().writer_missing_bytes(writer_model(self.settings))

    # ---------------------------------------------------------- server
    def ensure(self, progress: Callable[[str, float | None], None] | None = None,
               cancel: threading.Event | None = None) -> WriterServer:
        with self._lock:
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            model = writer_model(self.settings)
            if self._server is not None and self._server.alive() and self._server.model.name == model.file:
                return self._server
            if self._server is not None:
                self._server.stop()
                self._server = None
            assets = self._assets(progress)
            exe = assets.llama_server(cancel=cancel)
            path = assets.writer_model(model, cancel=cancel)
            if progress is not None:
                progress(tr("Loading the writing model"), None)
            server = WriterServer(exe, path, threads=self.settings.ai.writer_threads)
            server.start(cancel=cancel)
            self._server = server
            return server

    def stream(self, messages: list[dict[str, str]], kind: str, max_tokens: int,
               cancel: threading.Event | None = None, stats: dict | None = None,
               progress: Callable[[str, float | None], None] | None = None) -> Iterator[str]:
        server = self.ensure(progress, cancel)
        with self._lock:
            self._uses += 1
        try:
            yield from server.chat(messages, max_tokens=max_tokens, sampling=SAMPLING[kind], cancel=cancel,
                                   stats=stats)
        finally:
            with self._lock:
                self._uses -= 1
                if self._uses == 0:
                    self._schedule_idle_stop()

    def _schedule_idle_stop(self) -> None:
        minutes = self.settings.ai.writer_idle_minutes
        if minutes <= 0:
            return
        timer = threading.Timer(minutes * 60, self._stop_if_idle)
        timer.daemon = True
        timer.start()
        self._idle = timer

    def _stop_if_idle(self) -> None:
        with self._lock:
            if self._uses == 0 and self._server is not None:
                log.info("writing model idle: stopping llama-server")
                self._server.stop()
                self._server = None
            self._idle = None

    def shutdown(self) -> None:
        with self._lock:
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            if self._server is not None:
                self._server.stop()
                self._server = None


def clean_stats(stats: dict) -> str:
    t = stats.get("timings") or {}
    if not t:
        return ""
    return (f"{t.get('predicted_n', 0)} tok @ {t.get('predicted_per_second', 0):.1f}/s, "
            f"prompt {t.get('prompt_n', 0)} tok @ {t.get('prompt_per_second', 0):.0f}/s")
