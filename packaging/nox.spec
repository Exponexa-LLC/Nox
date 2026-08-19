# -*- mode: python ; coding: utf-8 -*-
"""Empacotamento do Exponexa com PyInstaller, em modo onedir.

Onedir e não onefile, de propósito:

- **inicia rápido** — onefile extrai o bundle inteiro para uma pasta temporária
  a cada execução, o que numa TUI aparece como atraso na abertura;
- **evita falso positivo de antivírus**, muito mais comum com o extrator do
  onefile;
- **facilita diagnóstico** — dá para olhar os arquivos que foram parar lá.

Build:

    pyinstaller packaging/nox.spec --noconfirm --clean

Resultado: `dist/nox/` com o executável e as dependências ao lado.
"""

import os

from PyInstaller.utils.hooks import collect_all

RAIZ = os.path.abspath(os.path.join(os.getcwd()))
PACOTE = os.path.join(RAIZ, "nox")

# O Textual carrega widgets, drivers e temas por importação dinâmica; sem o
# collect_all, o bundle sobe sem metade da biblioteca e quebra em runtime.
textual_datas, textual_binarios, textual_ocultos = collect_all("textual")
rich_datas, rich_binarios, rich_ocultos = collect_all("rich")

# O tema é lido em runtime por CSS_PATH. Vai para `nox/` dentro do bundle,
# que é o primeiro lugar onde `frozen.resource_path` procura.
datas = textual_datas + rich_datas + [
    (os.path.join(PACOTE, "theme.tcss"), "nox"),
]

analysis = Analysis(
    # O entry point importa `nox` como PACOTE. Apontar direto para
    # `nox/__main__.py` faria o PyInstaller rodá-lo como script de topo, e os
    # imports relativos quebrariam com "no known parent package".
    [os.path.join(RAIZ, "packaging", "entry.py")],
    pathex=[RAIZ],
    binaries=textual_binarios + rich_binarios,
    datas=datas,
    hiddenimports=textual_ocultos + rich_ocultos,
    hookspath=[],
    runtime_hooks=[],
    # As suítes de teste não vão para o executável do usuário.
    excludes=["tkinter", "unittest", "pydoc", "pytest", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="nox",
    debug=False,
    strip=False,
    upx=False,          # UPX aumenta muito o falso positivo de antivírus
    console=True,       # é uma TUI: precisa de console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,   # o runner define a arquitetura
    codesign_identity=None,
    entitlements_file=None,
)

COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="nox",
)
