"""Portuguese requests through the real Needle 3 engine (no mocked AI)."""

import pytest

from app.config.settings import Settings
from app.core.events import OutcomeKind
from app.core.pipeline import CommandProcessor
from app.i18n import set_language
from app.tools import load_all

pytestmark = pytest.mark.needle


@pytest.fixture()
def pt():
    set_language("pt")
    yield
    set_language("en")


@pytest.fixture()
def processor(engine, services, pt):
    return CommandProcessor(engine, load_all(), Settings(), services.memory)


def run(engine, fn, *args):
    return engine.submit(fn, *args).result()


def test_time_in_portuguese(engine, processor):
    out = run(engine, processor.handle, "que horas são")
    assert out.kind is OutcomeKind.DONE and out.message.startswith("Agora são")
    assert out.needle_input == "what time is it"


@pytest.mark.parametrize("text,tool", [
    ("escreve um poema pra mim", "write_text"), ("escreva um poema sobre o mar", "write_text"),
    ("abra o bloco de notas e escreva uma história de terror curta", "write_text"),
    ("abra o antigravity e escreva um código em .py pra criar um app de economia", "write_code"),
    ("crie um jogo da velha em python", "write_code"),
])
def test_creation_requests_in_portuguese(engine, services, pt, text, tool):
    out = run(engine, CommandProcessor(engine, load_all(), Settings(), services.memory, dry_run=True).handle, text)
    acted = [r.name for r in out.records if r.route.startswith("dry-run")]
    assert acted == [tool], [(r.name, r.route) for r in out.records]
    assert out.records[-1].arguments["request"] == text


@pytest.mark.parametrize("text", ["me conta uma piada", "qual é o sentido da vida", "não abra o chrome"])
def test_unsupported_in_portuguese(engine, processor, text):
    out = run(engine, processor.handle, text)
    assert out.kind is OutcomeKind.UNSUPPORTED and out.message == "Ainda não consigo fazer isso."
    assert all(r.result is None for r in out.records)


def test_shutdown_confirmation_in_portuguese(engine, processor, power_calls):
    out = run(engine, processor.handle, "desligue o computador")
    assert out.kind is OutcomeKind.CONFIRM and out.confirmation.title == "Desligar o computador?"
    assert power_calls == []
    out = run(engine, processor.handle, "sim")
    assert out.kind is OutcomeKind.DONE and power_calls == [["/s", "/t", "5"]]
    assert out.message == "Desligando em cinco segundos."


def test_clarify_and_confirm_delete_in_portuguese(engine, processor, services, sandbox):
    (sandbox / "a").mkdir()
    (sandbox / "b").mkdir()
    (sandbox / "a" / "plano.txt").write_text("1")
    (sandbox / "b" / "plano.txt").write_text("2")
    services.files.build()
    out = run(engine, processor.handle, "apague o arquivo plano.txt")
    assert out.kind is OutcomeKind.CLARIFY and out.message.startswith("Qual arquivo")
    out = run(engine, processor.handle, "o segundo")
    assert out.kind is OutcomeKind.CONFIRM and out.confirmation.title == "Excluir arquivo?"
    run(engine, processor.resolve_pending, False)
    assert (sandbox / "a" / "plano.txt").exists() and (sandbox / "b" / "plano.txt").exists()


def test_create_file_in_portuguese(engine, processor, sandbox):
    out = run(engine, processor.handle, f"crie um arquivo chamado lista.txt em {sandbox}")
    assert out.kind is OutcomeKind.DONE and (sandbox / "lista.txt").exists(), out
    assert out.message.startswith("Criei lista.txt")


@pytest.mark.parametrize("text,tool,args", [
    ("pesquise receita de bolo de cenoura no google", "search_web", {"query": "receita de bolo de cenoura"}),
    ("pesquisa no youtube música do Legião Urbana", "search_web", {"query": "música do Legião Urbana", "site": "youtube"}),
    ("abra o bloco de notas e digite olá mundo", "type_text", {"text": "olá mundo"}),
    ("aumente o volume", "media_control", {"action": "volume_up"}),
    ("salve o arquivo", "press_keys", {"keys": "ctrl+S"}),
    ("abrir o navegador", "open_application", {"application": "navegador"}),
])
def test_routing_and_arguments_in_portuguese(engine, services, pt, text, tool, args):
    dry = CommandProcessor(engine, load_all(), Settings(), services.memory, dry_run=True)
    out = run(engine, dry.handle, text)
    record = next((r for r in out.records if r.name == tool), None)
    assert record is not None, [(r.name, r.arguments, r.route) for r in out.records]
    assert record.route.startswith("dry-run")
    for key, value in args.items():
        assert record.arguments.get(key) == value, (key, record.arguments)
