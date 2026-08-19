# -*- coding: utf-8 -*-
"""Guarda de versão: a tag, o pacote e o pyproject têm de dizer o mesmo.

Uma release publicada com versão divergente é difícil de desfazer — o
instalador baixa `v0.7.0` e o binário se apresenta como `0.6.0`, e ninguém
entende por quê. Este script roda antes do build e falha cedo.

Uso:

    python packaging/version_check.py            # só compara pacote × pyproject
    python packaging/version_check.py v0.7.0     # compara também com a tag
"""

from __future__ import annotations

import os
import re
import sys
from typing import Optional, Tuple

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Semver simples, com `v` opcional na tag: v1.2.3 ou 1.2.3.
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def package_version(raiz: Optional[str] = None) -> str:
    """Lê `__version__` sem importar o pacote (evita puxar dependências)."""
    caminho = os.path.join(raiz or RAIZ, "nox", "__init__.py")
    with open(caminho, "r", encoding="utf-8") as handle:
        for linha in handle:
            achado = re.match(r'\s*__version__\s*=\s*["\']([^"\']+)["\']', linha)
            if achado:
                return achado.group(1)
    raise SystemExit("não achei __version__ em {0}".format(caminho))


def pyproject_version(raiz: Optional[str] = None) -> str:
    """Lê `version` do bloco [project] do pyproject, sem parser TOML."""
    caminho = os.path.join(raiz or RAIZ, "pyproject.toml")
    dentro = False
    with open(caminho, "r", encoding="utf-8") as handle:
        for linha in handle:
            limpo = linha.strip()
            if limpo.startswith("["):
                dentro = limpo == "[project]"
                continue
            if dentro:
                achado = re.match(r'version\s*=\s*["\']([^"\']+)["\']', limpo)
                if achado:
                    return achado.group(1)
    raise SystemExit("não achei version em {0}".format(caminho))


def normalize_tag(tag: str) -> str:
    """`v0.7.0` -> `0.7.0`. Tag fora do semver é recusada."""
    achado = SEMVER.match((tag or "").strip())
    if not achado:
        raise SystemExit(
            "tag fora do padrão semver: {0!r} — use algo como v0.7.0".format(tag))
    return ".".join(achado.groups())


def check(tag: Optional[str] = None, raiz: Optional[str] = None) -> Tuple[str, str]:
    """Compara as versões. Levanta SystemExit na divergência."""
    pacote = package_version(raiz)
    projeto = pyproject_version(raiz)
    if not SEMVER.match(pacote):
        raise SystemExit("versão do pacote fora do semver: {0!r}".format(pacote))
    if pacote != projeto:
        raise SystemExit(
            "divergência: nox/__init__.py diz {0} e pyproject.toml diz {1}".format(
                pacote, projeto))
    if tag:
        da_tag = normalize_tag(tag)
        if da_tag != pacote:
            raise SystemExit(
                "divergência: tag {0} e versão {1}".format(tag, pacote))
    return pacote, projeto


def main(argv=None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)

    # `--print` existe para o CI não precisar de um one-liner com regex e
    # aspas aninhadas — que quebra de shell para shell.
    if argumentos and argumentos[0] in ("--print", "-p"):
        print(package_version())
        return 0

    # `GITHUB_REF_NAME` está SEMPRE definido no GitHub Actions: num run de
    # branch ele vale "main", e usá-lo como reserva fazia a conferência tentar
    # validar "main" como semver e derrubar o build. Só vale quando a ref é
    # mesmo uma tag.
    ref_type = os.environ.get("GITHUB_REF_TYPE", "")
    padrao = os.environ.get("GITHUB_REF_NAME", "") if ref_type == "tag" else ""
    tag = argumentos[0] if argumentos else padrao
    pacote, _projeto = check(tag or None)
    print("versão conferida: {0}{1}".format(
        pacote, " (tag {0})".format(tag) if tag else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
