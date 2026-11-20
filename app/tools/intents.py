"""Trigger phrasings that tell dictation, composition and code apart.

"escreva comprar pão e leite" is dictation (type_text), "escreva um poema sobre
o mar" is composition (write_text), "crie um app de economia em python" is code
(write_code). All three reach Needle as English command skeletons ("write ...",
see app/ai/language.py); these patterns run on that skeleton.

The engine compiles triggers too, so they stay within the regex subset the
existing tools use: capturing groups, look-aheads, ``\\b``, ``[\\s\\S]``.
"""

from __future__ import annotations

TEXT_VERB = r"(write|compose|draft|create|make|generate)"
TEXT_NOUN = (r"(poem|poems|story|stories|essay|letter|email|e-mail|message|text|article|summary|song|music|lyrics|"
             r"speech|list|post|caption|joke|jokes|paragraph|review|report|recipe|note|description|bio|toast|prayer|"
             r"verse|verses|haiku|sonnet|rap|chronicle|cronica|roteiro|invitation|convite|resume|poetry)")
# "write a poem ...", "create a shopping list", "write me an email ..." (not "create a text file")
WRITE_TEXT = (rf"\b{TEXT_VERB}\s+(me\s+|for me\s+|to me\s+)?(a|an|some|one|two|three|four|five|\d+)\s+"
              rf"([\w-]+\s+){{0,3}}{TEXT_NOUN}\b(?!\s+(file|files|folder|document)\b)")
# "write about ...", "write something about ..."
WRITE_ABOUT = r"\b(write|compose|draft)\s+(me\s+|for me\s+)?(something\s+|a bit\s+|a little\s+)?about\b"

CODE_VERB = r"(write|create|make|build|develop|generate|program|code)"
CODE_NOUN = (r"(code|program|programs|script|scripts|app|apps|application|game|games|website|websites|webpage|"
             r"web page|landing page|function|bot|api|software|system|calculator|tracker|dashboard)")
CODE_LANG = (r"(\b(python|javascript|typescript|html|css|java|rust|golang|kotlin|php|sql|powershell)\b|\bc\+\+|\bc#|"
             r"\.(py|js|html|ts|java|cpp|cs|ps1)\b)")
NOT_NAMED_FILE = r"(?![\s\S]*\b(file|folder|directory)s?\s+(called|named)\b)"
WRITE_CODE = rf"\b{CODE_VERB}\b{NOT_NAMED_FILE}[\s\S]*\b{CODE_NOUN}\b"
WRITE_CODE_LANG = rf"\b{CODE_VERB}\b{NOT_NAMED_FILE}[\s\S]*{CODE_LANG}"

# Any composition request (used to keep type_text to pure dictation).
COMPOSE = rf"({WRITE_TEXT}|{WRITE_ABOUT}|{WRITE_CODE}|{WRITE_CODE_LANG})"

EDIT_VERB = r"(add|put|change|fix|improve|modify|update|include|rewrite|refactor|increase|decrease)"
EDIT_NOUN = (r"(code|program|app|game|script|site|website|page|button|buttons|screen|menu|function|feature|chart|"
             r"graph|color|colors|theme|layout|field|table|interface)")
EDIT_CODE = rf"\b{EDIT_VERB}\b(?![\s\S]*\b(file|files|folder|folders|directory)\b)[\s\S]*\b{EDIT_NOUN}\b"

RUN_PROGRAM = r"\b(run|execute|test)\b[\s\S]*\b(program|code|script|app|game|it|website|site|page)\b"
