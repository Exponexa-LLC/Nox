# -*- coding: utf-8 -*-
"""Execução por SSH: monta o argv do OpenSSH e roda, sem shell nenhum.

Sintaxe do cliente instalado (OpenSSH_for_Windows_9.5p2):

    ssh [opções] destination [command [argument ...]]

Não existe separador `--` entre destino e comando: o que vier depois do
destino é enviado ao servidor. Por isso o argv é montado exatamente como o
manual manda, e a lista **nunca** vira string — `shell=False`, sempre.

Nenhuma senha é pedida, lida ou guardada: `BatchMode=yes` faz o ssh falhar em
vez de abrir prompt invisível dentro da TUI, e a chave é apontada por caminho
(`-i`), nunca lida por este processo.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Callable, List, Optional

from . import remote_hosts

#: Segundos até desistir do handshake e da operação inteira.
CONNECT_TIMEOUT = 10
RUN_TIMEOUT = 45

#: Opções fixas. Todas defensivas — nenhuma delas afrouxa verificação.
def ssh_options(known_hosts: str) -> List[str]:
    return [
        "-o", "BatchMode=yes",            # nunca pergunta senha/passphrase
        "-o", "IdentitiesOnly=yes",       # só a chave declarada, sem varrer o agente
        "-o", "StrictHostKeyChecking=yes",  # host novo é erro, não é decisão minha
        "-o", "UserKnownHostsFile={0}".format(known_hosts),
        "-o", "ConnectTimeout={0}".format(CONNECT_TIMEOUT),
        "-o", "LogLevel=ERROR",
        "-n",                             # stdin fechado: nada de interativo
    ]


class Result(object):
    """Resultado de uma execução remota."""

    def __init__(self, ok, exit_code, stdout="", stderr="", duration=0.0,
                 argv=None, error=""):
        self.ok = ok
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration
        self.argv = list(argv or [])
        self.error = error


def build_command(host, argv, known_hosts: Optional[str] = None) -> List[str]:
    """Argv completo do ssh para rodar `argv` em `host`.

    `ssh [opções] -i <identity> -p <port> user@hostname <token>...` — sem `--`,
    porque o OpenSSH o mandaria para o shell do servidor como argumento.
    """
    if not argv:
        raise ValueError("argv remoto vazio")
    caminho = remote_hosts.require_known_hosts(known_hosts)
    comando = ["ssh"]
    comando += ssh_options(caminho)
    comando += ["-i", host.identity, "-p", str(host.port), host.destination()]
    comando += [str(token) for token in argv]
    return comando


def redact(comando) -> List[str]:
    """Argv para log: o caminho da chave vira um marcador."""
    limpo = []
    pular = False
    for token in comando:
        if pular:
            limpo.append("<identity>")
            pular = False
            continue
        if token == "-i":
            limpo.append(token)
            pular = True
            continue
        limpo.append(str(token))
    return limpo


def friendly_error(exit_code: int, stderr: str) -> str:
    """Traduz a falha do ssh sem despejar stderr cru na tela."""
    texto = (stderr or "").lower()
    if "permission denied" in texto or "publickey" in texto:
        return ("autenticação recusada. Se a chave exigir passphrase, o "
                "BatchMode não consegue destravá-la: configure o ssh-agent "
                "manualmente, numa etapa à parte — eu não mexo em serviços.")
    if "host key verification failed" in texto or "known_hosts" in texto:
        return ("host não confere com o known_hosts. Registre-o você mesmo "
                "fora do Exponexa; eu não aceito host novo automaticamente.")
    if "could not resolve" in texto or "name or service not known" in texto:
        return "não consegui resolver o endereço do host."
    if "connection refused" in texto:
        return "conexão recusada na porta indicada."
    if "connection timed out" in texto or "timed out" in texto:
        return "tempo esgotado ao conectar."
    if "no such identity" in texto or "identity file" in texto:
        return "a chave apontada em identity não pôde ser usada."
    if exit_code == 127:
        return "o comando não existe no servidor."
    return "falha na execução remota (código {0}).".format(exit_code)


def run(host, argv, runner: Optional[Callable] = None,
        known_hosts: Optional[str] = None, timeout: float = RUN_TIMEOUT) -> Result:
    """Executa uma operação já validada. `runner` injetável para teste."""
    comando = build_command(host, argv, known_hosts)
    executor = runner or run_process
    inicio = time.time()
    codigo, saida, erro = executor(comando, timeout)
    duracao = time.time() - inicio
    return Result(
        ok=codigo == 0,
        exit_code=codigo,
        stdout=saida,
        stderr=erro,
        duration=duracao,
        argv=redact(comando),
        error="" if codigo == 0 else friendly_error(codigo, erro),
    )


def run_process(comando: List[str], timeout: float):
    """Roda o ssh de verdade. Lista de argumentos, `shell=False`, sempre."""
    alvo = list(comando)
    resolvido = shutil.which(alvo[0])
    if not resolvido:
        return 127, "", "ssh não encontrado no PATH"
    alvo[0] = resolvido
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        processo = subprocess.Popen(
            alvo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            env=_clean_env(),
        )
        saida, erro = processo.communicate(timeout=timeout)
        return (processo.returncode,
                saida.decode("utf-8", "replace") if saida else "",
                erro.decode("utf-8", "replace") if erro else "")
    except subprocess.TimeoutExpired:
        try:
            processo.kill()
        except OSError:
            pass
        return 124, "", "connection timed out"
    except (OSError, subprocess.SubprocessError) as falha:
        return 1, "", str(falha)


def _clean_env():
    """Ambiente do filho sem credencial de modelo — como o backend já faz."""
    ambiente = dict(os.environ)
    for chave in list(ambiente):
        if chave.upper().startswith("ANTHROPIC_"):
            ambiente.pop(chave, None)
    return ambiente
