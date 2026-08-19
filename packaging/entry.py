# -*- coding: utf-8 -*-
"""Ponto de entrada do executável empacotado.

O PyInstaller executa o script informado como módulo de topo. Se apontássemos
direto para `nox/__main__.py`, os imports relativos (`from . import ...`)
quebrariam com "attempted relative import with no known parent package" — o
pacote precisa ser importado como pacote.

Este arquivo existe só para isso: importar `nox.__main__` e chamar `main()`.
Fora do bundle, nada aqui é usado — `python -m nox` e o entry point do
`pyproject.toml` continuam apontando para `nox.__main__:main`.
"""

import sys

from nox.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
