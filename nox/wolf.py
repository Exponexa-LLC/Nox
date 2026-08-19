"""Mascote estático, dados falsos e textos do 🐺 Exponexa (pacote `nox`).

Nada aqui faz rede, disco ou chamada de modelo: é tudo local e simulado.
"""

from __future__ import annotations

import random
import time
from typing import List, Tuple

from rich.text import Text

# --------------------------------------------------------------------------
# Mascote — versão compacta e ESTÁTICA do lobo pixelado original.
#
# Origem: arte-vetor do mascote (59x50 px), reamostrada para 12x10.
# O raster original foi reamostrado por média de área para 12x10 pixels,
# preservando proporção, silhueta e a paleta exata do mascote. Cada linha de
# texto desenha dois pixels com meio-blocos, então 10 px = 5 linhas.
# Não há animação: existe um único quadro, sempre de boca fechada.
# --------------------------------------------------------------------------

#: Paleta original do mascote (nenhuma cor nova foi inventada).
WOLF_PALETTE = {
    "E": "#e6e1d8",  # pelo claro
    "D": "#d3ccc1",  # pelo médio
    "B": "#b9b0a2",  # sombra
    "K": "#2e2822",  # focinho e faixa do olho
    "F": "#faf8f5",  # dentes
    "W": "#ffffff",  # brilho do olho
    "R": "#c4514c",  # boca
    "A": "#3d4670",  # ponta da orelha
}

#: 10 linhas de pixels x 12 colunas ("." = transparente).
WOLF_PIXELS: Tuple[str, ...] = (
    "............",
    ".....B..B...",
    ".....DEED...",
    ".....EDEEEB.",
    "....EBKDEEB.",
    "KFFFEEEEEEBB",
    "KKKWKDDDEDBB",
    "KBBDDDDDDDB.",
    ".DDDDDDDBBB.",
    ".......BBB..",
)

#: Largura fixa da arte, em colunas.
WOLF_WIDTH: int = 12

#: Altura fixa da arte, em linhas de texto.
WOLF_HEIGHT: int = 5

_UPPER_HALF = "▀"
_LOWER_HALF = "▄"


def wolf_art() -> Text:
    """Desenha o lobinho compacto estático com meio-blocos coloridos."""
    art = Text()
    for row in range(0, len(WOLF_PIXELS), 2):
        top = WOLF_PIXELS[row]
        bottom = WOLF_PIXELS[row + 1] if row + 1 < len(WOLF_PIXELS) else "." * WOLF_WIDTH
        for column in range(WOLF_WIDTH):
            above = WOLF_PALETTE.get(top[column])
            below = WOLF_PALETTE.get(bottom[column])
            if above and below:
                art.append(_UPPER_HALF, "{0} on {1}".format(above, below))
            elif above:
                art.append(_UPPER_HALF, above)
            elif below:
                art.append(_LOWER_HALF, below)
            else:
                art.append(" ")
        if row + 2 < len(WOLF_PIXELS):
            art.append("\n")
    return art


# --------------------------------------------------------------------------
# Sessão — identificador local, sem rede e sem persistência
# --------------------------------------------------------------------------


def new_session_id() -> str:
    """Gera um id de sessão local, apenas para exibição no cabeçalho."""
    return "sess-{0}{1:02x}".format(time.strftime("%H%M"), random.randrange(256))


# --------------------------------------------------------------------------
# Modelos "selecionáveis" — apenas rótulos, nenhum backend por trás
# --------------------------------------------------------------------------

MODEL_OPTIONS: List[Tuple[str, str]] = [
    ("sonnet", "sonnet"),
    ("opus", "opus"),
    ("haiku", "haiku"),
]

DEFAULT_MODEL: str = MODEL_OPTIONS[0][1]


def next_model(current: str) -> str:
    """Devolve o próximo modelo da lista, circulando."""
    values = [value for _label, value in MODEL_OPTIONS]
    if current not in values:
        return values[0]
    return values[(values.index(current) + 1) % len(values)]


# --------------------------------------------------------------------------
# Textos fixos
# --------------------------------------------------------------------------

BANNER: str = (
    "Conectado pela CLI oficial do Claude Code, na sessão já autenticada. "
    "Sem chave de API e sem ferramentas: o modelo só conversa."
)

#: Ajuda em pares rótulo/descrição, renderizada como bloco alinhado na TUI.
HELP_ROWS: List[Tuple[str, str]] = [
    ("/help", "mostra esta ajuda"),
    ("/new", "começa uma conversa nova (descarta o contexto)"),
    ("/clear", "limpa a tela, sem perder o contexto"),
    ("/model", "abre o seletor de modelos; /model <nome> escolhe direto"),
    ("/profile", "perfil de política: conversa, desenvolvimento, remoto"),
    ("/provider", "lista provedores; /provider <nome> troca"),
    ("/refresh-models", "reconsulta a lista; --sonda confirma com chamadas reais"),
    ("/remote", "diagnóstico em servidor autorizado, somente leitura"),
    ("/status", "provedor, backend, modelo, sessão, workspace e config"),
    ("/workspace", "mostra a pasta atual; /workspace <caminho> troca"),
    ("/copy", "copia a última resposta; /copy tudo copia o transcript"),
    ("/exit", "encerra o programa"),
    ("esc", "interrompe uma resposta em andamento"),
    ("ctrl+c", "copia a seleção feita com o mouse"),
    ("ctrl+y", "copia a última resposta"),
    ("", "qualquer outro texto vai para o modelo, com o contexto da conversa"),
]


# --------------------------------------------------------------------------
# Respostas simuladas
# --------------------------------------------------------------------------

_TEMPLATES: List[str] = [
    "Anotado: “{eco}”. Ainda estou sem backend, então isto é só uma resposta de vitrine.",
    "Se eu estivesse conectado, atacaria “{eco}” em três passos: entender, planejar, executar.",
    "“{eco}” — recebido em {chars} caracteres. Resposta simulada, sem nenhuma chamada externa.",
    "Farejei “{eco}” e não achei backend nenhum por perto. Protótipo visual, lembra?",
    "Boa. Guardei “{eco}” no transcript falso. Quando houver modelo, isto vira resposta de verdade.",
]


def fake_reply(text: str, model: str = DEFAULT_MODEL) -> str:
    """Gera uma resposta simulada para `text`, sem qualquer chamada externa."""
    clean = " ".join(text.split())
    eco = clean if len(clean) <= 48 else clean[:45] + "..."
    body = random.choice(_TEMPLATES).format(eco=eco, chars=len(clean))
    return "{0}\n[{1} · simulado]".format(body, model)
