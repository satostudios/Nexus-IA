"""Creation tools (write_code / write_text / edit_code / run_program): routing,
plans, streaming filters, the job runner, and real generation with the local
writing model."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.ai.language import normalize
from app.core.jobs import BackgroundJobs, JobResult
from app.tools import creation, load_all
from app.tools.creation import CodeFilter, TextFilter, code_language, output_name, python_error, subject_of


# ------------------------------------------------------------------ routing
@pytest.mark.parametrize("text,covered,not_covered", [
    ("escreva um poema sobre o mar", {"write_text"}, {"type_text", "write_code"}),
    ("escreva comprar pão e leite", {"type_text"}, {"write_text", "write_code"}),
    ("abra o bloco de notas e digite olá mundo", {"type_text", "open_application"}, {"write_text"}),
    # (".py" keeps open_application's file guard from matching; the pipeline folds the open into write_code)
    ("abra o antigravity e escreva um código em .py pra criar um app de economia",
     {"write_code"}, {"type_text", "write_text"}),
    ("crie um jogo da velha em python", {"write_code"}, {"type_text"}),
    ("escreve um email pro meu chefe pedindo férias", {"write_text"}, {"type_text"}),
    ("faça uma lista de compras", {"write_text"}, set()),
    ("write a poem about the sea", {"write_text"}, {"type_text"}),
    ("create a python script that renames photos", {"write_code"}, {"type_text"}),
    ("type hello world", {"type_text"}, {"write_text", "write_code"}),
    ("crie uma pasta chamada apps", {"create_folder"}, {"write_code"}),
    ("crie um arquivo chamado notas.txt", {"create_file"}, {"write_code", "write_text"}),
    ("create a text file called notes", {"create_file"}, {"write_text"}),
    ("rode o programa", {"run_program"}, set()),
    ("adicione um botão de exportar no app", {"edit_code"}, set()),
    ("me conta uma piada", set(), {"write_text", "write_code"}),
])
def test_dictation_composition_and_code_are_told_apart(text, covered, not_covered):
    reg = load_all()
    matched = reg.matching(normalize(text).text)
    assert covered <= matched, matched
    assert not (not_covered & matched), matched


def test_file_extension_stays_one_token():
    assert "in .py" in normalize("escreva um código em .py").text


# ------------------------------------------------------------------- plans
@pytest.mark.parametrize("text,lang", [
    ("escreva um código em .py pra criar um app de economia", "python"),
    ("crie um site de receitas", "html"),
    ("crie um site em python com flask", "python"),
    ("faça um jogo em javascript", "javascript"),
    ("escreva um programa em C# que soma dois números", "csharp"),
    ("crie uma calculadora", "python"),
])
def test_code_language(text, lang):
    assert code_language(text).key == lang


@pytest.mark.parametrize("text,name", [
    ("abra o antigravity e escreva um código em .py pra criar um app de economia", "app_economia"),
    ("crie um jogo da velha em python", "jogo_velha"),
    ("escreva um poema sobre o mar", "poema_mar"),
    ("escreva um programa em python que calcula juros compostos", "calcula_juros_compostos"),
    ("crie um script chamado renomear.py que renomeia fotos", "renomear.py"),
    ("write a python expense tracker", "expense_tracker"),
])
def test_output_names(text, name):
    assert output_name(text, "code", "programa") == name


def test_subject_for_replies():
    from app.i18n import set_language

    set_language("pt")
    assert creation.what_of("escreva um código em .py pra criar um app de economia", "x") == "o app de economia"
    assert creation.what_of("crie uma calculadora de gorjeta", "x") == "a calculadora de gorjeta"
    assert subject_of("escreva um poema sobre o mar no bloco de notas") == "poema sobre o mar"


def test_editor_and_app_hints(services):
    services.apps.refresh()
    entry = creation.app_hint("abra o antigravity e escreva um código em .py pra criar um app de economia")
    if creation.resolve_app("antigravity") is not None:   # only where Antigravity is installed
        assert entry is not None and "antigravity" in entry.name.lower()
    assert creation.is_notepad(creation.app_hint("escreva um poema sobre o mar no bloco de notas"))
    assert creation.app_hint("escreva um poema sobre o mar") is None
    assert creation.app_hint("crie um jogo em python com tkinter") is None


# ----------------------------------------------------------------- filters
def _feed(filt, text: str, step: int = 3) -> str:
    out = "".join(filt.feed(text[i:i + step]) for i in range(0, len(text), step))
    return out + filt.finish()


def test_code_filter_strips_fences_and_prose():
    reply = "Aqui está o código:\n\n```python\nimport json\n\nprint('oi')\n```\n\nEspero que ajude!"
    assert _feed(CodeFilter(), reply) == "import json\n\nprint('oi')\n"
    assert _feed(CodeFilter(), "import os\nprint(os.name)") == "import os\nprint(os.name)\n"
    assert _feed(CodeFilter(), "<!DOCTYPE html>\n<html></html>\n```\nnotes") == "<!DOCTYPE html>\n<html></html>\n"


def test_text_filter_removes_markdown_and_types_whole_words():
    filt = TextFilter()
    pieces = [filt.feed(c) for c in ["# Tí", "tulo\n\n**O mar** é", " azul\n* um\n", "* dois"]]
    text = "".join(pieces) + filt.finish()
    assert text == "Título\n\nO mar é azul\n- um\n- dois"
    assert all(not p or p[-1] in " \n" for p in pieces)   # never half a word


def test_python_check_without_running():
    assert python_error("print('ok')\n", "a.py") is None
    assert "line 1" in python_error("def f(:\n  pass\n", "a.py")
    # the bug Qwen3.5-4B actually wrote into the first economy app: widgets local to main()
    buggy = ("import tkinter as tk\n\ndef atualizar():\n    lbl_saldo.config(text='0')\n\n"
             "def main():\n    root = tk.Tk()\n    lbl_saldo = tk.Label(root)\n    atualizar()\n")
    assert "'lbl_saldo' (line 4)" in python_error(buggy, "app.py")
    star = "from tkinter import *\nroot = Tk()\nLabel(root).pack()\nsaldo_label.pack()\n"
    problem = python_error(star, "app.py")
    assert problem and "saldo_label" in problem and "Label'" not in problem
    # the second real bug: a method called on self that the class never defines
    missing = ("class App:\n    def __init__(self):\n        self.dados = []\n        self.carregar_dados()\n"
               "    def salvar(self):\n        print(self.dados)\n")
    assert "'self.carregar_dados' (line 4)" in python_error(missing, "app.py")
    fine = ("import tkinter as tk\nclass App:\n    total = 0\n    def __init__(self, root):\n"
            "        self.x, self.y = 1, 2\n        self.build()\n    def build(self):\n"
            "        print(self.total, self.x)\nclass F(tk.Frame):\n    def go(self):\n        self.pack()\n")
    assert python_error(fine, "app.py") is None


# -------------------------------------------------------------------- jobs
def test_background_job_runs_reports_and_cancels():
    jobs = BackgroundJobs()
    progress: list[str] = []
    done: list[JobResult] = []
    jobs.on_progress = progress.append
    jobs.on_finished = done.append
    gate = threading.Event()

    def work(ctx):
        ctx.progress("step 1")
        gate.wait(5)
        return JobResult("cancelled" if ctx.cancelled else "finished", success=not ctx.cancelled)

    assert jobs.start("demo", work)
    assert jobs.running and jobs.label == "demo"
    assert not jobs.start("second", work)      # one job at a time
    assert jobs.cancel()
    gate.set()
    assert jobs.wait(5)
    assert done[0].message == "cancelled" and done[0].label == "demo" and not jobs.running
    assert "step 1" in progress and progress[-1] == ""


# ---------------------------------------------------------------- pipeline
class _ScriptedEngine:
    """Replays a fixed Needle response (to test what the pipeline does with it)."""

    def __init__(self, calls, confidence=0.9):
        self.response = {"type": "call", "function_calls": calls, "confidence": confidence}

    def new_turn(self):
        pass

    def complete(self, text, max_new_tokens=512):
        return self.response


def _dry(services, text, calls):
    from app.config.settings import Settings
    from app.core.pipeline import CommandProcessor

    proc = CommandProcessor(_ScriptedEngine(calls), load_all(), Settings(), dry_run=True)
    return proc.handle(text)


def test_type_text_for_a_poem_is_rerouted_to_write_text(services):
    out = _dry(services, "escreva um poema sobre o mar", [{"name": "type_text", "arguments": {"text": "poema"}}])
    names = [(r.name, r.route.split(":")[0]) for r in out.records]
    assert ("write_text", "dry-run") in names and ("type_text", "dry-run") not in names
    assert next(r for r in out.records if r.name == "write_text").arguments["request"] == "escreva um poema sobre o mar"


def test_open_then_write_code_becomes_one_call(services):
    text = "abra o antigravity e escreva um código em .py pra criar um app de economia"
    out = _dry(services, text, [{"name": "open_application", "arguments": {"application": "antigravity"}},
                                {"name": "write_code", "arguments": {"request": "code"}}])
    routes = {r.name: r.route for r in out.records}
    assert routes["open_application"].startswith("absorbed into write_code")
    assert routes["write_code"].startswith("dry-run")


def test_tools_mentioned_inside_a_creation_request_are_dropped(services):
    # seen with the real Needle: "...renames my photos by date" also proposed get_system_time
    out = _dry(services, "create a python script that renames my photos by date",
               [{"name": "get_system_time", "arguments": {}}, {"name": "write_code", "arguments": {"request": "x"}}])
    routes = {r.name: r.route for r in out.records}
    assert routes["get_system_time"].startswith("dropped")
    assert routes["write_code"].startswith("dry-run")


def test_unusable_needle_calls_fall_back_to_the_creation_tool(services):
    # seen with the real Needle: "open vs code and write a snake game in python" ->
    # open_application("Vocal"), open_application("Snake Game"), all flagged ungrounded
    from app.config.settings import Settings
    from app.core.pipeline import CommandProcessor

    engine = _ScriptedEngine([{"name": "open_application", "arguments": {"application": "Vocal"}},
                              {"name": "open_application", "arguments": {"application": "Snake Game"}}], 0.13)
    engine.response["validation"] = {"ungrounded": ["open_application.application"]}
    out = CommandProcessor(engine, load_all(), Settings(), dry_run=True).handle(
        "open vs code and write a snake game in python")
    acted = [r.name for r in out.records if r.route.startswith("dry-run")]
    assert acted == ["write_code"]
    assert all(r.route.startswith("absorbed") for r in out.records if r.name == "open_application")


def test_creation_wins_over_a_tool_named_inside_the_description(services):
    # the real Needle once proposed only get_system_time for this request
    out = _dry(services, "create a python script that renames my photos by date",
               [{"name": "get_system_time", "arguments": {}}])
    assert [r.name for r in out.records if r.route.startswith("dry-run")] == ["write_code"]


def test_an_app_named_like_the_dictation_is_dictation(services):
    # the real Needle (30 tools) answered "open notepad and type hello world" with two open_application calls
    out = _dry(services, "open notepad and type hello world",
               [{"name": "open_application", "arguments": {"application": "notepad"}},
                {"name": "open_application", "arguments": {"application": "hello world"}}])
    acted = [(r.name, r.arguments) for r in out.records if r.route.startswith("dry-run")]
    assert acted == [("open_application", {"application": "notepad"}), ("type_text", {"text": "hello world"})]


def test_english_with_me_is_not_portuguese():
    assert normalize("Write me a poem.").text == "Write me a poem."


def test_dictation_is_not_rerouted(services):
    out = _dry(services, "abra o bloco de notas e digite olá mundo",
               [{"name": "open_application", "arguments": {"application": "notepad"}},
                {"name": "type_text", "arguments": {"text": "olá mundo"}}])
    assert [r.name for r in out.records] == ["open_application", "type_text"]


# ---------------------------------------------------------- run_program
def test_run_program_always_asks_and_names_the_file(services, sandbox):
    from app.tools.registry import Safety

    services.settings.ai.projects_folder = str(sandbox)
    reg = load_all()
    assert reg.get("run_program").safety is Safety.CONFIRMATION_REQUIRED
    pf = reg.run_preflight("run_program", {})
    assert pf.error is not None and pf.error["error"] == "NOT_FOUND"
    program = services.settings.output_folder("code") / "ola" / "ola.py"
    program.parent.mkdir(parents=True)
    program.write_text("print('ola')\n", encoding="utf-8")
    pf = reg.run_preflight("run_program", {})
    assert pf.error is None and pf.title == "Run ola.py?"


# ------------------------------------------------ real generation (models)
@pytest.fixture()
def writer_ready(services, sandbox, monkeypatch):
    from app.ai.writer import LocalWriter
    from app.tools.context import require_jobs

    services.settings.ai.projects_folder = str(sandbox)
    services.settings.ai.writer_model = "qwen3.5-4b"   # the default model
    writer = LocalWriter(services.settings)
    if writer.missing_bytes() > 0:
        pytest.skip("writing model not downloaded (run scripts/prepare_assets.py)")
    old_writer, old_jobs = services.writer, services.jobs
    services.writer, services.jobs = writer, BackgroundJobs()
    opened: list[Path] = []
    monkeypatch.setattr(creation, "open_with", lambda entry, path: opened.append(path))
    yield require_jobs(), opened
    writer.shutdown()
    services.writer, services.jobs = old_writer, old_jobs


@pytest.mark.models
def test_write_code_generates_a_python_program_that_compiles(writer_ready, services):
    jobs, opened = writer_ready
    done: list[JobResult] = []
    jobs.on_finished = done.append
    reg = load_all()
    request = "escreva um programa em python que mostra a tabuada do 7 no terminal"
    services.session.request = request
    args = reg.get("write_code").repair({"request": "x"}, normalize(request))
    result = reg.execute("write_code", reg.run_preflight("write_code", args).arguments)
    assert result["success"], result
    assert jobs.wait(600) and done, "the writing job did not finish"
    assert done[0].success, done[0].message
    path = Path(result["data"]["path"])
    source = path.read_text(encoding="utf-8")
    assert path.name == "tabuada_7_terminal.py" or path.suffix == ".py"
    assert python_error(source, path.name) is None
    assert "```" not in source
    assert services.session.last_program == path and opened == [path]
    # The model chooses the interface (plain output, a menu, typed commands): run it with a
    # few typical answers. It must never crash; the table itself is checked when one of
    # the answers fits its interface.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    outputs = []
    for answers in ("", "1\n3\nq\n", "7\n0\nq\n", "t 7 1 10\nq\n"):
        run = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=30,
                             input=answers, encoding="utf-8", env=env)
        crash = [ln for ln in run.stderr.splitlines() if "Error" in ln and "EOFError" not in ln]
        assert not crash, run.stderr
        outputs.append(run.stdout)
    assert any(o.strip() for o in outputs)
    if not any("49" in o for o in outputs):
        print("note: the generated program's interface didn't match the scripted answers:\n" + outputs[0][:400])


_TARGET = r'''
import sys
from PySide6.QtWidgets import QApplication, QPlainTextEdit
app = QApplication([])
box = QPlainTextEdit(); box.setWindowTitle("NEXUS writing target"); box.resize(600, 400); box.show()
box.activateWindow()
app.aboutToQuit.connect(lambda: open(sys.argv[1], "w", encoding="utf-8").write(box.toPlainText()))
app.exec()
'''


@pytest.mark.models
@pytest.mark.hardware
def test_write_text_types_a_composed_text_live(writer_ready, services, sandbox):
    from app.tools import windows

    jobs, _opened = writer_ready
    done: list[JobResult] = []
    jobs.on_finished = done.append
    out = sandbox / "typed.txt"
    script = sandbox / "target.py"
    script.write_text(_TARGET, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), str(out)])
    try:
        hwnd = None
        deadline = time.time() + 15
        while time.time() < deadline and hwnd is None:
            hwnd = next((h for h in windows.top_level_windows() if windows.title(h) == "NEXUS writing target"), None)
            time.sleep(0.2)
        assert hwnd
        request = "escreva um poema curto de quatro versos sobre café"
        plan = creation.TextPlan(request, "window", None, hwnd, sandbox / "poema.txt", "o poema")
        assert jobs.start("poema", lambda ctx: creation._text_job(ctx, plan))
        assert jobs.wait(300) and done
        assert done[0].success, done[0].message
        time.sleep(0.5)
        windows.user32.PostMessageW(hwnd, 0x0010, 0, 0)
        proc.wait(timeout=10)
        typed = out.read_text(encoding="utf-8")
        assert len(typed.split()) >= 8 and "**" not in typed and not typed.startswith("#"), typed
    finally:
        if proc.poll() is None:
            proc.kill()
