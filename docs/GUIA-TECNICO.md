# NEXUS: technical guide

> Guia técnico da NEXUS, em inglês. A apresentação em português está no [README](../README.md).


A local AI operating assistant for Windows. You talk or type; **Needle 3** works out which action you
mean; NEXUS checks whether that action may run, runs it on Windows, and answers in a short spoken
sentence. The whole chain runs on your machine.

```
UNDERSTAND  ->  ACT  ->  REPORT
```

It is not a chatbot. A request no tool covers gets *"I can't do that yet."*, never an invented answer.
When you ask it to **create** something (a poem, an email, a Python app, a website), a second local
model writes it, live, into Notepad or your code editor (see [Writing texts and programs](#writing-texts-and-programs)).

NEXUS speaks and understands **Brazilian Portuguese** and **English**. The language follows Windows, or
you can choose it under Settings → General.

## Guia rápido (português)

| Você diz ou digita | A NEXUS faz |
| --- | --- |
| *abra o bloco de notas* · *abre o chrome* · *abrir o navegador* | abre o aplicativo (navegador = o seu navegador padrão) |
| *feche o chrome* | fecha o aplicativo sem forçar (ele ainda pode pedir para salvar) |
| *pesquise receita de bolo de cenoura no google* · *pesquisa no youtube música do Legião Urbana* · *como está o tempo hoje* | abre a pesquisa no navegador |
| *abra o bloco de notas e digite olá, tudo bem?* | abre e digita o texto exatamente como você ditou |
| *digite …* · *escreva comprar pão e leite* · *aperte enter* · *salve o arquivo* | digita (ditado) ou aperta teclas no aplicativo que você está usando (pergunta antes se não foi a NEXUS que abriu esse aplicativo) |
| *abra o antigravity e escreva um código em .py pra criar um app de economia* · *crie um jogo da velha em python* · *crie um site para a minha padaria* | **escreve o programa ao vivo** no editor (Antigravity, VS Code…) e salva em `Documentos\NEXUS\Projetos` |
| *escreva um poema sobre o mar* · *escreve um email pro meu chefe pedindo folga* · *faça uma lista de compras* | **escreve o texto ao vivo** no Bloco de Notas (ou no app que você disser) e salva em `Documentos\NEXUS\Textos` |
| *adicione um gráfico de gastos no app* · *rode o programa* | altera o último programa · roda o programa (**sempre pede confirmação**) |
| *cancela* · *para de escrever* (ou Esc) | para a escrita; o que já foi escrito fica salvo |
| *aumente o volume* · *pausa a música* · *próxima música* · *deixa no mudo* | controla som e mídia |
| *abra minha pasta de downloads* · *procure arquivos chamados fatura* · *crie uma pasta chamada Projetos* | arquivos e pastas |
| *apague o arquivo velho.txt* · *mova foto.jpg para imagens* · *desligue o computador* | **sempre pede confirmação** (diga *sim* / *não* ou use os botões) |
| *que horas são* · *quanta memória ram eu tenho* · *tire um print da tela* · *bloqueie o computador* | sistema |
| *meu projeto de faculdade é D:\Faculdade\TCC* → *abra meu projeto de faculdade* | memória local |

**Escrever leva alguns minutos** num notebook sem placa de vídeo dedicada (o modelo roda no processador):
a NEXUS responde na hora (*"Certo! Vou escrever o app de economia em Python no Antigravity IDE. Te aviso
quando terminar."*), escreve em segundo plano enquanto continua ouvindo outros comandos, mostra o
progresso (*✎ app_economia.py: 84 linhas*) e avisa em voz quando termina. Na primeira vez ela pergunta
antes de baixar o modelo de escrita (2,6 GB).

**Modo conversa:** clique no microfone (ou Ctrl+Espaço) **uma vez**. A NEXUS ouve, responde em voz e
volta a ouvir sozinha. Um som curto avisa quando ela está ouvindo. Para sair, diga **"parar"**,
**"sair"** ou **"tchau"**, clique no microfone ou aperte Esc. Se clicar no microfone enquanto ela fala,
ela para de falar e passa a ouvir. Outros modos de ativação estão em Configurações → Áudio: *um comando
por clique* ou *segurar para falar*.

---

## Contents

1. [Architecture](#architecture)
2. [Needle integration](#needle-integration)
3. [Voice](#voice)
4. [Tools](#tools)
   · [Writing texts and programs](#writing-texts-and-programs)
5. [Security model](#security-model)
6. [Privacy](#privacy)
7. [Installation](#installation)
8. [First run and model management](#first-run-and-model-management)
9. [Using NEXUS](#using-nexus)
10. [Development](#development)
11. [Building NEXUS.exe](#building-nexusexe)
12. [Adding a tool](#adding-a-tool)
13. [Replacing speech engines](#replacing-speech-engines)
14. [Measured behaviour](#measured-behaviour)
15. [Troubleshooting](#troubleshooting)
16. [Known limitations](#known-limitations)

---

## Architecture

```
USER ── text ─────────────────────────────┐
  └─── microphone ─► capture ─► Whisper STT ┤   (local; Needle 3 has no audio input)
                                           ▼
                               CommandProcessor  (Needle thread)
                                           │  agent.complete(text)
                                           ▼
                                  NEEDLE 3  (tool choice + arguments + confidence)
                                           │
                             ┌─────────────┴──────────────┐
                             ▼                            ▼
                   NEXUS gate: schema check,     preflight: resolve "this folder",
                   command surface, grounding,   "report.pdf in Downloads", saved
                   confidence, safety class      places, protected paths
                             │
              execute / confirm / clarify / refuse / block
                             │
                             ▼
                   TOOL (Windows, files, apps, system)
                             │ structured result {success, message, data|error}
                             ├──► fed back to Needle (multi-step loop)
                             ▼
                   UI reply  +  local TTS (Piper) ──► speaker
                             │
                   NEXUS Core (GPU shader) reacts to mic, processing and TTS audio
```

| Layer | Module | Runs on |
| --- | --- | --- |
| UI, state, Core rendering | `app/ui/*`, `app/core/state.py` | Qt main thread (60 FPS) |
| Orchestration | `app/core/assistant.py` | main thread (signals only) |
| Command pipeline | `app/core/pipeline.py` | **Needle worker thread** |
| Needle engine | `app/ai/needle_engine.py` | Needle worker thread (the engine is one process-global, non-thread-safe model) |
| Tools | `app/tools/*` | Needle worker thread (called by the pipeline) |
| Microphone / playback | `app/audio/microphone.py`, `tts.py` | PortAudio callback threads |
| Speech recognition | `app/audio/stt.py` | STT thread |
| Speech synthesis | `app/audio/tts.py` | TTS thread |
| File / app indexes | `app/tools/file_index.py`, `app_resolver.py` | background threads |

Nothing slow ever runs on the UI thread; the Core keeps rendering at ~60 FPS during inference,
transcription and speech (measured 57-63 FPS on an Intel Iris Xe while processing and speaking).

### Project layout

```
NEXUS/
├── app/
│   ├── main.py              entry point (also --selftest, --minimized)
│   ├── selftest.py          headless check of every subsystem
│   ├── paths.py, logging_setup.py
│   ├── core/                assistant.py, pipeline.py, state.py, events.py, history.py
│   ├── ai/                  needle_engine.py, confidence.py, memory.py, model_assets.py
│   ├── audio/               microphone.py, audio_analyzer.py, stt.py, tts.py, audio_manager.py
│   ├── tools/               registry.py, applications.py, filesystem.py, system.py,
│   │                        screenshots.py, memory_tools.py, targets.py, app_resolver.py,
│   │                        file_index.py, context.py
│   ├── ui/                  main_window.py, nexus_core.py, core_dynamics.py, command_input.py,
│   │                        status.py, confirmation.py, history_panel.py, settings_window.py,
│   │                        title_bar.py, icons.py, theme.py, app_icon.py
│   └── config/              settings.py, autostart.py
├── assets/                  shaders/, fonts/ (Inter, IBM Plex Mono, OFL), config/app_aliases.json, icon/
├── scripts/                 prepare_assets.py, probe_routing.py, make_icon.py
├── tests/                   pytest suite (real Needle, real audio, real Windows APIs)
├── requirements.txt, requirements-dev.txt
├── setup.bat, run.bat, build.bat, nexus.spec
└── README.md
```

---

## Needle integration

**What comes from Needle** (`cactus-needle` 3.0.5, API verified against the repository source):

* `needle.tool` – every NEXUS tool is a decorated function; its signature becomes the argument schema,
  its docstring the description, `Args:` the per-argument descriptions, and `triggers=[...]` the
  phrasings that route to it;
* `needle.Needle(tools=..., system=..., tool_index_path=..., auto_date=False)` – one agent over all 21 tools;
* `agent.complete(text)` – each turn returns `function_calls`, `suppressed_calls`, `confidence`
  (calibrated), and `validation` (ungrounded fields, negation);
* `agent.reset()` – between independent commands;
* `agent.embed(text)` – semantic lookup of saved places;
* tool retrieval (Needle renders the 5 best tools per turn out of 21) and the grammar-constrained,
  schema-valid arguments.

**What NEXUS adds** (custom code): the manual tool loop with gating, the safety model, confirmation and
clarification, argument resolution (paths, apps, "this folder"), the tools themselves, audio, STT, TTS,
the UI and the Core.

### Why NEXUS drives the loop with `complete()` instead of `run()`

`agent.run()` executes the chosen Python function immediately. NEXUS must decide *before* execution
whether an action is allowed, so it uses the documented manual loop: `complete(text)` → gate each call →
execute → `complete(json.dumps(results))` so Needle can continue multi-step requests. When every tool
the request mentions has already run, the extra round trip is skipped (saves ~0.6-1 s per command).

### Portuguese on an English-trained model

Needle 3 is trained on English commands. On 48 Brazilian-Portuguese requests, fed to it raw:

* it picked the right tool 36/48 times;
* its **arguments** were unreliable: search queries kept the verb ("pesquise receita…"), "aumente o
  volume" became `volume_down`, "imagens" became "magens", and dictated text came back split into
  fragments.

`app/ai/language.py` therefore rewrites only the **command words** into English and leaves the content
untouched:

* "abra o bloco de notas e digite olá mundo" → `open the notepad and type olá mundo`
* "não abra o chrome" → `don't open the chrome`

It also remembers which of your words produced each token. Needle routes the normalised text, and every
argument it extracts is **mapped back onto your original words**. A few tools then take free text
verbatim from your sentence: `type_text` (dictation), `search_web` (query and site), `press_keys`,
`media_control`, `open_url`, and the memory tools. With this, the same Portuguese set routes **48/48**
(see [Measured behaviour](#measured-behaviour)). The debug panel shows the exact text Needle read
("ENTRADA DO NEEDLE").

Replies, confirmations, errors and the whole UI are translated (`app/i18n.py`). Yes/no answers ("sim",
"pode", "cancela"), choices ("o segundo") and "this folder"-style references ("esta pasta") are
understood in both languages.

### System facts

Needle receives facts only, never instructions:
`date: 2026-09-24 Thu; locale: pt-BR; device: Windows 11 desktop computer; user: <name>; assistant: NEXUS`.
The date is given to the day (not the minute) because changing the system text forces the engine to
re-process the whole prefix (≈15 s with 25 tools); the agent is rebuilt in the background when the day
or your name changes. The time comes from the `get_system_time` tool.

### Things verified about the current Needle release (September 2026)

| Finding | Consequence in NEXUS |
| --- | --- |
| Needle 3 is **text-only**: no `audio`/`sample_rate` parameters in the package, `"modalities": ["text"]` in the model config, nothing in the official docs. | Voice goes through a local Whisper STT behind `SpeechRecognizer` (spec §7 fallback). |
| `cactus-needle` 3.0.5 pins engine **3.0.2**, but Hugging Face only publishes Python engine wheels up to **3.0.1**; a fresh install fails with 404. | `ModelAssets.needle_engine()` detects the 404 and fetches the newest *published* engine with Needle's own `fetch_library`, pointing the documented `NEEDLE3_LIB_PATH` at it. Once 3.0.2 is published it is used automatically. |
| The published engine does **not write** `tool_index_path`. | NEXUS passes the path anyway; until the engine persists it, tool embeddings are rebuilt at each launch (≈12 s, in the background while the UI is up). |
| With 21 tools, Needle rarely returns an empty call for off-topic requests ("write me a poem" → `open_file("poem")` at confidence 0.99). | NEXUS gates on the **command surface**: a call is accepted only if the request matches one of the tool's declared `triggers` (Needle's own routing mechanism). See [Measured behaviour](#measured-behaviour). |
| Embeddings are strongly anisotropic (every pair scores 0.90-0.99 raw cosine). | Memory centres vectors on a fixed background vocabulary before comparing (true paraphrases 0.34-0.94, unrelated ≤ 0.32). |
| Telemetry is on by default in the package. | NEXUS sets `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1` unless you opt in (Settings → System). |

---

## Voice

```
MICROPHONE ─► capture (16 kHz mono) ─► analyzer (RMS, bands, voice activity) ─► endpoint on silence
          ─► Whisper (CPU, int8, local) ─► transcript ─► same pipeline as typed text
          ─► result message ─► Piper TTS ─► speaker (analyzer tap drives the Core)
          ─► (conversation mode) listen again
```

* **Microphone** (`sounddevice`/PortAudio): opened only while listening; delivers 16 kHz mono,
  resampling when the device cannot capture at 16 kHz; device enumeration, disconnect detection and a
  watchdog. If *System default* is a dead input (only ±1 LSB dither; seen with a virtual audio driver),
  pressing the mic automatically finds the real microphone, saves it and keeps listening.
* **Conversation mode** (the default activation): one click turns it on. NEXUS listens, answers out
  loud, then opens the microphone again by itself, with a short two-note chime (the chime's own sound is
  ignored).
  * While waiting it has no time-out: it keeps a 0.5 s pre-roll, so the start of your sentence is not cut.
  * It exits on "parar", "sair", "tchau", "stop" or "that's all", on a click on the mic, or on Esc.
  * Clicking the mic while NEXUS talks interrupts it and listens straight away.
  * Confirmations work hands-free: NEXUS asks the question, then listens for "sim"/"não".

  The other activation modes are *one command per click* and *hold to talk*.
* **Speech recognition** (`faster-whisper`):

  | Language | Model | Accuracy on synthetic speech | Time per command |
  | --- | --- | --- | --- |
  | Portuguese | multilingual `small` | 96 % similarity; `base` misheard e.g. "Que horas são?" → "Que orçam?" | ~3 s |
  | English | `base.en` | — | ~1.1 s |

  Measurements are from an i5-1235U. Segments with low confidence (`avg_logprob < -1.0`) are discarded,
  so garbled audio never becomes a command. No `initial_prompt` is used: a prompt listing example
  commands was tested and removed, because it turned unclear audio into a plausible wrong command.
* **Speech output**: Piper local neural voices, with Windows SAPI voices as the fallback.
  * Portuguese: `pt_BR-faber-medium`, with `cadu`, `jeff` and `edresson` downloadable in Settings.
  * English: `en_US-lessac-medium`.

  Speech is asynchronous, sentence by sentence, and can be interrupted in under 0.3 s; voice, speed and
  volume are configurable. TTS reads only the concise `message` of a result, never JSON, arguments or
  paths.

---

## Tools

| Tool | Safety | Notes |
| --- | --- | --- |
| `open_application` | safe | Resolver: your aliases → built-in aliases (locale-independent) → Start menu (`Get-StartApps`, Store and desktop apps) → App Paths → PATH. "chrome"/"google chrome", "vscode"/"code"/"visual studio code" map to the same app. "Chrome and Notepad" opens both. |
| `close_application` | safe | Graceful `WM_CLOSE` to the app's windows (it can still ask to save). Never touches the Windows shell or system processes. |
| `open_url` | safe | Domains, full URLs and common site names. "navegador"/"browser" as an app opens your default browser. |
| `search_web` | safe | Google (or Bing / DuckDuckGo, Settings → General), YouTube, Wikipedia, Maps, images. The query is your own words minus the command ("pesquise … no google"). Weather questions become a forecast search. |
| `type_text` | safe / **confirm** | Types exactly what you dictated, one character at a time (Windows 11 Notepad garbles bursts: "NEXUS" arrived as "....." before this was fixed). Into the app NEXUS just opened: immediately. Into any other window: asks first ("Digitar em “Documento1 - Word”?"). Never into terminals, the Run dialog or NEXUS itself. Dictation only: "escreva um poema" is `write_text`. |
| `write_code` | safe | Writes a new program with the [writing model](#writing-texts-and-programs) into `Documents\NEXUS\Projetos\<name>\<name>.py` (or .html, .js, …), live in the editor you named or the first one installed (Antigravity, VS Code, Cursor, Windsurf, Sublime, Notepad++, else Notepad). Python is syntax-checked and rewritten once if it doesn't compile. |
| `edit_code` | safe | Rewrites the last program with your change ("adicione um botão de exportar"); the previous version is kept as `<name>.anterior.py`, and restored if the change fails. |
| `write_text` | safe / **confirm** | Writes a new text (poem, email, letter, list…) and types it live: into a new file in `Documents\NEXUS\Textos` opened in Notepad (saved at the end), or into the app you named. "Aqui" / "nessa janela" types into your last window, after asking. If you switch windows it stops typing and saves the whole text instead. |
| `run_program` | **confirm** | Runs the last program NEXUS wrote (Python in a console window that stays open, HTML in the browser). Always asks: it is AI-generated code. |
| `press_keys` | safe / **confirm** | Keys and shortcuts: enter, tab, esc, arrows, F-keys, ctrl/alt/shift/win chords; "salve" = Ctrl+S, "desfaça" = Ctrl+Z, "nova aba", "selecione tudo", "atualize"… Same target rules as `type_text`. |
| `media_control` | safe | Volume up/down (±10 %), mute, play/pause, next, previous (system media keys). |
| `open_file`, `open_folder` | safe | Full paths, known folders, saved places, "this folder", "the second one", "report.pdf in Downloads", then a bounded search. |
| `search_files` | safe | Name, partial name, extension, folder. |
| `create_file`, `create_folder` | safe | Never overwrite. |
| `rename_file`, `copy_file`, `move_file` | **confirm** | Target resolved first; the dialog shows exactly what will change. |
| `delete_file` | **confirm** | Sends to the **Recycle Bin**. |
| `take_screenshot` | safe | All monitors → `Pictures\NEXUS\Screenshots\NEXUS_<date>_<time>.png`. |
| `get_system_time`, `get_system_information` | safe | |
| `lock_windows` | safe | |
| `shutdown_windows`, `restart_windows` | **confirm** | `shutdown /s|/r /t 5`, only after confirmation. |
| `remember_location`, `remember_app_alias`, `forget_location` | safe | Local memory ("My coding project is D:\Projects\Nexus"). |

The open-family tools share one target resolver, so if Needle picks `open_file` for "Open Chrome",
Chrome still opens.

File search covers Desktop, Documents, Downloads, Pictures, Music, Videos, OneDrive, saved places and
extra roots from settings. It is limited in depth, skips dependency and cache folders, is built in the
background and refreshed every 10 minutes. It never scans the whole drive.

Every tool returns the same contract:

```json
{"success": true,  "message": "Chrome opened.", "data": {}}
{"success": false, "message": "Chrome was not found.", "error": "APPLICATION_NOT_FOUND"}
```

---

## Writing texts and programs

Needle 3 is a small function-calling model: it decides that "abra o antigravity e escreva um código em
.py pra criar um app de economia" is `write_code(editor="Antigravity")`, but it cannot write the app.
That is done by a second local model, **Qwen3.5** (Apache-2.0), run by the official **llama.cpp**
`llama-server` on `127.0.0.1`. No cloud, no account, no key.

```
"abra o antigravity e escreva um código em .py pra criar um app de economia"
  Needle 3 ──► write_code  (+ open_application folded in: one call, no empty extra window)
  tool     ──► Documents\NEXUS\Projetos\app_economia\app_economia.py, opened in Antigravity
             ► answers at once: "Certo! Vou escrever o app de economia em Python no Antigravity IDE…"
  job      ──► Qwen3.5 streams the file; it is saved twice a second, so the editor shows it being written
             ► Python: compile() check; on a syntax error one automatic rewrite with the error message
             ► "Pronto! Escrevi o app de economia: 143 linhas em app_economia.py. Diga “rode o programa”…"
```

* **Background job.** Writing runs on its own thread; NEXUS keeps taking commands (and listening in
  voice mode). One job at a time. *cancela* / *para de escrever* / Esc stops it; what was written stays.
  The announcement never talks into an open microphone: listening pauses for it and resumes after.
* **Dictation vs composition.** "escreva comprar pão e leite" types those words (`type_text`); "escreva
  um poema sobre o mar" composes (`write_text`); "crie um app…" writes code (`write_code`). The
  triggers in `app/tools/intents.py` tell them apart; when Needle answers a composition request with
  `type_text`, the pipeline switches to the one creation tool the request matches (their arguments are
  rebuilt from your words, so nothing of the wrong call is kept).
* **Prompts** ask for the file only (fences and chatter are stripped as it streams), Portuguese UI
  texts, standard-library Python (tkinter for windows, JSON next to the program for data) so the result
  runs without installing anything, a single self-contained file for websites, and a lean solution
  (~150 lines) because every line costs time on a CPU.
* **Text** is typed word by word, markdown removed. Notepad gets a new named file (so nothing you had
  open is touched) and Ctrl+S at the end. Typing only continues while the target window is in the
  foreground.
* **Runtime.** `llama-server.exe` (llama.cpp **b11185**, CPU build, 18 MB) and the GGUF (pinned
  revision) are downloaded once, SHA-256 checked, resumable, into `%LOCALAPPDATA%\NEXUS\models\llama`
  and `...\writer`. The first write request asks before downloading. The server starts on demand
  (~5–11 s), runs at below-normal priority (it uses every core; the UI, microphone and Needle stay
  responsive), stops after 10 idle minutes (Settings → AI) to free its ~3 GB of RAM, is killed with
  NEXUS (job object, `atexit`) and orphans of a crashed NEXUS are cleaned up at the next start.
* **Development note.** Microsoft Store Python shows `%LOCALAPPDATA%` through a per-package overlay, and
  programs it starts inherit that view. `paths.launchable()` starts `llama-server` from the overlay's
  real folder; start `dist\NEXUS.exe` itself from Explorer or a normal shell, not from a Store-Python
  script, or it looks for its models in the overlay.

Settings → AI: **Writing model** (Qwen3.5 4B, default, best quality · Qwen3.5 2B, faster), **Code editor**
(empty = automatic), **Save creations in** (default `Documents\NEXUS`).

Measured on the development laptop (i5-1235U, 16 GB, no dedicated GPU):

| | Qwen3.5 4B (Q4_K_M, 2.6 GB) | Qwen3.5 2B (Q4_K_M, 1.3 GB) |
| --- | --- | --- |
| generation, short burst | 5.7 tokens/s | 9.4 tokens/s |
| generation, sustained (CPU at thermal limit) | 2.8–3.9 tokens/s | ~9 tokens/s over 2 min |
| "app de economia" (the request above) | 140–191 lines in 7.5–8.5 min | 99 lines in 115 s, but with logic bugs |
| a four-line poem | ~35 s | ~18 s |

The Iris Xe GPU (Vulkan build) was not faster (5.0 tok/s), and speculative decoding was slower on this
CPU (0.8B draft model: 2.0 tok/s; n-gram: 3.3 tok/s), so NEXUS uses the plain CPU build.

---

## Security model

*Needle decides which action the user wants. NEXUS decides whether it may run now.*

For each proposed call, in order:

1. **Schema validation**: unknown tools, missing, wrong-type, or verb-only arguments ("open" alone) are dropped.
2. **Command surface**: the request must match the tool's declared trigger phrasings. Otherwise the
   answer is *"I can't do that yet."* This can be switched off in Settings → AI.
3. **Grounding**: fields the engine flags as ungrounded are refused, unless the argument was rebuilt
   from your own words. A request the engine flags as negated is never executed without asking.
   Repeated identical calls in one request run once.
4. **Preflight**: the target is resolved.
   * Protected locations are **BLOCKED** outright: drive roots, Windows, Program Files, ProgramData, the
     profile root, AppData, the standard user folders themselves, and NEXUS's own folder.
   * Typing and key presses go straight only into the app NEXUS just opened. Any other window asks
     first. Terminals, the Run dialog and NEXUS itself are **BLOCKED**, because typed text there could
     become a command. The target window must be verified in the foreground before a single key is sent.
5. **Confidence gate** (Settings → AI):

   | Confidence | Safe tool | Confirmation-required tool |
   | --- | --- | --- |
   | ≥ 0.30 | execute | confirm |
   | 0.10 – 0.30 | confirm ("Open Chrome?") | confirm |
   | < 0.10 (the engine's own withholding floor) | refuse | refuse |
   | none (weights without a calibration head) | confirm | confirm |
   | engine-withheld (`suppressed_calls`) | confirm if ≥ 0.10 | confirm |

Destructive actions always need confirmation, whatever the confidence. Confirmation accepts the
buttons, *yes/confirm/go ahead* or *no/cancel* (typed or spoken), and Esc. Keyboard focus starts on
**Cancel**.

Ambiguity is never guessed. "Delete plan.txt" with two matches produces *"Which file do you mean:
plan.txt in a, or plan.txt in b?"*, and "the second one" or "the one in b" answers it.

---

## Privacy

* Microphone audio is processed in memory and never stored or sent anywhere. The microphone is open only
  while listening.
* Normal commands never reach a cloud API. Needle, Whisper and Piper all run locally.
* Internet is used only to download models the first time (Hugging Face: Needle engine and weights,
  Systran Whisper, rhasspy Piper voices, the Qwen3.5 writing model; GitHub: the llama.cpp runtime).
  After that NEXUS works offline. What you ask it to write never leaves the machine.
* Needle's anonymous usage counts are **off** by default.
* Logs stay local (`logs\nexus.log`, rotating, 3 × 2 MB). A filter masks anything that looks like a
  password, token or API key.
* Memory holds only what you ask NEXUS to remember, plus the last 50 successful commands (tool and
  arguments). History holds the last 200 command/reply pairs. Both are plain JSON in `%APPDATA%\NEXUS`
  and can be deleted at any time.

---

## Installation

Requirements: Windows 10/11 x64, Python **3.10–3.13** (python.org build recommended), a microphone and
speakers for voice, and an OpenGL 3.3 GPU for the shader Core (a software Core is used otherwise).

```bat
setup.bat
```

`setup.bat`:

1. checks Python
2. creates `.venv`
3. installs `requirements.txt`
4. installs `cactus-needle`
5. downloads and verifies the models
6. checks the microphone, the voice and a real Needle tool call

## First run and model management

On the first start, if a model is missing, NEXUS shows *"Preparing local AI core..."* with real download
progress. Text commands are available as soon as Needle is ready (~25 s on a laptop, mostly tool
embedding). Voice becomes available when Whisper and Piper are loaded, in parallel.

| Model | Size | Cache |
| --- | --- | --- |
| Needle 3 weights `needle3.cact` | 35 MB | `%USERPROFILE%\.cache\cactus-needle\v3\` (Needle's own cache) |
| Needle engine `libneedle.dll` | 1.2 MB | same, or `%LOCALAPPDATA%\NEXUS\models\needle\engine\<ver>\` (fallback, see above) |
| Whisper `base.en` | ~140 MB | `%LOCALAPPDATA%\NEXUS\models\whisper\` |
| Piper voice | ~60 MB each | `%LOCALAPPDATA%\NEXUS\models\piper\` |

Nothing is downloaded again once cached. For a tuned Needle model, set Settings → AI → *Needle model* to
a `.cact` file. LoRA builds without a calibration head report no confidence, so every action will ask
first.

Configuration: `%APPDATA%\NEXUS\config.json` (settings), `memory.json`, `history.jsonl`.

## Using NEXUS

| | |
| --- | --- |
| Enter | run the typed command |
| Ctrl+Space / mic button | talk |
| Esc | stop listening, cancel a confirmation, or stop speech |
| ↑ / ↓ | previous commands |
| Ctrl+H | history panel |
| Ctrl+D | debug panel (transcript, tool, arguments, confidence, route, result, timings; no model reasoning) |
| Ctrl+, | settings |

Examples: *Open Chrome* · *close notepad* · *go to github.com* · *open my downloads folder* ·
*find files named invoice* · *create a file called notes.txt on the desktop* · *delete old.txt* ·
*take a screenshot* · *what time is it* · *show system information* · *lock the computer* ·
*My coding project is D:\Projects\Nexus* → *open my coding project* ·
*Open my programming project and then open README.md*.

Settings has five sections:

* **Audio**: microphone, speaker, sensitivity, sample rate, activation mode, silence timeout, live mic test.
* **Voice**: on/off, Piper or Windows voices, voice (curated Piper voices download on demand), speed,
  volume, test.
* **AI**: Needle model path, tool index path, thresholds, command-surface gate, speech model.
* **System**: your name, start with Windows (per-user Run key), start minimized, telemetry.
* **Debug**: logs, level, show tool calls, show confidence.

---

## Development

```bat
run.bat               :: start without a console
run.bat --console     :: start with a console
.venv\Scripts\python -m pytest            :: 53 tests, ~45 s (real Needle / Whisper / Piper / mic)
.venv\Scripts\python -m scripts.probe_routing "open chrome" "delete old.txt"
.venv\Scripts\python -m app.main --selftest
```

> **Microsoft Store Python:** it redirects `%APPDATA%`/`%LOCALAPPDATA%` writes into its package sandbox
> (`%LOCALAPPDATA%\Packages\PythonSoftwareFoundation...\LocalCache`), including the *Start with Windows*
> registry value. Development still works; the packaged `NEXUS.exe` uses the real folders. Prefer the
> python.org installer.

## Building NEXUS.exe

```bat
build.bat            :: dist\NEXUS.exe (single file, ~163 MB)
build.bat onedir     :: dist\NEXUS\NEXUS.exe (folder; starts faster)
```

The build renders the icon, runs PyInstaller with `nexus.spec`, then runs the **packaged** exe with
`--selftest`. The self-test exercises Needle, three real text commands (including the shutdown
confirmation gate, with the power command intercepted), Piper, SAPI, Whisper on synthesized speech, the
microphone, a screenshot, the app index and OpenGL. Its report goes to `dist\logs\selftest.json`.

Bundled: Python, PySide6 (OpenGL modules only), cactus-needle, faster-whisper/CTranslate2, Piper
(espeak-ng data), onnxruntime, PortAudio, pywin32, shaders, fonts, default aliases.
**Not bundled** (downloaded on first run and cached): the Needle engine and weights, the Whisper model,
the Piper voices.

## Adding a tool

```python
# app/tools/system.py (or a new module listed in app/tools/__init__.py)
from app.tools.registry import Safety, nexus_tool, ok

@nexus_tool(safety=Safety.SAFE, triggers=[r"\bcalculator\b"], confirm="Open the calculator?")
def open_calculator():
    """Open Windows Calculator."""
    import os
    os.startfile("calc.exe")
    return ok("Calculator opened.")
```

`nexus_tool` applies `@needle.tool` and registers the safety class. Nothing else changes. For tools that
modify things, use `Safety.CONFIRMATION_REQUIRED` and add a `@my_tool.preflight` that resolves the
target and fills the confirmation text. Afterwards, run `scripts/probe_routing.py` with a few phrasings
to check that Needle routes them.

## Replacing speech engines

* **STT**: implement `SpeechRecognizer` (`load`, `ready`, `transcribe(audio_float32_16k) -> str`) in
  `app/audio/stt.py` and return it from `create_recognizer`.
* **TTS**: implement `SpeechSynthesizer` (`load`, `voices`, `synthesize(text, speed)` yielding
  `(float32 samples, rate)`). `LocalTTS` handles playback, interruption, volume and the analyzer tap.

---

## Measured behaviour

All numbers are from this machine (i5-1235U, Iris Xe, Windows 11), September 2026.

**Routing.** `scripts/probe_routing.py` runs requests through the real pipeline in dry-run mode: the
real Needle engine, normalisation, mapping back, repair, the trigger gate and the confidence policy,
with nothing executed. **106/106** route as expected: **48/48** in English and **58/58** in Portuguese,
including 16 creation requests (write_text, write_code, edit_code, run_program).

* Covered: apps, web search, typing, keys, media, files, system, memory, and the multi-step
  "abra o bloco de notas e digite olá mundo" → `open_application` + `type_text("olá mundo")`.
* Arguments are the user's words:
  * "pesquisa no youtube música do Legião Urbana" → query "música do Legião Urbana", site youtube
  * "digite olá, tudo bem com você?" → text "olá, tudo bem com você?"
* Off-topic requests are refused in both languages: *joke, meaning of life, hello, "não abra o chrome",
  "restart chrome"*.
* Creation requests Needle alone got wrong, and what NEXUS does with them: "open vs code and write a
  snake game in python" → Needle: three `open_application` calls ("Vocal", "Snake Game"); "crie um site
  para a minha padaria" → `open_url("my padaria")`; "...renames my photos by date" → `get_system_time`.
  All become the one creation tool the request matches (see *Writing texts and programs*).
* With 30 tools Needle started answering "open notepad and type hello world" with
  `open_application("hello world")`; an app name that is exactly the dictated text is now treated as
  dictation.
* Without triggers, the same model sent *"close chrome"* to `lock_windows`, *"quit notepad"* to
  `delete_file`, and *"Shutdown the computer"* to `restart_windows`, all at confidence 1.0.
* Without normalisation, raw Portuguese scored 36/48, with unusable arguments.

**Confidence calibration.** Correct routed calls scored 0.42–1.0 in English and 0.13–1.0 in Portuguese
(most ≥ 0.33). Off-topic calls scored 0.70–1.0. Confidence alone therefore cannot separate them: the
command-surface gate does. The thresholds (execute ≥ 0.30, refuse < 0.10, which is also the engine's
own floor) are set so correct calls run. Anything in between is shown to you first, e.g. "escreva
comprar pão e leite" at 0.13–0.19 asks before typing. Confidences shift by a few points whenever the tool
set changes, which is why the confirm band is wide.

**Latency.**

| Step | Time |
| --- | --- |
| Needle turn | 0.3–1.1 s (up to ~2 s under heavy load) |
| Tool | < 0.3 s; typing ~13 ms per character |
| Whisper | ~1.1 s English (`base.en`), ~3 s Portuguese (`small`) |
| Piper, first sentence | ~0.4 s |
| Voice command, end of speech → spoken reply | ~2.5–3.5 s English, ~4–5 s Portuguese |
| Startup | ~20 s until text commands work (tool embedding), voice in parallel |

**Tests.**

* `pytest`: 166 tests pass, including:
  * Portuguese through the real Needle engine: time, refusals, shutdown confirmation with "sim",
    clarification with "o segundo", file creation, and routing arguments;
  * creation requests routed by the real Needle engine (EN and PT), and the pipeline rules for them
    (reroute, fold "abra o …" in, drop tools named inside the description);
  * the real writing model: a Python program written, checked and run (it must not crash); a poem
    typed live into another application;
  * text inserted into a standard Win32 Edit control; real typing into another application;
  * the static check on the two real bugs Qwen3.5-4B wrote (widgets local to `main()`, a missing
    `self.` method);
  * the conversation loop with the real microphone, Needle, Whisper and TTS: listen → answer → listen
    again → "parar".
* Real Portuguese run of the GUI on this machine:
  * "que horas são" → "Agora são 14h54, sexta-feira, 25 de setembro."
  * "abra o bloco de notas e digite …" opened Notepad and typed the sentence (after the two typing fixes
    above, verified character-exact in Notepad).
  * "pesquise receita de bolo de cenoura no google" opened the search.
  * "desligue o computador" asked "Desligar o computador?".
* Packaged `NEXUS.exe --selftest`: all 14 checks pass, including creation routing and the writing
  model answering from inside the exe (see *Building NEXUS.exe*).
* Writing, end to end on this machine: "abra o antigravity e escreva um código em .py pra criar um app de
  economia" wrote `app_economia.py` live in Antigravity (191 lines, 8.4 min, passed the checks, starts
  and runs); "escreva um poema curto sobre café no bloco de notas" wrote it into a new Notepad tab and
  Notepad saved it (32 s).
* Real-microphone voice commands passed via an acoustic loopback (speaker → room → built-in mic):
  *"Take a screenshot."* → screenshot saved → spoken reply.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Stuck on "Preparing local AI core..." | First run needs internet for the models; check `logs\nexus.log`. Run `python -m scripts.prepare_assets`. |
| "Needle engine … not published" in the log | Expected with cactus-needle 3.0.5; the newest published engine is used instead. |
| Mic does nothing / "isn't picking up any sound" | Settings → Audio → pick your microphone and use *Test microphone*. Check Windows Settings → Privacy → Microphone. |
| Voice misheard | Speak close to the mic. English: try `small.en` (more accurate, slower). Portuguese already uses `small`; `base` is faster but misses more. |
| Typing went to the wrong place / asks "Digitar em …?" | NEXUS types into the app it just opened, otherwise into the last app you used and asks first. Click the target app once, then speak. |
| Conversation mode reacts to TV or people talking | Say "parar" or press Esc; use *one command per click* in Settings → Audio in noisy rooms. |
| "I can't do that yet." for a valid request | Phrase it with a supported verb (see examples), or turn off *Only act on supported command phrasings* in Settings → AI (less safe). |
| An app isn't found | Say the name as it appears in the Start menu, or teach it: *remember that editor means Visual Studio Code*. |
| Black or simple Core | OpenGL 3.3 unavailable (VM / remote desktop): the software Core is used. |
| No voice output | Settings → Voice → *Test voice*; switch to Windows voices if the Piper voice failed to download. |
| Writing is too slow | Settings → AI → *Writing model* → Qwen3.5 2B; ask for smaller programs; plug the laptop in (it throttles on battery). |
| "O modelo de escrita não iniciou" | See `logs\writer-server.log`; about 3 GB of free RAM is needed. |
| The code opened in Notepad instead of my editor | Name it ("…no antigravity") or set Settings → AI → *Code editor* to its Start-menu name. |

## Known limitations

* Needle 3 has no audio input, so speech recognition is Whisper (local).
* Needle is trained on English. Portuguese works through command-word normalisation plus argument
  mapping and repair (see *Portuguese on an English-trained model*). Unusual Portuguese phrasings whose
  verbs are not in the normaliser may be refused ("Ainda não consigo fazer isso"). Adding a verb is a
  one-line change in `app/ai/language.py`.
* Off-topic detection relies on the command-surface gate (trigger phrasings), because the base model
  seldom refuses on its own with 25 tools. Unusual phrasings of supported actions may be refused.
* Writing is slow on a laptop CPU: a ~150-line program takes several minutes with the 4B model, and the
  CPU slows down further under sustained load. The 2B model is about twice as fast but its programs
  more often have logic bugs. A 4B model is far from a large cloud model: programs are small and may
  need a follow-up ("corrija o botão de salvar"). Python is syntax-checked; runtime behaviour is not
  verified before you run it.
* Writing needs a one-time 2.6 GB download and ~3 GB of free RAM while the model is loaded.
* Opening a folder for the first time in Antigravity / VS Code shows *Restricted Mode* (workspace
  trust); the code is still shown and editable.
* NEXUS still doesn't chat: questions ("quem descobriu o Brasil?") are not answered, only creation
  requests use the writing model.
* Conversation mode listens continuously while on. Other people talking can start a command; everything
  still passes the same gates, and destructive actions still ask.
* The published engine doesn't persist the tool index, so each launch spends ~15 s embedding tools in
  the background.
* **Voice test B** ("Open Notepad." spoken into the microphone) was **not** verified with a real voice
  here. Laptop-speaker loopback was too distorted to transcribe that phrase (first attempt heard "Open
  Chrome", which led to removing the Whisper prompt; later attempts were rejected safely). The same
  real-microphone path passed test C ("Take a screenshot."). Please try it with your own voice.
* Closing apps is graceful: an app with unsaved work may stay open to ask you.
* *Start with Windows* writes `HKCU\...\Run`; under Microsoft Store Python this is sandboxed (works in the exe).
