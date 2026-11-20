"""Portuguese command understanding on top of an English-trained Needle.

Needle 3 is trained on English commands. Measured on 48 Brazilian-Portuguese
requests it still picked the right tool most of the time, but its *arguments*
were unreliable (verbs leaked into search queries, "aumente o volume" became
volume_down, "imagens" became "magens", dictated text was split into
fragments).

So NEXUS rewrites only the *command words* of a Portuguese request into
English ("abra o bloco de notas e digite olá mundo" -> "open the notepad and
type olá mundo"), leaving the content words untouched, and remembers where
every rewritten token came from. Needle routes the normalised text; each
argument it extracts is then mapped back onto the user's original words, so a
search query or dictated sentence reaches the tool exactly as spoken.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# URLs, Windows paths, dotted names ("lista.txt", "github.com") and bare
# extensions ("em .py") stay single tokens.
_TOKEN = re.compile(r"https?://\S+|[A-Za-z]:\\[^\s\"']*|\w+(?:[-'.]\w+)*|\.\w+|\S")


def fold(text: str) -> str:
    """Lower-case and strip accents: 'Área' -> 'area'."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


# Phrase rules: folded Portuguese token sequence -> English replacement.
# Longest match wins. Content words are never listed here.
_PHRASES: list[tuple[str, str]] = [
    # questions / whole commands
    ("o que voce consegue fazer", "what can you do"), ("o que voce sabe fazer", "what can you do"),
    ("o que voce pode fazer", "what can you do"), ("o que voce faz", "what can you do"),
    ("quais sao seus comandos", "what are your commands"), ("quais sao os comandos", "what are your commands"),
    ("quais comandos", "what are your commands"), ("como eu uso voce", "how do i use you"),
    ("me ajuda", "help"), ("me ajude", "help"), ("ajuda", "help"),
    ("que horas sao", "what time is it"), ("que hora e", "what time is it"), ("que horas e", "what time is it"),
    ("que dia e hoje", "what day is today"), ("que dia e", "what day is"), ("qual a data de hoje", "what is the date today"),
    ("qual e a data", "what is the date"), ("como esta o tempo", "what is the weather"), ("como vai estar o tempo", "what is the weather"),
    ("previsao do tempo", "weather forecast"), ("vai chover", "weather forecast"),
    ("captura de tela", "screenshot"), ("print da tela", "screenshot"), ("print de tela", "screenshot"),
    ("printscreen", "screenshot"), ("print screen", "screenshot"), ("um print", "a screenshot"), ("print", "screenshot"),
    ("informacoes do sistema", "system information"), ("informacao do sistema", "system information"),
    ("informacoes do computador", "system information"), ("informacoes do pc", "system information"),
    ("dados do sistema", "system information"), ("configuracao do computador", "system information"),
    ("memoria ram", "ram memory"), ("espaco em disco", "disk space"),
    # places and apps
    ("area de trabalho", "desktop"), ("bloco de notas", "notepad"), ("bloco de nota", "notepad"),
    ("explorador de arquivos", "file explorer"), ("gerenciador de tarefas", "task manager"),
    ("prompt de comando", "command prompt"), ("painel de controle", "control panel"),
    ("navegador de internet", "browser"), ("navegador", "browser"),
    # web
    ("na internet", "on the web"), ("na web", "on the web"), ("no google", "on google"), ("no youtube", "on youtube"),
    ("na wikipedia", "on wikipedia"), ("no mapa", "on maps"), ("no maps", "on maps"), ("no google maps", "on maps"),
    # media
    ("proxima musica", "next track"), ("proxima faixa", "next track"), ("pula a musica", "next track"),
    ("pular a musica", "next track"), ("pula essa musica", "next track"), ("passa a musica", "next track"),
    ("musica anterior", "previous track"), ("faixa anterior", "previous track"), ("volta a musica", "previous track"),
    ("tira do mudo", "unmute"), ("no mudo", "mute"), ("mais alto", "louder"), ("mais baixo", "quieter"),
    # keyboard
    ("selecione tudo", "select all"), ("seleciona tudo", "select all"), ("selecionar tudo", "select all"),
    ("nova aba", "new tab"), ("nova guia", "new tab"),
    # navigation
    ("entra no", "go to"), ("entre no", "go to"), ("entrar no", "go to"), ("entra na", "go to"), ("entre na", "go to"),
    ("acesse o", "go to"), ("acessa o", "go to"), ("acesse a", "go to"), ("vai para o", "go to"), ("va para o", "go to"),
    ("vai pro", "go to"), ("va pro", "go to"),
    # connectors
    ("e depois", "and then"), ("e em seguida", "and then"), ("em seguida", "then"), ("e entao", "and then"),
    ("depois disso", "then"), ("chamado", "called"), ("chamada", "called"), ("chamados", "called"),
    ("chamadas", "called"), ("com o nome", "called"), ("com nome", "called"), ("de nome", "called"),
    ("onde esta", "where is"), ("onde fica", "where is"), ("onde estao", "where are"), ("quer dizer", "means"),
    ("por favor", "please"), ("pra mim", "for me"), ("para mim", "for me"),
]

# Single words (folded). Imperative, infinitive and indicative forms people use.
_WORDS: dict[str, str] = {}


def _add(english: str, *forms: str) -> None:
    for f in forms:
        _WORDS[f] = english


_add("open", "abra", "abre", "abrir", "abri", "abrindo", "inicie", "inicia", "iniciar")
_add("run", "execute", "executa", "executar", "rode", "roda", "rodar")
_add("test", "teste", "testa", "testar")
# "fecho" is how Whisper often hears a quick "feche o …"
_add("close", "feche", "fecha", "fechar", "fecho", "encerre", "encerra", "encerrar")
_add("search", "pesquise", "pesquisa", "pesquisar", "busque", "busca", "buscar", "procure", "procura", "procurar",
     "googla", "googlar", "googlear")
_add("find", "encontre", "encontra", "encontrar", "ache", "acha", "achar", "localize", "localiza", "localizar")
_add("go to", "entre", "entra", "entrar", "acesse", "acessa", "acessar", "visite", "visita")
_add("delete", "apague", "apaga", "apagar", "exclua", "exclui", "excluir", "delete", "deleta", "deletar",
     "remova", "remove", "remover")
_add("rename", "renomeie", "renomeia", "renomear")
_add("copy", "copie", "copia", "copiar")
_add("move", "mova", "move", "mover")
_add("create", "crie", "cria", "criar")
_add("make", "faca", "faz", "fazer")
_add("take", "tire", "tira", "tirar", "bata", "bate", "bater")
_add("type", "digite", "digita", "digitar", "anote", "anota", "anotar")
# "escreva" is dictation ("escreva comprar pão") or composition ("escreva um poema"):
# English "write" keeps both readings; the tools' triggers tell them apart.
_add("write", "escreva", "escreve", "escrever", "redija", "redige", "redigir", "componha", "compoe", "compor")
_add("develop", "desenvolva", "desenvolve", "desenvolver")
_add("build", "construa", "constroi", "construir", "monte", "monta", "montar")
_add("generate", "gere", "gera", "gerar")
_add("program", "programe", "programa", "programar", "programinha")
_add("add", "adicione", "adiciona", "adicionar", "acrescente", "acrescenta", "acrescentar", "inclua", "inclui",
     "incluir")
_add("put", "coloque", "coloca", "colocar", "bote", "bota", "botar")
_add("change", "mude", "muda", "mudar", "altere", "altera", "alterar", "troque", "troca", "trocar", "modifique",
     "modifica", "modificar")
_add("fix", "corrija", "corrige", "corrigir", "conserte", "conserta", "consertar", "arrume", "arruma", "arrumar")
_add("improve", "melhore", "melhora", "melhorar")
_add("press", "aperte", "aperta", "apertar", "pressione", "pressiona", "pressionar", "tecle", "tecla", "teclar")
_add("save", "salve", "salva", "salvar")
_add("undo", "desfaca", "desfaz", "desfazer")
_add("redo", "refaca", "refaz", "refazer")
_add("paste", "cole", "cola", "colar")
_add("refresh", "atualize", "atualiza", "atualizar", "recarregue", "recarrega", "recarregar")
_add("lock", "bloqueie", "bloqueia", "bloquear", "trave", "trava", "travar")
_add("shut down", "desligue", "desliga", "desligar")
_add("restart", "reinicie", "reinicia", "reiniciar")
_add("increase", "aumente", "aumenta", "aumentar", "suba", "sobe", "subir")
_add("decrease", "diminua", "diminui", "diminuir", "abaixe", "abaixa", "abaixar", "reduza", "reduz", "reduzir")
_add("pause", "pause", "pausa", "pausar", "pare", "parar")
_add("play", "toque", "toca", "tocar", "continue", "continua", "continuar", "retome", "retoma")
_add("mute", "silencie", "silencia", "silenciar", "mudo", "muta", "mutar")
_add("remember", "lembre", "lembra", "lembrar", "memorize", "memoriza", "memorizar", "guarde", "guarda", "guardar")
_add("forget", "esqueca", "esquece", "esquecer")
_add("show", "mostre", "mostra", "mostrar", "exiba", "exibe", "exibir")
_add("means", "significa")
# nouns
_add("file", "arquivo")
_add("files", "arquivos")
_add("folder", "pasta", "diretorio")
_add("folders", "pastas", "diretorios")
_add("document", "documento")
_add("documents", "documentos")
_add("pictures", "imagens", "fotos")
_add("music", "musica", "musicas")
_add("videos", "videos")
_add("computer", "computador", "pc", "notebook", "maquina")
_add("calculator", "calculadora")
_add("settings", "configuracoes")
_add("screen", "tela")
_add("website", "site")
_add("page", "pagina")
_add("time", "hora", "horas", "horario")
_add("date", "data")
_add("today", "hoje")
_add("battery", "bateria")
_add("key", "tecla")
_add("shortcut", "atalho")
_add("text", "texto")
_add("project", "projeto")
_add("tab", "aba", "guia")
_add("window", "janela")
_add("volume", "volume")
_add("weather", "clima")
# things NEXUS can write
_add("code", "codigo", "codigos")
_add("app", "aplicativo", "aplicativos", "aplicacao", "app", "apps")
_add("game", "jogo", "jogos", "joguinho")
_add("system", "sistema")
_add("button", "botao", "botoes")
_add("chart", "grafico", "graficos")
_add("color", "cor", "cores")
_add("poem", "poema", "poemas", "poesia")
_add("story", "historia", "historinha", "conto")
_add("essay", "redacao", "dissertacao")
_add("letter", "carta")
_add("message", "mensagem", "recado")
_add("article", "artigo")
_add("summary", "resumo")
_add("song", "cancao")
_add("speech", "discurso")
_add("list", "lista")
_add("joke", "piada")
_add("email", "email", "e-mail")
_add("about", "sobre")
# function words
_add("the", "o", "a", "os", "as")
_add("a", "um", "uma")
_add("my", "meu", "minha", "meus", "minhas")
_add("to", "para", "pra", "pro")
_add("in", "em", "no", "na", "nos", "nas", "dentro")
_add("this", "esta", "este", "essa", "esse", "isto", "isso", "aquela", "aquele", "aquilo")
_add("it", "ele", "ela")
_add("and", "e")
_add("don't", "nao")

# Accented forms whose meaning differs from their unaccented spelling
# ("e" = and / "é" = is, "esta" = this / "está" = is).
_ACCENTED = {"é": "is", "está": "is", "estão": "are", "não": "don't", "você": "you"}

_PT_MARKERS = set(_WORDS) | {w for p, _ in _PHRASES for w in p.split()} | {"que", "de", "do", "da", "dos", "das", "com",
                                                                           "um", "uma", "voce", "sobre"}
_EN_MARKERS = {"the", "open", "close", "search", "find", "delete", "please", "what", "is", "my", "take", "type",
               "show", "go", "turn", "lock", "restart", "shut", "create", "make", "press", "copy", "move", "rename",
               "write", "run", "test", "build", "about", "an", "for", "with", "that", "and", "to", "of"}
# words spelled the same in both languages never make a request Portuguese on their own
_PT_ONLY = _PT_MARKERS - _EN_MARKERS - {"e", "a", "o", "print", "delete", "remove", "move", "pause", "volume", "pc",
                                         "videos", "site", "data", "execute", "app", "apps", "email", "e-mail", "me"}


def is_portuguese(text: str) -> bool:
    """Heuristic: does this request look Portuguese?"""
    if re.search(r"[ãõçáéíóúâêô]", text.lower()):
        return True
    words = [fold(w) for w in re.findall(r"[A-Za-zÀ-ÿ]+", text)]
    pt = sum(1 for w in words if w in _PT_ONLY)
    en = sum(1 for w in words if w in _EN_MARKERS)
    return pt > 0 and pt >= en


@dataclass
class Normalized:
    original: str
    text: str
    tokens: list[str] = field(default_factory=list)
    spans: list[tuple[int, int]] = field(default_factory=list)   # original char span per normalised token
    portuguese: bool = False

    def to_original(self, value: str) -> str:
        """Map an argument Needle extracted from ``text`` back onto the user's words."""
        if not self.portuguese or not value or not value.strip():
            return value
        if value in self.original:
            return value
        target = [t.lower() for t in _TOKEN.findall(value)]
        if not target:
            return value
        lowered = [t.lower() for t in self.tokens]
        n = len(target)
        best: tuple[int, int] | None = None
        for i in range(len(lowered) - n + 1):
            if lowered[i:i + n] == target:
                best = (i, i + n - 1)
                break
        if best is None:
            # Needle may drop or add a small word; accept the longest run of matches.
            best_len = 0
            for i in range(len(lowered)):
                k = 0
                while i + k < len(lowered) and k < n and lowered[i + k] == target[k]:
                    k += 1
                if k > best_len:
                    best_len, best = k, (i, i + k - 1)
            if best is None or best_len < max(1, n - 1):
                return value
        start = self.spans[best[0]][0]
        end = self.spans[best[1]][1]
        return self.original[start:end].strip(" .,!?;:")


def normalize(text: str, force: bool | None = None) -> Normalized:
    """English command skeleton for Needle, with a map back to ``text``."""
    portuguese = is_portuguese(text) if force is None else force
    if not portuguese:
        toks = [(m.group(0), m.span()) for m in _TOKEN.finditer(text)]
        return Normalized(text, text, [t for t, _ in toks], [s for _, s in toks], False)
    raw = [(m.group(0), m.span()) for m in _TOKEN.finditer(text)]
    folded = [fold(t) for t, _ in raw]
    phrase_rules = sorted(((p.split(), e.split()) for p, e in _PHRASES), key=lambda r: -len(r[0]))
    out_tokens: list[str] = []
    out_spans: list[tuple[int, int]] = []
    i = 0
    while i < len(raw):
        matched = False
        for pattern, english in phrase_rules:
            n = len(pattern)
            if folded[i:i + n] == pattern:
                span = (raw[i][1][0], raw[i + n - 1][1][1])
                out_tokens.extend(english)
                out_spans.extend([span] * len(english))
                i += n
                matched = True
                break
        if matched:
            continue
        token, span = raw[i]
        low = token.lower()
        english = _ACCENTED.get(low) or _WORDS.get(folded[i])
        if english is not None and not re.match(r"^[A-Za-z]:\\", token):
            parts = english.split()
            out_tokens.extend(parts)
            out_spans.extend([span] * len(parts))
        else:
            out_tokens.append(token)
            out_spans.append(span)
        i += 1
    rendered = _render(out_tokens)
    return Normalized(text, rendered, out_tokens, out_spans, True)


def _render(tokens: list[str]) -> str:
    out = ""
    for tok in tokens:
        if out and not re.match(r"^[.,!?;:)]$", tok) and not out.endswith(("(", " ")):
            out += " "
        out += tok
    return out.strip()
