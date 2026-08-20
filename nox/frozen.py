# -*- coding: utf-8 -*-
"""Suporte a execução congelada (PyInstaller) — caminhos e ambiente.

Duas coisas mudam quando o Exponexa roda como executável empacotado:

1. **Recursos ao lado do módulo somem do lugar esperado.** `theme.tcss` é
   resolvido em relação ao arquivo do módulo; congelado, ele vive dentro do
   bundle (`sys._MEIPASS`). `resource_path` cobre os dois casos.

2. **O runtime injeta variáveis que os processos-filhos herdam.** O PyInstaller
   altera `LD_LIBRARY_PATH`/`DYLD_LIBRARY_PATH` para achar as próprias
   bibliotecas e guarda o valor anterior em `*_ORIG`. Se essas variáveis
   vazarem para o `claude` ou para o `ssh`, eles podem carregar bibliotecas
   erradas e falhar de um jeito difícil de diagnosticar. `clean_env` desfaz
   isso — mesma disciplina que já aplicamos às `ANTHROPIC_*`.

Módulo puro: sem rede, sem subprocesso, sem estado global.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, Optional

#: Variáveis que o PyInstaller reescreve e restaura por `<nome>_ORIG`.
RESTORED_VARS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
                 "LIBPATH", "SHLIB_PATH")

#: Variáveis internas do bundle que nunca devem chegar a um processo-filho.
INTERNAL_PREFIXES = ("_MEI", "_PYI")


def is_frozen() -> bool:
    """Estamos rodando dentro de um bundle do PyInstaller?"""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def bundle_dir() -> Optional[str]:
    """Raiz dos dados embutidos, ou None fora do bundle."""
    return getattr(sys, "_MEIPASS", None) if is_frozen() else None


def resource_path(name: str, module_file: Optional[str] = None) -> str:
    """Caminho absoluto de um recurso que acompanha o pacote.

    Fora do bundle, resolve ao lado do módulo (o comportamento de sempre).
    Dentro, procura em `<bundle>/nox/<nome>` e, como reserva, na raiz do
    bundle — as duas disposições que um `.spec` pode produzir.
    """
    raiz = bundle_dir()
    if raiz:
        candidatos = (os.path.join(raiz, "nox", name), os.path.join(raiz, name))
        for caminho in candidatos:
            if os.path.exists(caminho):
                return caminho
        return candidatos[0]  # inexistente: o erro do Textual aponta o esperado
    base = os.path.dirname(os.path.abspath(module_file or __file__))
    return os.path.join(base, name)


def configure_console():
    """Alinha a saída do processo com o console, no Windows.

    O problema real: o console desta máquina responde CP850, mas o Python usa
    a página ANSI (cp1252) quando a saída é capturada por um pipe. Quem lê
    depois — PowerShell, terminal, arquivo — decodifica com uma terceira
    página, e "não" chega como "nÆo".

    A saída passa a ser UTF-8 e o console é avisado disso (CP 65001), de modo
    que emissor e leitor concordem. Vale para o Windows PowerShell 5.1, o
    PowerShell 7 e o executável congelado.

    Não mexe em código de saída, não engole exceção de ninguém e devolve uma
    função que restaura o codepage anterior — a alteração é da sessão de
    console, e devolvê-la é responsabilidade nossa.
    """
    if sys.platform != "win32":
        return lambda: None

    desfazer = []
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        anterior = kernel32.GetConsoleOutputCP()
        if anterior and anterior != 65001 and kernel32.SetConsoleOutputCP(65001):
            desfazer.append(lambda: kernel32.SetConsoleOutputCP(anterior))
    except Exception:
        pass  # sem console (serviço, pipe puro): seguimos só com o reconfigure

    for fluxo in (sys.stdout, sys.stderr):
        try:
            # `errors="replace"` para um caractere exótico nunca derrubar um
            # diagnóstico — perder um glifo é melhor que perder a mensagem.
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    def restaurar():
        for acao in desfazer:
            try:
                acao()
            except Exception:
                pass

    return restaurar


def clean_env(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Ambiente sem os rastros do bundle, para entregar a um processo-filho.

    Fora do bundle é uma cópia fiel: nada é removido de quem não foi congelado.
    """
    ambiente = dict(os.environ if env is None else env)
    if not is_frozen():
        return ambiente

    for nome in RESTORED_VARS:
        anterior = ambiente.pop(nome + "_ORIG", None)
        if anterior:
            ambiente[nome] = anterior      # devolve o valor de antes do bundle
        else:
            ambiente.pop(nome, None)       # não havia valor: sai de cena
    for chave in list(ambiente):
        if any(chave.startswith(prefixo) for prefixo in INTERNAL_PREFIXES):
            ambiente.pop(chave, None)
    return ambiente
