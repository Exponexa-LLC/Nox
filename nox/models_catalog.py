# -*- coding: utf-8 -*-
"""Catálogo local de modelos do Claude — e o aviso honesto sobre ele.

ISTO É UMA LISTA MANTIDA À MÃO. A CLI do Claude Code não oferece nenhum
comando para enumerar os modelos disponíveis para a sessão e o plano
autenticados: não existe `claude models list`, o `--model` do `--help` só cita
aliases por exemplo ("e.g. 'fable', 'opus', or 'sonnet'"), o seletor `/model`
da CLI é interativo, e a Models API oficial (`GET /v1/models`) exigiria uma
credencial de API — que este projeto não usa por decisão de arquitetura.

Por isso o catálogo tem data de revisão e cada linha exibida no seletor carrega
a sua procedência. O que é descoberto de verdade, sem chamar o modelo, vive em
`model_discovery`: os aliases que a CLI instalada documenta e o estado da
autenticação. A confirmação real de disponibilidade só existe pela sonda, que é
opcional, desligada por padrão e faz chamadas de verdade.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

#: Data da última revisão manual desta lista (ISO). Aparece no seletor.
CATALOG_REVIEWED = "2026-08-19"

#: Modelos atuais: (alias da CLI, nome exibido, id técnico, descrição curta).
#: O alias é o que vai no `--model` — é ele que o seletor aplica.
CURRENT: Tuple[Tuple[str, str, str, str], ...] = (
    ("fable", "Fable 5", "claude-fable-5",
     "o mais capaz; raciocínio longo, turnos demorados"),
    ("opus", "Opus 5", "claude-opus-5",
     "forte e equilibrado para trabalho difícil do dia a dia"),
    ("sonnet", "Sonnet 5", "claude-sonnet-5",
     "rápido e barato para a maior parte das conversas"),
    ("haiku", "Haiku 4.5", "claude-haiku-4-5",
     "o mais rápido e barato, para respostas curtas"),
)

#: Modelos que já foram atuais e não devem mais aparecer como disponíveis.
#: Servem para limpar cache antigo: se um deles estiver gravado, é descartado.
SUPERSEDED: Tuple[str, ...] = (
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-opus-4-5",
    "claude-3-5-haiku", "claude-3-haiku",
    "opus-4-8", "opus-4-7", "opus-4-6", "sonnet-4-6",
)


def current_rows() -> List[Tuple[str, str, str, str]]:
    """Cópia das linhas atuais, na ordem de exibição."""
    return [tuple(row) for row in CURRENT]


def aliases() -> List[str]:
    """Aliases de CLI conhecidos por este catálogo."""
    return [alias for alias, _nome, _id, _descricao in CURRENT]


def find(alias: str) -> Optional[Tuple[str, str, str, str]]:
    """Linha do catálogo para `alias`, ou None se for desconhecido."""
    for row in CURRENT:
        if row[0] == alias:
            return tuple(row)
    return None


def is_superseded(name: str) -> bool:
    """`name` é um modelo que saiu de linha e não deve mais ser oferecido?"""
    limpo = (name or "").strip().lower()
    if not limpo:
        return False
    if limpo in SUPERSEDED:
        return True
    # variações com sufixo de data, como claude-haiku-4-5-20251001
    return any(limpo.startswith(velho + "-") for velho in SUPERSEDED)
