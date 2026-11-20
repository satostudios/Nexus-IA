"""Measure how requests are routed through the real pipeline (nothing is executed).

For each request: the text Needle actually sees (Portuguese is normalised), the
calls Needle proposes, and the final tool + arguments after mapping back to
the user's words, repair, the command-surface gate and the confidence policy.

    python -m scripts.probe_routing                 # built-in English + Portuguese sets
    python -m scripts.probe_routing pt              # Portuguese set only
    python -m scripts.probe_routing "abra o chrome" # specific requests
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import paths  # noqa: E402
from app.ai.model_assets import ModelAssets  # noqa: E402
from app.ai.needle_engine import NeedleEngine  # noqa: E402
from app.config.settings import Settings  # noqa: E402
from app.core.pipeline import CommandProcessor  # noqa: E402
from app.i18n import set_language  # noqa: E402
from app.tools import load_all  # noqa: E402

# (request, expected tool(s) joined by "+", or "-" for "must not act")
EN: list[tuple[str, str]] = [
    ("Open Notepad.", "open_application"), ("Open Chrome", "open_application"),
    ("open visual studio code", "open_application"), ("launch discord", "open_application"),
    ("start photoshop", "open_application"), ("open file explorer", "open_application"),
    ("open the calculator", "open_application"), ("fire up spotify", "open_application"),
    ("close chrome", "close_application"), ("quit notepad", "close_application"),
    ("go to youtube.com", "open_url"), ("open github.com", "open_url"),
    ("search for carrot cake recipes on google", "search_web"), ("look up the dollar exchange rate", "search_web"),
    ("open README.md", "open_file"), ("open my downloads folder", "open_folder"),
    ("find files named invoice", "search_files"), ("where is my resume", "search_files"),
    ("create a file called notes.txt on the desktop", "create_file"),
    ("make a new folder called Projects", "create_folder"),
    ("rename notes.txt to todo.txt", "rename_file"), ("copy report.pdf to the desktop", "copy_file"),
    ("move photo.jpg to pictures", "move_file"), ("delete old.txt", "delete_file"),
    ("Take a screenshot.", "take_screenshot"), ("what time is it", "get_system_time"),
    ("how much ram do I have", "get_system_information"), ("lock the computer", "lock_windows"),
    ("Shutdown the computer.", "shutdown_windows"), ("restart the computer", "restart_windows"),
    ("type hello world", "type_text"), ("press enter", "press_keys"), ("save the file", "press_keys"),
    ("turn the volume up", "media_control"), ("pause the music", "media_control"), ("next track", "media_control"),
    ("My coding project is D:\\Projects\\Nexus", "remember_location"),
    ("open notepad and type hello world", "open_application+type_text"),
    ("Write me a poem.", "write_text"), ("write an email to my boss asking for a day off", "write_text"),
    ("create a python script that renames my photos by date", "write_code"),
    ("open vs code and write a snake game in python", "write_code"),
    ("make a website for my bakery", "write_code"), ("add a dark mode button to the app", "edit_code"),
    ("run the program", "run_program"),
    ("tell me a joke", "-"), ("hello", "-"), ("don't open chrome", "-"),
]
PT: list[tuple[str, str]] = [
    ("Abra o bloco de notas.", "open_application"), ("abre o chrome", "open_application"),
    ("abrir o navegador", "open_application"), ("inicie o spotify", "open_application"),
    ("abra a calculadora", "open_application"), ("feche o chrome", "close_application"),
    ("fecha o bloco de notas", "close_application"),
    ("pesquise receita de bolo de cenoura no google", "search_web"),
    ("pesquisa no youtube música do Legião Urbana", "search_web"),
    ("procura na internet o preço do dólar hoje", "search_web"), ("busca no google como fazer pão", "search_web"),
    ("como está o tempo hoje", "search_web"),
    ("abra o site youtube.com", "open_url"), ("entra no github.com", "open_url"),
    ("abra o arquivo relatorio.pdf", "open_file"), ("abra minha pasta de downloads", "open_folder"),
    ("abre a área de trabalho", "open_folder"),
    ("procure arquivos chamados fatura", "search_files"), ("onde está meu currículo", "search_files"),
    ("crie um arquivo chamado lista.txt", "create_file"), ("crie uma pasta chamada Projetos", "create_folder"),
    ("renomeie lista.txt para compras.txt", "rename_file"),
    ("copie relatorio.pdf para a área de trabalho", "copy_file"), ("mova foto.jpg para imagens", "move_file"),
    ("apague o arquivo velho.txt", "delete_file"), ("exclua esta pasta", "delete_file"),
    ("tire um print da tela", "take_screenshot"), ("faz uma captura de tela", "take_screenshot"),
    ("que horas são", "get_system_time"), ("que dia é hoje", "get_system_time"),
    ("quanta memória ram eu tenho", "get_system_information"), ("bloqueie o computador", "lock_windows"),
    ("desligue o computador", "shutdown_windows"), ("reinicie o computador", "restart_windows"),
    ("digite olá, tudo bem com você?", "type_text"), ("escreva comprar pão e leite", "type_text"),
    ("aperte enter", "press_keys"), ("salve o arquivo", "press_keys"),
    ("aumente o volume", "media_control"), ("pausa a música", "media_control"),
    ("próxima música", "media_control"), ("deixa no mudo", "media_control"),
    ("meu projeto de faculdade é D:\\Faculdade\\TCC", "remember_location"),
    ("abra o bloco de notas e digite olá mundo", "open_application+type_text"),
    ("escreva um poema sobre o mar", "write_text"), ("escreve um email pro meu chefe pedindo folga", "write_text"),
    ("abra o bloco de notas e escreva uma história de terror curta", "write_text"),
    ("faça uma lista de compras para um churrasco", "write_text"),
    ("abra o antigravity e escreva um código em .py pra criar um app de economia", "write_code"),
    ("crie um jogo da velha em python", "write_code"), ("crie um site para a minha padaria", "write_code"),
    ("escreva um programa em python que calcula juros compostos", "write_code"),
    ("adicione um gráfico de gastos no app", "edit_code"), ("rode o programa", "run_program"),
    ("me conta uma piada", "-"), ("oi", "-"), ("não abra o chrome", "-"), ("qual é o sentido da vida", "-"),
]


def main() -> int:
    args = sys.argv[1:]
    settings = Settings.load()
    registry = load_all()
    engine = NeedleEngine(settings, registry)
    assets = ModelAssets(paths.models_dir(), lambda label, frac: print(f"  .. {label}", flush=True))
    print("loading:", engine.submit(engine.load, assets).result())
    if args == ["pt"]:
        cases = PT
    elif args == ["en"]:
        cases = EN
    elif args:
        cases = [(q, "?") for q in args]
    else:
        cases = EN + PT
    ok = total = 0
    for text, expected in cases:
        set_language("pt" if (text, expected) in PT else "en")
        processor = CommandProcessor(engine, registry, settings, dry_run=True)
        started = time.perf_counter()
        out = engine.submit(processor.handle, text).result()
        ms = (time.perf_counter() - started) * 1000
        acted = [r for r in out.records if r.route.startswith("dry-run") and not r.route.endswith("refuse")]
        got = "+".join(r.name for r in acted) or "-"
        good = expected == "?" or got == expected or (expected != "-" and got.split("+")[0] == expected.split("+")[0]
                                                      and set(expected.split("+")) <= set(got.split("+")))
        if expected != "?":
            total += 1
            ok += good
        mark = "" if expected == "?" else ("OK " if good else "XX ")
        norm = out.needle_input if out.needle_input != text else None
        conf = out.records[0].confidence if out.records else None
        print(f"{mark}{ms:5.0f}ms conf={conf if conf is None else f'{conf:.2f}'} {text!r} [{expected}]")
        for r in out.records:
            print(f"      {r.name}({json.dumps(r.arguments, ensure_ascii=False)}) -> {r.route}")
        if norm:
            print(f"      needle saw: {norm!r}")
    if total:
        print(f"\n{ok}/{total} routed as expected")
    engine.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
