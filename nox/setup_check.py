# -*- coding: utf-8 -*-
"""Diagnóstico do `nox setup` — o que existe nesta máquina, sem mentir.

Verifica sistema, o próprio comando, a CLI do Claude e o estado da
autenticação. **Nunca** exibe token, e **nunca** chama o modelo: o mais longe
que vai é `claude --version` e `claude auth status`, que são locais.

Só existe um provedor funcional hoje. Este módulo diz isso com todas as letras
em vez de apresentar uma lista de provedores prontos que não existem.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Callable, Dict, List, Optional, Tuple

from . import backends
from . import config as config_mod
from . import frozen
from . import model_discovery
from . import profiles

#: Campos do `claude auth status` que podem ser exibidos. E-mail e orgId ficam
#: de fora de propósito: são dados pessoais e não fazem falta no diagnóstico.
SAFE_AUTH_FIELDS = ("loggedIn", "authMethod", "subscriptionType", "apiProvider")

OK = "ok"
AVISO = "aviso"
FALTA = "falta"


class Check(object):
    """Uma linha do diagnóstico."""

    def __init__(self, nome, estado, detalhe="", dica=""):
        self.nome = nome
        self.estado = estado
        self.detalhe = detalhe
        self.dica = dica

    def __repr__(self) -> str:  # pragma: no cover - depuração
        return "<Check {0} {1}>".format(self.nome, self.estado)


def check_sistema() -> Check:
    detalhe = "{0} {1} · Python {2}".format(
        platform.system() or "?", platform.machine() or "?",
        platform.python_version())
    if frozen.is_frozen():
        detalhe += " (executável empacotado)"
    return Check("sistema", OK, detalhe)


def check_comando() -> Check:
    """O comando `nox` está no PATH? Não estar não impede nada."""
    caminho = shutil.which("nox")
    if caminho:
        return Check("comando nox", OK, caminho)
    return Check(
        "comando nox", AVISO, "não está no PATH",
        "dá para rodar assim mesmo; o instalador cria o comando quando você pedir")


def check_claude(runner: Optional[Callable] = None) -> Tuple[Check, Dict]:
    """Presença e estado da CLI do Claude. Nenhuma chamada ao modelo."""
    executavel = shutil.which("claude")
    if not executavel:
        return (Check(
            "claude cli", FALTA, "comando `claude` não encontrado no PATH",
            "instale a CLI do Claude Code e autentique-se com `claude auth login`"),
            {})

    run = runner or model_discovery.run_cli
    versao = ""
    codigo, saida = run(["claude", "--version"])
    if codigo == 0 and saida:
        versao = saida.strip().splitlines()[0]

    codigo, saida = run(["claude", "auth", "status"])
    dados = model_discovery.parse_auth_status(saida) if codigo == 0 else {}
    if not dados:
        return (Check(
            "claude cli", AVISO, "{0} — não consegui ler o estado da conta".format(
                versao or executavel),
            "rode `claude auth status` para ver o que a CLI responde"), {})
    return (Check("claude cli", OK, versao or executavel), dados)


def check_autenticacao(dados: Dict) -> Check:
    """Estado da conta, sem exibir nada sensível."""
    if not dados:
        return Check(
            "autenticação", FALTA, "desconhecida",
            "a autenticação é sua e local: `claude auth login`. "
            "O Exponexa não copia, não pede e não guarda credencial.")
    if dados.get("loggedIn") is not True:
        return Check(
            "autenticação", FALTA, "a CLI não está autenticada",
            "rode `claude auth login` no seu terminal — eu não faço isso por você")
    partes = []
    for campo in SAFE_AUTH_FIELDS:
        if campo == "loggedIn":
            continue
        valor = dados.get(campo)
        if valor:
            partes.append(str(valor))
    return Check("autenticação", OK,
                 "conectado" + (" · " + " · ".join(partes) if partes else ""))


def check_config(path: Optional[str] = None) -> Check:
    """A configuração existente é preservada — nada é sobrescrito aqui."""
    caminho = path or config_mod.CONFIG_PATH
    if os.path.exists(caminho):
        return Check("config", OK, "{0} (preservado)".format(caminho))
    return Check("config", AVISO, "{0} ainda não existe".format(caminho),
                 "ele é criado com os padrões na primeira execução")


def provedores_funcionais() -> List[str]:
    """Provedores realmente utilizáveis agora — não é a lista do registro."""
    funcionais = []
    for nome in backends.PROVIDERS:
        if nome == "echo":
            continue  # eco local existe para teste, não é provedor de verdade
        backend = backends.get_backend(nome)
        if backend.available():
            funcionais.append(nome)
    return funcionais


def check_provedor() -> Check:
    """Escolhe o único provedor funcional, sem simular uma lista pronta."""
    funcionais = provedores_funcionais()
    planejados = [n for n in backends.PROVIDERS
                  if n not in funcionais and n != "echo"]
    if not funcionais:
        return Check(
            "provedor", FALTA, "nenhum provedor funcional",
            "o backend desta versão é o Claude, pela CLI oficial")
    if len(funcionais) == 1:
        return Check(
            "provedor", OK,
            "{0} (único funcional; {1} são pontos de extensão)".format(
                funcionais[0], ", ".join(planejados) or "nenhum outro"))
    return Check("provedor", OK, ", ".join(funcionais))


def check_perfil() -> Check:
    perfil = profiles.default()
    return Check("perfil padrão", OK,
                 "{0} · {1}".format(perfil.name, perfil.descricao))


def run_checks(runner: Optional[Callable] = None,
               config_path: Optional[str] = None) -> List[Check]:
    """Roda o diagnóstico inteiro. `runner` injetável mantém o teste offline."""
    claude, dados = check_claude(runner)
    return [
        check_sistema(),
        check_comando(),
        claude,
        check_autenticacao(dados),
        check_config(config_path),
        check_provedor(),
        check_perfil(),
    ]


def render(checks: List[Check]) -> str:
    """Relatório em texto, para o terminal."""
    simbolos = {OK: "ok  ", AVISO: "!   ", FALTA: "x   "}
    largura = max([len(c.nome) for c in checks] or [0])
    linhas = ["Exponexa · diagnóstico (nenhuma chamada ao modelo foi feita)", ""]
    for check in checks:
        linhas.append("{0}{1}  {2}".format(
            simbolos.get(check.estado, "    "), check.nome.ljust(largura),
            check.detalhe))
        if check.dica:
            linhas.append("    {0}  {1}".format(" " * largura, check.dica))
    faltando = [c for c in checks if c.estado == FALTA]
    linhas.append("")
    if faltando:
        linhas.append("pendências: " + ", ".join(c.nome for c in faltando))
    else:
        linhas.append("tudo pronto — rode `nox` para conversar.")
    return "\n".join(linhas)


def exit_code(checks: List[Check]) -> int:
    """0 quando dá para usar; 1 quando falta algo essencial."""
    return 1 if any(c.estado == FALTA for c in checks) else 0
