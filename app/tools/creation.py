"""Creation tools: write_code, edit_code, write_text, run_program.

Needle routes the request here; the words themselves are written by the local
writing model (app/ai/writer.py), in the background, while NEXUS keeps
listening:

* write_code writes ``Documents\\NEXUS\\Projetos\\<name>\\<name>.py`` (or .html,
  .js, ...) live, in the editor the user named ("abra o antigravity e escreva um
  código...") or the first code editor installed. Python is then compiled and,
  if it has a syntax error, rewritten once with the error message;
* edit_code rewrites the last program with a requested change (the previous
  version is kept next to it as ``<name>.anterior.py``);
* write_text types the text live into the app the user named. Notepad is the
  default: a new file in ``Documents\\NEXUS\\Textos``, saved at the end;
* run_program runs the last program, after the user confirms.

"cancela" / "para de escrever" (or Esc) stops a job; what was written so far
stays in the file. Only one writing job runs at a time.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator, Optional

from app.ai.language import fold
from app.i18n import is_pt, tr
from app.tools import intents, windows
from app.tools.context import require_jobs, require_settings, require_writer, services
from app.tools.registry import Preflight, Safety, ToolError, fail, nexus_tool, ok

if TYPE_CHECKING:
    from app.core.jobs import JobContext, JobResult
    from app.tools.app_resolver import AppEntry

log = logging.getLogger("nexus.tools.create")

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008

CODE_MAX_TOKENS = 7000
TEXT_MAX_TOKENS = 1800


# ============================================================== languages
@dataclass(frozen=True)
class CodeLang:
    key: str
    label: str
    ext: str


PYTHON = CodeLang("python", "Python", ".py")
HTML = CodeLang("html", "HTML", ".html")
_EXPLICIT: list[tuple[re.Pattern[str], CodeLang]] = [(re.compile(p, re.IGNORECASE), lang) for p, lang in [
    (r"\.py\b|\bpython\b", PYTHON),
    (r"\.html?\b|\bhtml\b", HTML),
    (r"\.tsx?\b|\btypescript\b", CodeLang("typescript", "TypeScript", ".ts")),
    (r"\.m?js\b|\bjavascript\b|\bnode(?:\.?js)?\b", CodeLang("javascript", "JavaScript", ".js")),
    (r"\.java\b|\bjava\b", CodeLang("java", "Java", ".java")),
    (r"\.cs\b|\bc#|\bc ?sharp\b", CodeLang("csharp", "C#", ".cs")),
    (r"\.cpp\b|\bc\+\+|\bcpp\b", CodeLang("cpp", "C++", ".cpp")),
    (r"\.rs\b|\brust\b", CodeLang("rust", "Rust", ".rs")),
    (r"\.go\b|\bgolang\b", CodeLang("go", "Go", ".go")),
    (r"\.php\b|\bphp\b", CodeLang("php", "PHP", ".php")),
    (r"\.ps1\b|\bpowershell\b", CodeLang("powershell", "PowerShell", ".ps1")),
    (r"\.sql\b|\bsql\b", CodeLang("sql", "SQL", ".sql")),
]]
_BY_EXT = {lang.ext: lang for _, lang in _EXPLICIT} | {".htm": HTML}
_WEB = re.compile(r"\b(site|website|p[aá]gina|web ?page|landing ?page|portf[oó]lio)\b", re.IGNORECASE)


def code_language(text: str) -> CodeLang:
    """The language the user asked for (the first one named); a website is HTML, anything else Python."""
    hits = [(m.start(), lang) for pattern, lang in _EXPLICIT if (m := pattern.search(text))]
    if hits:
        return min(hits, key=lambda h: h[0])[1]
    return HTML if _WEB.search(text) else PYTHON


# ================================================================= names
_NOUN = (r"app|aplicativo|aplica[çc][ãa]o|programa|programinha|jogo|joguinho|game|site|website|p[aá]gina|script|"
         r"sistema|bot|calculadora|ferramenta|gerenciador|controle|planilha|agenda|lista|cron[oô]metro|rel[oó]gio|"
         r"conversor|quiz|editor|simulador|dashboard|painel|api|servidor|tracker|program|application|tool|manager|"
         r"calculator|converter|timer|clock|poema|poesia|hist[oó]ria|historinha|conto|reda[çc][ãa]o|carta|e-?mail|"
         r"mensagem|texto|artigo|resumo|m[uú]sica|can[çc][ãa]o|discurso|piada|receita|roteiro|poem|story|essay|"
         r"letter|message|text|article|summary|song|speech|joke|recipe|list|c[óo]digo|code")
_GENERIC = {"app", "aplicativo", "aplicacao", "programa", "programinha", "script", "program", "application",
            "codigo", "code", "texto", "text", "sistema", "ferramenta", "tool"}
# "um app de economia", "a python expense tracker" (English puts modifiers first)
_SUBJECT = re.compile(rf"\b(?:um|uma|o|a|an?|the|meu|minha|my|some)\s+((?:[\w-]+\s+){{0,2}}?(?:{_NOUN})\b"
                      rf"[^,.;:!?\n]*)", re.IGNORECASE)
_IN_APP = re.compile(r"\s+(?:no|na|in|into|on|using|usando o|usando a)\s+(?:o\s+|a\s+|the\s+|my\s+|meu\s+)?"
                     r"(?:bloco de notas|notepad\+*|word|wordpad|antigravity(?: ide)?|vs ?code|visual studio code|"
                     r"cursor|windsurf|sublime(?: text)?|pycharm|notion|obsidian|google docs|onenote|outlook|gmail|"
                     r"whatsapp|editor)\b.*$", re.IGNORECASE)
_CLAUSE = re.compile(r"\s+(?:que|usando|using|with|com|that|which|onde|where)\s+(.*)$", re.IGNORECASE)
_TAIL = re.compile(r"\s+(?:e|and)\s+(?:depois|salv\w*|abr\w*|rod\w*|then|save|open|run)\b.*$", re.IGNORECASE)
_NAMED = re.compile(r"\b(?:chamad[oa]s?|com o nome(?: de)?|de nome|named|called)\s+[\"“']?([\w\-]+(?:\.\w{1,5})?)",
                    re.IGNORECASE)
_STOP = {"de", "da", "do", "das", "dos", "e", "o", "a", "os", "as", "um", "uma", "para", "pra", "pro", "of", "the",
         "for", "to", "and", "an", "meu", "minha", "my", "simples", "simple", "pequeno", "pequena", "small", "basico",
         "basica", "basic", "novo", "nova", "new", "em", "in", "no", "na", "nos", "nas", "com", "with", "sobre",
         "about", "que", "that", "py", "python", "html", "javascript", "js", "me", "mim", "bem", "muito", "um"}


def slugify(text: str, max_words: int = 4) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", fold(text)) if w not in _STOP]
    return "_".join(words[:max_words])


_ARTICLE = {"um": "o", "uma": "a", "o": "o", "a": "a", "meu": "o", "minha": "a"}
_WHERE = re.compile(r"\s+(?:em|in|no|na|pelo|pela|using|usando)\s+(?:o\s+|a\s+|the\s+)?\.?[\w+#]+\s*$",
                    re.IGNORECASE)


def _subject(text: str) -> tuple[str, str]:
    best = ("", "")
    for m in _SUBJECT.finditer(text):
        article = m.group(0).split()[0].lower()
        phrase = _IN_APP.sub("", _TAIL.sub("", m.group(1))).strip()
        head = fold(phrase.split()[0]) if phrase.split() else ""
        clause = _CLAUSE.search(phrase)
        core = phrase[:clause.start()] if clause else phrase
        while _WHERE.search(core):
            core = _WHERE.sub("", core)
        if head in _GENERIC and len(slugify(core).split("_")) <= 1:
            # "um programa que calcula juros" -> "calcula juros"; keep looking for a better noun phrase
            if not best[1]:
                best = ("", clause.group(1).strip() if clause else core.strip())
            continue
        return article, core.strip()
    return best


def subject_of(text: str) -> str:
    """What is being created: 'escreva um código em .py pra criar um app de economia' -> 'app de economia'."""
    return _subject(text)[1]


def what_of(text: str, fallback: str) -> str:
    """The subject with a definite article for replies: 'o app de economia', 'the expense tracker'."""
    article, subject = _subject(text)
    if not subject or len(subject) > 60:
        return fallback
    if is_pt():
        return f"{_ARTICLE[article]} {subject}" if article in _ARTICLE else subject
    return f"the {subject}" if article in ("a", "an", "the", "my", "some") else subject


def output_name(original: str, kind: str, default: str) -> str:
    named = _NAMED.search(original)
    if named:
        return named.group(1)
    return slugify(subject_of(original)) or default


def unique_path(folder: Path, stem: str, ext: str, own_folder: bool) -> Path:
    for i in range(1, 100):
        name = stem if i == 1 else f"{stem}_{i}"
        path = (folder / name / f"{name}{ext}") if own_folder else (folder / f"{name}{ext}")
        if not path.exists() and not (own_folder and path.parent.exists()):
            return path
    return folder / f"{stem}_{int(time.time())}{ext}"


# ================================================================ apps
_HINT = re.compile(r"\b(?:abr[aei]\w*|open|inici\w*|launch|no|na|in|into|com o|com a|usando o|usando a|using)\s+"
                   r"(?:o\s+|a\s+|the\s+|my\s+|meu\s+)?([A-Za-z][\w+#.\-]*(?:\s+[A-Za-z][\w+#.\-]*){0,2})",
                   re.IGNORECASE)
_HINT_CUT = re.compile(r"\s+(?:e|and|pra|para|to|que|that|then|depois|com|with|um|uma|an?|o|sobre|about)\b.*$",
                       re.IGNORECASE)
_NOT_APP = re.compile(r"^(python|javascript|typescript|html|css|java|go|rust|php|sql|powershell|portugu[eê]s|"
                      r"ingl[eê]s|english|portuguese|tkinter|pygame|flask|django|react|node|arquivo|pasta|file|"
                      r"folder|desktop|documentos|documents|internet|web|google|youtube|tela|screen|computador|pc|"
                      r"computer|mar|sea|brasil|brazil|mundo|world|estilo|style|formato|format)$", re.IGNORECASE)
_HERE = re.compile(r"\b(aqui|nessa janela|nesta janela|neste campo|nesse campo|here|this window|this field)\b",
                   re.IGNORECASE)
_EDITORS = ["Antigravity IDE", "Antigravity", "Visual Studio Code", "Cursor", "Windsurf", "Sublime Text", "Notepad++"]
_LIVE = re.compile(r"antigravity|visual studio code|vs ?code|vscodium|cursor|windsurf|sublime|zed|pycharm|intellij|"
                   r"webstorm", re.IGNORECASE)


def _resolver():  # noqa: ANN202
    from app.tools.app_resolver import AppResolver

    if services.apps is None:
        services.apps = AppResolver()
    return services.apps


def resolve_app(name: str | None) -> "AppEntry | None":
    name = (name or "").strip(" .,!?\"'")
    if len(name) < 3 or _NOT_APP.match(name):
        return None
    res = _resolver().resolve(name)
    return res.entry if res.entry is not None and res.score >= 0.88 else None


def app_hint(original: str) -> "AppEntry | None":
    """The app named in the request: 'abra o antigravity e ...', '... no bloco de notas'."""
    for m in _HINT.finditer(original):
        phrase = _HINT_CUT.sub("", m.group(1)).strip(" .,!?")
        entry = resolve_app(phrase)
        if entry is not None:
            return entry
    return None


def is_notepad(entry: "AppEntry | None") -> bool:
    if entry is None:
        return False
    name = fold(entry.name)
    return "++" not in name and (name in ("notepad", "bloco de notas") or "windowsnotepad" in entry.target.lower()
                                 or entry.target.lower().endswith("\\notepad.exe"))


def default_editor() -> "AppEntry | None":
    configured = require_settings().ai.code_editor
    for name in ([configured] if configured else []) + _EDITORS:
        entry = resolve_app(name)
        if entry is not None:
            return entry
    return resolve_app("notepad")


def _shortcut_target(name: str) -> str | None:
    """The .exe behind a Start-menu shortcut called ``name`` (Win32 apps listed by AppID)."""
    roots = [Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
             Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" /
             "Programs"]
    want = (name + ".lnk").lower()
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None
    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        for root in roots:
            if not root.is_dir():
                continue
            for lnk in root.rglob("*.lnk"):
                if lnk.name.lower() == want:
                    target = shell.CreateShortcut(str(lnk)).TargetPath
                    if target and target.lower().endswith(".exe") and os.path.isfile(target):
                        return target
    except Exception:
        log.debug("shortcut lookup failed for %s", name, exc_info=True)
    finally:
        pythoncom.CoUninitialize()
    return None


def executable_for(entry: "AppEntry") -> str | None:
    if is_notepad(entry):
        return "notepad.exe"
    if entry.kind == "path" and entry.target.lower().endswith(".exe") and os.path.isfile(entry.target):
        return entry.target
    if entry.kind == "appsfolder":
        return _shortcut_target(entry.name)
    return None


def open_with(entry: "AppEntry | None", path: Path) -> None:
    exe = executable_for(entry) if entry is not None else None
    if exe:
        subprocess.Popen([exe, str(path)], cwd=str(path.parent), close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        os.startfile(str(path))  # type: ignore[attr-defined]
    services.session.touch(path)


def _wait_window(match: Callable[[int], bool], timeout: float) -> int | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd in windows.top_level_windows():
            if match(hwnd) and not windows.unsafe_target(hwnd):
                return hwnd
        time.sleep(0.25)
    return None


# ============================================================== filters
_CODE_START = re.compile(r'^(import |from |#|//|/\*|"""|\'\'\'|<|def |class |@|const |let |var |function |async |'
                         r'package |using |public |namespace |fn |use |select |create |param|\$|if __name__|'
                         r'[A-Za-z_][\w.]*\s*[=(])', re.IGNORECASE)


class CodeFilter:
    """Turns the model's streamed reply into the file's content: drops a
    ```lang fence and any prose around it, line by line."""

    def __init__(self) -> None:
        self._buf = ""
        self.state = "start"   # start | prose | code | fenced | done
        self._held: list[str] = []
        self.emitted = 0

    def feed(self, text: str) -> str:
        self._buf += text
        out = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            out.append(self._line(line))
        return "".join(out)

    def finish(self) -> str:
        tail, self._buf = self._buf, ""
        out = self._line(tail) if tail.strip() else ""
        if not self.emitted and self._held:
            # never saw a fence: what looked like prose was the file after all
            out = "\n".join(self._held) + "\n" + out
            self._held = []
        return out

    def _line(self, line: str) -> str:
        s = line.strip()
        if self.state == "done":
            return ""
        if self.state in ("code", "fenced"):
            if s.startswith("```"):
                self.state = "done"
                return ""
            self.emitted += 1
            return line.rstrip() + "\n"
        if s.startswith("```"):
            self.state = "fenced"
            self._held = []
            return ""
        if not s:
            return ""
        if self.state == "start" and _CODE_START.match(s):
            self.state = "code"
            self.emitted += 1
            return line.rstrip() + "\n"
        self.state = "prose"
        self._held.append(line)
        return ""


class TextFilter:
    """Streams prose for typing: whole words at a time, markdown marks removed."""

    def __init__(self) -> None:
        self._buf = ""
        self._line_start = True
        self._started = False

    def feed(self, text: str) -> str:
        self._buf += text
        cut = max(self._buf.rfind(" "), self._buf.rfind("\n"))
        if cut < 0:
            return ""
        ready, self._buf = self._buf[:cut + 1], self._buf[cut + 1:]
        return self._clean(ready)

    def finish(self) -> str:
        rest, self._buf = self._buf, ""
        return self._clean(rest).rstrip()

    def _clean(self, text: str) -> str:
        text = text.replace("**", "").replace("__", "")
        out = []
        for part in re.split(r"(\n)", text):
            if part == "\n":
                if self._started:
                    out.append("\n")
                self._line_start = True
                continue
            if not part:
                continue
            if self._line_start:
                part = re.sub(r"^\s*#{1,6}\s*", "", part)
                part = re.sub(r"^(\s*)\*\s+", r"\1- ", part)
                if not self._started:
                    part = part.lstrip()
                    if not part:
                        continue
            self._started = True
            self._line_start = False
            out.append(part)
        return "".join(out)


# ============================================================== prompts
_TERMINAL = re.compile(r"\b(terminal|console|linha de comando|prompt|cmd|command line|cli|no texto)\b", re.IGNORECASE)


def _wants_terminal(text: str) -> bool:
    return bool(_TERMINAL.search(text))


def _code_messages(original: str, filename: str, lang: CodeLang) -> list[dict[str, str]]:
    if is_pt():
        rules = [f"Você é um programador sênior. Escreva o arquivo {filename} ({lang.label}) completo, que faça "
                 "exatamente o que o usuário pediu, pronto para usar.",
                 "Responda SOMENTE com o conteúdo do arquivo, sem explicações antes ou depois.",
                 "Código completo e funcional: nada de TODO, reticências ou partes para o usuário completar.",
                 "Textos da interface, mensagens e comentários em português do Brasil.",
                 "Seja objetivo: uma solução enxuta (até umas 150 linhas) que tenha tudo o que foi pedido."]
        if lang is PYTHON:
            rules += ["Use só a biblioteca padrão do Python 3 (para interface gráfica, tkinter), a menos que o "
                      "usuário peça outra biblioteca.",
                      ("É um programa de terminal: use print() e input(), sem janela." if _wants_terminal(original)
                       else "Se for um app com janela, organize tudo numa classe App, com os widgets guardados em "
                       "self e os botões chamando métodos da classe; se o pedido for para o terminal, use print() "
                       "e input(), sem janela."),
                      "Nunca use `from ... import *`.",
                      "Pense no que o usuário espera usar de verdade (por exemplo, num app de finanças: registrar "
                      "receitas e despesas, ver o saldo e o histórico) e inclua isso.",
                      f"O programa deve rodar com `python {filename}` e terminar com "
                      "`if __name__ == \"__main__\":`.",
                      "Só guarde dados em arquivo se o pedido precisar (registros, histórico, cadastro); aí use um "
                      "JSON na mesma pasta do programa (caminho baseado em `__file__`) e funcione mesmo quando o "
                      "arquivo ainda não existe."]
        elif lang is HTML:
            rules += ["Um único arquivo HTML com o CSS e o JavaScript embutidos, visual moderno e responsivo."]
        user = f"Pedido: {original}"
    else:
        rules = [f"You are a senior programmer. Write the complete file {filename} ({lang.label}) that does exactly "
                 "what the user asked, ready to use.",
                 "Reply ONLY with the file's content, no explanation before or after.",
                 "Complete, working code: no TODOs, ellipses or parts left for the user.",
                 "Be concise: a lean solution (about 150 lines at most) that has everything that was asked."]
        if lang is PYTHON:
            rules += ["Use only the Python 3 standard library (tkinter for a GUI) unless the user asks for another "
                      "library.",
                      ("It is a terminal program: use print() and input(), no window." if _wants_terminal(original)
                       else "If it is an app with a window, put everything in an App class, widgets stored on self, "
                       "buttons calling its methods; if the request is for the terminal, use print() and input(), "
                       "no window."),
                      "Never use `from ... import *`.",
                      "Think about what the user will actually want to do with it (e.g. a finance app: record "
                      "income and expenses, see the balance and history) and include it.",
                      f"It must run with `python {filename}` and end with `if __name__ == \"__main__\":`.",
                      "Only store data in a file if the request needs it (records, history); then use a JSON file "
                      "next to the program (path based on `__file__`) and work when that file doesn't exist yet."]
        elif lang is HTML:
            rules += ["A single HTML file with its CSS and JavaScript inline, modern and responsive."]
        user = f"Request: {original}"
    system = rules[0] + "\n" + "\n".join(f"- {r}" for r in rules[1:])
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _edit_messages(change: str, path: Path, source: str, lang: CodeLang) -> list[dict[str, str]]:
    messages = _code_messages(change, path.name, lang)
    if is_pt():
        messages[1]["content"] = (f"Este é o arquivo atual {path.name}:\n```{lang.key}\n{source}\n```\n\n"
                                  f"Alteração pedida: {change}\n\nReescreva o arquivo completo já com a alteração, "
                                  "mantendo o que já funciona.")
    else:
        messages[1]["content"] = (f"This is the current file {path.name}:\n```{lang.key}\n{source}\n```\n\n"
                                  f"Requested change: {change}\n\nRewrite the complete file with the change, keeping "
                                  "what already works.")
    return messages


def _fix_messages(messages: list[dict[str, str]], source: str, error: str) -> list[dict[str, str]]:
    fix = (f"Esse código tem um erro: {error}. Reescreva o arquivo completo corrigido." if is_pt() else
           f"That code has an error: {error}. Rewrite the complete file, fixed.")
    return messages + [{"role": "assistant", "content": source}, {"role": "user", "content": fix}]


def _text_messages(original: str, app_name: str) -> list[dict[str, str]]:
    if is_pt():
        system = ("Você é um escritor habilidoso. Escreva exatamente o texto que o usuário pediu, em português do "
                  "Brasil, pronto para ser usado.\n"
                  "- Responda SOMENTE com o texto final: sem introdução (\"Aqui está...\") e sem comentários no fim.\n"
                  "- Texto simples, sem markdown: nada de **, # ou tabelas. Listas podem usar \"- \".\n"
                  f"- O texto será digitado no {app_name}.\n"
                  "- Se o pedido não disser o tamanho, seja conciso.")
        user = f"Pedido: {original}"
    else:
        system = ("You are a skilled writer. Write exactly the text the user asked for, ready to use.\n"
                  "- Reply ONLY with the final text: no introduction (\"Here is...\") and no closing remarks.\n"
                  "- Plain text, no markdown: no **, # or tables. Lists may use \"- \".\n"
                  f"- The text will be typed into {app_name}.\n"
                  "- If the request doesn't say how long, be concise.")
        user = f"Request: {original}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ================================================================ checks
def python_error(source: str, filename: str) -> str | None:
    """Problems that would stop the program, found without running it: syntax
    errors and names used but never defined (pyflakes). The second is the
    typical small-model bug: tkinter widgets created inside ``main()`` and used
    from other functions compiles fine and crashes on start."""
    line = "linha" if is_pt() else "line"
    try:
        tree = compile(source, filename, "exec", flags=0x400)   # ast.PyCF_ONLY_AST
    except SyntaxError as exc:
        return f"{exc.msg} ({line} {exc.lineno})"
    except (ValueError, TypeError) as exc:
        return str(exc)
    try:
        from pyflakes import checker, messages
    except ImportError:
        return None
    undefined = []
    for m in checker.Checker(tree, filename=filename).messages:
        if isinstance(m, messages.UndefinedName):
            undefined.append((m.message_args[0], m.lineno))
        elif isinstance(m, messages.ImportStarUsage) and not _star_provides(m.message_args):
            undefined.append((m.message_args[0], m.lineno))
    problems = []
    if undefined:
        seen: dict[str, int] = {}
        for name, lineno in undefined:
            seen.setdefault(name, lineno)
        listed = ", ".join(f"'{n}' ({line} {n_line})" for n, n_line in list(seen.items())[:6])
        problems.append(f"nomes usados sem estarem definidos nesse escopo: {listed}" if is_pt() else
                        f"names used where they are not defined: {listed}")
    for cls, missing in _missing_self_members(tree):
        listed = ", ".join(f"'self.{n}' ({line} {n_line})" for n, n_line in missing[:6])
        problems.append(f"a classe {cls} usa {listed}, que não existe(m) na classe" if is_pt() else
                        f"class {cls} uses {listed}, which it never defines")
    return "; ".join(problems) or None


def _missing_self_members(tree) -> list[tuple[str, list[tuple[str, int]]]]:  # noqa: ANN001
    """``self.x`` read in a class that never defines ``x`` (as a method, class
    attribute or ``self.x = ...``). Only classes without base classes are
    checked: inherited members (tk.Frame, ...) can't be known statically."""
    import ast

    out = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if any(not (isinstance(b, ast.Name) and b.id == "object") for b in cls.bases) or cls.keywords:
            continue
        defined: set[str] = set()
        used: dict[str, int] = {}
        for node in ast.walk(cls):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node in cls.body:
                defined.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node in cls.body:
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    for leaf in ast.walk(t):
                        if isinstance(leaf, ast.Name):
                            defined.add(leaf.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    defined.add(node.attr)
                else:
                    used.setdefault(node.attr, node.lineno)
        missing = sorted(((n, ln) for n, ln in used.items() if n not in defined and not n.startswith("__")),
                         key=lambda item: item[1])
        if missing:
            out.append((cls.name, missing))
    return out


def _star_provides(args: tuple) -> bool:
    """Does a standard-library ``from x import *`` really provide the name? (only stdlib is imported)"""
    import importlib
    import sys as _sys

    name, modules = args[0], str(args[1]).split(", ")
    for mod in modules:
        if mod.split(".")[0] not in _sys.stdlib_module_names:
            return True          # can't tell without importing third-party code: assume it does
        try:
            if hasattr(importlib.import_module(mod), name):
                return True
        except Exception:  # noqa: BLE001
            return True
    return False


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".nexus-tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _busy_error() -> None:
    jobs = require_jobs()
    if jobs.running:
        raise ToolError("BUSY", tr("I'm still writing {name}. Say “cancel” to stop.", name=jobs.label))


def _download_confirmation(pf: Preflight) -> Preflight:
    """First use: the writing model must be downloaded; ask before starting a multi-GB download."""
    missing = require_writer().missing_bytes()
    if missing > 0:
        size = f"{missing / 1e9:.1f} GB".replace(".", ",") if is_pt() else f"{missing / 1e9:.1f} GB"
        pf.title = pf.spoken = tr("To write this I first need to download the writing model once ({size}). "
                                  "Download it now?", size=size)
        pf.safety = Safety.CONFIRMATION_REQUIRED
    return pf


# ============================================================ streaming
def _stream(ctx: "JobContext", messages: list[dict[str, str]], kind: str, max_tokens: int,
            stats: dict) -> Iterator[str]:
    def on_asset(label: str, _fraction: float | None) -> None:
        ctx.progress(label)

    return require_writer().stream(messages, kind, max_tokens, cancel=ctx.cancel_event, stats=stats,
                                   progress=on_asset)


def _stream_code(ctx: "JobContext", messages: list[dict[str, str]], path: Path, label: str) -> tuple[str, dict]:
    """Stream the model's file straight into ``path`` (flushed twice a second, so
    an editor that watches the file shows it being written)."""
    stats: dict = {}
    filt = CodeFilter()
    parts: list[str] = []
    last = 0.0
    lines = 0
    try:
        for delta in _stream(ctx, messages, "code", CODE_MAX_TOKENS, stats):
            piece = filt.feed(delta)
            if not piece:
                continue
            parts.append(piece)
            lines += piece.count("\n")
            now = time.monotonic()
            if now - last > 0.5:
                last = now
                _write(path, "".join(parts))
                ctx.progress(tr("{label}: {count} lines", label=label, count=lines))
        parts.append(filt.finish())
    finally:
        if parts:
            _write(path, "".join(parts))
    return "".join(parts), stats


def _failure(exc: BaseException) -> "JobResult":
    from app.ai.model_assets import AssetError
    from app.ai.writer import WriterError
    from app.core.jobs import JobResult

    if isinstance(exc, (AssetError, WriterError)):
        return JobResult(str(exc), success=False)
    if isinstance(exc, (urllib.error.URLError, ConnectionError, TimeoutError, OSError)):
        log.warning("writing model connection failed", exc_info=exc)
        return JobResult(tr("The writing model stopped responding."), success=False)
    log.error("writing job failed", exc_info=exc)
    return JobResult(tr("Something went wrong doing that."), success=False)


# ================================================================== code
@dataclass
class CodePlan:
    request: str
    lang: CodeLang
    path: Path
    editor: "AppEntry | None"
    live: bool
    what: str          # "o app de economia" — for messages

    @property
    def editor_name(self) -> str:
        return self.editor.name if self.editor is not None else tr("the default app")


def _code_plan(request: str, editor: str | None) -> CodePlan:
    original = request or services.session.request
    lang = code_language(original)
    named = _NAMED.search(original)
    if named and Path(named.group(1)).suffix.lower() in _BY_EXT:
        lang = _BY_EXT[Path(named.group(1)).suffix.lower()]
    stem = Path(output_name(original, "code", tr("program"))).stem
    folder = require_settings().output_folder("code")
    path = unique_path(folder, stem, lang.ext, own_folder=True)
    entry = resolve_app(editor) or app_hint(original) or default_editor()
    live = entry is not None and bool(_LIVE.search(entry.name))
    return CodePlan(original, lang, path, entry, live, what_of(original, path.name))


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Write {name}?",
    triggers=[intents.WRITE_CODE, intents.WRITE_CODE_LANG],
    absorbs_open="editor",
    self_contained=True,
)
def write_code(request: str, editor: Optional[str] = None):
    """Write a new program, app, game, script or website with AI, save it as a file and open it in a code editor.

    Args:
        request: what the program should do, in the user's words
        editor: the code editor to open it in, only if the user named one, e.g. Antigravity, VS Code
    """
    _busy_error()
    plan = _code_plan(request, editor)
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    _write(plan.path, "")
    if plan.live:
        open_with(plan.editor, plan.path)
    label = plan.path.name
    started = require_jobs().start(label, lambda ctx: _code_job(ctx, plan))
    if not started:
        raise ToolError("BUSY", tr("I'm still writing {name}. Say “cancel” to stop.", name=require_jobs().label))
    return ok(tr("On it: writing {what} in {lang} in {editor}. I'll tell you when it's done.",
                 what=plan.what, lang=plan.lang.label, editor=plan.editor_name),
              path=str(plan.path), language=plan.lang.key, editor=plan.editor_name)


def _code_job(ctx: "JobContext", plan: CodePlan) -> "JobResult":
    from app.ai.writer import Cancelled
    from app.core.jobs import JobResult

    messages = _code_messages(plan.request, plan.path.name, plan.lang)
    started = time.perf_counter()
    try:
        source, stats = _stream_code(ctx, messages, plan.path, plan.path.name)
        error = python_error(source, plan.path.name) if plan.lang is PYTHON and source.strip() else None
        fixed = False
        if error:
            log.info("generated %s has an error (%s); asking for a fix", plan.path.name, error)
            ctx.progress(tr("Fixing an error in {name}…", name=plan.path.name))
            source, stats = _stream_code(ctx, _fix_messages(messages, source, error), plan.path,
                                          tr("{name} (fixing)", name=plan.path.name))
            error = python_error(source, plan.path.name)
            fixed = error is None
    except Cancelled:
        return JobResult(tr("Stopped writing. What I had written is in {name}.", name=plan.path.name),
                         success=False, extra=[str(plan.path)])
    except Exception as exc:  # noqa: BLE001
        return _failure(exc)
    from app.ai.writer import clean_stats

    log.info("wrote %s in %.0fs (%s)", plan.path, time.perf_counter() - started, clean_stats(stats))
    if not source.strip():
        return JobResult(tr("The writing model didn't return any code."), success=False)
    if not plan.live:
        try:
            open_with(plan.editor, plan.path)
        except OSError:
            log.warning("could not open %s", plan.path, exc_info=True)
    services.session.last_program = plan.path
    lines = source.count("\n")
    if stats.get("finish_reason") == "length":
        return JobResult(tr("I wrote {count} lines of {name}, but it got too long and was cut off.",
                            count=lines, name=plan.path.name), success=False, extra=[str(plan.path)])
    if error:
        return JobResult(tr("I wrote {name} ({count} lines), but it still has an error: {error}.",
                            name=plan.path.name, count=lines, error=error), success=False, extra=[str(plan.path)])
    message = tr("Done! I wrote {what}: {count} lines in {name}.", what=plan.what, count=lines, name=plan.path.name)
    if fixed:
        message += " " + tr("I found and fixed an error along the way.")
    if plan.lang is PYTHON or plan.lang is HTML:
        message += " " + tr("Say “run the program” to try it.")
    return JobResult(message, extra=[str(plan.path)])


def _request_repair(args: dict, request) -> dict:  # noqa: ANN001
    original = getattr(request, "original", "") or services.session.request
    text = re.sub(r"^\s*(?:nexus|ok nexus|hey nexus)[,\s]+", "", original, flags=re.IGNORECASE).strip()
    return dict(args, request=text or args.get("request", ""))


def _app_arg_repair(key: str) -> Callable[[dict, object], dict]:
    def repair(args: dict, request) -> dict:  # noqa: ANN001
        # Only an app the user actually named counts ("no bloco de notas", "abra o
        # antigravity e ..."): Needle guesses one from the content ("write an
        # email" -> app="email"), which would open the wrong program.
        args = _request_repair(args, request)
        entry = app_hint(getattr(request, "original", "") or services.session.request)
        args[key] = entry.name if entry is not None else None
        return args
    return repair


write_code.repair(_app_arg_repair("editor"))


@write_code.preflight
def _code_preflight(args: dict) -> Preflight:
    _busy_error()
    request = str(args.get("request") or "").strip()
    if not request:
        raise ToolError("INVALID_ARGUMENTS", tr("What should I write?"))
    plan = _code_plan(request, args.get("editor"))
    title = tr("Write {name} in {editor}?", name=plan.path.name, editor=plan.editor_name)
    pf = Preflight(arguments={"request": request, "editor": args.get("editor")}, title=title, spoken=title,
                   detail=str(plan.path))
    return _download_confirmation(pf)


# ================================================================== edit
_GENERIC_PROGRAM = {"program", "programa", "programs", "code", "codigo", "app", "aplicativo", "script", "it",
                    "ele", "jogo", "game", "site", "website", "pagina", "page", "projeto", "project"}


def _last_program(name: str | None = None) -> Path | None:
    folder = require_settings().output_folder("code")
    candidates: list[Path] = []
    if folder.is_dir():
        candidates = sorted((p for p in folder.glob("*/*") if p.suffix.lower() in _BY_EXT and
                             ".anterior" not in p.name), key=lambda p: p.stat().st_mtime, reverse=True)
    slug = slugify(name or "")
    if slug and slug not in _GENERIC_PROGRAM:   # "rode o programa" = the last one
        for p in candidates:
            if slug in p.stem or p.stem in slug:
                return p
    last = services.session.last_program
    if last is not None and last.is_file():
        return last
    return candidates[0] if candidates else None


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Change {name}?",
    triggers=[intents.EDIT_CODE],
    self_contained=True,
)
def edit_code(change: str):
    """Change, fix or improve the program NEXUS just wrote, e.g. add a button or a chart.

    Args:
        change: the change the user wants, in their words
    """
    _busy_error()
    path = _last_program()
    if path is None:
        return fail("NOT_FOUND", tr("I haven't written a program yet."))
    lang = _BY_EXT.get(path.suffix.lower(), PYTHON)
    source = path.read_text(encoding="utf-8", errors="replace")
    backup = path.with_name(f"{path.stem}.anterior{path.suffix}")
    shutil.copy2(path, backup)
    entry = app_hint(services.session.request) or default_editor()
    live = entry is not None and bool(_LIVE.search(entry.name))
    if live:
        open_with(entry, path)
    plan = CodePlan(change, lang, path, entry, live, path.stem.replace("_", " "))
    messages = _edit_messages(change, path, source, lang)
    started = require_jobs().start(path.name, lambda ctx: _edit_job(ctx, plan, messages, source))
    if not started:
        raise ToolError("BUSY", tr("I'm still writing {name}. Say “cancel” to stop.", name=require_jobs().label))
    return ok(tr("On it: changing {name}. I'll tell you when it's done.", name=path.name), path=str(path))


def _edit_job(ctx: "JobContext", plan: CodePlan, messages: list[dict[str, str]], before: str) -> "JobResult":
    from app.ai.writer import Cancelled
    from app.core.jobs import JobResult

    try:
        source, stats = _stream_code(ctx, messages, plan.path, plan.path.name)
        error = python_error(source, plan.path.name) if plan.lang is PYTHON else None
        if error:
            ctx.progress(tr("Fixing an error in {name}…", name=plan.path.name))
            source, stats = _stream_code(ctx, _fix_messages(messages, source, error), plan.path,
                                          tr("{name} (fixing)", name=plan.path.name))
            error = python_error(source, plan.path.name)
    except Cancelled:
        _write(plan.path, before)
        return JobResult(tr("Stopped. {name} is back as it was.", name=plan.path.name), success=False)
    except Exception as exc:  # noqa: BLE001
        _write(plan.path, before)
        return _failure(exc)
    if not source.strip() or stats.get("finish_reason") == "length":
        _write(plan.path, before)
        return JobResult(tr("The change didn't work out, so I kept {name} as it was.", name=plan.path.name),
                         success=False)
    if not plan.live:
        open_with(plan.editor, plan.path)
    services.session.last_program = plan.path
    if error:
        return JobResult(tr("I changed {name}, but it still has an error: {error}.", name=plan.path.name,
                            error=error), success=False, extra=[str(plan.path)])
    return JobResult(tr("Done! I changed {name}. The previous version is in {backup}.", name=plan.path.name,
                        backup=f"{plan.path.stem}.anterior{plan.path.suffix}"), extra=[str(plan.path)])


@edit_code.repair
def _edit_repair(args: dict, request) -> dict:  # noqa: ANN001
    original = getattr(request, "original", "") or services.session.request
    return dict(args, change=re.sub(r"^\s*(?:nexus|ok nexus)[,\s]+", "", original, flags=re.IGNORECASE).strip())


@edit_code.preflight
def _edit_preflight(args: dict) -> Preflight:
    _busy_error()
    path = _last_program()
    if path is None:
        raise ToolError("NOT_FOUND", tr("I haven't written a program yet."))
    title = tr("Change {name}?", name=path.name)
    return _download_confirmation(Preflight(arguments={"change": str(args.get("change") or "")}, title=title,
                                            spoken=title, detail=str(path)))


# ================================================================== text
@dataclass
class TextPlan:
    request: str
    mode: str                  # "notepad" (new file) | "launch" | "window"
    entry: "AppEntry | None"
    hwnd: int | None
    path: Path                 # where the text is saved
    what: str

    @property
    def app_name(self) -> str:
        if self.mode == "notepad":
            return tr("Notepad")
        if self.mode == "window" and self.hwnd:
            return windows.title(self.hwnd)[:60] or tr("that window")
        return self.entry.name if self.entry is not None else tr("Notepad")


def _text_plan(request: str, app: str | None) -> TextPlan:
    original = request or services.session.request
    stem = output_name(original, "text", tr("text"))
    folder = require_settings().output_folder("text")
    path = unique_path(folder, Path(stem).stem, ".txt", own_folder=False)
    what = what_of(original, tr("the text"))
    if _HERE.search(original) and not app:
        last = windows.tracker.last
        if last is not None and windows.exists(last.hwnd):
            return TextPlan(request, "window", None, last.hwnd, path, what)
    entry = resolve_app(app) or app_hint(original)
    if entry is None or is_notepad(entry):
        return TextPlan(request, "notepad", entry, None, path, what)
    return TextPlan(request, "launch", entry, None, path, what)


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Write {name}?",
    triggers=[intents.WRITE_TEXT, intents.WRITE_ABOUT],
    absorbs_open="app",
    self_contained=True,
)
def write_text(request: str, app: Optional[str] = None):
    """Write a new text with AI (poem, story, email, letter, essay, message, list) and type it into an app.

    Args:
        request: what to write, in the user's words
        app: the app to write it in, only if the user named one, e.g. Notepad, Word
    """
    _busy_error()
    plan = _text_plan(request, app)
    started = require_jobs().start(plan.path.name, lambda ctx: _text_job(ctx, plan))
    if not started:
        raise ToolError("BUSY", tr("I'm still writing {name}. Say “cancel” to stop.", name=require_jobs().label))
    return ok(tr("On it: writing {what} in {app}.", what=plan.what, app=plan.app_name), path=str(plan.path))


def _open_text_target(plan: TextPlan) -> int | None:
    if plan.mode == "window":
        return plan.hwnd
    if plan.mode == "notepad":
        _write(plan.path, "")
        subprocess.Popen(["notepad.exe", str(plan.path)], close_fds=True, creationflags=DETACHED_PROCESS)
        stem = plan.path.stem.lower()
        return _wait_window(lambda h: stem in windows.title(h).lower(), 12.0)
    from app.tools.applications import find_windows, open_app_entry

    assert plan.entry is not None
    launched = services.session.launched
    if not (launched and launched[0] is plan.entry and time.time() - launched[1] < 90):
        open_app_entry(plan.entry.name, plan.entry)
    deadline = time.time() + 12.0
    while time.time() < deadline:
        wins = [w for w in find_windows(plan.entry, plan.entry.name) if not windows.unsafe_target(w[0])]
        if wins:
            return wins[0][0]
        time.sleep(0.25)
    return None


def _text_job(ctx: "JobContext", plan: TextPlan) -> "JobResult":
    """Write the text into the target as it is generated.

    Apps with a standard text control (Windows 11 Notepad's RichEditD2DPT,
    classic Notepad, WordPad...) get the words inserted straight into that
    control: exact (keystrokes injected into a busy Notepad arrived garbled,
    "profundo" -> "rrofundo") and independent of which window is in front.
    Other apps are typed into, and only while they stay in the foreground.
    """
    from app.ai.writer import Cancelled
    from app.core.jobs import JobResult

    messages = _text_messages(plan.request, plan.app_name)
    stats: dict = {}
    written: list[str] = []
    delivered = True          # every word reached the target so far
    control: int | None = None
    hwnd: int | None = None
    try:
        require_writer().ensure(lambda label, _f: ctx.progress(label), ctx.cancel_event)
        hwnd = _open_text_target(plan)
        if hwnd is None or windows.unsafe_target(hwnd):
            delivered = False
        else:
            control = windows.text_control(hwnd)
            if control is None and not windows.focus(hwnd):
                delivered = False
            elif control is not None and plan.mode != "window":
                windows.focus(hwnd)   # show it being written; not needed for the text to arrive
            time.sleep(0.3)
        filt = TextFilter()
        ctx.progress(tr("Writing {name}…", name=plan.what))

        def deliver(piece: str) -> None:
            nonlocal delivered
            if not delivered or not piece:
                return
            if control is not None:
                delivered = windows.insert_text(control, piece)
            elif windows.user32.GetForegroundWindow() != hwnd:
                delivered = False   # you switched windows: never type somewhere else
                log.info("focus left the target window; saving the text instead of typing it")
            else:
                windows.type_text(piece)

        for delta in _stream(ctx, messages, "text", TEXT_MAX_TOKENS, stats):
            piece = filt.feed(delta)
            if piece:
                written.append(piece)
                deliver(piece)
        tail = filt.finish()
        written.append(tail)
        deliver(tail)
    except Cancelled:
        text = "".join(written)
        if text.strip() and plan.mode != "notepad":
            _write(plan.path, text)
        return JobResult(tr("Stopped writing."), success=False)
    except Exception as exc:  # noqa: BLE001
        return _failure(exc)
    text = "".join(written).strip()
    if not text:
        return JobResult(tr("The writing model didn't return any text."), success=False)
    if plan.mode == "notepad" and delivered:
        # Notepad saves it (Ctrl+S, the file already has its name) when it is still
        # in front; otherwise, or if that didn't take, NEXUS writes the file itself.
        saved = False
        if hwnd is not None and windows.user32.GetForegroundWindow() == hwnd:
            windows.press(["ctrl", "S"])
            time.sleep(0.8)
            saved = not windows.title(hwnd).lstrip().startswith("*")
        if not saved:
            _write(plan.path, text + "\n")
        return JobResult(tr("Done! I wrote {what} in Notepad and saved it as {name}.", what=plan.what,
                            name=plan.path.name), extra=[str(plan.path)])
    if delivered:
        return JobResult(tr("Done! I wrote {what} in {app}.", what=plan.what, app=plan.app_name))
    target = plan.path if plan.mode != "notepad" else plan.path.with_name(f"{plan.path.stem}_completo.txt")
    _write(target, text + "\n")
    return JobResult(tr("You switched windows, so I stopped typing. The whole text is saved in {name}.",
                        name=target.name), extra=[str(target)])


write_text.repair(_app_arg_repair("app"))


@write_text.preflight
def _text_preflight(args: dict) -> Preflight:
    _busy_error()
    request = str(args.get("request") or "").strip()
    if not request:
        raise ToolError("INVALID_ARGUMENTS", tr("What should I write?"))
    plan = _text_plan(request, args.get("app"))
    title = tr("Write {what} in {app}?", what=plan.what, app=plan.app_name)
    pf = Preflight(arguments={"request": request, "app": args.get("app")}, title=title, spoken=title,
                   detail=plan.app_name)
    if plan.mode == "window":
        if plan.hwnd is None or windows.unsafe_target(plan.hwnd):
            raise ToolError("BLOCKED", tr("For safety I don't type into terminals."))
        pf.safety = Safety.CONFIRMATION_REQUIRED   # typing into a window NEXUS didn't open
    return _download_confirmation(pf)


# =================================================================== run
_PYTHON_CMD: list[str] | None = None


def python_command() -> list[str] | None:
    """A real Python 3 interpreter to run the user's programs with (never NEXUS itself)."""
    global _PYTHON_CMD
    if _PYTHON_CMD is not None:
        return _PYTHON_CMD
    for cand in (["py", "-3"], ["python"], ["python3"]):
        exe = shutil.which(cand[0])
        if not exe:
            continue
        try:
            r = subprocess.run([exe, *cand[1:], "-c", "import sys; print(sys.version_info[0])"], capture_output=True,
                               timeout=15, stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout.strip() == b"3":
            _PYTHON_CMD = [exe, *cand[1:]]
            return _PYTHON_CMD
    return None


@nexus_tool(
    safety=Safety.CONFIRMATION_REQUIRED,
    confirm="Run {name}?",
    triggers=[intents.RUN_PROGRAM],
)
def run_program(program: Optional[str] = None):
    """Run the program or script that NEXUS wrote.

    Args:
        program: which program, only if the user named it
    """
    path = _last_program(program)
    if path is None:
        return fail("NOT_FOUND", tr("I haven't written a program yet."))
    ext = path.suffix.lower()
    if ext in (".html", ".htm"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return ok(tr("Opening {name} in the browser.", name=path.name), path=str(path))
    if ext == ".py":
        cmd = python_command()
        if cmd is None:
            return fail("PYTHON_NOT_FOUND", tr("Python isn't installed, so I can't run {name}.", name=path.name))
        runner = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        line = f'cmd.exe /k "title NEXUS - {path.name} & {runner} "{path.name}""'
    elif ext == ".js" and shutil.which("node"):
        line = f'cmd.exe /k "title NEXUS - {path.name} & node "{path.name}""'
    elif ext == ".ps1":
        line = f'cmd.exe /k "powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{path.name}""'
    else:
        return fail("UNSUPPORTED", tr("I can't run {name} files yet.", name=ext))
    subprocess.Popen(line, cwd=str(path.parent), creationflags=CREATE_NEW_CONSOLE)
    services.session.last_program = path
    return ok(tr("Running {name}.", name=path.name), path=str(path))


@run_program.preflight
def _run_preflight(args: dict) -> Preflight:
    path = _last_program(args.get("program"))
    if path is None:
        raise ToolError("NOT_FOUND", tr("I haven't written a program yet."))
    jobs = require_jobs()
    if jobs.running and jobs.label == path.name:
        raise ToolError("BUSY", tr("I'm still writing {name}; ask me again when it's done.", name=path.name))
    title = tr("Run {name}?", name=path.name)
    return Preflight(arguments={"program": path.stem}, title=title, spoken=title, detail=str(path))
