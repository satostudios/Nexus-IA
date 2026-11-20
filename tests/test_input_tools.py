"""Web search, keyboard and media tools: argument extraction and real typing."""

import subprocess
import sys
import time

import pytest

from app.ai.language import normalize
from app.tools import load_all
from app.tools.input_control import media_action, parse_keys
from app.tools.web import query_from, site_from


@pytest.mark.parametrize("text,query,site", [
    ("pesquisa no youtube música do Legião Urbana", "música do Legião Urbana", "youtube"),
    ("busca no google como fazer pão", "como fazer pão", None),
    ("pesquise receita de bolo de cenoura no google", "receita de bolo de cenoura", None),
    ("procura na internet o preço do dólar hoje", "preço do dólar hoje", None),
    ("search for carrot cake recipes on google", "carrot cake recipes", None),
    ("pesquisa na wikipédia sobre buracos negros", "buracos negros", "wikipedia"),
    # seen in real use: the search verb in the middle, "google" as part of an app name
    ("abra o google chrome e pesquise o canal Você sabia? no youtube", "canal Você sabia?", "youtube"),
    ("pesquise sobre demogorgons", "demogorgons", None),
])
def test_search_query_is_the_users_words(text, query, site):
    assert query_from(text) == query and site_from(text) == site


@pytest.mark.parametrize("text,chord", [
    ("aperte enter", ["enter"]), ("press enter", ["enter"]), ("save the file", ["ctrl", "S"]),
    ("press ctrl + s", ["ctrl", "S"]), ("undo that", ["ctrl", "Z"]), ("aperte tab", ["tab"]),
    ("press alt f4", ["alt", "f4"]), ("select all", ["ctrl", "A"]),
])
def test_key_parsing(text, chord):
    assert parse_keys(normalize(text).text) == chord


@pytest.mark.parametrize("text,action", [
    ("aumente o volume", "volume_up"), ("abaixa o volume", "volume_down"), ("próxima música", "next"),
    ("música anterior", "previous"), ("pausa a música", "play_pause"), ("deixa no mudo", "mute"),
    ("turn the volume up", "volume_up"),
])
def test_media_action(text, action):
    assert media_action(normalize(text).text) == action


def test_dictation_is_verbatim_and_composition_is_refused(services):
    spec = load_all().get("type_text")
    for request, expected in (("digite olá, tudo bem com você?", "olá, tudo bem com você?"),
                              ("abra o bloco de notas e digite olá mundo", "olá mundo"),
                              ("escreva comprar pão e leite", "comprar pão e leite"),
                              ("abra o bloco de notas e digite Olá! Este texto foi digitado pela NEXUS.",
                               "Olá! Este texto foi digitado pela NEXUS."),
                              ("type hello world", "hello world")):
        services.session.request = request
        assert spec.repair({"text": "fragment"}, normalize(request))["text"] == expected
    services.session.request = "escreve um poema pra mim"
    assert spec.repair({"text": "poema"}, normalize("escreve um poema pra mim"))["text"] == ""


_TYPING_TARGET = r'''
import sys
from PySide6.QtWidgets import QApplication, QLineEdit
app = QApplication([])
box = QLineEdit(); box.setWindowTitle("NEXUS typing target"); box.resize(500, 60); box.show()
box.activateWindow()
app.aboutToQuit.connect(lambda: open(sys.argv[1], "w", encoding="utf-8").write(box.text()))
app.exec()
'''


@pytest.mark.hardware
def test_real_typing_into_another_application(sandbox):
    from app.tools import windows

    out = sandbox / "typed.txt"
    script = sandbox / "target.py"
    script.write_text(_TYPING_TARGET, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), str(out)])
    try:
        hwnd = None
        deadline = time.time() + 15
        while time.time() < deadline and hwnd is None:
            hwnd = next((h for h in windows.top_level_windows() if windows.title(h) == "NEXUS typing target"), None)
            time.sleep(0.2)
        assert hwnd, "target window did not appear"
        assert not windows.unsafe_target(hwnd)
        assert windows.focus(hwnd)
        text = "Olá! NEXUS digitou: açúcar, CAFÉ e pão — 123."
        windows.type_text(text)
        time.sleep(0.5)
        windows.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        proc.wait(timeout=10)
        assert out.read_text(encoding="utf-8") == text
    finally:
        if proc.poll() is None:
            proc.kill()


_EDIT_TARGET = r'''
import ctypes, sys
from ctypes import wintypes
u = ctypes.windll.user32
u.CreateWindowExW.restype = wintypes.HWND
u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                              wintypes.HINSTANCE, wintypes.LPVOID]
# a plain top-level EDIT: the same kind of control classic Notepad / WordPad use
edit = u.CreateWindowExW(0, "EDIT", "NEXUS edit target", 0x10CF0004 | 0x0004 | 0x0040, 100, 100, 500, 300,
                         None, None, None, None)
msg = wintypes.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
'''


@pytest.mark.hardware
def test_text_goes_straight_into_standard_edit_controls(sandbox):
    """Windows 11 Notepad garbled injected keystrokes when busy ("profundo" -> "rrofundo");
    text for standard (Rich)Edit controls is inserted with EM_REPLACESEL instead."""
    from app.tools import windows

    script = sandbox / "edit_target.py"
    script.write_text(_EDIT_TARGET, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)])
    try:
        hwnd = None
        deadline = time.time() + 15
        while time.time() < deadline and hwnd is None:
            hwnd = next((h for h in windows.top_level_windows() if windows.title(h).startswith("NEXUS edit")), None)
            time.sleep(0.2)
        assert hwnd, "edit window did not appear"
        control = windows.text_control(hwnd)
        assert control == hwnd and windows.class_name(control) == "Edit"
        windows.caret_to_end(control)
        for word in ["O mar é um azul", " profundo e antigo,\n", "Açúcar, café — 123!"]:
            assert windows.insert_text(control, word)
        assert windows.control_text(control).endswith("O mar é um azul profundo e antigo,\r\nAçúcar, café — 123!")
    finally:
        proc.kill()


def test_terminals_are_never_typing_targets():
    from app.tools import windows

    assert "cmd.exe" in windows.TERMINAL_PROCESSES and "powershell.exe" in windows.TERMINAL_PROCESSES
    assert "ConsoleWindowClass" in windows.TERMINAL_CLASSES
