# -*- coding: utf-8 -*-
"""Catálogo de hosts remotos autorizados — validação, sem nenhum segredo.

O arquivo `~/.nox/hosts.json` é escrito **pelo usuário**: ele é o único lugar
com usuário e endereço dos servidores. Aqui só se valida o que foi declarado.

Nada de credencial passa por este módulo: guardamos o *caminho* da chave, nunca
o conteúdo — a chave não é lida, só entregue ao `ssh` pelo `-i`. Campos com
cara de segredo (senha, passphrase, token, chave privada) fazem o host inteiro
ser recusado, com erro explícito.
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Tuple

#: Nome do arquivo, dentro da pasta de configuração (`~/.nox`).
HOSTS_FILE = "hosts.json"

#: Campos que jamais podem aparecer num host declarado.
FORBIDDEN_FIELDS = (
    "password", "passwd", "senha", "passphrase", "token", "secret",
    "private_key", "privatekey", "key_data", "credential", "credentials",
)

#: Campos aceitos. Qualquer outro é recusado — nada entra por engano.
ALLOWED_FIELDS = (
    "alias", "user", "hostname", "port", "identity", "descricao", "description",
)

_ALIAS = re.compile(r"^[a-z0-9_-]+$")
_USER = re.compile(r"^[^\s\\/@]+$")
_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

#: Marcas de conteúdo de chave privada, para recusar colagem indevida.
KEY_MARKERS = ("-----BEGIN", "PRIVATE KEY", "ssh-rsa ", "ssh-ed25519 ")


class HostError(ValueError):
    """Host declarado de forma inválida ou insegura."""


class Host(object):
    """Um servidor autorizado, já validado."""

    def __init__(self, alias, user, hostname, port, identity, descricao=""):
        self.alias = alias
        self.user = user
        self.hostname = hostname
        self.port = port
        self.identity = identity
        self.descricao = descricao

    def destination(self) -> str:
        """`user@hostname`, como o OpenSSH espera no lugar do destino."""
        return "{0}@{1}".format(self.user, self.hostname)

    def label(self) -> str:
        return "{0}:{1}".format(self.destination(), self.port)

    def __repr__(self) -> str:  # pragma: no cover - depuração
        return "<Host {0} {1}>".format(self.alias, self.label())


# ------------------------------------------------------------- validadores


def _sem_controle(valor: str) -> bool:
    return not any(ord(caractere) < 32 or ord(caractere) == 127
                   for caractere in valor)


def valid_alias(valor) -> str:
    if not isinstance(valor, str) or not _ALIAS.match(valor or ""):
        raise HostError(
            "alias inválido: {0!r} — use apenas a-z, 0-9, _ e -".format(valor))
    return valor


def valid_user(valor) -> str:
    if not isinstance(valor, str) or not valor:
        raise HostError("user é obrigatório")
    if not _sem_controle(valor) or not _USER.match(valor) or len(valor) > 64:
        raise HostError(
            "user inválido: {0!r} — sem espaço, @, barra ou controle".format(valor))
    return valor


def valid_hostname(valor) -> str:
    if not isinstance(valor, str) or not valor:
        raise HostError("hostname é obrigatório")
    if not _sem_controle(valor) or len(valor) > 253:
        raise HostError("hostname inválido: caractere de controle ou longo demais")
    for proibido in (" ", "@", "\\", "/", "\t"):
        if proibido in valor:
            raise HostError(
                "hostname inválido: {0!r} contém {1!r}".format(valor, proibido))
    if valor.startswith("-"):
        raise HostError(
            "hostname não pode começar com hífen: {0!r} "
            "(seria lido como opção pelo ssh)".format(valor))
    if _is_ipv4(valor):
        return valor
    rotulos = valor.rstrip(".").split(".")
    for rotulo in rotulos:
        if not _LABEL.match(rotulo):
            raise HostError("hostname inválido: rótulo {0!r}".format(rotulo))
    # último rótulo todo numérico só existe em IPv4 — e este aqui não é um,
    # senão já teria sido aceito acima (ex.: 256.1.1.1.1, 999.999.999.999)
    if rotulos[-1].isdigit():
        raise HostError(
            "hostname inválido: {0!r} não é IPv4 válido nem nome DNS".format(valor))
    return valor


def _is_ipv4(valor: str) -> bool:
    partes = valor.split(".")
    if len(partes) != 4:
        return False
    for parte in partes:
        if not parte.isdigit() or not 0 <= int(parte) <= 255:
            return False
        if len(parte) > 1 and parte[0] == "0":
            return False
    return True


def valid_port(valor) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise HostError("port deve ser inteiro: {0!r}".format(valor))
    if not 1 <= valor <= 65535:
        raise HostError("port fora da faixa 1–65535: {0}".format(valor))
    return valor


def valid_identity(valor) -> str:
    """Expande `~` e exige um arquivo regular — sem abrir nem ler nada."""
    if not isinstance(valor, str) or not valor:
        raise HostError("identity é obrigatório (caminho da chave)")
    if any(marca in valor for marca in KEY_MARKERS):
        raise HostError(
            "identity deve ser o CAMINHO da chave, nunca o conteúdo dela")
    caminho = os.path.abspath(os.path.expanduser(valor))
    if not os.path.exists(caminho):
        raise HostError("chave não encontrada: {0}".format(caminho))
    if not os.path.isfile(caminho):
        raise HostError("identity não é um arquivo: {0}".format(caminho))
    return caminho


def known_hosts_path() -> str:
    """Caminho absoluto de `~/.ssh/known_hosts`."""
    return os.path.abspath(
        os.path.join(os.path.expanduser("~"), ".ssh", "known_hosts"))


def require_known_hosts(path: Optional[str] = None) -> str:
    """Exige o known_hosts: sem ele não há verificação de host, logo não há ida."""
    caminho = path or known_hosts_path()
    if not os.path.isfile(caminho):
        raise HostError(
            "known_hosts não encontrado em {0} — conecte-se uma vez fora do "
            "Exponexa para registrar o host; eu não aceito host novo "
            "automaticamente.".format(caminho))
    return caminho


def parse_host(raw) -> Host:
    """Valida um host declarado e devolve o objeto pronto."""
    if not isinstance(raw, dict):
        raise HostError("cada host deve ser um objeto JSON")
    for campo in raw:
        minusculo = str(campo).lower()
        if any(proibido in minusculo for proibido in FORBIDDEN_FIELDS):
            raise HostError(
                "campo proibido em hosts.json: {0!r} — segredos não são "
                "aceitos aqui".format(campo))
        if minusculo not in ALLOWED_FIELDS:
            raise HostError("campo desconhecido em hosts.json: {0!r}".format(campo))
    for valor in raw.values():
        if isinstance(valor, str) and any(m in valor for m in KEY_MARKERS):
            raise HostError("conteúdo de chave detectado em hosts.json")
    return Host(
        alias=valid_alias(raw.get("alias")),
        user=valid_user(raw.get("user")),
        hostname=valid_hostname(raw.get("hostname")),
        port=valid_port(raw.get("port", 22)),
        identity=valid_identity(raw.get("identity")),
        descricao=str(raw.get("descricao") or raw.get("description") or ""),
    )


def load_hosts(path: str) -> Tuple[List[Host], List[str]]:
    """Lê o arquivo e devolve (hosts válidos, problemas encontrados)."""
    problemas: List[str] = []
    if not path or not os.path.exists(path):
        return [], ["nenhum host declarado em {0}".format(path)]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            dados = json.load(handle)
    except (OSError, ValueError) as erro:
        return [], ["não consegui ler {0}: {1}".format(path, erro)]

    lista = dados.get("hosts") if isinstance(dados, dict) else dados
    if not isinstance(lista, list):
        return [], ["{0} deve conter uma lista em \"hosts\"".format(path)]

    hosts, vistos = [], set()
    for item in lista:
        try:
            host = parse_host(item)
        except HostError as erro:
            problemas.append(str(erro))
            continue
        if host.alias in vistos:
            problemas.append("alias repetido: {0}".format(host.alias))
            continue
        vistos.add(host.alias)
        hosts.append(host)
    return hosts, problemas


def find(hosts, alias: str) -> Optional[Host]:
    for host in hosts:
        if host.alias == alias:
            return host
    return None


#: Modelo mostrado na TUI quando não há hosts declarados. Os perfis são os dois
#: já existentes em ~/.ssh; usuário e endereço ficam para você preencher.
EXAMPLE = """{
  "hosts": [
    {"alias": "codeplay", "user": "SEU_USUARIO", "hostname": "SEU_HOST",
     "port": 22, "identity": "~/.ssh/codeplay_vps", "descricao": "VPS codeplay"},
    {"alias": "vellar", "user": "SEU_USUARIO", "hostname": "SEU_HOST",
     "port": 22, "identity": "~/.ssh/vellar_vps", "descricao": "VPS vellar"}
  ]
}"""
