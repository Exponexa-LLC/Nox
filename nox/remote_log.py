# -*- coding: utf-8 -*-
"""Registro das operações remotas — só metadados, já redigidos.

Grava uma linha JSON por operação em `~/.nox/remote.log`: quando, em qual host,
qual operação, o argv com a chave mascarada, o código de saída e a duração.

O que NÃO é gravado: saída do comando, stderr cru, caminho de chave, nada que
se pareça com segredo. O log serve para você saber o que foi executado, não
para virar cópia do conteúdo dos seus servidores.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

#: Nome do arquivo dentro da pasta de configuração.
LOG_FILE = "remote.log"

#: Tamanho máximo antes de rotacionar (1 MB) — mantém só o arquivo anterior.
MAX_BYTES = 1024 * 1024

#: Coisas que nunca podem entrar no log, mesmo que apareçam por engano.
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN[^\n]*-----"),
    re.compile(r"(?i)(pass(word|phrase)|secret|token|api[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)authorization:\s*\S+"),
)


def redact_text(texto: str) -> str:
    """Apaga qualquer coisa com cara de segredo de um texto curto."""
    limpo = str(texto or "")
    for padrao in _SECRET_PATTERNS:
        limpo = padrao.sub("<redigido>", limpo)
    return limpo


def entry(host_alias: str, operation: str, params: Optional[Dict],
          argv: List[str], exit_code: int, duration: float,
          output_bytes: int, when: Optional[float] = None) -> Dict:
    """Monta o registro. `argv` já deve vir redigido pelo `remote_ssh.redact`."""
    return {
        "quando": time.strftime("%Y-%m-%dT%H:%M:%S",
                                time.localtime(when or time.time())),
        "host": str(host_alias),
        "operacao": str(operation),
        "parametros": {str(k): redact_text(v) for k, v in (params or {}).items()},
        "argv": [redact_text(token) for token in argv],
        "exit": int(exit_code),
        "duracao_s": round(float(duration), 3),
        "bytes_recebidos": int(output_bytes),
    }


def append(path: str, registro: Dict) -> bool:
    """Acrescenta uma linha JSONL. Falha em silêncio — log não derruba a TUI."""
    if not path:
        return False
    try:
        pasta = os.path.dirname(path)
        if pasta and not os.path.isdir(pasta):
            os.makedirs(pasta)
        _rotate(path)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(registro, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _rotate(path: str) -> None:
    try:
        if os.path.exists(path) and os.path.getsize(path) >= MAX_BYTES:
            anterior = path + ".1"
            if os.path.exists(anterior):
                os.remove(anterior)
            os.rename(path, anterior)
    except OSError:
        pass


def read_last(path: str, limit: int = 10) -> List[Dict]:
    """Últimos registros, para o `/remote log` mostrar o que já rodou."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            linhas = handle.readlines()[-limit:]
    except OSError:
        return []
    registros = []
    for linha in linhas:
        try:
            registros.append(json.loads(linha))
        except ValueError:
            continue
    return registros
