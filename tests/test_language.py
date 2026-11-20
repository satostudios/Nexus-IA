"""Portuguese understanding: normaliser, mapping back, parsers, messages."""

import datetime

import pytest

from app.ai.language import is_portuguese, normalize
from app.core.assistant import is_exit_phrase
from app.core.pipeline import parse_yes_no
from app.i18n import clock, set_language, tr


@pytest.mark.parametrize("text,expected", [
    ("Abra o bloco de notas.", "open the notepad."),
    ("abra o bloco de notas e digite olá mundo", "open the notepad and type olá mundo"),
    ("pesquise receita de bolo de cenoura no google", "search receita de bolo de cenoura on google"),
    ("que horas são", "what time is it"),
    ("não abra o chrome", "don't open the chrome"),
    ("exclua esta pasta", "delete this folder"),
    ("renomeie lista.txt para compras.txt", "rename lista.txt to compras.txt"),
    ("copie relatorio.pdf para a área de trabalho", "copy relatorio.pdf to the desktop"),
    ("próxima música", "next track"),
    ("entra no github.com", "go to github.com"),
])
def test_normaliser_keeps_content_and_translates_commands(text, expected):
    n = normalize(text)
    assert n.portuguese and n.text == expected


def test_english_is_left_alone():
    for text in ("open chrome", "take a screenshot", "what time is it", "delete old.txt"):
        n = normalize(text)
        assert not n.portuguese and n.text == text


def test_paths_and_names_survive():
    n = normalize("meu projeto de faculdade é D:\\Faculdade\\TCC")
    assert "D:\\Faculdade\\TCC" in n.text


def test_arguments_map_back_to_the_users_words():
    n = normalize("pesquisa como ir para o centro de BH")
    assert n.to_original("como ir to the centro de BH") == "como ir para o centro de BH"
    n = normalize("abra o bloco de notas")
    assert n.to_original("notepad") == "bloco de notas"


def test_language_detection():
    assert is_portuguese("abre o chrome") and is_portuguese("que horas são")
    assert not is_portuguese("open chrome") and not is_portuguese("take a screenshot")


def test_yes_no_in_portuguese():
    for yes in ("sim", "Sim.", "pode", "pode sim", "confirma", "claro", "manda ver", "isso mesmo"):
        assert parse_yes_no(yes) is True, yes
    for no in ("não", "nao", "cancela", "deixa pra lá", "esquece", "negativo"):
        assert parse_yes_no(no) is False, no
    assert parse_yes_no("abra o chrome") is None


def test_exit_phrases_end_voice_mode():
    for text in ("parar", "Pare.", "sair", "tchau", "tchau, obrigado", "pode parar", "é só isso", "stop",
                 "ok, pode parar"):
        assert is_exit_phrase(text), text
    for text in ("pare a música", "abra o chrome", "obrigado"):
        assert not is_exit_phrase(text), text


def test_messages_and_clock_in_portuguese():
    set_language("pt")
    assert tr("I can't do that yet.") == "Ainda não consigo fazer isso."
    assert tr("{name} opened.", name="Chrome") == "Abrindo Chrome."
    t, d = clock(datetime.datetime(2026, 9, 25, 9, 5))
    assert t == "9h05" and d == "sexta-feira, 25 de setembro"
    set_language("en")
    assert tr("{name} opened.", name="Chrome") == "Chrome opened."
