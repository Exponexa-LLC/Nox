"""Exponexa — TUI do harness (Textual), no pacote técnico `nox`.

O nome é dividido de propósito: **Exponexa** é o que o usuário vê (título da
TUI, cabeçalho, boas-vindas) e **nox** é o nome técnico — pacote, pasta, módulo
e comando de terminal. Um não deve vazar para o lugar do outro.

O backend padrão é a CLI oficial do Claude Code (`claude -p`), usando a sessão
já autenticada. Sem SDK anthropic, sem ANTHROPIC_API_KEY e sem ferramentas.

Como executar: `python -m nox` com o ambiente do projeto ativo, ou o comando
`nox` (instalado como entry point, ou o `nox.cmd` da raiz num checkout local).
Existe no PyPI um pacote de automação também chamado `nox`; por isso a
distribuição aqui se chama `exponexa-nox` e o ambiente do projeto deve ficar
isolado. A única dependência é o Textual.
"""

from __future__ import annotations

__all__ = [
    "APP_COMMAND",
    "APP_NAME",
    "APP_TITLE",
    "__version__",
]

__version__ = "0.7.0"

#: Nome técnico: pacote Python, pasta e módulo (`python -m nox`).
APP_NAME = "nox"

#: Nome público, exibido na interface. Nunca use isto em caminho ou import.
APP_TITLE = "Exponexa"

#: Comando de terminal que inicia a aplicação.
APP_COMMAND = "nox"
