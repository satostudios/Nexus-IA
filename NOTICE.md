# Avisos de terceiros

O código deste repositório é MIT (veja [LICENSE](LICENSE)). A NEXUS usa bibliotecas e modelos de terceiros, cada um com a sua licença. Eles não fazem parte do código deste repositório: são instalados pelo `setup.bat` ou baixados na primeira abertura.

| Componente | Uso | Licença |
| --- | --- | --- |
| cactus-needle (Needle 3) | escolhe a ferramenta para cada pedido | Apache-2.0 |
| faster-whisper / CTranslate2 | reconhecimento de voz local | MIT |
| Modelos Whisper (Systran) | reconhecimento de voz | MIT |
| piper-tts | voz neural local | GPL-3.0-or-later |
| Vozes Piper | voz | licença de cada voz (veja o cartão do modelo) |
| llama.cpp (`llama-server`) | roda o modelo de escrita | MIT |
| Qwen3.5 | modelo de escrita | Apache-2.0 |
| PySide6 (Qt) | interface | LGPL-3.0 |
| sounddevice / PortAudio | microfone e som | MIT |
| pywin32, psutil, mss, numpy, Send2Trash, pyflakes | integração com o Windows | licenças próprias (PSF, BSD, MIT) |
| Inter, IBM Plex Mono | fontes em `assets/fonts` | SIL Open Font License 1.1 |

**Sobre o `NEXUS.exe` publicado nos Releases:** o executável empacota o Python e as bibliotecas acima, inclusive o `piper-tts`, que é GPL-3.0. Por isso o executável, como um todo, é distribuído nos termos da GPL-3.0, e o código-fonte completo dele é este repositório. O código da própria NEXUS continua MIT.
