Model files are not stored here.

NEXUS downloads its models once and caches them outside the project:

| Model | Location |
| --- | --- |
| Needle 3 weights (`needle3.cact`, 35 MB) | `%USERPROFILE%\.cache\cactus-needle\v3\<engine>\` (cactus-needle's own cache) |
| Needle 3 engine (`libneedle.dll`, 1.2 MB) | same cache, or `%LOCALAPPDATA%\NEXUS\models\needle\engine\<version>\` when the pinned engine is not published |
| Whisper `base.en` (CTranslate2, ~140 MB) | `%LOCALAPPDATA%\NEXUS\models\whisper\base.en\` |
| Piper voice (`en_US-lessac-medium`, ~60 MB) | `%LOCALAPPDATA%\NEXUS\models\piper\` |

Run `python -m scripts.prepare_assets` (or `setup.bat`) to fetch them.
