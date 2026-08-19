# -*- coding: utf-8 -*-
"""Catálogo de perfis do 🐺 Exponexa — dados, não mecanismo.

Três perfis embutidos, e a possibilidade de você declarar os seus em
`~/.nox/profiles.json` (arquivo opcional, que a aplicação nunca cria sozinha).

Sobre `conversa` × `desenvolvimento`: hoje eles diferem em **um** item —
`models.probe`, a única capacidade local extra que realmente existe. Não há
diferença inventada, porque **ainda não existem ferramentas locais**. Quando
elas forem implementadas (`local.tools`), é em `desenvolvimento` que nascem,
sob confirmação, e a distância entre os dois perfis aumenta de verdade.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from . import policy as policy_mod

#: Nome do arquivo opcional de perfis do usuário, dentro de `~/.nox`.
PROFILES_FILE = "profiles.json"

#: Perfil usado por padrão e como queda segura — o mais contido.
DEFAULT_PROFILE = "conversa"

#: Capacidades comuns a todos: conversa e o básico da interface.
_BASE_CAPS = ("chat", "model.switch", "provider.switch", "workspace.switch",
              "models.discover")


def _builtin() -> Tuple[policy_mod.Policy, ...]:
    return (
        policy_mod.Policy(
            name="conversa",
            descricao="conversa, modelos e workspace; sem ferramentas, sem remoto",
            capabilities=_BASE_CAPS,
            remote_operations=(),
            require_remote_log=False,
        ),
        policy_mod.Policy(
            name="desenvolvimento",
            descricao=("máxima flexibilidade local desta versão; ferramentas "
                       "locais ainda não existem"),
            capabilities=_BASE_CAPS + ("models.probe",),
            remote_operations=(),
            require_remote_log=False,
        ),
        policy_mod.Policy(
            name="diagnostico-remoto",
            descricao="as nove leituras remotas, com confirmação e trilha",
            capabilities=_BASE_CAPS + ("remote.read",),
            remote_operations=policy_mod.CAPS.REMOTE_OPERATIONS,
            require_remote_log=True,
        ),
    )


#: Perfis embutidos, já passados pelos tetos.
BUILTIN: Tuple[policy_mod.Policy, ...] = tuple(
    policy_mod.enforce(perfil)[0] for perfil in _builtin())


def names() -> List[str]:
    return [perfil.name for perfil in BUILTIN]


def find(name: str, extras=None) -> Optional[policy_mod.Policy]:
    """Perfil pelo nome, entre os embutidos e os seus."""
    for perfil in list(extras or ()) + list(BUILTIN):
        if perfil.name == name:
            return perfil
    return None


def default() -> policy_mod.Policy:
    perfil = find(DEFAULT_PROFILE)
    assert perfil is not None, "o perfil padrão precisa existir"
    return perfil


def rows(extras=None):
    """Linhas do seletor: (valor, exibido, capacidades, descrição)."""
    linhas = []
    for perfil in list(BUILTIN) + list(extras or ()):
        marcas = []
        if perfil.allows("remote.read"):
            marcas.append("remoto")
        if perfil.allows("models.probe"):
            marcas.append("sonda")
        linhas.append((perfil.name, perfil.name,
                       "+".join(marcas) if marcas else "local",
                       perfil.descricao))
    return linhas


def load_user_profiles(path: str) -> Tuple[List[policy_mod.Policy], List[str]]:
    """Lê `~/.nox/profiles.json`, se existir. Sem arquivo, nada acontece.

    Cada perfil declara uma `base` entre os embutidos e escolhe capacidades do
    registro da aplicação — podendo ligar mais do que a base liga, desde que os
    tetos permitam. Nada aqui cria ferramenta nova nem toca em segredo.
    """
    if not path or not os.path.exists(path):
        return [], []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            dados = json.load(handle)
    except (OSError, ValueError) as erro:
        return [], ["não consegui ler {0}: {1}".format(path, erro)]

    lista = dados.get("profiles", dados.get("perfis")) if isinstance(dados, dict) else dados
    if not isinstance(lista, list):
        return [], ['{0} deve conter uma lista em "profiles".'.format(path)]

    perfis, avisos, vistos = [], [], set(names())
    for bruto in lista:
        if not isinstance(bruto, dict):
            avisos.append("perfil inválido ignorado (não é objeto).")
            continue
        nome_base = str(bruto.get("base") or DEFAULT_PROFILE)
        base = find(nome_base)
        if base is None:
            avisos.append(
                "base desconhecida {0!r}; usando {1}.".format(
                    nome_base, DEFAULT_PROFILE))
            base = default()
        perfil, problemas = policy_mod.from_dict(base, bruto)
        avisos.extend(problemas)
        if perfil.name in vistos:
            avisos.append(
                "perfil {0!r} ignorado: nome já existe.".format(perfil.name))
            continue
        vistos.add(perfil.name)
        perfis.append(perfil)
    return perfis, avisos


def resolve(name: str, extras=None) -> Tuple[policy_mod.Policy, List[str]]:
    """Perfil pedido, ou o padrão com aviso — nunca cai no permissivo."""
    perfil = find(name, extras)
    if perfil is None:
        return default(), [
            "perfil desconhecido: {0} — usando {1}.".format(
                name, DEFAULT_PROFILE)]
    return perfil, []


#: Modelo mostrado a quem quiser declarar perfis próprios.
EXAMPLE = """{
  "profiles": [
    {"nome": "leitura-vps", "base": "conversa",
     "capacidades": ["chat", "model.switch", "remote.read"],
     "remote_operations": ["conexao", "disco", "log"],
     "require_remote_log": true, "max_output_lines": 60}
  ]
}"""
