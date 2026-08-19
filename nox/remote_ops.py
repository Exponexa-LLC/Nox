# -*- coding: utf-8 -*-
"""Allowlist de operações remotas — só leitura, só argv fixo.

Não existe caminho neste módulo para comando arbitrário: cada operação tem uma
lista de tokens fixa, e os poucos parâmetros aceitos passam por regex estrita.
Não há redirecionamento, pipe, `sudo`, escrita, nem shell interativo — o modo
somente leitura não é uma opção que se desliga, é a ausência de código.

`conexao` é apenas a prova de que a autenticação funciona e um comando fixo
roda: executa `true` no servidor e olha o código de saída.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

#: Caracteres que denunciam tentativa de sair da allowlist.
SHELL_METACHARS = ";|&$`><\\\n\r\t\"'*?()[]{}!#~ "

#: Tokens de comando que jamais podem aparecer numa operação.
FORBIDDEN_TOKENS = ("sudo", "su", "sh", "bash", "zsh", "rm", "mv", "cp", "dd",
                    "tee", "chmod", "chown", "kill", "reboot", "shutdown",
                    "apt", "yum", "curl", "wget", "nc", "python", "perl")

_UNIT = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


class OpError(ValueError):
    """Operação desconhecida ou parâmetro recusado."""


class Operation(object):
    """Uma operação de leitura autorizada."""

    def __init__(self, name, label, descricao, tokens, param=None,
                 param_label="", lines=False):
        self.name = name
        self.label = label
        self.descricao = descricao
        self.tokens = tuple(tokens)
        self.param = param          # None, ou o nome do parâmetro exigido
        self.param_label = param_label
        self.lines = lines          # aceita número de linhas?

    def needs_param(self) -> bool:
        return self.param is not None


#: As nove operações da fase 1, na ordem em que aparecem no menu.
OPERATIONS: Tuple[Operation, ...] = (
    Operation("conexao", "conexão", "testa autenticação e execução",
              ("true",)),
    Operation("hostname", "hostname", "nome completo da máquina",
              ("hostname", "-f")),
    Operation("sistema", "sistema", "kernel e arquitetura",
              ("uname", "-a")),
    Operation("uptime", "uptime", "há quanto tempo está de pé",
              ("uptime",)),
    Operation("disco", "disco", "uso dos sistemas de arquivos",
              ("df", "-h")),
    Operation("processos", "processos", "processos por uso de CPU",
              ("ps", "aux", "--sort=-%cpu")),
    Operation("servico", "serviço", "status de um serviço systemd",
              ("systemctl", "status", "--no-pager"),
              param="unidade", param_label="<unidade>"),
    Operation("containers", "containers", "containers docker em execução",
              ("docker", "ps")),
    Operation("log", "log", "últimas linhas do journal de uma unidade",
              ("journalctl", "--no-pager", "-u"),
              param="unidade", param_label="<unidade> [linhas]", lines=True),
)

#: Primeiro token de cada operação — tudo que o servidor pode ver executando.
READ_ONLY_COMMANDS = frozenset(op.tokens[0] for op in OPERATIONS)


def find(name: str) -> Optional[Operation]:
    for operation in OPERATIONS:
        if operation.name == name:
            return operation
    return None


def rows():
    """Linhas para o menu: (valor, exibido, parâmetro, descrição)."""
    return [(op.name, op.label, op.param_label, op.descricao)
            for op in OPERATIONS]


def _reject_metachars(valor: str, campo: str) -> None:
    for caractere in valor:
        if caractere in SHELL_METACHARS or ord(caractere) < 32:
            raise OpError(
                "{0} inválido: {1!r} não é aceito".format(campo, caractere))
    if ".." in valor or "/" in valor:
        raise OpError("{0} inválido: caminho não é aceito aqui".format(campo))


def validate_unit(valor) -> str:
    """Nome de unidade/serviço: estrito, e sem nada que o shell interprete."""
    if not isinstance(valor, str) or not valor:
        raise OpError("informe a unidade (ex.: nginx)")
    _reject_metachars(valor, "unidade")
    if not _UNIT.match(valor):
        raise OpError(
            "unidade inválida: {0!r} — use letras, números, . _ @ -".format(valor))
    return valor


def validate_lines(valor, padrao: int = 100) -> int:
    if valor in (None, "", []):
        return padrao
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        raise OpError("número de linhas inválido: {0!r}".format(valor))
    if not 1 <= numero <= 500:
        raise OpError("número de linhas fora da faixa 1–500: {0}".format(numero))
    return numero


def build_argv(name: str, params: Optional[Dict] = None) -> List[str]:
    """Monta o argv remoto de uma operação autorizada.

    Devolve uma LISTA de tokens — quem executa nunca junta isso numa string.
    """
    operation = find(name)
    if operation is None:
        raise OpError("operação desconhecida: {0!r}".format(name))
    params = params or {}
    argv = list(operation.tokens)

    if operation.needs_param():
        argv.append(validate_unit(params.get(operation.param)))
    if operation.lines:
        argv.extend(["-n", str(validate_lines(params.get("linhas")))])

    audit(argv)
    return argv


def audit(argv) -> None:
    """Última barreira: nada fora da allowlist sai daqui."""
    if not argv:
        raise OpError("argv vazio")
    if argv[0] not in READ_ONLY_COMMANDS:
        raise OpError("comando fora da allowlist: {0!r}".format(argv[0]))
    for token in argv:
        minusculo = str(token).lower()
        if minusculo in FORBIDDEN_TOKENS:
            raise OpError("token proibido: {0!r}".format(token))
        for caractere in str(token):
            if caractere in ";|&$`><\\\n\r" or ord(caractere) < 32:
                raise OpError("metacaractere em {0!r}".format(token))
