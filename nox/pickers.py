# -*- coding: utf-8 -*-
"""Seletores visuais de modelo e provedor — só montagem de linhas e desenho.

Nada aqui consulta o app nem guarda estado: as linhas chegam prontas de quem
sabe (o backend ativo, no caso dos modelos; o registro de provedores, no caso
dos provedores), e as funções só decidem como isso vira texto na tela. Assim o
conteúdo do menu nunca é uma lista fixa da interface.
"""

from __future__ import annotations

from typing import List, Tuple

from rich.text import Text

#: Cores, iguais às do resto da TUI (nenhuma cor nova foi inventada).
AMBER = "#e0a458"
BRIGHT = "#d7dde5"
MUTED = "#7a8494"
FAINT = "#4d5666"

#: Rodapé de ajuda, mostrado em ambos os menus.
HINT = "↑↓ navega · enter aplica · esc cancela"


def model_rows(backend):
    """Modelos do backend ATIVO, prontos para o menu.

    Devolve (valor, nome exibido, alias técnico, descrição): o valor é o que vai
    para o `--model` ao aplicar, e as outras três são as colunas da tela — é
    assim que "Opus 5" aparece sem que a TUI aplique "Opus 5" no lugar de
    "opus". Backends que só sabem `models()` continuam funcionando.
    """
    escolhas = getattr(backend, "model_choices", None)
    if callable(escolhas):
        return [(row[0], row[1], row[2], row[3]) for row in escolhas()]
    linhas = getattr(backend, "model_rows", None)
    if callable(linhas):
        return [(row[0], row[0], row[1], row[2]) for row in linhas()]
    return [(name, name, "", "") for name in backend.models()]


def provider_rows(providers, active, backend, probe) -> List[Tuple[str, str, str]]:
    """Provedores como (nome, estado, motivo/rótulo).

    `probe(nome)` devolve um backend só para consulta; o provedor ativo usa o
    backend que já está em uso, para não instanciar nada à toa.
    """
    rows = []
    for name in providers:
        if name == active:
            if backend.available():
                rows.append((name, "ativo · pronto", backend.label))
            else:
                rows.append((
                    name,
                    "ativo · não conectado",
                    backend.unavailable_reason() or backend.label,
                ))
            continue
        candidate = probe(name)
        if candidate.available():
            rows.append((name, "configurado", candidate.label))
        else:
            rows.append((
                name,
                "não configurado",
                candidate.unavailable_reason() or "motivo desconhecido",
            ))
    return rows


def _fit(text: str, room: int) -> str:
    """Corta `text` para caber em `room` colunas, com reticências."""
    if room <= 0:
        return ""
    if len(text) <= room:
        return text
    if room == 1:
        return "…"
    return text[: room - 1] + "…"


def render_menu(title, rows, index, width) -> Text:
    """Desenha o menu: título, uma linha por item e o rodapé de ajuda.

    Cada item de `rows` termina em (esquerda, meio, direita); quando tem um
    campo a mais, o primeiro é o valor aplicado e não é desenhado. A largura
    disponível é respeitada coluna a coluna: em 80 colunas nada quebra linha.
    """
    rows = [tuple(row)[-3:] for row in rows]
    available = max(24, int(width) - 6)
    first = max([len(row[0]) for row in rows] or [0])
    second = max([len(row[1]) for row in rows] or [0])
    # o meio nunca come mais que metade do que sobra depois da primeira coluna
    second = min(second, max(8, (available - first - 4) // 2))

    text = Text()
    text.append(_fit(title, available), style=MUTED)
    for position, (left, middle, right) in enumerate(rows):
        text.append(chr(10))
        chosen = position == index
        text.append("▸ " if chosen else "  ", style=AMBER)
        text.append(
            _fit(left, first).ljust(first + 1),
            style=("bold " + BRIGHT) if chosen else BRIGHT,
        )
        text.append(_fit(middle, second).ljust(second + 1), style=MUTED)
        room = available - 2 - first - 1 - second - 1
        text.append(_fit(right, room), style=FAINT)
    text.append(chr(10))
    text.append(_fit(HINT, available), style=FAINT)
    return text
