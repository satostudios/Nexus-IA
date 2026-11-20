"""User-facing text in English and Brazilian Portuguese.

``tr("Chrome opened.")`` style: the English sentence is the key; the
Portuguese catalogue below translates it. Placeholders use ``{name}``.
Anything missing falls back to English, so a new message never breaks.
"""

from __future__ import annotations

import ctypes
import datetime

_lang = "en"

PT: dict[str, str] = {
    # ---- states
    "STARTING": "INICIANDO", "READY": "PRONTO", "LISTENING": "OUVINDO", "PROCESSING": "PROCESSANDO",
    "SPEAKING": "FALANDO", "DONE": "FEITO", "ERROR": "ERRO", "CONFIRM": "CONFIRMAR", "VOICE MODE": "MODO VOZ",
    # ---- startup
    "Preparing local AI core...": "Preparando o núcleo de IA local...",
    "Loading tools": "Carregando ferramentas",
    "Preparing local AI core": "Preparando o núcleo de IA local",
    "Starting Needle 3": "Iniciando o Needle 3",
    "Downloading Needle engine": "Baixando o motor do Needle",
    "Downloading Needle 3 weights": "Baixando os pesos do Needle 3",
    "Downloading speech model ({name})": "Baixando o modelo de fala ({name})",
    "Downloading voice {name}": "Baixando a voz {name}",
    "Downloading voice config": "Baixando a configuração da voz",
    "The AI core could not start.": "O núcleo de IA não pôde iniciar.",
    "The AI core failed to start ({name}).": "O núcleo de IA falhou ao iniciar ({name}).",
    "Still preparing the AI core…": "Ainda preparando o núcleo de IA…",
    "One moment…": "Um momento…",
    "Voice input is still loading…": "A entrada de voz ainda está carregando…",
    "Voice input isn't available. Check the log for details.": "A entrada de voz não está disponível. Veja o log.",
    "I didn't hear anything.": "Não ouvi nada.",
    "Using microphone: {name}": "Usando o microfone: {name}",
    "Voice mode on. I'm listening.": "Modo de voz ativado. Estou ouvindo.",
    "Voice mode off.": "Modo de voz desativado.",
    "See you later.": "Até mais.",
    # ---- UI
    "Type a command...": "Digite um comando...",
    "Voice mode: just talk. Say \"stop\" to finish.": "Modo voz: é só falar. Diga \"parar\" para sair.",
    # ---- settings window
    "SETTINGS": "CONFIGURAÇÕES", "General": "Geral", "Audio": "Áudio", "Voice": "Voz", "AI": "IA",
    "System": "Sistema", "Debug": "Depuração", "Language": "Idioma", "Automatic (Windows)": "Automático (Windows)",
    "Your name": "Seu nome", "Optional": "Opcional", "Web search": "Pesquisa na web",
    "NEXUS understands and answers in the chosen language. Changing it switches the voice and the speech model "
    "(downloaded once if needed).":
        "A NEXUS entende e responde no idioma escolhido. Trocar o idioma troca a voz e o modelo de fala "
        "(baixados uma vez, se necessário).",
    "System default": "Padrão do sistema", "(default)": "(padrão)", "Microphone": "Microfone",
    "Speaker": "Alto-falante", "Sensitivity": "Sensibilidade", "Sample rate": "Taxa de amostragem",
    "Activation": "Ativação", "Conversation (listen, answer, listen again)": "Conversa (ouve, responde e volta a ouvir)",
    "One command per click": "Um comando por clique", "Hold to talk": "Segurar para falar",
    "Silence to stop": "Silêncio para encerrar", "Sound when listening starts": "Som ao começar a ouvir",
    "Test microphone": "Testar microfone", "Listening on {name}…": "Ouvindo em {name}…",
    "Speak responses": "Falar as respostas", "Engine": "Motor", "Piper (neural, local)": "Piper (neural, local)",
    "Windows voices (built in)": "Vozes do Windows (nativas)", "Language default ({name})": "Padrão do idioma ({name})",
    "(download ~60 MB)": "(baixar ~60 MB)", "Speed": "Velocidade", "Volume": "Volume", "Test voice": "Testar voz",
    "Needle model (.cact)": "Modelo do Needle (.cact)",
    "Base Needle 3 (downloaded automatically)": "Needle 3 base (baixado automaticamente)",
    "Tool index": "Índice de ferramentas", "Act at confidence ≥": "Agir com confiança ≥",
    "Refuse below": "Recusar abaixo de", "Only act on supported command phrasings": "Só agir em frases de comando suportadas",
    "Speech model": "Modelo de fala",
    "Safe actions at or above the first threshold run immediately; between the two NEXUS asks first. Deleting, "
    "moving, copying, renaming, shutdown and restart always ask. Model changes apply after restart.":
        "Ações seguras a partir do primeiro limite rodam na hora; entre os dois, a NEXUS pergunta antes. Excluir, "
        "mover, copiar, renomear, desligar e reiniciar sempre pedem confirmação. Mudanças de modelo valem após reiniciar.",
    "Start with Windows": "Iniciar com o Windows", "Start minimized": "Iniciar minimizado",
    "Allow Needle's anonymous usage counts": "Permitir estatísticas anônimas do Needle",
    "Write detailed logs": "Gravar logs detalhados", "Log level": "Nível de log",
    "Show tool calls (debug panel)": "Mostrar chamadas de ferramentas (painel de depuração)",
    "Show confidence under replies": "Mostrar a confiança nas respostas",
    "Open log folder": "Abrir pasta de logs", "Open data folder": "Abrir pasta de dados",
    "Couldn't change the Windows startup setting.": "Não consegui alterar a inicialização com o Windows.",
    "Language changes apply fully after restarting NEXUS.": "A troca de idioma vale por completo após reiniciar a NEXUS.",
    "Talk to NEXUS (Ctrl+Space)": "Falar com a NEXUS (Ctrl+Espaço)",
    "History (Ctrl+H)": "Histórico (Ctrl+H)", "Settings (Ctrl+,)": "Configurações (Ctrl+,)",
    "Minimize": "Minimizar", "Maximize": "Maximizar", "Restore": "Restaurar", "Close": "Fechar",
    "Cancel": "Cancelar", "Confirm": "Confirmar", "Save": "Salvar",
    "HISTORY": "HISTÓRICO", "YOU": "VOCÊ", "Nothing yet.": "Nada ainda.", "Run a command.": "Execute um comando.",
    "TRANSCRIPT": "TRANSCRIÇÃO", "TOOL": "FERRAMENTA", "ARGUMENTS": "ARGUMENTOS", "CONFIDENCE": "CONFIANÇA",
    "ROUTE": "ROTA", "RESULT": "RESULTADO", "OUTCOME": "DESFECHO", "TIMING": "TEMPO", "ENGINE": "MOTOR",
    "NEEDLE INPUT": "ENTRADA DO NEEDLE",
    "none (no call proposed)": "nenhuma (nenhuma chamada proposta)", "(withheld by engine)": "(retida pelo motor)",
    "confidence": "confiança",
    # ---- pipeline
    "I can't do that yet.": "Ainda não consigo fazer isso.",
    "I'm not sure what you mean.": "Não tenho certeza do que você quer.",
    "My AI core had a problem. Please try again.": "Meu núcleo de IA teve um problema. Tente de novo.",
    "Something went wrong.": "Algo deu errado.",
    "Cancelled.": "Cancelado.", "Okay.": "Tudo bem.", "There's nothing to confirm.": "Não há nada para confirmar.",
    "Did you mean: {name}": "Você quis dizer: {name}",
    "That action isn't allowed.": "Essa ação não é permitida.",
    "Run {name}?": "Executar {name}?",
    # ---- registry
    "Done.": "Feito.", "That didn't work.": "Não deu certo.",
    "Windows denied permission for that.": "O Windows negou permissão para isso.",
    "I couldn't find that.": "Não encontrei isso.",
    "Something went wrong doing that.": "Algo deu errado ao fazer isso.",
    "Something went wrong preparing that.": "Algo deu errado ao preparar isso.",
    # ---- applications
    "{name} opened.": "Abrindo {name}.",
    "{names} and {last} opened.": "Abrindo {names} e {last}.",
    "Which app do you mean: {options}?": "Qual aplicativo: {options}?",
    "{name} doesn't seem to be installed.": "{name} não parece estar instalado.",
    "I couldn't find an app called {name}.": "Não encontrei um aplicativo chamado {name}.",
    "{name} isn't open.": "{name} não está aberto.",
    "{name} didn't close. It may be asking you to save something.":
        "{name} não fechou. Talvez esteja pedindo para salvar algo.",
    "{name} closed.": "Fechei {name}.",
    "Close {name}?": "Fechar {name}?", "Open {name}?": "Abrir {name}?",
    "That doesn't look like a web address.": "Isso não parece um endereço da web.",
    "Searching for {name}.": "Pesquisando {name}.",
    "Searching YouTube for {name}.": "Pesquisando {name} no YouTube.",
    "Search for {name}?": "Pesquisar {name}?",
    "What should I search for?": "O que devo pesquisar?",
    "Folder opened.": "Pasta aberta.",
    "{name} opened in the browser.": "Abrindo {name} no navegador.",
    # ---- files
    "I couldn't find {name}.": "Não encontrei {name}.",
    "{name} isn't a valid name.": "{name} não é um nome válido.",
    "That": "Isso",
    "I couldn't find a folder called {name}.": "Não encontrei uma pasta chamada {name}.",
    "File search isn't ready yet.": "A busca de arquivos ainda não está pronta.",
    "I couldn't find any {name}.": "Não encontrei nenhum {name}.",
    "files": "arquivo", "{ext} files": "arquivo {ext}",
    "I found {name}.": "Encontrei {name}.",
    "I found {count} matches. The top one is {name}.": "Encontrei {count} resultados. O primeiro é {name}.",
    "{name} already exists in {folder}.": "{name} já existe em {folder}.",
    "{name} created in {folder}.": "Criei {name} em {folder}.",
    "Folder {name} created in {folder}.": "Criei a pasta {name} em {folder}.",
    "I won't {verb} {name}. It's a protected location.": "Não vou {verb} {name}. É um local protegido.",
    "delete": "excluir", "move": "mover", "rename": "renomear",
    "Renamed to {name}.": "Renomeado para {name}.",
    "{name} already exists there.": "{name} já existe lá.",
    "The destination isn't a folder.": "O destino não é uma pasta.",
    "I can't {verb} a folder into itself.": "Não dá para {verb} uma pasta para dentro dela mesma.",
    "copy": "copiar",
    "Copied to {name}.": "Copiado para {name}.", "Moved to {name}.": "Movido para {name}.",
    "File moved to the Recycle Bin.": "Arquivo enviado para a Lixeira.",
    "Folder moved to the Recycle Bin.": "Pasta enviada para a Lixeira.",
    "Rename file?": "Renomear arquivo?", "Rename folder?": "Renomear pasta?",
    "Copy file?": "Copiar arquivo?", "Copy folder?": "Copiar pasta?",
    "Move file?": "Mover arquivo?", "Move folder?": "Mover pasta?",
    "Delete file?": "Excluir arquivo?", "Delete folder?": "Excluir pasta?",
    "Rename {name} to {new}?": "Renomear {name} para {new}?",
    "Copy {name} to {folder}?": "Copiar {name} para {folder}?",
    "Move {name} to {folder}?": "Mover {name} para {folder}?",
    "Delete the file {name}? Please confirm.": "Excluir o arquivo {name}? Confirme, por favor.",
    "Delete the folder {name}? Please confirm.": "Excluir a pasta {name}? Confirme, por favor.",
    "Which {kind} do you mean: {options}?": "Qual {kind}: {options}?",
    "file": "arquivo", "folder": "pasta",
    "{name} in {folder}": "{name} em {folder}",
    "{a}, or {b}": "{a} ou {b}",
    "Create {name}?": "Criar {name}?", "Create folder {name}?": "Criar a pasta {name}?",
    # ---- system
    "It's {clock}, {day}.": "Agora são {clock}, {day}.",
    "Locked.": "Computador bloqueado.", "Windows didn't lock.": "O Windows não bloqueou.",
    "Shutting down in five seconds.": "Desligando em cinco segundos.",
    "Restarting in five seconds.": "Reiniciando em cinco segundos.",
    "Shut down the computer?": "Desligar o computador?", "Restart the computer?": "Reiniciar o computador?",
    "Lock the computer?": "Bloquear o computador?", "Tell the time?": "Dizer as horas?",
    "Show help?": "Mostrar a ajuda?",
    "I can write texts and programs for you, open and close programs, search the web, type into Notepad, control "
    "volume and music, handle files and folders, take screenshots and tell the time. Try: open Antigravity and "
    "write a Python expense tracker.":
        "Eu escrevo textos e programas pra você, abro e fecho programas, pesquiso na internet, digito no Bloco de "
        "Notas, controlo o volume e a música, cuido de arquivos e pastas, tiro prints e digo as horas. Experimente: "
        "abra o Antigravity e escreva um app de economia em Python.",
    "Show system information?": "Mostrar informações do sistema?",
    "{gb} GB memory, {pct}% used": "{gb} GB de memória, {pct}% em uso",
    "battery {pct}%": "bateria em {pct}%",
    "Screenshot saved.": "Captura de tela salva.", "Take a screenshot?": "Tirar uma captura de tela?",
    # ---- keyboard / media
    "Typed.": "Pronto, digitei.",
    "Type into {name}?": "Digitar em {name}?",
    "What should I type?": "O que devo digitar?",
    "That's a text to write, not to dictate: ask me to “write” it.":
        "Isso é um texto pra eu escrever, não pra digitar: peça “escreva” que eu crio.",
    # ---- writing (write_text / write_code / edit_code / run_program)
    "Projects": "Projetos", "Texts": "Textos", "program": "programa", "text": "texto", "the text": "o texto",
    "Notepad": "Bloco de Notas", "that window": "essa janela", "the default app": "o app padrão",
    "What should I write?": "O que devo escrever?",
    "I'm still writing {name}. Say “cancel” to stop.": "Ainda estou escrevendo {name}. Diga “cancela” para parar.",
    "I'm still writing {name}; ask me again when it's done.":
        "Ainda estou escrevendo {name}; peça de novo quando eu terminar.",
    "To write this I first need to download the writing model once ({size}). Download it now?":
        "Para escrever isso eu preciso baixar o modelo de escrita uma única vez ({size}). Baixar agora?",
    "Write {name} in {editor}?": "Escrever {name} no {editor}?",
    "Write {what} in {app}?": "Escrever {what} no {app}?",
    "Write {name}?": "Escrever {name}?",
    "On it: writing {what} in {lang} in {editor}. I'll tell you when it's done.":
        "Certo! Vou escrever {what} em {lang} no {editor}. Te aviso quando terminar.",
    "On it: writing {what} in {app}.": "Certo! Vou escrever {what} no {app}.",
    "On it: changing {name}. I'll tell you when it's done.": "Certo! Vou alterar {name}. Te aviso quando terminar.",
    "{label}: {count} lines": "{label}: {count} linhas",
    "Writing {name}…": "Escrevendo {name}…",
    "Fixing an error in {name}…": "Corrigindo um erro em {name}…",
    "{name} (fixing)": "{name} (corrigindo)",
    "Stopped writing. What I had written is in {name}.": "Parei de escrever. O que eu já tinha escrito está em {name}.",
    "Stopped writing.": "Parei de escrever.",
    "Stopped. {name} is back as it was.": "Parei. {name} voltou a ser como era.",
    "The writing model didn't return any code.": "O modelo de escrita não devolveu nenhum código.",
    "The writing model didn't return any text.": "O modelo de escrita não devolveu nenhum texto.",
    "The writing model stopped responding.": "O modelo de escrita parou de responder.",
    "I wrote {count} lines of {name}, but it got too long and was cut off.":
        "Escrevi {count} linhas de {name}, mas ficou longo demais e foi cortado.",
    "I wrote {name} ({count} lines), but it still has an error: {error}.":
        "Escrevi {name} ({count} linhas), mas ainda tem um erro: {error}.",
    "Done! I wrote {what}: {count} lines in {name}.": "Pronto! Escrevi {what}: {count} linhas em {name}.",
    "I found and fixed an error along the way.": "Encontrei e corrigi um erro no caminho.",
    "Say “run the program” to try it.": "Diga “rode o programa” para testar.",
    "I haven't written a program yet.": "Ainda não escrevi nenhum programa.",
    "Change {name}?": "Alterar {name}?",
    "The change didn't work out, so I kept {name} as it was.":
        "A alteração não deu certo, então mantive {name} como estava.",
    "I changed {name}, but it still has an error: {error}.": "Alterei {name}, mas ainda tem um erro: {error}.",
    "Done! I changed {name}. The previous version is in {backup}.":
        "Pronto! Alterei {name}. A versão anterior está em {backup}.",
    "Done! I wrote {what} in Notepad and saved it as {name}.":
        "Pronto! Escrevi {what} no Bloco de Notas e salvei como {name}.",
    "Done! I wrote {what} in {app}.": "Pronto! Escrevi {what} no {app}.",
    "You switched windows, so I stopped typing. The whole text is saved in {name}.":
        "Você mudou de janela, então parei de digitar. O texto inteiro está salvo em {name}.",
    "Run {name}?": "Rodar {name}?",
    "Running {name}.": "Rodando {name}.",
    "Opening {name} in the browser.": "Abrindo {name} no navegador.",
    "Python isn't installed, so I can't run {name}.": "O Python não está instalado, então não consigo rodar {name}.",
    "I can't run {name} files yet.": "Ainda não sei rodar arquivos {name}.",
    "Nothing is being written right now.": "Não estou escrevendo nada agora.",
    "best quality": "melhor qualidade", "faster": "mais rápido",
    "Automatic (Antigravity, VS Code, Cursor… or Notepad)": "Automático (Antigravity, VS Code, Cursor… ou Bloco de Notas)",
    "Writing model": "Modelo de escrita", "Code editor": "Editor de código", "Save creations in": "Salvar criações em",
    "Downloading the writing engine": "Baixando o motor de escrita",
    "Downloading the writing model": "Baixando o modelo de escrita",
    "Loading the writing model": "Carregando o modelo de escrita",
    "Could not download the writing engine ({name}).": "Não consegui baixar o motor de escrita ({name}).",
    "Could not download the writing model ({name}).": "Não consegui baixar o modelo de escrita ({name}).",
    "The writing model isn't downloaded yet and there is no internet connection.":
        "O modelo de escrita ainda não foi baixado e não há conexão com a internet.",
    "The writing model failed to start. See logs/writer-server.log.":
        "O modelo de escrita não iniciou. Veja logs/writer-server.log.",
    "The writing model took too long to start.": "O modelo de escrita demorou demais para iniciar.",
    "There's no window to type into.": "Não há uma janela para digitar.",
    "For safety I don't type into terminals.": "Por segurança, não digito em terminais.",
    "Which key should I press?": "Qual tecla devo apertar?",
    "Pressed {name}.": "Apertei {name}.", "Press {name} in {window}?": "Apertar {name} em {window}?",
    "Volume up.": "Volume aumentado.", "Volume down.": "Volume diminuído.", "Mute toggled.": "Mudo alternado.",
    "Play/pause.": "Reproduzir/pausar.", "Next track.": "Próxima faixa.", "Previous track.": "Faixa anterior.",
    "Which media action?": "Qual ação de mídia?",
    # ---- memory
    "What should I call that place?": "Como devo chamar esse lugar?",
    "I can't find {name}.": "Não encontro {name}.",
    "Got it. {name} is {path}.": "Entendido. {name} é {path}.",
    "Got it. {alias} means {app}.": "Entendido. {alias} significa {app}.",
    "What nickname should I use?": "Qual apelido devo usar?",
    "I don't have anything saved as {name}.": "Não tenho nada salvo como {name}.",
    "Which one: {options}?": "Qual deles: {options}?",
    "Forgot {name}.": "Esqueci {name}.",
    "Remember {name} as {path}?": "Lembrar {name} como {path}?",
    # ---- audio errors
    "No microphone was found.": "Nenhum microfone foi encontrado.",
    "The microphone couldn't be opened. It may be in use or disabled.":
        "Não consegui abrir o microfone. Ele pode estar em uso ou desativado.",
    "The microphone was disconnected.": "O microfone foi desconectado.",
    "The microphone stopped responding.": "O microfone parou de responder.",
    "The microphone isn't picking up any sound. Choose one in Settings.":
        "O microfone não está captando som. Escolha outro nas Configurações.",
    "Speech recognition is still loading.": "O reconhecimento de fala ainda está carregando.",
    "I couldn't understand the audio.": "Não entendi o áudio.",
    "This is how I sound.": "É assim que eu falo.",
}

_WEEKDAYS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
_MONTHS_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
              "novembro", "dezembro"]


def set_language(lang: str) -> None:
    global _lang
    _lang = "pt" if lang.lower().startswith("pt") else "en"


def language() -> str:
    return _lang


def is_pt() -> bool:
    return _lang == "pt"


def tr(text: str, **kw: object) -> str:
    template = PT.get(text, text) if _lang == "pt" else text
    try:
        return template.format(**kw) if kw else template
    except (KeyError, IndexError, ValueError):
        return text.format(**kw) if kw else text


def join_options(options: list[str]) -> str:
    """'a, b, or c' / 'a, b ou c'."""
    if len(options) <= 1:
        return "".join(options)
    return tr("{a}, or {b}", a=", ".join(options[:-1]), b=options[-1])


def clock(now: datetime.datetime) -> tuple[str, str]:
    """(time, day) spoken naturally in the current language."""
    if _lang == "pt":
        return (f"{now.hour}h{now.minute:02d}" if now.minute else f"{now.hour} horas",
                f"{_WEEKDAYS_PT[now.weekday()]}, {now.day} de {_MONTHS_PT[now.month - 1]}")
    return now.strftime("%I:%M %p").lstrip("0"), f"{now.strftime('%A, %B')} {now.day}"


def system_language() -> str:
    """'pt' when Windows' UI language is Portuguese, else 'en'."""
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        return "pt" if (lang_id & 0x3FF) == 0x16 else "en"
    except Exception:
        return "en"
